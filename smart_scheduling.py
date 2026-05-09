"""
MediSlot - Smart Scheduling Algorithm
Provides auto-slot recommendation, load balancing, overbooking buffer, and priority queue logic.
"""

from datetime import datetime, date


def get_noshow_buffer(slot_id):
    """If a slot has a no-show pattern, allow 1 extra booking."""
    from models import QueueLog, Appointment
    past_appointments = Appointment.query.filter_by(slot_id=slot_id).all()
    noshow_count = sum(1 for a in past_appointments if a.status == 'no_show')
    total = len(past_appointments)
    if total >= 3 and (noshow_count / total) >= 0.3:
        return 1
    return 0


def recommend_slot(doctor_id):
    """Suggest the earliest available slot for a given doctor."""
    from models import Slot, Appointment
    today = date.today().isoformat()
    slots = Slot.query.filter(
        Slot.doctor_id == doctor_id,
        Slot.date >= today,
        Slot.is_blocked == False
    ).order_by(Slot.date, Slot.time_label).all()

    for slot in slots:
        if slot.available_spots > 0:
            return slot
    return None


def recommend_alternative_doctor(department_id, preferred_date, preferred_time):
    """Load balancing: suggest another doctor in the same department with availability."""
    from models import Doctor, Slot
    doctors = Doctor.query.filter_by(department_id=department_id, is_available=True).all()

    best_slot = None
    best_available = 0

    for doctor in doctors:
        slot = Slot.query.filter_by(
            doctor_id=doctor.id,
            date=preferred_date,
            time_label=preferred_time,
            is_blocked=False
        ).first()
        if slot and slot.available_spots > best_available:
            best_slot = slot
            best_available = slot.available_spots

    return best_slot


def assign_token(slot_id, appointment_type='normal'):
    """
    Assign a token number for a slot.
    Urgent patients get token 0 (front of queue).
    Normal/follow-up get the next sequential number.
    """
    from models import Appointment
    if appointment_type == 'urgent':
        existing_urgent = Appointment.query.filter_by(
            slot_id=slot_id, appointment_type='urgent'
        ).filter(Appointment.status.notin_(['cancelled'])).count()
        return existing_urgent  # 0, 1, 2... for multiple urgents

    max_token = Appointment.query.filter(
        Appointment.slot_id == slot_id,
        Appointment.status.notin_(['cancelled']),
        Appointment.appointment_type != 'urgent'
    ).count()

    urgent_count = Appointment.query.filter_by(
        slot_id=slot_id, appointment_type='urgent'
    ).filter(Appointment.status.notin_(['cancelled'])).count()

    return urgent_count + max_token + 1


def calculate_live_eta(appointment_id):
    """
    Recalculate estimated wait using rolling average of last 10 consultation times.
    Returns estimated minutes.
    """
    from models import Appointment, QueueLog, db
    appointment = Appointment.query.get(appointment_id)
    if not appointment:
        return 0

    # Get rolling average consultation time from queue_log
    recent_seen = db.session.query(QueueLog).filter(
        QueueLog.action == 'seen'
    ).order_by(QueueLog.timestamp.desc()).limit(10).all()

    avg_time = 10  # default 10 min per patient
    if len(recent_seen) >= 2:
        times = []
        for log in recent_seen:
            called_log = QueueLog.query.filter_by(
                appointment_id=log.appointment_id,
                action='called'
            ).first()
            if called_log:
                diff = (log.timestamp - called_log.timestamp).total_seconds() / 60
                if 1 <= diff <= 60:
                    times.append(diff)
        if times:
            avg_time = sum(times) / len(times)

    # Calculate position in queue
    waiting_ahead = Appointment.query.filter(
        Appointment.slot_id == appointment.slot_id,
        Appointment.status == 'waiting',
        Appointment.token_number < appointment.token_number
    ).count()

    # Add currently called patient time
    called = Appointment.query.filter(
        Appointment.slot_id == appointment.slot_id,
        Appointment.status == 'called'
    ).first()

    extra = 0
    if called:
        called_log = QueueLog.query.filter_by(
            appointment_id=called.id, action='called'
        ).first()
        if called_log:
            elapsed = (datetime.utcnow() - called_log.timestamp).total_seconds() / 60
            remaining = max(0, avg_time - elapsed)
            extra = remaining

    return round(waiting_ahead * avg_time + extra)


def get_wasted_slot_alerts():
    """Find slots that are empty but surrounded by full slots."""
    from models import Slot
    today = date.today().isoformat()
    alerts = []
    slots = Slot.query.filter(
        Slot.date == today,
        Slot.is_blocked == False
    ).order_by(Slot.doctor_id, Slot.time_label).all()

    grouped = {}
    for s in slots:
        grouped.setdefault(s.doctor_id, []).append(s)

    for doc_id, doc_slots in grouped.items():
        for i, slot in enumerate(doc_slots):
            if slot.booked_count == 0:
                prev_full = i > 0 and doc_slots[i - 1].booked_count >= doc_slots[i - 1].max_capacity
                next_full = i < len(doc_slots) - 1 and doc_slots[i + 1].booked_count >= doc_slots[i + 1].max_capacity
                if prev_full or next_full:
                    alerts.append({
                        'slot_id': slot.id,
                        'doctor_id': doc_id,
                        'time': slot.time_label,
                        'message': f'Empty slot at {slot.time_label} surrounded by full slots'
                    })
    return alerts
