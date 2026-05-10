from flask import Blueprint, render_template, jsonify
from auth_utils import role_required
from models import db, Appointment, Doctor, Slot, Department
from sqlalchemy import func
from datetime import datetime, timedelta

analytics_bp = Blueprint('analytics', __name__)

@analytics_bp.route('/api/analytics/realtime')
@role_required(['super_admin', 'receptionist'])
def get_realtime_analytics():
    today = datetime.now().date().isoformat()
    
    # 1. Live Appointments Counter (Total for today)
    total_today = Appointment.query.join(Slot).filter(Slot.date == today).count()
    
    # 2. Slot Fill Rate (Today's slots)
    slots_raw = Slot.query.filter_by(date=today).all()
    fill_rate = [{
        "time": s.time_label,
        "booked": s.booked_count,
        "cap": s.max_capacity
    } for s in slots_raw]
    
    # 3. Appointment Status Breakdown (Today)
    status_raw = db.session.query(
        Appointment.status, func.count(Appointment.id)
    ).join(Slot).filter(Slot.date == today).group_by(Appointment.status).all()
    status_breakdown = {item[0]: item[1] for item in status_raw}
    
    # 4. Doctor Load (Today)
    load_raw = db.session.query(
        Doctor.name, func.count(Appointment.id)
    ).join(Slot, Slot.doctor_id == Doctor.id).filter(Slot.date == today).group_by(Doctor.id).all()
    doctor_load = [{"name": item[0], "count": item[1]} for item in load_raw]
    doctor_load.sort(key=lambda x: x['count'], reverse=True)
    
    # 5. Hourly Booking Trend (Today)
    # We'll extract the hour from booked_at (UTC to local might be tricky, but let's use the DB's booked_at)
    # Actually, Slot time might be better for "Booking Trend" if we mean appointment times, 
    # but "Booking Trend" usually means when they were booked.
    # The prompt says "X-axis: hours of today (12AM - 12AM)". This usually implies the hour of the appointment.
    hourly_raw = db.session.query(
        func.substr(Slot.time_label, 1, func.instr(Slot.time_label, ':') - 1),
        func.substr(Slot.time_label, -2),
        func.count(Appointment.id)
    ).join(Appointment).filter(Slot.date == today).group_by(
        func.substr(Slot.time_label, 1, func.instr(Slot.time_label, ':') - 1),
        func.substr(Slot.time_label, -2)
    ).all()
    
    # Helper to convert "9", "AM" to 9, etc.
    hourly_trend = [0] * 24
    for h, ampm, count in hourly_raw:
        hr = int(h)
        if ampm == 'PM' and hr != 12: hr += 12
        if ampm == 'AM' and hr == 12: hr = 0
        hourly_trend[hr] = count

    return jsonify({
        "total_today": total_today,
        "fill_rate": fill_rate,
        "status_breakdown": status_breakdown,
        "doctor_load": doctor_load,
        "hourly_trend": hourly_trend
    })

@analytics_bp.route('/admin/analytics')
@role_required(['super_admin', 'receptionist'])
def dashboard():
    return render_template('admin/analytics.html')
