"""API routes: queue status, analytics data"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from models import db, Appointment, Slot, Doctor, Patient, QueueLog, Department
from smart_scheduling import calculate_live_eta
from datetime import date, timedelta, datetime
from sqlalchemy import func

api_bp = Blueprint('api', __name__)

@api_bp.route('/api/queue-status/<int:appt_id>')
def queue_status(appt_id):
    appt = Appointment.query.get(appt_id)
    if not appt:
        return jsonify({'error': 'Not found'}), 404
    slot = Slot.query.get(appt.slot_id)
    waiting_ahead = Appointment.query.filter(
        Appointment.slot_id == appt.slot_id,
        Appointment.status == 'waiting',
        Appointment.token_number < appt.token_number
    ).count()
    called = Appointment.query.filter_by(slot_id=appt.slot_id, status='called').first()
    position = waiting_ahead + (1 if called and called.id != appt.id else 0)
    eta = position * 10
    return jsonify({
        'status': appt.status,
        'token': appt.token_number,
        'position': position,
        'eta_minutes': eta,
        'slot_time': slot.time_label,
        'is_next': waiting_ahead == 0 and appt.status == 'waiting'
    })

@api_bp.route('/api/slots/<int:doctor_id>')
def get_doctor_slots(doctor_id):
    today = date.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(7)]
    slots = Slot.query.filter(
        Slot.doctor_id == doctor_id, 
        Slot.date.in_(dates), 
        Slot.is_blocked == False
    ).order_by(Slot.date, Slot.time_label).all()
    
    slots_by_date = {d: [] for d in dates}
    for s in slots:
        slots_by_date[s.date].append({
            'id': s.id,
            'time_label': s.time_label,
            'available_spots': s.available_spots,
            'max_capacity': s.max_capacity
        })
    return jsonify(slots_by_date)

@api_bp.route('/api/appointments')
@login_required
def get_user_appointments():
    appts = Appointment.query.filter_by(patient_id=current_user.id).all()
    result = []
    for a in appts:
        slot = Slot.query.get(a.slot_id)
        doc = Doctor.query.get(slot.doctor_id)
        result.append({
            'id': a.id,
            'date': slot.date,
            'time': slot.time_label,
            'doctor_name': doc.name,
            'status': a.status,
            'token': a.token_number,
            'type': a.appointment_type,
            'notes': a.notes
        })
    return jsonify(result)

@api_bp.route('/api/analytics')
def analytics():
    today = date.today()
    # Appointments per day (last 7 days)
    daily = []
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        count = Appointment.query.join(Slot).filter(Slot.date == d).count()
        daily.append({'date': d, 'count': count})
    # Status breakdown
    statuses = {}
    for s in ['waiting', 'called', 'seen', 'cancelled', 'no_show']:
        statuses[s] = Appointment.query.filter_by(status=s).count()
    # Peak hours
    time_labels = []
    for hour in range(9, 17):
        for minute in [0, 30]:
            h = hour % 12 or 12
            ampm = 'AM' if hour < 12 else 'PM'
            time_labels.append(f"{h}:{minute:02d} {ampm}")
    peak = []
    for tl in time_labels:
        count = Appointment.query.join(Slot).filter(Slot.time_label == tl).count()
        peak.append({'time': tl, 'count': count})
    # Doctor performance
    doc_perf = []
    for doc in Doctor.query.all():
        doc_slots = Slot.query.filter_by(doctor_id=doc.id).all()
        total = 0; seen = 0; noshows = 0
        consult_times = []
        days_set = set()
        for s in doc_slots:
            appts = Appointment.query.filter_by(slot_id=s.id).all()
            total += len(appts)
            days_set.add(s.date)
            for a in appts:
                if a.status == 'seen': seen += 1
                if a.status == 'no_show': noshows += 1
                # Calc consultation time
                called_log = QueueLog.query.filter_by(appointment_id=a.id, action='called').first()
                seen_log = QueueLog.query.filter_by(appointment_id=a.id, action='seen').first()
                if called_log and seen_log:
                    diff = (seen_log.timestamp - called_log.timestamp).total_seconds() / 60
                    if 1 <= diff <= 60:
                        consult_times.append(diff)
        num_days = max(len(days_set), 1)
        doc_perf.append({
            'name': doc.name,
            'specialization': doc.specialization,
            'avg_patients': round(total / num_days, 1),
            'noshow_rate': round(noshows / total * 100, 1) if total else 0,
            'avg_consult': round(sum(consult_times) / len(consult_times), 1) if consult_times else 0,
            'high_noshow': (noshows / total * 100 > 30) if total else False
        })
    # Department popularity
    dept_pop = []
    for dept in Department.query.all():
        docs = Doctor.query.filter_by(department_id=dept.id).all()
        count = 0
        for doc in docs:
            for s in Slot.query.filter_by(doctor_id=doc.id).all():
                count += Appointment.query.filter_by(slot_id=s.id).count()
        dept_pop.append({'name': dept.name, 'count': count})
    dept_pop.sort(key=lambda x: x['count'], reverse=True)

    return jsonify({
        'daily': daily,
        'statuses': statuses,
        'peak_hours': peak,
        'doctor_performance': doc_perf,
        'department_popularity': dept_pop
    })
