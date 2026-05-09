"""Patient routes: booking, dashboard, profile"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_required, current_user
from models import db, Doctor, Department, Slot, Appointment, QueueLog, Patient
from smart_scheduling import recommend_slot, recommend_alternative_doctor, assign_token
from datetime import datetime, date, timedelta
from extensions import socketio

patient_bp = Blueprint('patient', __name__)

@patient_bp.route('/book')
@login_required
def book():
    departments = Department.query.all()
    dept_id = request.args.get('department', type=int)
    if dept_id:
        doctors = Doctor.query.filter_by(department_id=dept_id, is_available=True).all()
    else:
        doctors = Doctor.query.filter_by(is_available=True).all()
    # Add recommendation info
    for doc in doctors:
        rec = recommend_slot(doc.id)
        doc.recommended_slot = rec
    return render_template('book.html', departments=departments, doctors=doctors,
                           selected_dept=dept_id)

@patient_bp.route('/book/slots/<int:doctor_id>/<string:selected_date>')
@login_required
def get_slots(doctor_id, selected_date):
    slots = Slot.query.filter_by(doctor_id=doctor_id, date=selected_date, is_blocked=False)\
        .order_by(Slot.time_label).all()
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
    # Check for any existing active appointment (prevent double-booking)
    existing = Appointment.query.filter_by(
        patient_id=current_user.id
    ).filter(
        Appointment.status.in_(['waiting', 'called'])
    ).first()
    if existing:
        ex_slot = Slot.query.get(existing.slot_id)
        ex_doc = Doctor.query.get(ex_slot.doctor_id)
        flash(f'You already have an active appointment (Token #{existing.token_number} '
              f'with {ex_doc.name} at {ex_slot.time_label}). '
              f'Please cancel it before booking a new one.', 'error')
        return redirect(url_for('patient.book'))
    token = assign_token(slot_id, appt_type)
    appt = Appointment(patient_id=current_user.id, slot_id=slot_id,
                       token_number=token, status='waiting', appointment_type=appt_type,
                       notes=notes)
    db.session.add(appt)
    db.session.flush()
    log = QueueLog(appointment_id=appt.id, action='booked')
    db.session.add(log)
    db.session.commit()
    
    # Emit real-time events
    socketio.emit('slot_update', {'slot_id': slot_id, 'available': slot.available_spots})
    socketio.emit('queue_update', {'slot_id': slot_id})
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
    return render_template('dashboard.html', active=active, past=past, positions=positions, etas=etas)

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
    appt.status = 'cancelled'
    log = QueueLog(appointment_id=appt.id, action='cancelled')
    db.session.add(log)
    db.session.commit()
    
    slot = Slot.query.get(appt.slot_id)
    # Emit real-time events
    socketio.emit('slot_update', {'slot_id': slot.id, 'available': slot.available_spots})
    socketio.emit('queue_update', {'slot_id': slot.id})
    socketio.emit('admin_dashboard_update', {
        'action': f"Appointment #{appt.token_number} cancelled",
        'time': datetime.now().strftime("%I:%M:%S %p"),
        'type': 'cancelled'
    })
    
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
        session['patient_name'] = current_user.name
        flash('Profile updated!', 'success')
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
    return render_template('profile.html', patient=current_user, appointments=all_appts,
                           total_visits=total_visits, fav_doc=fav_doc, fav_dept=fav_dept)
