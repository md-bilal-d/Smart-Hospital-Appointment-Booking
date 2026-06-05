import os
import io
from fpdf import FPDF
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_file
from flask_login import login_required, current_user
from models import db, Doctor, Department, Slot, Appointment, QueueLog, Patient, Prescription, Review, MedicalRecord, VitalsReading, Invoice, Article, WaitlistEntry
from smart_scheduling import recommend_slot, recommend_alternative_doctor, assign_token
from datetime import datetime, date, timedelta
from extensions import socketio
from utils import audit_logger, predict_no_show
from werkzeug.utils import secure_filename
patient_bp = Blueprint('patient', __name__)
@patient_bp.route('/book')
@login_required
def book():
    departments = Department.query.all()
    dept_id = request.args.get('department', type=int)
    doctor_id = request.args.get('doctor_id', type=int)
    
    active_appts = Appointment.query.filter_by(patient_id=current_user.id).filter(
        Appointment.status.in_(['waiting', 'called'])
    ).all()
    
    if dept_id:
        doctors = Doctor.query.filter_by(department_id=dept_id, is_available=True).all()
    else:
        doctors = Doctor.query.filter_by(is_available=True).all()
        
    # Add recommendation info
    for doc in doctors:
        rec = recommend_slot(doc.id)
        doc.recommended_slot = rec
        
    preselected_doctor = None
    if doctor_id:
        preselected_doctor = Doctor.query.get(doctor_id)
        
    return render_template('book.html', departments=departments, doctors=doctors,
                           selected_dept=dept_id, active_appts=active_appts, current_step=1,
                           preselected_doctor=preselected_doctor)

@patient_bp.route('/doctor/<int:doctor_id>/profile')
@login_required
def doctor_profile(doctor_id):
    doctor = Doctor.query.get_or_404(doctor_id)
    reviews = Review.query.filter_by(doctor_id=doctor_id).order_by(Review.created_at.desc()).all()
    
    # Calculate star distribution
    distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in reviews:
        if r.rating in distribution:
            distribution[r.rating] += 1
            
    total_reviews = len(reviews)
    dist_pct = {}
    for stars in range(1, 6):
        count = distribution[stars]
        dist_pct[stars] = round((count / total_reviews * 100), 1) if total_reviews else 0
        
    # Process reviews list with masked patient names
    processed_reviews = []
    for r in reviews:
        name_parts = r.patient.name.split()
        masked_name = name_parts[0] + ' ' + (name_parts[-1][0] + '.' if len(name_parts) > 1 else '')
        processed_reviews.append({
            'patient_name': masked_name,
            'rating': r.rating,
            'feedback': r.feedback or '',
            'created_at': r.created_at.strftime('%d %b %Y')
        })
        
    return render_template('doctor_profile.html', doctor=doctor, reviews=processed_reviews, dist_pct=dist_pct)

@patient_bp.route('/book/slots/<int:doctor_id>/<string:selected_date>')
@login_required
def get_slots(doctor_id, selected_date):
    slots = Slot.query.filter_by(doctor_id=doctor_id, date=selected_date, is_blocked=False).all()
    slots.sort(key=lambda s: datetime.strptime(s.time_label, "%I:%M %p"))
    doctor = Doctor.query.get_or_404(doctor_id)
    dates = [(date.today() + timedelta(days=i)).isoformat() for i in range(7)]
    # Check for alternative doctors
    alt_slot = None
    dept_id = doctor.department_id
    if dept_id:
        for s in slots:
            if s.available_spots == 0:
                alt = recommend_alternative_doctor(dept_id, selected_date, s.time_label)
                if alt and alt.doctor_id != doctor_id:
                    alt_slot = alt
                    break
    return render_template('slots.html', slots=slots, doctor=doctor, dates=dates,
                           selected_date=selected_date, alt_slot=alt_slot)

@patient_bp.route('/book/confirm', methods=['POST'])
@login_required
def confirm_booking():
    slot_id = request.form.get('slot_id', type=int)
    appt_type = request.form.get('appointment_type', 'normal')
    notes = request.form.get('notes', '')
    slot = Slot.query.get_or_404(slot_id)
    
    # Check capacity
    if slot.available_spots <= 0:
        flash('This slot is full. Please choose another.', 'error')
        return redirect(url_for('patient.book'))

    # Fix Token #0 bug and Sequential assignment
    existing_count = Appointment.query.filter_by(slot_id=slot.id).filter(
        Appointment.status.in_(['waiting', 'called'])
    ).count()
    token = existing_count + 1
    
    risk = predict_no_show(current_user.id)
    
    # New Fields
    pref_lang = request.form.get('preferred_language', 'English')
    cons_mode = request.form.get('consultation_mode', 'In-Person')
    em_name = request.form.get('emergency_contact_name', '')
    em_phone = request.form.get('emergency_contact_phone', '')

    appt = Appointment(patient_id=current_user.id, slot_id=slot_id,
                       token_number=token, status='waiting', appointment_type=appt_type,
                       notes=notes, risk_flag=risk,
                       preferred_language=pref_lang, consultation_mode=cons_mode,
                       emergency_contact_name=em_name, emergency_contact_phone=em_phone)
    db.session.add(appt)
    db.session.flush()
    
    log = QueueLog(appointment_id=appt.id, action='booked')
    db.session.add(log)
    db.session.commit()
    
    # Bug 3 Fix: Ensure session persists
    session['patient_id'] = current_user.id
    session['patient_name'] = current_user.name
    
    doctor = slot.doctor
    audit_logger.log_action('Appointment Booked', f'Token #{token} with {doctor.name}')
    
    # Emit real-time events
    socketio.emit('slot_update', {'slot_id': slot_id, 'available': slot.available_spots})
    socketio.emit('queue_update', {'slot_id': slot_id})
    socketio.emit('new_booking', {
        'time': datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        'doctor_id': doctor.id,
        'doctor_name': doctor.name,
        'slot_id': slot_id,
        'time_label': slot.time_label,
        'status': 'waiting'
    })
    socketio.emit('admin_dashboard_update', {
        'action': f"New booking: Token #{token} ({appt_type})",
        'time': datetime.now().strftime("%I:%M:%S %p"),
        'type': 'booked'
    })
    
    return redirect(url_for('patient.booking_success', appt_id=appt.id))

