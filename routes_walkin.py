"""
MediSlot - Walk-in Queue Management & Waitlist Routes
Handles walk-in patient registration, waitlist management, and public queue display.
"""

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from models import db, Patient, Doctor, Department, Slot, Appointment, QueueLog, WaitlistEntry
from auth_utils import role_required, get_current_user
from utils import predict_no_show, AuditLogger
from extensions import socketio
from translations import gettext as _
from datetime import datetime, date as dt_date
import bcrypt

walkin_bp = Blueprint('walkin', __name__)


# ─── Helper: Auto-promote from waitlist ───────────────────────────────────────
def try_promote_waitlist(doctor_id, slot_date, freed_slot=None):
    """Check if any waitlist entries can be promoted for the given doctor+date.
    Called when an appointment is cancelled or marked as no-show.
    Returns the promoted WaitlistEntry or None.
    """
    # Find the first waiting entry for this doctor+date, ordered by priority then position
    entry = WaitlistEntry.query.filter_by(
        doctor_id=doctor_id,
        requested_date=slot_date,
        status='waiting'
    ).order_by(
        # urgent first, then by position
        db.case(
            (WaitlistEntry.priority == 'urgent', 0),
            else_=1
        ),
        WaitlistEntry.position
    ).first()

    if not entry:
        return None

    # Find an available slot for this doctor on the requested date
    if freed_slot and freed_slot.available_spots > 0:
        target_slot = freed_slot
    else:
        # Look for any slot with capacity
        available_slots = Slot.query.filter_by(
            doctor_id=doctor_id,
            date=slot_date,
            is_blocked=False
        ).all()
        target_slot = None
        for s in available_slots:
            if s.available_spots > 0:
                # If they had a preferred time, try to match it
                if entry.preferred_time and s.time_label == entry.preferred_time:
                    target_slot = s
                    break
                elif not target_slot:
                    target_slot = s
        # If preferred time wasn't found, use whatever is available
        if not target_slot:
            for s in available_slots:
                if s.available_spots > 0:
                    target_slot = s
                    break

    if not target_slot:
        return None

    # Assign token number
    existing_count = Appointment.query.filter(
        Appointment.slot_id == target_slot.id,
        Appointment.status.in_(['waiting', 'called'])
    ).count()
    token_number = existing_count + 1

    # Create the appointment
    risk = predict_no_show(entry.patient_id)
    appointment = Appointment(
        patient_id=entry.patient_id,
        slot_id=target_slot.id,
        token_number=token_number,
        status='waiting',
        appointment_type='normal',
        risk_flag=risk,
        notes=entry.notes or ''
    )
    db.session.add(appointment)
    db.session.flush()  # get the appointment ID

    # Log it
    db.session.add(QueueLog(appointment_id=appointment.id, action='booked'))

    # Update waitlist entry
    entry.status = 'promoted'
    entry.promoted_at = datetime.utcnow()
    entry.appointment_id = appointment.id

    db.session.commit()

    # Emit socket events
    socketio.emit('queue_update', {
        'slot_id': target_slot.id,
        'doctor_id': doctor_id,
        'action': 'waitlist_promoted',
        'patient_name': entry.patient.name,
        'token': token_number
    })
    socketio.emit('waitlist_update', {
        'doctor_id': doctor_id,
        'date': slot_date,
        'action': 'promoted',
        'entry_id': entry.id,
        'patient_name': entry.patient.name
    })
    socketio.emit('admin_dashboard_update', {})

    return entry


