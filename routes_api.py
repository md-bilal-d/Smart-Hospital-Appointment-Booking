"""API routes: queue status, analytics data"""
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from models import db, Appointment, Slot, Doctor, Patient, QueueLog, Department
from smart_scheduling import calculate_live_eta
from datetime import date, timedelta, datetime
from sqlalchemy import func
import re
from translations import gettext as _

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
    ).all()
    # Sort chronologically: date first, then parse time label
    slots.sort(key=lambda x: (x.date, datetime.strptime(x.time_label, "%I:%M %p")))
    
    slots_by_date = {d: [] for d in dates}
    for s in slots:
        slots_by_date[s.date].append({
            'id': s.id,
            'time_label': s.time_label,
            'available_spots': s.available_spots,
            'max_capacity': s.max_capacity
        })
    return jsonify(slots_by_date)

@api_bp.route('/api/slots/<int:doctor_id>/<string:selected_date>')
def get_slots_by_date(doctor_id, selected_date):
    slots = Slot.query.filter_by(doctor_id=doctor_id, date=selected_date, is_blocked=False).all()
    slots.sort(key=lambda x: datetime.strptime(x.time_label, "%I:%M %p"))
    
    result = []
    for s in slots:
        result.append({
            'id': s.id,
            'time': s.time_label,
            'available': s.available_spots
        })
    return jsonify({'slots': result})

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

@api_bp.route('/api/doctor/<int:doctor_id>/stats')
def get_doctor_stats(doctor_id):
    today_str = date.today().isoformat()
    
    # 1. Today's Queue Progress (Doughnut)
    # Waiting, Called, Seen, No-Show
    queue_data = db.session.query(
        Appointment.status, func.count(Appointment.id)
    ).join(Slot).filter(
        Slot.doctor_id == doctor_id,
        Slot.date == today_str
    ).group_by(Appointment.status).all()
    
    queue_stats = {s: 0 for s in ['waiting', 'called', 'seen', 'no_show']}
    for status, count in queue_data:
        if status in queue_stats:
            queue_stats[status] = count

    # 2. Weekly Patient Volume (Bar)
    weekly_volume = []
    for i in range(6, -1, -1):
        d = (date.today() - timedelta(days=i))
        count = Appointment.query.join(Slot).filter(
            Slot.doctor_id == doctor_id,
            Slot.date == d.isoformat(),
            Appointment.status == 'seen'
        ).count()
        weekly_volume.append({
            'day': d.strftime('%a'),
            'count': count,
            'is_today': i == 0
        })

    # 3. Hourly Slot Load (Line)
    # Assume 9AM to 5PM (17:00)
    hourly_load = []
    for hour in range(9, 18):
        # Match time labels like "09:00 AM", "09:30 AM"
        h12 = hour % 12 or 12
        ampm = 'AM' if hour < 12 else 'PM'
        pattern = f"{h12:02d}:%" # Rough match for Slot.time_label
        
        count = Appointment.query.join(Slot).filter(
            Slot.doctor_id == doctor_id,
            Slot.date == today_str,
            Slot.time_label.like(f"{h12:02d}:%"),
            Slot.time_label.like(f"%{ampm}")
        ).count()
        hourly_load.append({'hour': f"{h12}{ampm}", 'count': count})

    # 4. Monthly Performance (Line) - last 30 days
    monthly_trend = []
    for i in range(29, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        scheduled = Appointment.query.join(Slot).filter(
            Slot.doctor_id == doctor_id,
            Slot.date == d
        ).count()
        seen = Appointment.query.join(Slot).filter(
            Slot.doctor_id == doctor_id,
            Slot.date == d,
            Appointment.status == 'seen'
        ).count()
        monthly_trend.append({'date': d[-5:], 'scheduled': scheduled, 'seen': seen})

    # 5. Appointment Type Breakdown (Polar)
    type_data = db.session.query(
        Appointment.appointment_type, func.count(Appointment.id)
    ).join(Slot).filter(
        Slot.doctor_id == doctor_id
    ).group_by(Appointment.appointment_type).all()
    
    type_breakdown = {t: 0 for t in ['Normal', 'Urgent', 'Follow-up']}
    for t, count in type_data:
        if t in type_breakdown:
            type_breakdown[t] = count

    # Quick Stats
    total_week = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id,
        Slot.date >= (date.today() - timedelta(days=7)).isoformat()
    ).count()
    
    avg_patients = round(Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id,
        Appointment.status == 'seen'
    ).count() / 30, 1) # Simple avg over 30 days

    no_show_total = Appointment.query.join(Slot).filter(
        Slot.doctor_id == doctor_id,
        Appointment.status == 'no_show'
    ).count()
    total_appts = Appointment.query.join(Slot).filter(Slot.doctor_id == doctor_id).count()
    no_show_rate = round((no_show_total / total_appts * 100), 1) if total_appts else 0

    return jsonify({
        'queue': queue_stats,
        'weekly': weekly_volume,
        'hourly': hourly_load,
        'monthly': monthly_trend,
        'types': type_breakdown,
        'quick_stats': {
            'total_week': total_week,
            'avg_patients': avg_patients,
            'no_show_rate': no_show_rate
        }
    })

