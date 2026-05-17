"""Doctor routes: dashboard, queue management, prescription uploads"""
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app, jsonify
from models import db, Doctor, Slot, Appointment, QueueLog, Patient, Prescription, Review
from datetime import date, timedelta, datetime
from extensions import socketio
from auth_utils import role_required
from utils import audit_logger
from werkzeug.utils import secure_filename

doctor_bp = Blueprint('doctor', __name__)

@doctor_bp.route('/doctor/<int:doctor_id>')
@role_required(['doctor', 'super_admin'])
def doctor_dashboard(doctor_id):
    if session.get('user_role') == 'super_admin':
        pass # Allow super admins to view
    elif session.get('user_role') != 'doctor' or session.get('doctor_id') != doctor_id:
        flash('Unauthorized access to this dashboard. Please login as the correct doctor.', 'error')
        return redirect(url_for('auth.doctor_login'))
        
    doctor = Doctor.query.get_or_404(doctor_id)
    today = date.today()
    today_str = today.isoformat()
    
    # Accurate counts from DB
    patients_today = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id, Slot.date == today_str
    ).count()
    
    seen_today = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id, Slot.date == today_str, Appointment.status == 'seen'
    ).count()
    
    waiting_today = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id, Slot.date == today_str, Appointment.status == 'waiting'
    ).count()
    
    # Queue for today (waiting or called)
    queue_appts = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id,
        Slot.date == today_str,
        Appointment.status.in_(['waiting', 'called'])
    ).order_by(Appointment.token_number).all()
    
    # Timeline data (slots for today)
    slots_today = Slot.query.filter_by(doctor_id=doctor_id, date=today_str)\
        .order_by(Slot.time_label).all()
    
    timeline = []
    for s in slots_today:
        timeline.append({
            'time': s.time_label,
            'count': s.booked_count,
            'capacity': s.max_capacity,
            'is_full': s.booked_count >= s.max_capacity,
            'is_empty': s.booked_count == 0
        })

    # Fetch reviews
    reviews = Review.query.filter_by(doctor_id=doctor_id).order_by(Review.created_at.desc()).limit(10).all()
    avg_rating = 0
    if reviews:
        total_rating = sum(r.rating for r in reviews)
        avg_rating = round(total_rating / len(reviews), 1)

    return render_template('doctor/dashboard.html', 
                         doctor=doctor, 
                         queue=queue_appts,
                         today=today_str,
                         patients_today=patients_today,
                         seen_today=seen_today,
                         waiting_today=waiting_today,
                         timeline=timeline,
                         reviews=reviews,
                         avg_rating=avg_rating)

@doctor_bp.route('/doctor/call/<int:appt_id>', methods=['POST'])
@role_required(['doctor'])
def call_patient(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    doctor_id = session.get('doctor_id')
    
    # Verify doctor owns this appt
    if appt.slot.doctor_id != doctor_id:
        return jsonify({"error": "Unauthorized"}), 403
        
    # Mark others as seen if they were called
    current_called = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id,
        Appointment.status == 'called'
    ).first()
    if current_called:
        current_called.status = 'seen'
        
    appt.status = 'called'
    db.session.commit()
    
    socketio.emit('queue_update', {'slot_id': appt.slot_id})
    audit_logger.log_action('call_patient', f"Called patient for Appt #{appt.id}")
    
    flash(f"Calling token #{appt.token_number}", "success")
    return redirect(url_for('doctor.doctor_dashboard', doctor_id=doctor_id))

@doctor_bp.route('/doctor/complete/<int:appt_id>', methods=['POST'])
@role_required(['doctor'])
def complete_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    notes = request.form.get('notes', '')
    
    appt.status = 'seen'
    appt.notes = notes
    
    # Handle prescription upload
    file = request.files.get('prescription')
    if file and file.filename != '':
        filename = secure_filename(f"presc_{appt.id}_{file.filename}")
        os.makedirs(os.path.join(current_app.root_path, 'static', 'prescriptions'), exist_ok=True)
        path = os.path.join('static', 'prescriptions', filename)
        file.save(os.path.join(current_app.root_path, path))
        
        presc = Prescription(appointment_id=appt.id, doctor_id=doctor_id, patient_id=appt.patient_id, file_path=path)
        db.session.add(presc)
        
    db.session.commit()
    socketio.emit('queue_update', {'slot_id': appt.slot_id})
    audit_logger.log_action('complete_appointment', f"Completed Appt #{appt.id}")
    
    flash("Appointment completed successfully.", "success")
    return redirect(url_for('doctor.doctor_dashboard', doctor_id=session.get('doctor_id')))

@doctor_bp.route('/doctor/<int:doctor_id>/toggle-availability', methods=['POST'])
@role_required(['doctor'])
def toggle_availability(doctor_id):
    if session.get('doctor_id') != doctor_id:
        return jsonify({"error": "Unauthorized"}), 403
    doctor = Doctor.query.get_or_404(doctor_id)
    doctor.is_available = not doctor.is_available
    db.session.commit()
    return jsonify({ 'success': True, 'is_available': doctor.is_available })

@doctor_bp.route('/doctor/<int:doctor_id>/schedule')
@role_required(['doctor', 'super_admin'])
def doctor_schedule(doctor_id):
    if session.get('user_role') != 'super_admin' and session.get('doctor_id') != doctor_id:
        flash('Unauthorized access to this schedule.', 'error')
        return redirect(url_for('auth.login'))
        
    doctor = Doctor.query.get_or_404(doctor_id)
    today = date.today().isoformat()
    end_date = (date.today() + timedelta(days=7)).isoformat()
    
    slots = Slot.query.filter(
        Slot.doctor_id == doctor_id,
        Slot.date >= today,
        Slot.date <= end_date
    ).order_by(Slot.date, Slot.time_label).all()
    
    schedule_data = []
    for s in slots:
        appts = Appointment.query.filter_by(slot_id=s.id).all()
        patients = []
        for a in appts:
            p = Patient.query.get(a.patient_id)
            patients.append({'name': p.name, 'token': a.token_number})
        
        status = 'open'
        if s.is_blocked: status = 'blocked'
        elif s.booked_count >= s.max_capacity: status = 'full'
        
        schedule_data.append({
            'slot': s,
            'patients': patients,
            'booked': s.booked_count,
            'capacity': s.max_capacity,
            'status': status
        })
        
    return render_template('doctor/schedule.html', doctor=doctor, schedule=schedule_data)

@doctor_bp.route('/doctor/no-show/<int:appt_id>', methods=['POST'])
@role_required(['doctor'])
def no_show_appointment(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    doctor_id = session.get('doctor_id')
    
    if appt.slot.doctor_id != doctor_id:
        return jsonify({"error": "Unauthorized"}), 403
        
    appt.status = 'no_show'
    db.session.commit()
    
    socketio.emit('queue_update', {'slot_id': appt.slot_id})
    audit_logger.log_action('no_show', f"Marked Appt #{appt.id} as No Show")
    
    flash("Appointment marked as No Show.", "info")
    return redirect(url_for('doctor.doctor_dashboard', doctor_id=doctor_id))