# ─── Walk-in Management Dashboard ─────────────────────────────────────────────
@walkin_bp.route('/walk-in')
@role_required(['receptionist', 'super_admin'])
def walkin_dashboard():
    today = dt_date.today().isoformat()

    # Get all doctors with their departments
    doctors = Doctor.query.filter_by(is_active=True, is_available=True).all()
    departments = Department.query.all()

    # Today's walk-in appointments
    walkin_appointments = Appointment.query.join(Slot).filter(
        Slot.date == today,
        Appointment.appointment_type == 'walk_in'
    ).order_by(Appointment.token_number).all()

    # Active waitlist entries
    waitlist_entries = WaitlistEntry.query.filter_by(
        status='waiting'
    ).order_by(WaitlistEntry.requested_date, WaitlistEntry.position).all()

    # All patients (for search autocomplete)
    patients = Patient.query.order_by(Patient.name).all()

    # Stats
    total_walkins = len(walkin_appointments)
    currently_waiting = sum(1 for a in walkin_appointments if a.status == 'waiting')
    currently_called = sum(1 for a in walkin_appointments if a.status == 'called')

    # Calculate average wait time from QueueLog
    avg_wait = 0
    seen_walkins = [a for a in walkin_appointments if a.status == 'seen']
    if seen_walkins:
        total_wait = 0
        count = 0
        for appt in seen_walkins:
            booked_log = QueueLog.query.filter_by(appointment_id=appt.id, action='booked').first()
            seen_log = QueueLog.query.filter_by(appointment_id=appt.id, action='seen').first()
            if booked_log and seen_log:
                wait = (seen_log.timestamp - booked_log.timestamp).total_seconds() / 60
                total_wait += wait
                count += 1
        avg_wait = round(total_wait / count) if count > 0 else 0

    stats = {
        'total_walkins': total_walkins,
        'currently_waiting': currently_waiting,
        'currently_called': currently_called,
        'avg_wait_minutes': avg_wait,
        'waitlist_count': len(waitlist_entries)
    }

    return render_template('admin/walkin_dashboard.html',
                           doctors=doctors,
                           departments=departments,
                           walkin_appointments=walkin_appointments,
                           waitlist_entries=waitlist_entries,
                           patients=patients,
                           stats=stats)


