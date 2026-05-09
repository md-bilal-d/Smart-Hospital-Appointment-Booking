"""Admin routes: dashboard, slot management, doctor management, analytics"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response
from models import db, Doctor, Department, Slot, Appointment, QueueLog, Patient
from smart_scheduling import get_wasted_slot_alerts
from datetime import date, datetime
import csv, io
from extensions import socketio

admin_bp = Blueprint('admin', __name__)

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged_in'):
            flash('Admin access required.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated

@admin_bp.route('/admin')
@admin_required
def dashboard():
    today = date.today().isoformat()
    slots_today = Slot.query.filter_by(date=today).all()
    all_appts = []
    for s in slots_today:
        all_appts.extend(Appointment.query.filter_by(slot_id=s.id).all())
    stats = {
        'total': len(all_appts),
        'seen': sum(1 for a in all_appts if a.status=='seen'),
        'waiting': sum(1 for a in all_appts if a.status=='waiting'),
        'no_show': sum(1 for a in all_appts if a.status=='no_show'),
        'cancelled': sum(1 for a in all_appts if a.status=='cancelled'),
        'called': sum(1 for a in all_appts if a.status=='called'),
    }
    doctors = Doctor.query.all()
    # Build slot grid
    time_labels = []
    for hour in range(9, 17):
        for minute in [0, 30]:
            h = hour % 12 or 12
            ampm = 'AM' if hour < 12 else 'PM'
            time_labels.append(f"{h}:{minute:02d} {ampm}")
    grid = {}
    for tl in time_labels:
        grid[tl] = {}
        for doc in doctors:
            slot = Slot.query.filter_by(doctor_id=doc.id, date=today, time_label=tl).first()
            if slot:
                grid[tl][doc.id] = {'slot': slot, 'count': slot.booked_count, 'cap': slot.max_capacity, 'blocked': slot.is_blocked}
            else:
                grid[tl][doc.id] = None
    # No-show rate alerts
    noshow_alerts = []
    for doc in doctors:
        doc_slots = Slot.query.filter_by(doctor_id=doc.id).all()
        total = 0; noshows = 0
        for s in doc_slots:
            appts = Appointment.query.filter_by(slot_id=s.id).all()
            total += len(appts)
            noshows += sum(1 for a in appts if a.status=='no_show')
        rate = (noshows/total*100) if total > 0 else 0
        if rate > 30:
            noshow_alerts.append({'doctor': doc.name, 'rate': round(rate,1)})
    wasted = get_wasted_slot_alerts()
    departments = Department.query.all()
    return render_template('admin/dashboard.html', stats=stats, doctors=doctors,
                           time_labels=time_labels, grid=grid, today=today,
                           noshow_alerts=noshow_alerts, wasted_alerts=wasted, departments=departments)

@admin_bp.route('/admin/slot-patients/<int:slot_id>')
@admin_required
def slot_patients(slot_id):
    slot = Slot.query.get_or_404(slot_id)
    appts = Appointment.query.filter_by(slot_id=slot_id).order_by(Appointment.token_number).all()
    patients_data = []
    for a in appts:
        p = Patient.query.get(a.patient_id)
        patients_data.append({'appointment': a, 'patient': p})
    doctor = Doctor.query.get(slot.doctor_id)
    return render_template('admin/slot_patients.html', slot=slot, patients=patients_data, doctor=doctor)

@admin_bp.route('/admin/action/<int:appt_id>/<string:action>', methods=['POST'])
@admin_required
def patient_action(appt_id, action):
    appt = Appointment.query.get_or_404(appt_id)
    if action == 'seen':
        appt.status = 'seen'
    elif action == 'no_show':
        appt.status = 'no_show'
    elif action == 'call':
        appt.status = 'called'
    log = QueueLog(appointment_id=appt.id, action=action)
    db.session.add(log)
    db.session.commit()
    
    # Emit real-time events
    socketio.emit('queue_update', {'slot_id': appt.slot_id})
    socketio.emit('admin_dashboard_update', {
        'action': f"Appointment #{appt.token_number} marked as {action}",
        'time': datetime.now().strftime("%I:%M:%S %p"),
        'type': action
    })
    
    flash(f'Patient marked as {action}.', 'success')
    return redirect(url_for('admin.slot_patients', slot_id=appt.slot_id))

@admin_bp.route('/admin/block-slot/<int:slot_id>', methods=['POST'])
@admin_required
def block_slot(slot_id):
    slot = Slot.query.get_or_404(slot_id)
    slot.is_blocked = not slot.is_blocked
    slot.block_reason = request.form.get('reason', '')
    db.session.commit()
    status = 'blocked' if slot.is_blocked else 'unblocked'
    flash(f'Slot {status}.', 'info')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/admin/add-doctor', methods=['POST'])
@admin_required
def add_doctor():
    doc = Doctor(
        name=request.form['name'],
        specialization=request.form['specialization'],
        experience_years=int(request.form['experience']),
        department_id=int(request.form['department_id']),
        profile_pic_url=f"https://api.dicebear.com/9.x/initials/svg?seed={''.join(w[0] for w in request.form['name'].split())}&backgroundColor=4f46e5",
        is_available=True
    )
    db.session.add(doc)
    db.session.commit()
    # Generate slots for new doctor
    from datetime import timedelta
    today = date.today()
    time_labels = []
    for hour in range(9, 17):
        for minute in [0, 30]:
            h = hour % 12 or 12
            ampm = 'AM' if hour < 12 else 'PM'
            time_labels.append(f"{h}:{minute:02d} {ampm}")
    for d in range(7):
        dt = (today + timedelta(days=d)).isoformat()
        for tl in time_labels:
            s = Slot(doctor_id=doc.id, date=dt, time_label=tl, max_capacity=3)
            db.session.add(s)
    db.session.commit()
    flash(f'Doctor {doc.name} added with slots!', 'success')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/admin/history')
@admin_required
def history():
    q = request.args.get('q', '')
    dt = request.args.get('date', '')
    query = Appointment.query.join(Patient, Appointment.patient_id==Patient.id)
    if q:
        query = query.filter((Patient.name.ilike(f'%{q}%'))|(Patient.phone.ilike(f'%{q}%')))
    if dt:
        query = query.join(Slot, Appointment.slot_id==Slot.id).filter(Slot.date==dt)
    appts = query.order_by(Appointment.booked_at.desc()).limit(100).all()
    return render_template('admin/history.html', appointments=appts, q=q, dt=dt)

@admin_bp.route('/admin/export-csv')
@admin_required
def export_csv():
    today = date.today().isoformat()
    slots = Slot.query.filter_by(date=today).all()
    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(['Token','Patient','Phone','Doctor','Time','Status','Type','Notes'])
    for s in slots:
        doc = Doctor.query.get(s.doctor_id)
        for a in Appointment.query.filter_by(slot_id=s.id).order_by(Appointment.token_number).all():
            p = Patient.query.get(a.patient_id)
            writer.writerow([a.token_number, p.name, p.phone, doc.name, s.time_label, a.status, a.appointment_type, a.notes])
    output = si.getvalue()
    return Response(output, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment;filename=appointments_{today}.csv'})

@admin_bp.route('/admin/analytics')
@admin_required
def analytics():
    return render_template('admin/analytics.html')