@patient_bp.route('/book/success/<int:appt_id>')
@login_required
def booking_success(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.dashboard'))
    slot = Slot.query.get(appt.slot_id)
    doctor = Doctor.query.get(slot.doctor_id)
    return render_template('booking_success.html', appt=appt, slot=slot, doctor=doctor)

@patient_bp.route('/calendar')
@login_required
def calendar():
    return render_template('calendar.html')

@patient_bp.route('/dashboard')
@login_required
def dashboard():
    today = date.today().isoformat()
    active = Appointment.query.join(Slot).filter(
        Appointment.patient_id == current_user.id,
        Appointment.status.in_(['waiting','called']),
        Slot.date >= today
    ).order_by(Slot.date, Slot.time_label).all()
    positions = {}
    etas = {}
    for appt in active:
        waiting_ahead = Appointment.query.filter(
            Appointment.slot_id == appt.slot_id,
            Appointment.status == 'waiting',
            Appointment.token_number < appt.token_number
        ).count()
        called = Appointment.query.filter_by(slot_id=appt.slot_id, status='called').first()
        positions[appt.id] = waiting_ahead + (1 if called and called.id != appt.id else 0)
        etas[appt.id] = positions[appt.id] * 10

    past = Appointment.query.join(Slot).filter(
        Appointment.patient_id == current_user.id,
        Appointment.status.in_(['seen','cancelled','no_show'])
    ).order_by(Appointment.booked_at.desc()).limit(20).all()
    
    prescriptions = Prescription.query.filter_by(patient_id=current_user.id).order_by(Prescription.uploaded_at.desc()).all()
    
    # Stats for Dashboard
    total_count = Appointment.query.filter_by(patient_id=current_user.id).count()
    seen_count = Appointment.query.filter_by(patient_id=current_user.id, status='seen').count()
    waiting_count = len(active)

    # Waitlist entries for this patient
    waitlist_entries = WaitlistEntry.query.filter_by(
        patient_id=current_user.id,
        status='waiting'
    ).order_by(WaitlistEntry.created_at).all()

    return render_template('dashboard.html', active=active, past=past, positions=positions, etas=etas, prescriptions=prescriptions,
                           total_count=total_count, seen_count=seen_count, waiting_count=waiting_count,
                           waitlist_entries=waitlist_entries)

@patient_bp.route('/cancel/<int:appt_id>', methods=['POST'])
@login_required
def cancel(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.dashboard'))
    if appt.status != 'waiting':
        flash('Cannot cancel this appointment.', 'error')
        return redirect(url_for('patient.dashboard'))
    
    try:
        appt.status = 'cancelled'
        log = QueueLog(appointment_id=appt.id, action='cancelled')
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        flash('Database error occurred while cancelling.', 'error')
        print(f"Cancel Error (DB): {e}")
        return redirect(url_for('patient.dashboard'))
    
    try:
        audit_logger.log_action('Appointment Cancelled', f'Token #{appt.token_number} cancelled by patient')
    except Exception as e:
        print(f"Cancel Error (AuditLog): {e}")
    
    try:
        slot = Slot.query.get(appt.slot_id)
        # Emit real-time events
        socketio.emit('slot_update', {'slot_id': slot.id, 'available': slot.available_spots})
        socketio.emit('queue_update', {'slot_id': slot.id})
        socketio.emit('status_update', {
            'appt_id': appt.id,
            'new_status': 'cancelled',
            'slot_id': slot.id,
            'doctor_id': slot.doctor_id
        })
        socketio.emit('admin_dashboard_update', {
            'action': f"Appointment #{appt.token_number} cancelled",
            'time': datetime.now().strftime("%I:%M:%S %p"),
            'type': 'cancelled'
        })
    except Exception as e:
        print(f"Cancel Error (SocketIO): {e}")
    
    # Auto-promote from waitlist if someone is waiting for this doctor+date
    try:
        from routes_walkin import try_promote_waitlist
        slot = Slot.query.get(appt.slot_id)
        promoted = try_promote_waitlist(slot.doctor_id, slot.date, freed_slot=slot)
        if promoted:
            flash(f'Appointment cancelled. A waitlisted patient has been promoted.', 'info')
        else:
            flash('Appointment cancelled.', 'info')
    except Exception as e:
        print(f"Cancel Error (Waitlist Promotion): {e}")
        flash('Appointment cancelled.', 'info')
    
    return redirect(url_for('patient.dashboard'))

@patient_bp.route('/profile', methods=['GET','POST'])
@login_required
def profile():
    from models import ReminderPreference
    pref = ReminderPreference.query.filter_by(patient_id=current_user.id).first()
    if not pref:
        pref = ReminderPreference(patient_id=current_user.id, email_enabled=True, in_app_enabled=True, reminder_minutes_before=30)
        db.session.add(pref)
        db.session.commit()

    if request.method == 'POST':
        current_user.name = request.form['name']
        current_user.phone = request.form['phone']
        current_user.email = request.form['email']
        
        # Update reminder preferences
        pref.email_enabled = 'email_enabled' in request.form
        pref.in_app_enabled = 'in_app_enabled' in request.form
        reminder_mins = request.form.get('reminder_minutes_before', type=int)
        if reminder_mins in [15, 30, 60, 120]:
            pref.reminder_minutes_before = reminder_mins
            
        db.session.commit()
        session['user_name'] = current_user.name
        flash('Profile and preferences updated!', 'success')
        audit_logger.log_action('Profile Updated', f'Patient {current_user.name} updated profile')
    
    # Health summary
    total_visits = Appointment.query.filter_by(patient_id=current_user.id, status='seen').count()
    all_appts = Appointment.query.filter_by(patient_id=current_user.id).order_by(Appointment.booked_at.desc()).all()
    # Most visited doctor
    doc_counts = {}
    dept_counts = {}
    for a in all_appts:
        s = Slot.query.get(a.slot_id)
        d = Doctor.query.get(s.doctor_id)
        doc_counts[d.name] = doc_counts.get(d.name, 0) + 1
        if d.department:
            dept_counts[d.department.name] = dept_counts.get(d.department.name, 0) + 1
    fav_doc = max(doc_counts, key=doc_counts.get) if doc_counts else 'N/A'
    fav_dept = max(dept_counts, key=dept_counts.get) if dept_counts else 'N/A'
    
    prescriptions = Prescription.query.filter_by(patient_id=current_user.id).order_by(Prescription.uploaded_at.desc()).all()
    records = MedicalRecord.query.filter_by(patient_id=current_user.id).order_by(MedicalRecord.uploaded_at.desc()).all()
    
    return render_template('profile.html', patient=current_user, appointments=all_appts,
                           total_visits=total_visits, fav_doc=fav_doc, fav_dept=fav_dept, 
                           prescriptions=prescriptions, records=records, pref=pref)

@patient_bp.route('/reschedule/<int:appt_id>', methods=['POST'])
@login_required
def reschedule(appt_id):
    new_slot_id = request.form.get('slot_id', type=int)
    old_appt = Appointment.query.get_or_404(appt_id)
    
    if old_appt.patient_id != current_user.id:
        return {"error": "Unauthorized"}, 403
    
    if old_appt.status != 'waiting':
        return {"error": "Only waiting appointments can be rescheduled"}, 400

    new_slot = Slot.query.get_or_404(new_slot_id)
    if new_slot.available_spots <= 0:
        return {"error": "New slot is full"}, 400

    # Cancel old
    old_appt.status = 'cancelled'
    db.session.add(QueueLog(appointment_id=old_appt.id, action='cancelled'))
    
    # Create new with same details
    existing_count = Appointment.query.filter_by(slot_id=new_slot.id).filter(
        Appointment.status.in_(['waiting', 'called'])
    ).count()
    token = existing_count + 1

    new_appt = Appointment(
        patient_id=current_user.id,
        slot_id=new_slot_id,
        token_number=token,
        status='waiting',
        appointment_type=old_appt.appointment_type,
        notes=old_appt.notes,
        preferred_language=old_appt.preferred_language,
        consultation_mode=old_appt.consultation_mode,
        emergency_contact_name=old_appt.emergency_contact_name,
        emergency_contact_phone=old_appt.emergency_contact_phone,
        risk_flag=old_appt.risk_flag
    )
    db.session.add(new_appt)
    db.session.flush()
    db.session.add(QueueLog(appointment_id=new_appt.id, action='booked'))
    
    db.session.commit()
    
    # Sockets for both slots
    socketio.emit('slot_update', {'slot_id': old_appt.slot_id, 'available': old_appt.slot.available_spots})
    socketio.emit('slot_update', {'slot_id': new_slot_id, 'available': new_slot.available_spots})
    socketio.emit('queue_update', {'slot_id': old_appt.slot_id})
    socketio.emit('queue_update', {'slot_id': new_slot_id})
    
    audit_logger.log_action('Appointment Rescheduled', f'From slot {old_appt.slot_id} to {new_slot_id}')
    
    flash('Appointment rescheduled successfully!', 'success')
    return redirect(url_for('patient.dashboard'))

@patient_bp.route('/review/<int:appt_id>', methods=['POST'])
@login_required
def submit_review(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.patient_id != current_user.id:
        return {"error": "Unauthorized"}, 403
        
    if appt.status != 'seen':
        flash('You can only review completed appointments.', 'error')
        return redirect(url_for('patient.dashboard'))
        
    rating = request.form.get('rating', type=int)
    feedback = request.form.get('feedback', '')
    
    if not rating or rating < 1 or rating > 5:
        flash('Invalid rating.', 'error')
        return redirect(url_for('patient.dashboard'))
        
    # Check if already reviewed
    existing = Review.query.filter_by(appointment_id=appt.id).first()
    if existing:
        flash('You have already reviewed this appointment.', 'error')
        return redirect(url_for('patient.dashboard'))
        
    doctor_id = appt.slot.doctor_id
    review = Review(appointment_id=appt.id, patient_id=current_user.id, doctor_id=doctor_id, rating=rating, feedback=feedback)
    db.session.add(review)
    db.session.commit()
    
    audit_logger.log_action('Review Submitted', f'Patient {current_user.name} rated Doctor {doctor_id} with {rating} stars')
    flash('Thank you for your feedback!', 'success')
    return redirect(url_for('patient.dashboard'))


@patient_bp.route('/symptom-checker', methods=['GET', 'POST'])
@login_required
def symptom_checker():
    recommendation = None
    department = None
    query = ""
    
    if request.method == 'POST':
        query = request.form.get('symptoms', '').strip().lower()
        symptom_map = {
            'chest pain': 'Cardiology',
            'palpitations': 'Cardiology',
            'heart': 'Cardiology',
            'cardio': 'Cardiology',
            
            'bone': 'Orthopedics',
            'joint': 'Orthopedics',
            'fracture': 'Orthopedics',
            'muscle': 'Orthopedics',
            'back pain': 'Orthopedics',
            'sprain': 'Orthopedics',
            
            'skin': 'Dermatology',
            'rash': 'Dermatology',
            'acne': 'Dermatology',
            'itching': 'Dermatology',
            'hair': 'Dermatology',
            
            'headache': 'Neurology',
            'migraine': 'Neurology',
            'seizure': 'Neurology',
            'brain': 'Neurology',
            'paralysis': 'Neurology',
            
            'fever': 'General Medicine',
            'cold': 'General Medicine',
            'cough': 'General Medicine',
            'flu': 'General Medicine',
            'stomach': 'General Medicine',
            'infection': 'General Medicine',
            
            'ear': 'ENT',
            'nose': 'ENT',
            'throat': 'ENT',
            'hearing': 'ENT',
            'sinus': 'ENT',
            
            'pregnancy': 'Gynecology',
            'menstrual': 'Gynecology',
            'period': 'Gynecology',
            'gyne': 'Gynecology',
            
            'child': 'Pediatrics',
            'baby': 'Pediatrics',
            'pediatric': 'Pediatrics',
            'kid': 'Pediatrics'
        }
        
        # Match keywords
        matched_dept = None
        for keyword, dept in symptom_map.items():
            if keyword in query:
                matched_dept = dept
                break
                
        if matched_dept:
            department = Department.query.filter_by(name=matched_dept).first()
            if department:
                recommendation = {
                    'department': department,
                    'doctors': Doctor.query.filter_by(department_id=department.id, is_available=True).all()
                }
        
        if not recommendation:
            # Default fallback to General Medicine
            department = Department.query.filter_by(name='General Medicine').first()
            if department:
                recommendation = {
                    'department': department,
                    'doctors': Doctor.query.filter_by(department_id=department.id, is_available=True).all(),
                    'is_fallback': True
                }

    return render_template('symptom_checker.html', query=query, recommendation=recommendation)


@patient_bp.route('/upload-record', methods=['POST'])
@login_required
def upload_record():
    file = request.files.get('medical_record')
    description = request.form.get('description', '')
    category = request.form.get('category', 'Other')
    
    referrer = request.referrer
    if referrer and 'medical-records' in referrer:
        redirect_url = url_for('patient.medical_records')
    else:
        redirect_url = url_for('patient.profile')

    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(redirect_url)
        
    filename = secure_filename(f"record_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
    os.makedirs(os.path.join(current_app.root_path, 'static', 'records'), exist_ok=True)
    path = os.path.join('static', 'records', filename)
    file.save(os.path.join(current_app.root_path, path))
    
    record = MedicalRecord(patient_id=current_user.id, file_path=path, description=description, category=category)
    db.session.add(record)
    db.session.commit()
    
    audit_logger.log_action('Medical Record Uploaded', f'Patient {current_user.name} uploaded record: {description} under category {category}')
    flash('Medical record uploaded successfully!', 'success')
    return redirect(redirect_url)


@patient_bp.route('/delete-record/<int:record_id>', methods=['POST'])
@login_required
def delete_record(record_id):
    record = MedicalRecord.query.get_or_404(record_id)
    if record.patient_id != current_user.id:
        flash('Unauthorized to delete this record.', 'error')
        return redirect(url_for('patient.profile'))
        
    try:
        file_abs_path = os.path.join(current_app.root_path, record.file_path)
        if os.path.exists(file_abs_path):
            os.remove(file_abs_path)
    except Exception as e:
        print(f"Error removing physical file: {e}")
        
    db.session.delete(record)
    db.session.commit()
    
    audit_logger.log_action('Medical Record Deleted', f'Patient {current_user.name} deleted record: {record.description}')
    flash('Medical record deleted successfully!', 'success')
    
    referrer = request.referrer
    if referrer and 'medical-records' in referrer:
        return redirect(url_for('patient.medical_records'))
    return redirect(url_for('patient.profile'))


@patient_bp.route('/medical-records')
@login_required
def medical_records():
    # 1. Fetch appointments (with Doctor details, ordered by date desc)
    appointments = Appointment.query.join(Slot).filter(
        Appointment.patient_id == current_user.id
    ).order_by(Slot.date.desc(), Slot.time_label.desc()).all()

    # 2. Fetch prescriptions (ordered by uploaded_at desc)
    prescriptions = Prescription.query.filter_by(
        patient_id=current_user.id
    ).order_by(Prescription.uploaded_at.desc()).all()

    # 3. Fetch uploaded medical records (ordered by uploaded_at desc)
    records = MedicalRecord.query.filter_by(
        patient_id=current_user.id
    ).order_by(MedicalRecord.uploaded_at.desc()).all()

    # 4. Fetch vitals readings (ordered by logged_at desc)
    vitals = VitalsReading.query.filter_by(
        patient_id=current_user.id
    ).order_by(VitalsReading.logged_at.desc()).all()

    # Create a unified timeline
    timeline = []

    for appt in appointments:
        timeline.append({
            'type': 'appointment',
            'date': datetime.strptime(appt.slot.date, '%Y-%m-%d'),
            'date_str': appt.slot.date,
            'time': appt.slot.time_label,
            'title': f"Appointment with Dr. {appt.slot.doctor.name}",
            'subtitle': appt.slot.doctor.specialization,
            'details': appt.notes or 'No notes provided by patient.',
            'status': appt.status,
            'doctor_notes': appt.notes if appt.status == 'seen' else '', 
            'badge_class': f"status-{appt.status}",
            'icon_class': 'fa-stethoscope text-indigo-400',
            'id': appt.id
        })

    for pr in prescriptions:
        timeline.append({
            'type': 'prescription',
            'date': pr.uploaded_at,
            'date_str': pr.uploaded_at.strftime('%Y-%m-%d'),
            'time': pr.uploaded_at.strftime('%I:%M %p'),
            'title': f"Prescription from Dr. {pr.doctor.name if pr.doctor else 'General'}",
            'subtitle': pr.doctor.specialization if pr.doctor else '',
            'details': pr.notes or 'No prescription notes.',
            'file_path': url_for('static', filename=pr.file_path),
            'icon_class': 'fa-file-prescription text-purple-400',
            'id': pr.id
        })

    for rec in records:
        timeline.append({
            'type': 'record',
            'date': rec.uploaded_at,
            'date_str': rec.uploaded_at.strftime('%Y-%m-%d'),
            'time': rec.uploaded_at.strftime('%I:%M %p'),
            'title': rec.description or 'Uploaded Health Document',
            'subtitle': rec.category or 'Other',
            'details': f"Category: {rec.category or 'Other'}",
            'file_path': url_for('static', filename=rec.file_path),
            'category': rec.category or 'Other',
            'icon_class': 'fa-file-medical text-emerald-400',
            'id': rec.id
        })

    for v in vitals:
        vitals_details = f"BP: {v.blood_pressure_sys}/{v.blood_pressure_dia} mmHg | Sugar: {v.blood_sugar} mg/dL | HR: {v.heart_rate} bpm | Weight: {v.weight} kg"
        timeline.append({
            'type': 'vitals',
            'date': v.logged_at,
            'date_str': v.logged_at.strftime('%Y-%m-%d'),
            'time': v.logged_at.strftime('%I:%M %p'),
            'title': 'Logged Vitals Reading',
            'subtitle': f"BMI: {v.bmi or 'N/A'}",
            'details': vitals_details,
            'vitals_data': {
                'bp': f"{v.blood_pressure_sys}/{v.blood_pressure_dia}" if (v.blood_pressure_sys and v.blood_pressure_dia) else None,
                'sugar': v.blood_sugar,
                'hr': v.heart_rate,
                'weight': v.weight,
                'height': v.height,
                'bmi': v.bmi,
                'notes': v.notes
            },
            'icon_class': 'fa-heartbeat text-rose-400',
            'id': v.id
        })

    # Sort timeline by date descending
    timeline.sort(key=lambda x: x['date'], reverse=True)

    # Get latest vitals for the summary widget
    latest_vitals = vitals[0] if vitals else None

    # Categories list for filtering
    categories = ["Lab Report", "Prescription", "Scan/X-ray", "Vaccination", "Other"]

    return render_template('medical_records.html', 
                           timeline=timeline, 
                           latest_vitals=latest_vitals, 
                           categories=categories)


@patient_bp.route('/health-tracker')
@login_required
def health_tracker():
    readings = VitalsReading.query.filter_by(patient_id=current_user.id).order_by(VitalsReading.logged_at.asc()).all()
    
    # Calculate health insights based on the latest reading
    latest = VitalsReading.query.filter_by(patient_id=current_user.id).order_by(VitalsReading.logged_at.desc()).first()
    
    insights = []
    recommended_dept = None
    
    if latest:
        # Blood Pressure Alert Check
        bp_alert = False
        if latest.blood_pressure_sys and latest.blood_pressure_sys > 140:
            bp_alert = True
        if latest.blood_pressure_dia and latest.blood_pressure_dia > 90:
            bp_alert = True
            
        if bp_alert:
            # Suggest Cardiology (Dept 1)
            dept = Department.query.filter_by(name='Cardiology').first()
            dept_id = dept.id if dept else 1
            recommended_dept = {
                'id': dept_id,
                'name': 'Cardiology',
                'reason': f"Your latest Blood Pressure reading of {latest.blood_pressure_sys}/{latest.blood_pressure_dia} mmHg is elevated. We recommend booking a slot with a Cardiologist."
            }
            insights.append({
                'type': 'warning',
                'message': f"Elevated Blood Pressure: {latest.blood_pressure_sys}/{latest.blood_pressure_dia} mmHg. Avoid strenuous activity and consult a doctor."
            })
            
        # Blood Sugar Alert Check
        sugar_alert = False
        if latest.blood_sugar and latest.blood_sugar > 140:
            sugar_alert = True
            
        if sugar_alert:
            # Suggest General Medicine (Dept 5)
            dept = Department.query.filter_by(name='General Medicine').first()
            dept_id = dept.id if dept else 5
            if not recommended_dept: # Priority to BP or if none, set this
                recommended_dept = {
                    'id': dept_id,
                    'name': 'General Medicine',
                    'reason': f"Your latest Blood Sugar reading of {latest.blood_sugar} mg/dL is high. We recommend booking a slot with a General Physician."
                }
            insights.append({
                'type': 'warning',
                'message': f"Elevated Blood Sugar: {latest.blood_sugar} mg/dL. Monitor diet and consult a General Physician."
            })

        # Heart Rate Alert Check
        hr_alert = False
        if latest.heart_rate and (latest.heart_rate > 100 or latest.heart_rate < 60):
            hr_alert = True
            
        if hr_alert:
            insights.append({
                'type': 'info',
                'message': f"Irregular Heart Rate: {latest.heart_rate} bpm (Resting). If persistent, consult our cardiology or general medicine department."
            })
            
        # If all normal
        if not bp_alert and not sugar_alert and not hr_alert:
            insights.append({
                'type': 'success',
                'message': "All tracked vitals are in the healthy clinical range! Keep up the great work."
            })
    else:
        insights.append({
            'type': 'info',
            'message': "Welcome to your health dashboard! Log your first reading below to start tracking your vitals over time."
        })

    # Prepare data for Chart.js
    chart_data = {
        'dates': [r.logged_at.strftime('%d %b %H:%M') for r in readings],
        'bp_sys': [r.blood_pressure_sys for r in readings],
        'bp_dia': [r.blood_pressure_dia for r in readings],
        'sugar': [r.blood_sugar for r in readings],
        'hr': [r.heart_rate for r in readings]
    }
    
    return render_template('health_tracker.html', readings=readings, latest=latest, insights=insights, recommended_dept=recommended_dept, chart_data=chart_data)


@patient_bp.route('/health-tracker/log', methods=['POST'])
@login_required
def log_vitals():
    try:
        bp_sys = request.form.get('blood_pressure_sys', type=int)
        bp_dia = request.form.get('blood_pressure_dia', type=int)
        sugar = request.form.get('blood_sugar', type=int)
        hr = request.form.get('heart_rate', type=int)
        weight = request.form.get('weight', type=float)
        height = request.form.get('height', type=float)
        notes = request.form.get('notes', '')
        
        bmi = None
        if weight and height:
            # BMI = weight (kg) / (height (m) ^ 2)
            height_m = height / 100.0
            bmi = round(weight / (height_m ** 2), 1)

        reading = VitalsReading(
            patient_id=current_user.id,
            blood_pressure_sys=bp_sys,
            blood_pressure_dia=bp_dia,
            blood_sugar=sugar,
            heart_rate=hr,
            weight=weight,
            height=height,
            bmi=bmi,
            notes=notes
        )
        
        db.session.add(reading)
        db.session.commit()
        
        audit_logger.log_action('Vitals Logged', f'Patient {current_user.name} logged new vitals reading (BMI: {bmi})')
        flash('Vitals logged successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error logging vitals: {str(e)}', 'error')
        print(f"Error logging vitals: {e}")
        
    return redirect(url_for('patient.health_tracker'))

@patient_bp.route('/appointment/<int:appt_id>/download_pdf')
@login_required
def download_pdf(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.dashboard'))
        
    slot = appt.slot
    doctor = slot.doctor
    
    pdf = FPDF()
    pdf.add_page()
    
    # Hospital Branding
    pdf.set_font("helvetica", "B", 24)
    pdf.set_text_color(15, 23, 42) # slate-900
    pdf.cell(0, 15, "MediSlot Hospital", ln=True, align='C')
    pdf.set_font("helvetica", "I", 12)
    pdf.set_text_color(100, 116, 139) # slate-500
    pdf.cell(0, 10, "Smart Appointment Booking System", ln=True, align='C')
    pdf.ln(10)
    
    # Title
    pdf.set_font("helvetica", "B", 16)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(0, 10, "Medical Summary & Prescription", ln=True, align='C')
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(10)
    
    # Details
    pdf.set_font("helvetica", "", 12)
    
    def add_row(label, value):
        pdf.set_font("helvetica", "B", 12)
        pdf.cell(50, 10, f"{label}:")
        pdf.set_font("helvetica", "", 12)
        pdf.cell(0, 10, str(value), ln=True)

    add_row("Patient Name", current_user.name)
    add_row("Doctor Name", doctor.name)
    add_row("Specialization", doctor.specialization)
    add_row("Appointment Date", slot.date)
    add_row("Time", slot.time_label)
    add_row("Consultation Mode", appt.consultation_mode)
    add_row("Status", appt.status.capitalize())
    add_row("Token Number", f"#{appt.token_number}")
    
    pdf.ln(10)
    pdf.set_font("helvetica", "B", 14)
    pdf.cell(0, 10, "Doctor's Notes", ln=True)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)
    
    pdf.set_font("helvetica", "", 12)
    if appt.notes:
        pdf.multi_cell(0, 10, appt.notes)
    else:
        pdf.set_font("helvetica", "I", 12)
        pdf.cell(0, 10, "No notes provided for this appointment.", ln=True)
        
    pdf.ln(20)
    
    # Vitals if any recent
    latest_vital = VitalsReading.query.filter_by(patient_id=current_user.id).order_by(VitalsReading.logged_at.desc()).first()
    if latest_vital and (datetime.utcnow() - latest_vital.logged_at).days <= 7:
        pdf.set_font("helvetica", "B", 14)
        pdf.cell(0, 10, "Recent Vitals", ln=True)
        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
        pdf.ln(5)
        
        pdf.set_font("helvetica", "", 12)
        if latest_vital.blood_pressure_sys and latest_vital.blood_pressure_dia:
            add_row("Blood Pressure", f"{latest_vital.blood_pressure_sys}/{latest_vital.blood_pressure_dia} mmHg")
        if latest_vital.heart_rate:
            add_row("Heart Rate", f"{latest_vital.heart_rate} bpm")
        if latest_vital.blood_sugar:
            add_row("Blood Sugar", f"{latest_vital.blood_sugar} mg/dL")
            
    pdf.ln(20)
    pdf.set_font("helvetica", "I", 10)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 10, "This is a computer-generated document and requires no physical signature.", ln=True, align='C')
    pdf.cell(0, 10, f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ln=True, align='C')

    pdf_bytes = pdf.output()
    buffer = io.BytesIO(pdf_bytes)
    
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f"MediSlot_Summary_{appt.id}.pdf",
        mimetype='application/pdf'
    )

@patient_bp.route('/invoices')
@login_required
def invoices():
    patient_invoices = Invoice.query.filter_by(patient_id=current_user.id).order_by(Invoice.issued_at.desc()).all()
    return render_template('patient_invoices.html', invoices=patient_invoices)

@patient_bp.route('/invoices/pay/<int:invoice_id>', methods=['POST'])
@login_required
def pay_invoice(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.invoices'))
        
    if invoice.status == 'paid':
        flash('Invoice is already paid.', 'info')
        return redirect(url_for('patient.invoices'))
        
    # Simulate payment processing
    invoice.status = 'paid'
    invoice.paid_at = datetime.utcnow()
    db.session.commit()
    
    audit_logger.log_action('pay_invoice', f"Patient paid Invoice #{invoice.id} for {invoice.amount}")
    flash('Payment successful! Thank you.', 'success')
    return redirect(url_for('patient.invoices'))

@patient_bp.route('/articles')
def articles():
    """Public health blog page - accessible to everyone."""
    category_filter = request.args.get('category')
    query = Article.query.filter_by(is_published=True)
    if category_filter:
        query = query.filter_by(category=category_filter)
    articles_list = query.order_by(Article.created_at.desc()).all()
    categories = ['Health Tip', 'Announcement', 'News']
    return render_template('articles.html', articles=articles_list, categories=categories, selected_category=category_filter)

@patient_bp.route('/articles/<int:article_id>')
def article_detail(article_id):
    """Single article view."""
    article = Article.query.get_or_404(article_id)
    if not article.is_published:
        flash('This article is not available.', 'error')
        return redirect(url_for('patient.articles'))
    # Get related articles (same category, excluding current)
    related = Article.query.filter(
        Article.category == article.category,
        Article.id != article.id,
        Article.is_published == True
    ).order_by(Article.created_at.desc()).limit(3).all()
    return render_template('article_detail.html', article=article, related=related)


@patient_bp.route('/ward-booking')
@login_required
def ward_booking():
    from models import Ward, Bed
    wards = Ward.query.all()
    
    # Check if patient is currently admitted
    bed_admission = Bed.query.filter_by(patient_id=current_user.id).first()
    days_admitted = 0
    accumulated_cost = 0.0
    if bed_admission and bed_admission.admitted_at:
        days_admitted = max(1, (datetime.utcnow() - bed_admission.admitted_at).days)
        accumulated_cost = days_admitted * bed_admission.ward.cost_per_day
        
    return render_template('ward_booking.html', 
                           wards=wards, 
                           bed_admission=bed_admission, 
                           days_admitted=days_admitted, 
                           accumulated_cost=accumulated_cost)


@patient_bp.route('/ward-booking/request/<int:ward_id>', methods=['POST'])
@login_required
def request_bed(ward_id):
    from models import Ward, Bed
    
    # Check if already admitted
    existing = Bed.query.filter_by(patient_id=current_user.id).first()
    if existing:
        flash('You already have an active bed booking.', 'error')
        return redirect(url_for('patient.ward_booking'))
        
    ward = Ward.query.get_or_404(ward_id)
    available_bed = Bed.query.filter_by(ward_id=ward_id, status='available').first()
    
    if not available_bed:
        flash('No available beds in this ward currently.', 'error')
        return redirect(url_for('patient.ward_booking'))
        
    try:
        available_bed.status = 'occupied'
        available_bed.patient_id = current_user.id
        available_bed.admitted_at = datetime.utcnow()
        available_bed.expected_discharge = datetime.utcnow() + timedelta(days=3)
        
        db.session.commit()
        
        # Emit WebSocket event
        socketio.emit('bed_update', {
            'bed_id': available_bed.id,
            'ward_id': ward_id,
            'status': 'occupied',
            'bed_number': available_bed.bed_number,
            'patient_name': current_user.name
        })
        
        audit_logger.log_action('Bed Booked', f"Patient {current_user.name} reserved Bed {available_bed.bed_number} in {ward.name}")
        flash(f'Bed {available_bed.bed_number} in {ward.name} reserved successfully!', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Database error occurred while reserving: {str(e)}', 'error')
        print(f"Bed Reserve Error: {e}")
        
    return redirect(url_for('patient.ward_booking'))


@patient_bp.route('/hospital-map')
@login_required
def hospital_map():
    departments = Department.query.all()
    doctors = Doctor.query.filter_by(is_available=True).all()
    return render_template('hospital_map.html', departments=departments, doctors=doctors)


# ─── Patient Waitlist Routes ──────────────────────────────────────────────────
@patient_bp.route('/waitlist/join', methods=['POST'])
@login_required
def join_waitlist():
    """Patient joins waitlist for a fully-booked doctor+date."""
    doctor_id = request.form.get('doctor_id', type=int)
    requested_date = request.form.get('requested_date', date.today().isoformat())
    preferred_time = request.form.get('preferred_time', '')
    notes = request.form.get('notes', '')

    if not doctor_id:
        flash('Please select a doctor.', 'error')
        return redirect(url_for('patient.book'))

    doctor = Doctor.query.get_or_404(doctor_id)

    # Check if already on waitlist
    existing = WaitlistEntry.query.filter_by(
        patient_id=current_user.id,
        doctor_id=doctor_id,
        requested_date=requested_date,
        status='waiting'
    ).first()
    if existing:
        flash('You are already on the waitlist for this doctor and date.', 'warning')
        return redirect(url_for('patient.book'))

    # Calculate position
    last_position = db.session.query(db.func.max(WaitlistEntry.position)).filter_by(
        doctor_id=doctor_id,
        requested_date=requested_date,
        status='waiting'
    ).scalar() or 0

    entry = WaitlistEntry(
        patient_id=current_user.id,
        doctor_id=doctor_id,
        department_id=doctor.department_id or 1,
        requested_date=requested_date,
        preferred_time=preferred_time or None,
        priority='normal',
        notes=notes,
        position=last_position + 1
    )
    db.session.add(entry)
    db.session.commit()

    audit_logger.log_action('Waitlist Joined', f'Patient joined waitlist for Dr. {doctor.name} on {requested_date} (Position #{entry.position})')

    socketio.emit('waitlist_update', {
        'doctor_id': doctor_id,
        'date': requested_date,
        'action': 'added',
        'entry_id': entry.id
    })

    flash(f'Added to waitlist for Dr. {doctor.name}! You are at position #{entry.position}. We will notify you when a slot opens.', 'success')
    return redirect(url_for('patient.dashboard'))


@patient_bp.route('/waitlist/cancel/<int:entry_id>', methods=['POST'])
@login_required
def cancel_waitlist(entry_id):
    """Patient cancels their own waitlist entry."""
    entry = WaitlistEntry.query.get_or_404(entry_id)
    if entry.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.dashboard'))
    if entry.status != 'waiting':
        flash('This waitlist entry has already been processed.', 'warning')
        return redirect(url_for('patient.dashboard'))

    entry.status = 'cancelled'
    db.session.commit()

    audit_logger.log_action('Waitlist Cancelled', f'Patient cancelled waitlist for Dr. {entry.doctor.name}')

    socketio.emit('waitlist_update', {
        'doctor_id': entry.doctor_id,
        'date': entry.requested_date,
        'action': 'removed',
        'entry_id': entry.id
    })

    flash('Removed from waitlist.', 'info')
    return redirect(url_for('patient.dashboard'))

