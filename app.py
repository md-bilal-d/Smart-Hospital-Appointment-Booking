import os, atexit
from flask import Flask
from flask_login import LoginManager
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail, Message
from apscheduler.schedulers.background import BackgroundScheduler
from extensions import socketio
from models import db, Patient, Staff, Doctor

login_manager = LoginManager()
jwt = JWTManager()
limiter = Limiter(key_func=get_remote_address)
mail = Mail()

def create_app():
    app = Flask(__name__)
    app.config.from_pyfile('config.py')
    
    db.init_app(app)
    login_manager.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)
    mail.init_app(app)

    from translations import gettext
    from flask import session
    app.jinja_env.globals.update(_=gettext, get_locale=lambda: session.get('lang', 'en'))
    
    login_manager.login_view = 'auth.login'

    @login_manager.user_loader
    def load_user(user_id):
        if not user_id:
            return None
        if user_id.startswith('patient_'):
            return Patient.query.get(int(user_id.split('_')[1]))
        elif user_id.startswith('staff_'):
            return Staff.query.get(int(user_id.split('_')[1]))
        return None

    from routes_auth import auth_bp
    from routes_patient import patient_bp
    from routes_admin import admin_bp
    from routes_doctor import doctor_bp
    from routes_api import api_bp
    from routes_superadmin import superadmin_bp
    from routes_analytics import analytics_bp
    from routes_walkin import walkin_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(patient_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(doctor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(superadmin_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(walkin_bp)
    
    @app.route('/debug-paths')
    def debug_paths():
        import routes_doctor
        return f"Doctor BP File: {routes_doctor.__file__}"



    # Ensure directories exist
    os.makedirs(os.path.join(app.root_path, 'static', 'prescriptions'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'static', 'records'), exist_ok=True)

    # Ensure database schema is up-to-date with category column
    with app.app_context():
        try:
            from sqlalchemy import text
            result = db.session.execute(text("PRAGMA table_info(medical_records)")).fetchall()
            columns = [row[1] for row in result]
            if 'category' not in columns:
                db.session.execute(text("ALTER TABLE medical_records ADD COLUMN category VARCHAR(50) DEFAULT 'Other'"))
                db.session.commit()
                print("Database migrated: added category column to medical_records.")
        except Exception as e:
            print(f"Database migration error: {e}")

    socketio.init_app(app, cors_allowed_origins="*")

    @app.errorhandler(404)
    def not_found(e):
        from flask import render_template
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        from flask import render_template
        return render_template('500.html'), 500

    # APScheduler for appointment reminders & No-Show alerts
    def check_reminders():
        with app.app_context():
            from datetime import datetime, timedelta, date as dt_date
            from models import Appointment, Slot, ReminderLog, ReminderPreference
            now = datetime.now()
            today = dt_date.today().isoformat()

            # --- High Risk Reminder Logic (always 1 hour ahead) ---
            target_1h = now + timedelta(hours=1)
            target_time_1h = f"{target_1h.hour % 12 or 12:02d}:{30 if target_1h.minute >= 30 else 0:02d} {'AM' if target_1h.hour < 12 else 'PM'}"

            risk_appts = Appointment.query.join(Slot).filter(
                Slot.date == today,
                Slot.time_label == target_time_1h,
                Appointment.risk_flag == 'high',
                Appointment.status == 'waiting'
            ).all()
            for ra in risk_appts:
                print(f"🚨 HIGH RISK REMINDER: Call {ra.patient.name} at {ra.patient.phone} for {ra.slot.time_label} appointment.")

            # --- Patient Preference-Based Reminders ---
            # Check each preference interval: 15, 30, 60, 120 minutes
            for minutes_before in [15, 30, 60, 120]:
                target = now + timedelta(minutes=minutes_before)
                target_time_h = target.hour % 12 or 12
                target_ampm = 'AM' if target.hour < 12 else 'PM'

                for m in [0, 30]:
                    tl = f"{target_time_h:02d}:{m:02d} {target_ampm}"
                    slots = Slot.query.filter_by(date=today, time_label=tl).all()
                    for slot in slots:
                        appts = Appointment.query.filter_by(
                            slot_id=slot.id, reminder_sent=False
                        ).filter(
                            Appointment.status.in_(['waiting', 'called'])
                        ).all()

                        for a in appts:
                            p = Patient.query.get(a.patient_id)
                            doc = Doctor.query.get(slot.doctor_id)
                            if not p or not doc:
                                continue

                            # Get patient preference (default: 30 min, both channels)
                            pref = ReminderPreference.query.filter_by(patient_id=p.id).first()
                            pref_minutes = pref.reminder_minutes_before if pref else 30
                            email_on = pref.email_enabled if pref else True
                            inapp_on = pref.in_app_enabled if pref else True

                            # Only fire if this interval matches the patient's preference
                            if minutes_before != pref_minutes:
                                continue

                            msg_preview = f"Reminder: Token #{a.token_number} with {doc.name} at {slot.time_label} today"
                            print(f"📱 REMINDER: {p.name} - {msg_preview}")

                            # --- Email Reminder ---
                            if email_on and p.email:
                                try:
                                    email_html = f"""
                                    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:520px;margin:0 auto;background:linear-gradient(135deg,#1a1a2e,#16213e);border-radius:20px;overflow:hidden;border:1px solid rgba(255,255,255,0.1);">
                                        <div style="background:linear-gradient(135deg,#667eea,#764ba2);padding:28px 32px;">
                                            <h1 style="margin:0;color:white;font-size:22px;">🏥 MediSlot Appointment Reminder</h1>
                                        </div>
                                        <div style="padding:32px;color:#e2e8f0;">
                                            <p style="font-size:16px;margin:0 0 20px;">Hi <strong>{p.name}</strong>,</p>
                                            <p style="font-size:15px;margin:0 0 24px;color:#94a3b8;">Your appointment is coming up in <strong style="color:#818cf8;">{minutes_before} minutes</strong>.</p>
                                            <div style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);border-radius:16px;padding:24px;margin-bottom:24px;">
                                                <table style="width:100%;border-collapse:collapse;">
                                                    <tr><td style="padding:8px 0;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:2px;">Doctor</td><td style="padding:8px 0;color:white;font-weight:bold;text-align:right;">{doc.name}</td></tr>
                                                    <tr><td style="padding:8px 0;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:2px;">Specialty</td><td style="padding:8px 0;color:#818cf8;text-align:right;">{doc.specialization}</td></tr>
                                                    <tr><td style="padding:8px 0;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:2px;">Time</td><td style="padding:8px 0;color:#34d399;font-weight:bold;text-align:right;">{slot.time_label}</td></tr>
                                                    <tr><td style="padding:8px 0;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:2px;">Token</td><td style="padding:8px 0;color:#fbbf24;font-weight:bold;font-size:20px;text-align:right;">#{a.token_number}</td></tr>
                                                    <tr><td style="padding:8px 0;color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:2px;">Mode</td><td style="padding:8px 0;color:white;text-align:right;">{a.consultation_mode}</td></tr>
                                                </table>
                                            </div>
                                            <p style="font-size:13px;color:#64748b;margin:0;">Please arrive 5-10 minutes early. — Team MediSlot</p>
                                        </div>
                                    </div>
                                    """
                                    email_msg = Message(
                                        subject=f"⏰ MediSlot: Appointment in {minutes_before} min with {doc.name}",
                                        recipients=[p.email],
                                        html=email_html
                                    )
                                    mail.send(email_msg)
                                    db.session.add(ReminderLog(
                                        appointment_id=a.id, patient_id=p.id,
                                        reminder_type='email', delivery_status='sent',
                                        message_preview=msg_preview
                                    ))
                                except Exception as e:
                                    print(f"❌ Email failed for {p.name}: {e}")
                                    db.session.add(ReminderLog(
                                        appointment_id=a.id, patient_id=p.id,
                                        reminder_type='email', delivery_status='failed',
                                        message_preview=f"FAILED: {str(e)[:100]}"
                                    ))

                            # --- In-App Socket.IO Reminder ---
                            if inapp_on:
                                try:
                                    socketio.emit('appointment_reminder', {
                                        'patient_id': p.id,
                                        'appt_id': a.id,
                                        'token': a.token_number,
                                        'doctor_name': doc.name,
                                        'specialization': doc.specialization,
                                        'time_label': slot.time_label,
                                        'minutes_before': minutes_before,
                                        'consultation_mode': a.consultation_mode,
                                        'message': msg_preview
                                    })
                                    db.session.add(ReminderLog(
                                        appointment_id=a.id, patient_id=p.id,
                                        reminder_type='in_app', delivery_status='sent',
                                        message_preview=msg_preview
                                    ))
                                except Exception as e:
                                    print(f"❌ In-app reminder failed for {p.name}: {e}")

                            a.reminder_sent = True

            db.session.commit()

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_reminders, trigger='interval', seconds=300)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

    return app

if __name__ == '__main__':
    app = create_app()
    socketio.run(app, debug=True, port=5000, use_reloader=False)



