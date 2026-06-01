"""Auth routes: register, login, OTP, logout"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask_login import login_user, logout_user
from models import db, Patient, Staff
import bcrypt, random
from datetime import datetime
from utils import audit_logger
from translations import gettext as _

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/set-language/<string:lang>')
def set_language(lang):
    if lang in ['en', 'es', 'fr', 'hi']:
        session['lang'] = lang
        flash(_('Language changed successfully.'), 'success')
    ref = request.referrer
    if ref:
        return redirect(ref)
    return redirect(url_for('auth.landing'))

@auth_bp.route('/')
def landing():
    from models import Doctor
    doctors = Doctor.query.filter_by(is_available=True).limit(6).all()
    return render_template('landing.html', doctors=doctors)

@auth_bp.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name = request.form['name']
        phone = request.form['phone']
        email = request.form['email']
        password = request.form['password']
        if Patient.query.filter((Patient.email==email)|(Patient.phone==phone)).first():
            flash(_('Email or phone already registered.'), 'error')
            return redirect(url_for('auth.register'))
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        p = Patient(name=name, phone=phone, email=email, password_hash=pw_hash, role='patient')
        db.session.add(p)
        db.session.commit()
        audit_logger.log_action('Patient Registered', f'New patient: {name}')
        flash(_('Registration successful! Please login.'), 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        # Check Staff first
        staff = Staff.query.filter_by(email=email).first()
        if staff and bcrypt.checkpw(password.encode(), staff.password_hash.encode()):
            if not staff.is_active:
                flash(_('Account deactivated. Contact Super Admin.'), 'error')
                return redirect(url_for('auth.login'))
            
            session['user_id'] = staff.id
            session['user_role'] = staff.role
            session['full_user_id'] = f"staff_{staff.id}"
            session['user_name'] = staff.name
            staff.last_login = datetime.utcnow()
            db.session.commit()
            
            login_user(staff)
            audit_logger.log_action('Staff Login', f'{staff.name} logged in as {staff.role}')
            
            if staff.role == 'super_admin':
                return redirect(url_for('superadmin.dashboard'))
            elif staff.role == 'doctor':
                session['doctor_id'] = staff.doctor_id
                session['doctor_name'] = staff.name
                return redirect(url_for('doctor.doctor_dashboard', doctor_id=staff.doctor_id))
            else: # receptionist
                return redirect(url_for('admin.dashboard'))

        # Check Patient
        patient = Patient.query.filter_by(email=email).first()
        if patient and bcrypt.checkpw(password.encode(), patient.password_hash.encode()):
            otp = str(random.randint(100000, 999999))
            from datetime import timedelta
            session['otp'] = otp
            session['temp_patient_id'] = patient.id
            session['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
            print(f"OTP for {patient.email}: {otp}")
            return redirect(url_for('auth.verify_otp'))
            
        audit_logger.log_action('Failed Login Attempt', f'Email: {email}')
        flash(_('Invalid email or password.'), 'error')
    return render_template('login.html')

@auth_bp.route('/doctor-login', methods=['GET', 'POST'])
def doctor_login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # Check Doctors table directly
        from models import Doctor
        doctor = Doctor.query.filter_by(email=email).first()
        
        if not doctor:
            flash(_('No doctor account found with this email'), 'error')
            return redirect(url_for('auth.doctor_login'))
            
        if bcrypt.checkpw(password.encode(), doctor.password_hash.encode()):
            if not getattr(doctor, 'is_active', True):
                flash(_('Account deactivated. Contact Super Admin.'), 'error')
                return redirect(url_for('auth.doctor_login'))
            
            # Set sessions as requested
            session['doctor_id'] = doctor.id
            session['doctor_name'] = doctor.name
            session['user_role'] = 'doctor'  # Added for role_required support
            session['role'] = 'doctor'
            
            print(f"Doctor ID: {doctor.id}, Name: {doctor.name}") # Fix 3 - Debug print
            audit_logger.log_action('Doctor Login', f'{doctor.name} logged in')
            return redirect(url_for('doctor.doctor_dashboard', doctor_id=doctor.id))
            
        flash(_('Incorrect password'), 'error')
        return redirect(url_for('auth.doctor_login'))
        
    return render_template('doctor_login.html')

@auth_bp.route('/verify-otp', methods=['GET','POST'])
def verify_otp():
    if 'otp' not in session:
        return redirect(url_for('auth.login'))
        
    if request.method == 'POST':
        entered = request.form['otp'].strip()
        expiry_str = session.get('otp_expiry')
        
        if not expiry_str:
            flash(_('OTP session invalid. Please login again.'), 'error')
            session.pop('otp', None)
            return redirect(url_for('auth.login'))
            
        try:
            otp_expiry = datetime.fromisoformat(expiry_str)
            if datetime.utcnow() > otp_expiry:
                flash(_('OTP expired. Please request a new one.'), 'error')
                session.pop('otp', None)
                return redirect(url_for('auth.login'))
        except ValueError:
            flash(_('OTP session corrupted. Please login again.'), 'error')
            session.pop('otp', None)
            return redirect(url_for('auth.login'))
            
        if entered == session.get('otp'):
            patient = Patient.query.get(session.get('temp_patient_id'))
            login_user(patient)
            
            session['patient_id'] = session.pop('temp_patient_id')
            session.pop('otp', None)
            session.pop('otp_expiry', None)
            
            session['user_id'] = patient.id
            session['user_role'] = 'patient'
            session['patient_name'] = patient.name
            session['full_user_id'] = f"patient_{patient.id}"
            session['user_name'] = patient.name
            
            audit_logger.log_action('Patient Login', f'{patient.name} logged in')
            flash(_('Login successful!'), 'success')
            return redirect(url_for('patient.dashboard'))
            
        flash(_('Wrong OTP'), 'error')
        return redirect(url_for('auth.verify_otp'))
        
    otp = session.get('otp', 'Not generated')
    return render_template('verify_otp.html', otp=otp)

@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    if 'temp_patient_id' not in session:
        return redirect(url_for('auth.login'))
    
    patient = Patient.query.get(session['temp_patient_id'])
    if not patient:
        return redirect(url_for('auth.login'))
        
    otp = str(random.randint(100000, 999999))
    from datetime import timedelta
    session['otp'] = otp
    session['otp_expiry'] = (datetime.utcnow() + timedelta(minutes=5)).isoformat()
    
    print(f"[SMS] New OTP for {patient.email}: {otp}")
    
    return redirect(url_for('auth.verify_otp'))

@auth_bp.route('/logout')
def logout():
    audit_logger.log_action('Logout', f"User {session.get('user_name')} logged out")
    logout_user()
    session.clear()
    flash(_('Logged out successfully.'), 'success')
    return redirect(url_for('auth.landing'))

@auth_bp.route('/unauthorized')
def unauthorized():
    return render_template('404.html', message="Unauthorized Access. You do not have permission to view this page."), 403