@api_bp.route('/api/chat', methods=['POST'])
def chat():
    data = request.json
    if not data or 'message' not in data:
        return jsonify({'error': 'No message provided'}), 400
    
    msg = data['message'].lower()
    
    rules = {
        'Cardiology': ['heart', 'chest pain', 'palpitations', 'blood pressure'],
        'Neurology': ['headache', 'migraine', 'dizziness', 'numbness', 'seizure'],
        'Orthopedic': ['bone', 'fracture', 'joint', 'back pain', 'knee', 'muscle'],
        'Pediatric': ['child', 'baby', 'toddler', 'fever', 'vaccine'],
        'Dermatology': ['skin', 'rash', 'acne', 'itching', 'mole'],
        'General': ['fever', 'cough', 'cold', 'fatigue', 'weakness']
    }
    
    suggested_dept = None
    for dept_name, keywords in rules.items():
        if any(re.search(rf"\b{kw}\b", msg) for kw in keywords):
            suggested_dept = dept_name
            break
            
    if suggested_dept:
        dept = Department.query.filter(Department.name.ilike(f"%{suggested_dept}%")).first()
        if dept:
            return jsonify({
                'reply': _("Based on your symptoms, I recommend booking an appointment with the **{dept_name}** department.", dept_name=dept.name),
                'department_id': dept.id,
                'department_name': dept.name
            })
            
    return jsonify({
        'reply': _("I'm an AI assistant, and I couldn't clearly match your symptoms. Could you provide more details, or would you like to see a General Physician?"),
        'department_id': None
    })