# ─── Register Existing Patient as Walk-in ──────────────────────────────────────
@walkin_bp.route('/walk-in/register', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def register_walkin():
    patient_id = request.form.get('patient_id')
    doctor_id = request.form.get('doctor_id')
    priority = request.form.get('priority', 'normal')
    notes = request.form.get('notes', '')

    if not patient_id or not doctor_id:
        flash(_('Please select a patient and doctor.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    patient = Patient.query.get(int(patient_id))
    doctor = Doctor.query.get(int(doctor_id))
    if not patient or not doctor:
        flash(_('Patient or doctor not found.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    today = dt_date.today().isoformat()

    # Find next available slot for this doctor today
    available_slots = Slot.query.filter_by(
        doctor_id=doctor.id,
        date=today,
        is_blocked=False
    ).all()

    target_slot = None
    for s in available_slots:
        if s.available_spots > 0:
            target_slot = s
            break

    if not target_slot:
        flash(_('No available slots for this doctor today. Consider adding to waitlist.'), 'warning')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Assign token number
    existing_count = Appointment.query.filter(
        Appointment.slot_id == target_slot.id,
        Appointment.status.in_(['waiting', 'called'])
    ).count()

    if priority == 'urgent':
        token_number = 0  # Front of queue
    else:
        token_number = existing_count + 1

    # Calculate no-show risk
    risk = predict_no_show(patient.id)

    # Create appointment
    appointment = Appointment(
        patient_id=patient.id,
        slot_id=target_slot.id,
        token_number=token_number,
        status='waiting',
        appointment_type='walk_in',
        risk_flag=risk,
        notes=notes
    )
    db.session.add(appointment)
    db.session.flush()

    # Queue log
    db.session.add(QueueLog(appointment_id=appointment.id, action='booked'))
    db.session.commit()

    # Audit log
    AuditLogger.log_action(
        'Walk-in Registered',
        f'Patient {patient.name} registered as walk-in for Dr. {doctor.name} (Token #{token_number})'
    )

    # Emit socket events
    socketio.emit('queue_update', {
        'slot_id': target_slot.id,
        'doctor_id': doctor.id,
        'action': 'new_walkin',
        'patient_name': patient.name,
        'token': token_number
    })
    socketio.emit('new_booking', {
        'patient': patient.name,
        'doctor': doctor.name,
        'time': target_slot.time_label,
        'type': 'walk_in'
    })
    socketio.emit('admin_dashboard_update', {})

    flash(_('Walk-in patient registered successfully! Token #') + str(token_number), 'success')
    return redirect(url_for('walkin.walkin_dashboard'))


# ─── Quick Register New Patient + Walk-in ──────────────────────────────────────
@walkin_bp.route('/walk-in/quick-register', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def quick_register_walkin():
    name = request.form.get('name', '').strip()
    phone = request.form.get('phone', '').strip()
    email = request.form.get('email', '').strip()
    doctor_id = request.form.get('doctor_id')
    priority = request.form.get('priority', 'normal')
    notes = request.form.get('notes', '')

    if not name or not phone or not email or not doctor_id:
        flash(_('Name, phone, email, and doctor are required.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Check if patient already exists
    existing = Patient.query.filter(
        (Patient.phone == phone) | (Patient.email == email)
    ).first()
    if existing:
        flash(_('Patient already exists. Use the existing patient search instead.'), 'warning')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Create patient with a default password (they can change later)
    default_pw = bcrypt.hashpw('walkin123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    patient = Patient(
        name=name,
        phone=phone,
        email=email,
        password_hash=default_pw,
        role='patient'
    )
    db.session.add(patient)
    db.session.flush()

    # Now register as walk-in (reuse the logic)
    doctor = Doctor.query.get(int(doctor_id))
    if not doctor:
        flash(_('Doctor not found.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    today = dt_date.today().isoformat()
    available_slots = Slot.query.filter_by(
        doctor_id=doctor.id,
        date=today,
        is_blocked=False
    ).all()

    target_slot = None
    for s in available_slots:
        if s.available_spots > 0:
            target_slot = s
            break

    if not target_slot:
        db.session.commit()  # Still save the patient
        flash(_('Patient created but no slots available. Added to system for future bookings.'), 'warning')
        return redirect(url_for('walkin.walkin_dashboard'))

    existing_count = Appointment.query.filter(
        Appointment.slot_id == target_slot.id,
        Appointment.status.in_(['waiting', 'called'])
    ).count()
    token_number = 0 if priority == 'urgent' else existing_count + 1

    appointment = Appointment(
        patient_id=patient.id,
        slot_id=target_slot.id,
        token_number=token_number,
        status='waiting',
        appointment_type='walk_in',
        risk_flag='high',  # New patients default to high risk
        notes=notes
    )
    db.session.add(appointment)
    db.session.flush()
    db.session.add(QueueLog(appointment_id=appointment.id, action='booked'))
    db.session.commit()

    AuditLogger.log_action(
        'Walk-in Quick Register',
        f'New patient {name} created and registered as walk-in for Dr. {doctor.name} (Token #{token_number})'
    )

    socketio.emit('queue_update', {
        'slot_id': target_slot.id,
        'doctor_id': doctor.id,
        'action': 'new_walkin',
        'patient_name': name,
        'token': token_number
    })
    socketio.emit('new_booking', {
        'patient': name, 'doctor': doctor.name,
        'time': target_slot.time_label, 'type': 'walk_in'
    })
    socketio.emit('admin_dashboard_update', {})

    flash(_('New patient registered and added to walk-in queue! Token #') + str(token_number), 'success')
    return redirect(url_for('walkin.walkin_dashboard'))


# ─── Check-in Walk-in Patient ──────────────────────────────────────────────────
@walkin_bp.route('/walk-in/check-in/<int:appt_id>', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def checkin_walkin(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.status != 'waiting':
        flash(_('Can only check in patients who are waiting.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    appt.status = 'called'
    db.session.add(QueueLog(appointment_id=appt.id, action='called'))
    db.session.commit()

    socketio.emit('queue_update', {
        'slot_id': appt.slot_id,
        'doctor_id': appt.slot.doctor_id,
        'action': 'called',
        'patient_name': appt.patient.name,
        'token': appt.token_number
    })

    flash(_('Patient called! Token #') + str(appt.token_number), 'success')
    return redirect(url_for('walkin.walkin_dashboard'))


# ─── Waitlist Management ──────────────────────────────────────────────────────
@walkin_bp.route('/waitlist')
@role_required(['receptionist', 'super_admin'])
def waitlist_view():
    entries = WaitlistEntry.query.filter_by(status='waiting').order_by(
        WaitlistEntry.requested_date, WaitlistEntry.position
    ).all()
    promoted = WaitlistEntry.query.filter_by(status='promoted').order_by(
        WaitlistEntry.promoted_at.desc()
    ).limit(20).all()
    return render_template('admin/walkin_dashboard.html',
                           waitlist_entries=entries,
                           promoted_entries=promoted)


@walkin_bp.route('/waitlist/add', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def add_to_waitlist():
    patient_id = request.form.get('patient_id')
    doctor_id = request.form.get('doctor_id')
    requested_date = request.form.get('requested_date', dt_date.today().isoformat())
    preferred_time = request.form.get('preferred_time', '')
    priority = request.form.get('priority', 'normal')
    notes = request.form.get('notes', '')

    if not patient_id or not doctor_id:
        flash(_('Patient and doctor are required for waitlist.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    doctor = Doctor.query.get(int(doctor_id))
    if not doctor:
        flash(_('Doctor not found.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Check if already on waitlist for same doctor+date
    existing = WaitlistEntry.query.filter_by(
        patient_id=int(patient_id),
        doctor_id=int(doctor_id),
        requested_date=requested_date,
        status='waiting'
    ).first()
    if existing:
        flash(_('Patient is already on the waitlist for this doctor and date.'), 'warning')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Calculate position
    last_position = db.session.query(db.func.max(WaitlistEntry.position)).filter_by(
        doctor_id=int(doctor_id),
        requested_date=requested_date,
        status='waiting'
    ).scalar() or 0

    entry = WaitlistEntry(
        patient_id=int(patient_id),
        doctor_id=int(doctor_id),
        department_id=doctor.department_id or 1,
        requested_date=requested_date,
        preferred_time=preferred_time or None,
        priority=priority,
        notes=notes,
        position=last_position + 1
    )
    db.session.add(entry)
    db.session.commit()

    AuditLogger.log_action(
        'Waitlist Added',
        f'Patient added to waitlist for Dr. {doctor.name} on {requested_date} (Position #{entry.position})'
    )

    socketio.emit('waitlist_update', {
        'doctor_id': int(doctor_id),
        'date': requested_date,
        'action': 'added',
        'entry_id': entry.id
    })

    flash(_('Patient added to waitlist at position #') + str(entry.position), 'success')
    return redirect(url_for('walkin.walkin_dashboard'))


@walkin_bp.route('/waitlist/promote/<int:entry_id>', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def promote_waitlist(entry_id):
    entry = WaitlistEntry.query.get_or_404(entry_id)
    if entry.status != 'waiting':
        flash(_('This waitlist entry has already been processed.'), 'warning')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Find available slot
    available_slots = Slot.query.filter_by(
        doctor_id=entry.doctor_id,
        date=entry.requested_date,
        is_blocked=False
    ).all()

    target_slot = None
    for s in available_slots:
        if s.available_spots > 0:
            if entry.preferred_time and s.time_label == entry.preferred_time:
                target_slot = s
                break
            elif not target_slot:
                target_slot = s

    if not target_slot:
        flash(_('No available slots to promote this patient. Try again later.'), 'error')
        return redirect(url_for('walkin.walkin_dashboard'))

    # Create appointment
    existing_count = Appointment.query.filter(
        Appointment.slot_id == target_slot.id,
        Appointment.status.in_(['waiting', 'called'])
    ).count()
    token_number = existing_count + 1

    risk = predict_no_show(entry.patient_id)
    appointment = Appointment(
        patient_id=entry.patient_id,
        slot_id=target_slot.id,
        token_number=token_number,
        status='waiting',
        appointment_type='normal',
        risk_flag=risk,
        notes=entry.notes or ''
    )
    db.session.add(appointment)
    db.session.flush()
    db.session.add(QueueLog(appointment_id=appointment.id, action='booked'))

    entry.status = 'promoted'
    entry.promoted_at = datetime.utcnow()
    entry.appointment_id = appointment.id
    db.session.commit()

    AuditLogger.log_action(
        'Waitlist Promoted',
        f'Patient {entry.patient.name} promoted from waitlist to Token #{token_number} with Dr. {entry.doctor.name}'
    )

    socketio.emit('queue_update', {
        'slot_id': target_slot.id,
        'doctor_id': entry.doctor_id,
        'action': 'waitlist_promoted'
    })
    socketio.emit('waitlist_update', {
        'doctor_id': entry.doctor_id,
        'date': entry.requested_date,
        'action': 'promoted',
        'entry_id': entry.id,
        'patient_name': entry.patient.name
    })
    socketio.emit('admin_dashboard_update', {})

    flash(_('Patient promoted from waitlist! Token #') + str(token_number), 'success')
    return redirect(url_for('walkin.walkin_dashboard'))


@walkin_bp.route('/waitlist/remove/<int:entry_id>', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def remove_from_waitlist(entry_id):
    entry = WaitlistEntry.query.get_or_404(entry_id)
    if entry.status != 'waiting':
        flash(_('This waitlist entry has already been processed.'), 'warning')
        return redirect(url_for('walkin.walkin_dashboard'))

    entry.status = 'cancelled'
    db.session.commit()

    AuditLogger.log_action(
        'Waitlist Removed',
        f'Patient {entry.patient.name} removed from waitlist for Dr. {entry.doctor.name}'
    )

    socketio.emit('waitlist_update', {
        'doctor_id': entry.doctor_id,
        'date': entry.requested_date,
        'action': 'removed',
        'entry_id': entry.id
    })

    flash(_('Patient removed from waitlist.'), 'success')
    return redirect(url_for('walkin.walkin_dashboard'))


# ─── Public Queue Display Board ────────────────────────────────────────────────
@walkin_bp.route('/queue-display')
def queue_display():
    """Public-facing queue display for lobby TV screens. No auth required."""
    today = dt_date.today().isoformat()

    # Get all active doctors with today's appointments
    doctors = Doctor.query.filter_by(is_active=True, is_available=True).all()
    doctors_queue = []

    for doctor in doctors:
        # Get today's appointments for this doctor
        appointments = Appointment.query.join(Slot).filter(
            Slot.doctor_id == doctor.id,
            Slot.date == today,
            Appointment.status.in_(['waiting', 'called', 'seen'])
        ).order_by(Appointment.token_number).all()

        # Currently being served (called)
        called = [a for a in appointments if a.status == 'called']
        current_token = called[0].token_number if called else None
        current_patient = called[0].patient.name.split()[0] if called else None

        # Waiting list
        waiting = [a for a in appointments if a.status == 'waiting']
        waiting_list = []
        for i, a in enumerate(waiting[:5]):  # Show next 5
            est_minutes = (i + 1) * 10  # Rough estimate: 10 min per patient
            waiting_list.append({
                'token_number': a.token_number,
                'patient_first_name': a.patient.name.split()[0],
                'estimated_time': f'{est_minutes} min'
            })

        # Only include doctors who have patients today
        total_today = len(appointments)
        seen_count = sum(1 for a in appointments if a.status == 'seen')

        doctors_queue.append({
            'doctor_name': doctor.name,
            'doctor_specialization': doctor.specialization,
            'current_token': current_token,
            'current_patient_name': current_patient,
            'waiting_list': waiting_list,
            'total_patients': total_today,
            'seen_count': seen_count,
            'waiting_count': len(waiting)
        })

    return render_template('queue_display.html',
                           doctors_queue=doctors_queue,
                           hospital_name='MediSlot')


# ─── API: Queue Display Data ──────────────────────────────────────────────────
@walkin_bp.route('/api/queue-display')
def api_queue_display():
    """JSON endpoint for the queue display board to refresh data."""
    today = dt_date.today().isoformat()
    doctors = Doctor.query.filter_by(is_active=True, is_available=True).all()
    result = []

    for doctor in doctors:
        appointments = Appointment.query.join(Slot).filter(
            Slot.doctor_id == doctor.id,
            Slot.date == today,
            Appointment.status.in_(['waiting', 'called', 'seen'])
        ).order_by(Appointment.token_number).all()

        called = [a for a in appointments if a.status == 'called']
        waiting = [a for a in appointments if a.status == 'waiting']

        result.append({
            'doctor_name': doctor.name,
            'doctor_specialization': doctor.specialization,
            'current_token': called[0].token_number if called else None,
            'current_patient_name': called[0].patient.name.split()[0] if called else None,
            'waiting_list': [{
                'token_number': a.token_number,
                'patient_first_name': a.patient.name.split()[0],
                'estimated_time': f'{(i + 1) * 10} min'
            } for i, a in enumerate(waiting[:5])],
            'total_patients': len(appointments),
            'seen_count': sum(1 for a in appointments if a.status == 'seen'),
            'waiting_count': len(waiting)
        })

    return jsonify(result)


# ─── API: Search Patients (for autocomplete) ──────────────────────────────────
@walkin_bp.route('/api/walk-in/search-patient')
@role_required(['receptionist', 'super_admin'])
def search_patient():
    query = request.args.get('q', '').strip()
    if len(query) < 2:
        return jsonify([])

    patients = Patient.query.filter(
        (Patient.name.ilike(f'%{query}%')) |
        (Patient.phone.ilike(f'%{query}%'))
    ).limit(10).all()

    return jsonify([{
        'id': p.id,
        'name': p.name,
        'phone': p.phone,
        'email': p.email
    } for p in patients])


# ─── API: Waitlist Status ─────────────────────────────────────────────────────
@walkin_bp.route('/api/waitlist-status/<int:entry_id>')
def waitlist_status(entry_id):
    entry = WaitlistEntry.query.get_or_404(entry_id)
    ahead = WaitlistEntry.query.filter(
        WaitlistEntry.doctor_id == entry.doctor_id,
        WaitlistEntry.requested_date == entry.requested_date,
        WaitlistEntry.status == 'waiting',
        WaitlistEntry.position < entry.position
    ).count()

    return jsonify({
        'id': entry.id,
        'status': entry.status,
        'position': entry.position,
        'ahead_count': ahead,
        'doctor_name': entry.doctor.name,
        'requested_date': entry.requested_date,
        'priority': entry.priority,
        'created_at': entry.created_at.isoformat() if entry.created_at else None,
        'promoted_at': entry.promoted_at.isoformat() if entry.promoted_at else None,
        'appointment_id': entry.appointment_id
    })
