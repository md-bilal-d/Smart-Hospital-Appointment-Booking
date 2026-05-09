"""Auth routes: register, login, OTP, logout"""
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from flask_login import login_user, logout_user
from models import db, Patient
import bcrypt, random
from datetime import datetime

auth_bp = Blueprint('auth', __name__)

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
            flash('Email or phone already registered.', 'error')
            return redirect(url_for('auth.register'))
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        p = Patient(name=name, phone=phone, email=email, password_hash=pw_hash)
        db.session.add(p)
        db.session.commit()
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html')

@auth_bp.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        # Admin check
        if email == 'admin' and password == 'admin123':
            session['is_admin'] = True
            session['admin_logged_in'] = True
            return redirect(url_for('admin.dashboard'))
        patient = Patient.query.filter_by(email=email).first()
        if patient and bcrypt.checkpw(password.encode(), patient.password_hash.encode()):
            otp = str(random.randint(100000, 999999))
            session['otp'] = otp
            session['otp_patient_id'] = patient.id
            session['otp_time'] = datetime.utcnow().isoformat()
            print(f"[SMS] OTP for {patient.name}: {otp}")
            flash(f'Your OTP is: {otp} (simulated SMS)', 'otp')
            return redirect(url_for('auth.verify_otp'))
        flash('Invalid email or password.', 'error')
    return render_template('login.html')

@auth_bp.route('/verify-otp', methods=['GET','POST'])
def verify_otp():
    if 'otp' not in session:
        return redirect(url_for('auth.login'))
    if request.method == 'POST':
        entered = request.form['otp']
        otp_time = datetime.fromisoformat(session.get('otp_time',''))
        elapsed = (datetime.utcnow() - otp_time).total_seconds()
        if elapsed > 300:
            flash('OTP expired. Please login again.', 'error')
            session.pop('otp', None)
            return redirect(url_for('auth.login'))
        if entered == session.get('otp'):
            patient = Patient.query.get(session['otp_patient_id'])
            login_user(patient)
            session.pop('otp', None)
            session.pop('otp_patient_id', None)
            session.pop('otp_time', None)
            session['patient_name'] = patient.name
            flash('Login successful!', 'success')
            return redirect(url_for('patient.dashboard'))
        flash('Invalid OTP. Try again.', 'error')
    return render_template('verify_otp.html')

@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    if 'otp_patient_id' in session:
        otp = str(random.randint(100000, 999999))
        session['otp'] = otp
        session['otp_time'] = datetime.utcnow().isoformat()
        patient = Patient.query.get(session['otp_patient_id'])
        print(f"[SMS] OTP resent for {patient.name}: {otp}")
        flash(f'New OTP: {otp} (simulated SMS)', 'otp')
    return redirect(url_for('auth.verify_otp'))

@auth_bp.route('/logout')
def logout():
    logout_user()
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('auth.landing'))

@auth_bp.route('/doctor-login', methods=['GET','POST'])
def doctor_login():
    from models import Doctor
    if request.method == 'POST':
        doctor_id = request.form.get('doctor_id')
        doc = Doctor.query.get(doctor_id)
        if doc:
            session['doctor_id'] = doc.id
            session['doctor_name'] = doc.name
            return redirect(url_for('doctor.dashboard', doctor_id=doc.id))
        flash('Doctor not found.', 'error')
    doctors = Doctor.query.all()
    return render_template('doctor_login.html', doctors=doctors)