@api_bp.route('/api/symptom-checker/chat', methods=['POST'])
@login_required
def symptom_checker_chat():
    from models import Review
    
    data = request.json
    if not data or 'message' not in data:
        return jsonify({'error': 'No message provided'}), 400
    
    msg = data['message'].lower().strip()
    
    # Advanced keywords dict
    rules = {
        'Cardiology': ['heart', 'chest pain', 'palpitations', 'blood pressure', 'cardio', 'shortness of breath', 'chest tightness'],
        'Neurology': ['headache', 'migraine', 'dizziness', 'numbness', 'seizure', 'tingling', 'brain', 'fainting'],
        'Orthopedics': ['bone', 'fracture', 'joint', 'back pain', 'knee', 'muscle', 'sprain', 'injury', 'broken', 'spine'],
        'Pediatrics': ['child', 'baby', 'toddler', 'pediatric', 'kid', 'infant'],
        'Dermatology': ['skin', 'rash', 'acne', 'itching', 'mole', 'eczema', 'allergy', 'burn'],
        'ENT': ['ear', 'nose', 'throat', 'hearing', 'sinus', 'tonsil', 'voice', 'nasal'],
        'Gynecology': ['pregnancy', 'menstrual', 'period', 'gyne', 'female health', 'uterus'],
        'General Medicine': ['fever', 'cough', 'cold', 'fatigue', 'weakness', 'flu', 'stomach', 'infection', 'throat infection']
    }
    
    suggested_dept = None
    matched_keyword = None
    for dept_name, keywords in rules.items():
        for kw in keywords:
            if re.search(rf"\b{kw}\b", msg):
                suggested_dept = dept_name
                matched_keyword = kw
                break
        if suggested_dept:
            break
            
    is_fallback = False
    if not suggested_dept:
        # Default fallback to General Medicine
        suggested_dept = 'General Medicine'
        is_fallback = True

    dept = Department.query.filter(Department.name.ilike(f"%{suggested_dept}%")).first()
    if not dept:
        # If still not found, get first department in DB
        dept = Department.query.first()
        is_fallback = True
        
    if not dept:
        return jsonify({'reply': _("I'm sorry, I couldn't access the hospital departments. Please contact reception.")}), 500

    # Get doctors in this department
    doctors = Doctor.query.filter_by(department_id=dept.id, is_available=True).all()
    
    # Build reply text
    if is_fallback:
        reply = _("I couldn't quite pinpoint a specific specialty for those symptoms, so I recommend consulting with our **General Medicine** department for an initial evaluation. Our general physicians can refer you to specialists if needed. Here are our available doctors:")
    else:
        reply = _("Based on your mention of **'{matched_keyword}'**, I highly recommend consulting a specialist in our **{dept_name}** department ({dept_description}). Here are our available specialists:", matched_keyword=matched_keyword, dept_name=dept.name, dept_description=dept.description)

    # Build doctor objects
    doc_list = []
    for doc in doctors:
        # Calculate rating
        reviews = Review.query.filter_by(doctor_id=doc.id).all()
        avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else 4.8
        num_reviews = len(reviews) if reviews else 14
        
        doc_list.append({
            'id': doc.id,
            'name': doc.name,
            'specialization': doc.specialization,
            'experience_years': doc.experience_years,
            'profile_pic_url': doc.profile_pic_url or f"https://api.dicebear.com/9.x/initials/svg?seed={doc.name}",
            'rating': avg_rating,
            'num_reviews': num_reviews
        })

    return jsonify({
        'reply': reply,
        'is_fallback': is_fallback,
        'department': {
            'id': dept.id,
            'name': dept.name,
            'description': dept.description
        },
        'doctors': doc_list
    })


