"""Doctor routes: dashboard, queue management, notes, availability"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, Doctor, Slot, Appointment, QueueLog, Patient
from datetime import date, timedelta, datetime
from extensions import socketio

doctor_bp = Blueprint('doctor', __name__)

def doctor_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'doctor_id' not in session:
            flash('Doctor login required.', 'error')
            return redirect(url_for('auth.doctor_login'))
        return f(*args, **kwargs)
    return decorated

@doctor_bp.route('/doctor/<int:doctor_id>')
@doctor_required
def dashboard(doctor_id):
    if session.get('doctor_id') != doctor_id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('auth.doctor_login'))
    doctor = Doctor.query.get_or_404(doctor_id)
    today = date.today().isoformat()
    slots_today = Slot.query.filter_by(doctor_id=doctor_id, date=today)\
        .order_by(Slot.time_label).all()
    queue = []
    for s in slots_today:
        appts = Appointment.query.filter_by(slot_id=s.id)\
            .order_by(Appointment.token_number).all()
        for a in appts:
            p = Patient.query.get(a.patient_id)
            queue.append({'appointment': a, 'patient': p, 'slot': s})
    # Weekly view
    weekly = {}
    for d in range(7):
        dt = (date.today() + timedelta(days=d)).isoformat()
        count = 0
        day_slots = Slot.query.filter_by(doctor_id=doctor_id, date=dt).all()
        for s in day_slots:
            count += Appointment.query.filter_by(slot_id=s.id)\
                .filter(Appointment.status.notin_(['cancelled'])).count()
        weekly[dt] = count
    return render_template('doctor/dashboard.html', doctor=doctor, queue=queue,
                           today=today, weekly=weekly)

@doctor_bp.route('/doctor/<int:doctor_id>/call-next', methods=['POST'])
@doctor_required
def call_next(doctor_id):
    today = date.today().isoformat()
    # Mark current called as seen
    slots = Slot.query.filter_by(doctor_id=doctor_id, date=today).all()
    for s in slots:
        called = Appointment.query.filter_by(slot_id=s.id, status='called').first()
        if called:
            called.status = 'seen'
            log = QueueLog(appointment_id=called.id, action='seen')
            db.session.add(log)
            db.session.commit()
            
            socketio.emit('queue_update', {'slot_id': s.id})
            socketio.emit('admin_dashboard_update', {
                'action': f"Appointment #{called.token_number} marked as seen",
                'time': datetime.now().strftime("%I:%M:%S %p"),
                'type': 'seen'
            })
    # Find next waiting
    for s in slots:
        nxt = Appointment.query.filter_by(slot_id=s.id, status='waiting')\
            .order_by(Appointment.token_number).first()
        if nxt:
            nxt.status = 'called'
            log = QueueLog(appointment_id=nxt.id, action='called')
            db.session.add(log)
            db.session.commit()
            
            socketio.emit('queue_update', {'slot_id': s.id})
            socketio.emit('admin_dashboard_update', {
                'action': f"Appointment #{nxt.token_number} called",
                'time': datetime.now().strftime("%I:%M:%S %p"),
                'type': 'called'
            })
            p = Patient.query.get(nxt.patient_id)
            flash(f'Calling Token #{nxt.token_number} - {p.name}', 'success')
            return redirect(url_for('doctor.dashboard', doctor_id=doctor_id))
    db.session.commit()
    flash('No more patients in queue.', 'info')
    return redirect(url_for('doctor.dashboard', doctor_id=doctor_id))

@doctor_bp.route('/doctor/<int:doctor_id>/add-notes/<int:appt_id>', methods=['POST'])
@doctor_required
def add_notes(doctor_id, appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    appt.notes = request.form.get('notes', '')
    db.session.commit()
    flash('Notes saved.', 'success')
    return redirect(url_for('doctor.dashboard', doctor_id=doctor_id))

@doctor_bp.route('/doctor/<int:doctor_id>/toggle-availability', methods=['POST'])
@doctor_required
def toggle_availability(doctor_id):
    doc = Doctor.query.get_or_404(doctor_id)
    doc.is_available = not doc.is_available
    db.session.commit()
    status = 'available' if doc.is_available else 'unavailable'
    flash(f'You are now {status}.', 'info')
    return redirect(url_for('doctor.dashboard', doctor_id=doctor_id))

@doctor_bp.route('/doctor/logout')
def doctor_logout():
    session.pop('doctor_id', None)
    session.pop('doctor_name', None)
    return redirect(url_for('auth.landing'))
