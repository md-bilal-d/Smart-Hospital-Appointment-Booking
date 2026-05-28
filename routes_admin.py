"""Admin routes: dashboard, slot management, doctor management"""
import csv, io
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, Response, jsonify
from models import db, Doctor, Department, Slot, Appointment, QueueLog, Patient, Invoice
from smart_scheduling import get_wasted_slot_alerts
from datetime import date, datetime
from utils import audit_logger, generate_time_labels
from extensions import socketio
from auth_utils import role_required
from utils import audit_logger

admin_bp = Blueprint('admin', __name__)

@admin_bp.route('/admin')
@role_required(['receptionist', 'super_admin'])
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
    time_labels = generate_time_labels()
            
    grid = {}
    for tl in time_labels:
        grid[tl] = {}
        for doc in doctors:
            slot = Slot.query.filter_by(doctor_id=doc.id, date=today, time_label=tl).first()
            if slot:
                grid[tl][doc.id] = {'slot': slot, 'count': slot.booked_count, 'cap': slot.max_capacity, 'blocked': slot.is_blocked}
            else:
                grid[tl][doc.id] = None
                
    wasted = get_wasted_slot_alerts()
    departments = Department.query.all()
    
    # Calculate Revenue Stats for today
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    invoices_today = Invoice.query.filter(Invoice.issued_at >= today_start).all()
    revenue_stats = {
        'today': sum(inv.amount for inv in invoices_today if inv.status == 'paid'),
        'pending': sum(inv.amount for inv in invoices_today if inv.status == 'unpaid')
    }
    
    return render_template('admin/dashboard.html', 
                         stats=stats, 
                         doctors=doctors,
                         time_labels=time_labels, 
                         grid=grid, 
                         today=today,
                         wasted_alerts=wasted, 
                         departments=departments,
                         revenue_stats=revenue_stats)

@admin_bp.route('/admin/slot-patients/<int:slot_id>')
@role_required(['receptionist', 'super_admin'])
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
@role_required(['receptionist', 'super_admin'])
def patient_action(appt_id, action):
    appt = Appointment.query.get_or_404(appt_id)
    if action == 'seen':
        appt.status = 'seen'
    elif action == 'no_show':
        appt.status = 'no_show'
    elif action == 'call':
        appt.status = 'called'
        
    db.session.commit()
    
    # Emit real-time events
    socketio.emit('queue_update', {'slot_id': appt.slot_id})
    slot = Slot.query.get(appt.slot_id)
    socketio.emit('status_update', {
        'appt_id': appt.id,
        'new_status': appt.status,
        'slot_id': appt.slot_id,
        'doctor_id': slot.doctor_id
    })
    audit_logger.log_action('patient_action', f"Marked Appt #{appt.id} as {action}")
    
    flash(f'Patient marked as {action}.', 'success')
    return redirect(url_for('admin.slot_patients', slot_id=appt.slot_id))

@admin_bp.route('/admin/block-slot/<int:slot_id>', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def block_slot(slot_id):
    slot = Slot.query.get_or_404(slot_id)
    slot.is_blocked = not slot.is_blocked
    slot.block_reason = request.form.get('reason', '')
    db.session.commit()
    
    status = 'blocked' if slot.is_blocked else 'unblocked'
    audit_logger.log_action('block_slot', f"{status.capitalize()} slot #{slot.id}")
    
    flash(f'Slot {status}.', 'info')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/admin/add-doctor', methods=['POST'])
@role_required(['super_admin'])
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
    
    # Generate slots for new doctor (7 days)
    from datetime import timedelta
    today = date.today()
    time_labels = generate_time_labels()
            
    for d in range(7):
        dt = (today + timedelta(days=d)).isoformat()
        for tl in time_labels:
            s = Slot(doctor_id=doc.id, date=dt, time_label=tl, max_capacity=3)
            db.session.add(s)
    db.session.commit()
    
    audit_logger.log_action('add_doctor', f"Added doctor {doc.name}")
    flash(f'Doctor {doc.name} added with slots!', 'success')
    return redirect(url_for('admin.dashboard'))

@admin_bp.route('/admin/history')
@role_required(['receptionist', 'super_admin'])
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
@role_required(['super_admin'])
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
            writer.writerow([a.token_number, p.name, p.phone, f"Dr. {doc.name}", s.time_label, a.status, a.appointment_type, a.notes])
    
    audit_logger.log_action('export_csv', f"Exported appointment data for {today}")
    output = si.getvalue()
    return Response(output, mimetype='text/csv',
                    headers={'Content-Disposition': f'attachment;filename=appointments_{today}.csv'})

@admin_bp.route('/admin/invoices')
@role_required(['receptionist', 'super_admin'])
def invoices():
    all_invoices = Invoice.query.order_by(Invoice.issued_at.desc()).all()
    # stats
    total_revenue = sum(inv.amount for inv in all_invoices if inv.status == 'paid')
    pending_revenue = sum(inv.amount for inv in all_invoices if inv.status == 'unpaid')
    return render_template('admin/invoices.html', invoices=all_invoices, total_revenue=total_revenue, pending_revenue=pending_revenue)

@admin_bp.route('/admin/generate-invoice/<int:appt_id>', methods=['POST'])
@role_required(['receptionist', 'super_admin'])
def generate_invoice(appt_id):
    appt = Appointment.query.get_or_404(appt_id)
    if appt.status != 'seen':
        flash('Can only generate invoice for completed (seen) appointments.', 'error')
        return redirect(url_for('admin.dashboard'))
        
    existing = Invoice.query.filter_by(appointment_id=appt.id).first()
    if existing:
        flash('Invoice already exists for this appointment.', 'error')
        return redirect(url_for('admin.dashboard'))
        
    amount = request.form.get('amount', type=float)
    description = request.form.get('description', '')
    
    if not amount or amount <= 0:
        flash('Invalid amount.', 'error')
        return redirect(url_for('admin.dashboard'))
        
    invoice = Invoice(appointment_id=appt.id, patient_id=appt.patient_id, amount=amount, description=description)
    db.session.add(invoice)
    db.session.commit()
    
    audit_logger.log_action('generate_invoice', f"Generated invoice for Appt #{appt.id} Amount: {amount}")
    flash(f'Invoice generated successfully for {amount}', 'success')
    return redirect(url_for('admin.invoices'))