@api_bp.route('/api/symptom-checker/book-inline', methods=['POST'])
@login_required
def book_inline():
    from extensions import socketio
    from utils import audit_logger, predict_no_show
    
    data = request.json
    if not data or 'slot_id' not in data:
        return jsonify({'success': False, 'error': 'Missing slot_id'}), 400
        
    slot_id = data.get('slot_id')
    appt_type = data.get('appointment_type', 'normal')
    notes = data.get('notes', '')
    pref_lang = data.get('preferred_language', 'English')
    cons_mode = data.get('consultation_mode', 'In-Person')
    em_name = data.get('emergency_contact_name', '')
    em_phone = data.get('emergency_contact_phone', '')
    
    slot = Slot.query.get(slot_id)
    if not slot:
        return jsonify({'success': False, 'error': 'Invalid slot selected'}), 404
        
    if slot.available_spots <= 0:
        return jsonify({'success': False, 'error': _('This slot is full. Please choose another.')}), 400
        
    # sequential token assignment
    existing_count = Appointment.query.filter_by(slot_id=slot.id).filter(
        Appointment.status.in_(['waiting', 'called'])
    ).count()
    token = existing_count + 1
    
    risk = predict_no_show(current_user.id)
    
    try:
        appt = Appointment(
            patient_id=current_user.id,
            slot_id=slot_id,
            token_number=token,
            status='waiting',
            appointment_type=appt_type,
            notes=notes,
            risk_flag=risk,
            preferred_language=pref_lang,
            consultation_mode=cons_mode,
            emergency_contact_name=em_name,
            emergency_contact_phone=em_phone
        )
        db.session.add(appt)
        db.session.flush()
        
        log = QueueLog(appointment_id=appt.id, action='booked')
        db.session.add(log)
        db.session.commit()
        
        # Emit socket io events
        doctor = slot.doctor
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
            'action': f"New inline booking: Token #{token} ({appt_type})",
            'time': datetime.now().strftime("%I:%M:%S %p"),
            'type': 'booked'
        })
        
        audit_logger.log_action('Appointment Booked Inline', f'Token #{token} with {doctor.name} via Chatbot')
        
        # calculate dynamic ETA
        position = existing_count + 1
        eta = position * 10
        
        return jsonify({
            'success': True,
            'appointment': {
                'id': appt.id,
                'token': token,
                'date': slot.date,
                'time': slot.time_label,
                'doctor_name': doctor.name,
                'specialization': doctor.specialization,
                'consultation_mode': cons_mode,
                'eta_minutes': eta
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'Database error: {str(e)}'}), 500


@api_bp.route('/api/symptom-checker/wizard-analyze', methods=['POST'])
@login_required
def wizard_analyze():
    from models import Review
    
    data = request.json or {}
    primary_area = data.get('primary_area', 'General Medicine')
    severity = int(data.get('severity', 1))
    duration = data.get('duration', 'Less than 24h')
    accompanying = data.get('accompanying_symptoms', [])
    
    # Map primary_area categories to Department Names in DB
    area_mapping = {
        'Neurology': 'Neurology',
        'Cardiology': 'Cardiology',
        'General Medicine': 'General Medicine',
        'Orthopedics': 'Orthopedics',
        'Dermatology': 'Dermatology',
        'ENT': 'ENT',
        'Gynecology': 'Gynecology',
        'Pediatrics': 'Pediatrics'
    }
    
    mapped_dept_name = area_mapping.get(primary_area, 'General Medicine')
    dept = Department.query.filter(Department.name.ilike(f"%{mapped_dept_name}%")).first()
    if not dept:
        dept = Department.query.first()
        
    if not dept:
        return jsonify({'success': False, 'error': 'No departments configured in system.'}), 500
        
    # Calculate Urgency Score & Triage Category
    # Rules: High if severity >= 8, or Medium if severity >= 5, or based on accompanying symptoms/duration
    if severity >= 8:
        urgency = _("High")
        urgency_desc = _("Urgent consultation highly recommended. Please arrange to see a specialist promptly.")
    elif severity >= 5 or duration in ['4-7 Days', 'Chronic / Multi-week'] or len(accompanying) >= 3:
        urgency = _("Medium")
        urgency_desc = _("Moderate symptoms detected. Scheduled clinical checkup is recommended.")
    else:
        urgency = _("Low")
        urgency_desc = _("Mild symptom indicators. Routine or general health consultation advised.")
        
    # Generate diagnostic summary text
    symptoms_joined = ", ".join(accompanying) if accompanying else "none specified"
    summary_text = _("You have indicated primary symptoms in **{dept_name}** with a severity of **{severity}/10** lasting for **{duration}**. Accompanying symptoms: *{symptoms_joined}*. **Triage Assessment:** {urgency_desc}",
                     dept_name=dept.name,
                     severity=severity,
                     duration=duration,
                     symptoms_joined=symptoms_joined,
                     urgency_desc=urgency_desc)
    
    # Fetch active doctors
    doctors = Doctor.query.filter_by(department_id=dept.id, is_available=True).all()
    doc_list = []
    for doc in doctors:
        reviews = Review.query.filter_by(doctor_id=doc.id).all()
        avg_rating = round(sum(r.rating for r in reviews) / len(reviews), 1) if reviews else 4.8
        num_reviews = len(reviews) if reviews else 14
        
        doc_list.append({
            'id': doc.id,
            'name': doc.name,
            'specialization': doc.specialization,
            'experience_years': doc.experience_years,
            'profile_pic_url': doc.profile_pic_url or f"https://api.dicebear.com/9.x/initials/svg?seed={doc.name}",
            'rating': avg_rating,
            'num_reviews': num_reviews
        })
        
    return jsonify({
        'success': True,
        'department': {
            'id': dept.id,
            'name': dept.name,
            'description': dept.description
        },
        'urgency_level': urgency,
        'analysis_summary': summary_text,
        'doctors': doc_list
    })


