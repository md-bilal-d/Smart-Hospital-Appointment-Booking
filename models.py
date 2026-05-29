"""
MediSlot - Database Models
All SQLAlchemy models for the Smart Hospital Appointment Booking System.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()


class Patient(UserMixin, db.Model):
    __tablename__ = 'patients'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default='patient')  # patient
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    appointments = db.relationship('Appointment', backref='patient', lazy=True)

    def get_id(self):
        return f"patient_{self.id}"


class Staff(UserMixin, db.Model):
    __tablename__ = 'staff'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # super_admin, doctor, receptionist
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return f"staff_{self.id}"


class Doctor(db.Model):
    __tablename__ = 'doctors'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(200), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    specialization = db.Column(db.String(100), nullable=False)
    experience_years = db.Column(db.Integer, nullable=False)
    profile_pic_url = db.Column(db.String(300), default='')
    is_available = db.Column(db.Boolean, default=True)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)

    slots = db.relationship('Slot', backref='doctor', lazy=True)

    def get_id(self):
        return f"doctor_{self.id}"


class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text, default='')

    doctors = db.relationship('Doctor', backref='department', lazy=True)


class Slot(db.Model):
    __tablename__ = 'slots'
    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # YYYY-MM-DD
    time_label = db.Column(db.String(20), nullable=False)  # e.g. "09:00 AM"
    max_capacity = db.Column(db.Integer, default=3)
    is_blocked = db.Column(db.Boolean, default=False)
    block_reason = db.Column(db.String(200), default='')

    appointments = db.relationship('Appointment', backref='slot', lazy=True)

    @property
    def booked_count(self):
        return Appointment.query.filter(
            Appointment.slot_id == self.id,
            Appointment.status.notin_(['cancelled'])
        ).count()

    @property
    def effective_capacity(self):
        """Allow overbooking buffer if there's a no-show pattern."""
        from smart_scheduling import get_noshow_buffer
        return self.max_capacity + get_noshow_buffer(self.id)

    @property
    def available_spots(self):
        return max(0, self.effective_capacity - self.booked_count)


class Appointment(db.Model):
    __tablename__ = 'appointments'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    slot_id = db.Column(db.Integer, db.ForeignKey('slots.id'), nullable=False)
    token_number = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='waiting')  # waiting/called/seen/cancelled/no_show
    appointment_type = db.Column(db.String(20), default='normal')  # normal/urgent/follow-up
    booked_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, default='')
    reminder_sent = db.Column(db.Boolean, default=False)
    risk_flag = db.Column(db.String(10), default='low')  # low/medium/high
    
    # New Features
    preferred_language = db.Column(db.String(20), default='English')
    consultation_mode = db.Column(db.String(20), default='In-Person')
    emergency_contact_name = db.Column(db.String(100), nullable=True)
    emergency_contact_phone = db.Column(db.String(20), nullable=True)


class Prescription(db.Model):
    __tablename__ = 'prescriptions'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    file_path = db.Column(db.String(300), nullable=False)
    notes = db.Column(db.Text, default='')
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    appointment = db.relationship('Appointment', backref='prescription', uselist=False)


class QueueLog(db.Model):
    __tablename__ = 'queue_log'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=False)
    action = db.Column(db.String(50), nullable=False)  # booked/called/seen/cancelled/no_show
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    appointment = db.relationship('Appointment', backref='logs')


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(50), nullable=False)
    user_name = db.Column(db.String(100), nullable=True)
    user_role = db.Column(db.String(20), nullable=False)
    action = db.Column(db.String(100), nullable=False)
    details = db.Column(db.Text, nullable=True)
    ip_address = db.Column(db.String(50), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Review(db.Model):
    __tablename__ = 'reviews'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id'), nullable=False)
    rating = db.Column(db.Integer, nullable=False) # 1 to 5
    feedback = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    appointment = db.relationship('Appointment', backref=db.backref('review', uselist=False))
    patient = db.relationship('Patient', backref='reviews')
    doctor = db.relationship('Doctor', backref='reviews')


class MedicalRecord(db.Model):
    __tablename__ = 'medical_records'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    file_path = db.Column(db.String(300), nullable=False)
    description = db.Column(db.String(200), default='')
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref='medical_records')


class ReminderLog(db.Model):
    """Tracks every reminder sent to patients for audit and history."""
    __tablename__ = 'reminder_logs'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    reminder_type = db.Column(db.String(20), nullable=False)  # email / in_app / browser
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivery_status = db.Column(db.String(20), default='sent')  # sent / failed / pending
    message_preview = db.Column(db.Text, default='')

    appointment = db.relationship('Appointment', backref='reminder_logs')
    patient = db.relationship('Patient', backref='reminder_logs')


class ReminderPreference(db.Model):
    """Patient-level reminder notification settings."""
    __tablename__ = 'reminder_preferences'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False, unique=True)
    email_enabled = db.Column(db.Boolean, default=True)
    in_app_enabled = db.Column(db.Boolean, default=True)
    reminder_minutes_before = db.Column(db.Integer, default=30)  # 15 / 30 / 60 / 120

    patient = db.relationship('Patient', backref=db.backref('reminder_preference', uselist=False))


class VitalsReading(db.Model):
    """Patient logged vitals and metrics for tracking and analysis."""
    __tablename__ = 'vitals_readings'
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    blood_pressure_sys = db.Column(db.Integer, nullable=True)  # Systolic mmHg
    blood_pressure_dia = db.Column(db.Integer, nullable=True)  # Diastolic mmHg
    blood_sugar = db.Column(db.Integer, nullable=True)         # Sugar mg/dL
    heart_rate = db.Column(db.Integer, nullable=True)          # Heart Rate bpm
    weight = db.Column(db.Float, nullable=True)                # Weight kg
    height = db.Column(db.Float, nullable=True)                # Height cm
    bmi = db.Column(db.Float, nullable=True)                   # BMI auto-calculated
    notes = db.Column(db.String(200), default='')
    logged_at = db.Column(db.DateTime, default=datetime.utcnow)

    patient = db.relationship('Patient', backref=db.backref('vitals_readings', lazy=True, order_by='VitalsReading.logged_at.desc()'))



class Invoice(db.Model):
    __tablename__ = 'invoices'
    id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey('appointments.id'), nullable=False)
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='unpaid')  # unpaid / paid
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    paid_at = db.Column(db.DateTime, nullable=True)
    description = db.Column(db.Text, default='')

    appointment = db.relationship('Appointment', backref=db.backref('invoice', uselist=False))
    patient = db.relationship('Patient', backref='invoices')


class Article(db.Model):
    __tablename__ = 'articles'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default='Health Tip') # Health Tip / Announcement / News
    author_id = db.Column(db.Integer, db.ForeignKey('staff.id'), nullable=False)
    is_published = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    author = db.relationship('Staff', backref='articles')
