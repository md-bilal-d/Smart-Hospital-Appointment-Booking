from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from models import db, Doctor, Department, Slot, Appointment, QueueLog, Patient, Prescription, Review
from smart_scheduling import recommend_slot, recommend_alternative_doctor, assign_token
from datetime import datetime, date, timedelta
from extensions import socketio
from utils import audit_logger, predict_no_show
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
    if request.method == 'POST':
        current_user.name = request.form['name']
        current_user.phone = request.form['phone']
        current_user.email = request.form['email']
        db.session.commit()
        session['user_name'] = current_user.name
        flash('Profile updated!', 'success')
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
    
    return render_template('profile.html', patient=current_user, appointments=all_appts,
                           total_visits=total_visits, fav_doc=fav_doc, fav_dept=fav_dept, prescriptions=prescriptions)

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
