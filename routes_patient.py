import os
import io
from fpdf import FPDF
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, send_file
from flask_login import login_required, current_user
from models import db, Doctor, Department, Slot, Appointment, QueueLog, Patient, Prescription, Review, MedicalRecord, VitalsReading, Invoice, Article
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
    return render_template('book.html', departments=departments, doctors=doctors,
                           selected_dept=dept_id, active_appts=active_appts, current_step=1)

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

    return render_template('dashboard.html', active=active, past=past, positions=positions, etas=etas, prescriptions=prescriptions,
                           total_count=total_count, seen_count=seen_count, waiting_count=waiting_count)

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
    
    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('patient.profile'))
        
    filename = secure_filename(f"record_{current_user.id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}")
    os.makedirs(os.path.join(current_app.root_path, 'static', 'records'), exist_ok=True)
    path = os.path.join('static', 'records', filename)
    file.save(os.path.join(current_app.root_path, path))
    
    record = MedicalRecord(patient_id=current_user.id, file_path=path, description=description)
    db.session.add(record)
    db.session.commit()
    
    audit_logger.log_action('Medical Record Uploaded', f'Patient {current_user.name} uploaded record: {description}')
    flash('Medical record uploaded successfully!', 'success')
    return redirect(url_for('patient.profile'))


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

