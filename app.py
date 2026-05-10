import os, atexit
from flask import Flask
from flask_login import LoginManager
from flask_jwt_extended import JWTManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from apscheduler.schedulers.background import BackgroundScheduler
from extensions import socketio
from models import db, Patient, Staff, Doctor

login_manager = LoginManager()
jwt = JWTManager()
limiter = Limiter(key_func=get_remote_address)

def create_app():
    app = Flask(__name__)
    app.config.from_pyfile('config.py')
    
    db.init_app(app)
    login_manager.init_app(app)
    jwt.init_app(app)
    limiter.init_app(app)
    
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(patient_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(doctor_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(superadmin_bp)
    app.register_blueprint(analytics_bp)
    
    @app.route('/debug-paths')
    def debug_paths():
        import routes_doctor
        return f"Doctor BP File: {routes_doctor.__file__}"



    # Ensure directories exist
    os.makedirs(os.path.join(app.root_path, 'static', 'prescriptions'), exist_ok=True)

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
            from models import Appointment, Slot
            now = datetime.now()
            
            # High Risk Reminder Logic
            target_1h = now + timedelta(hours=1)
            target_time_1h = f"{target_1h.hour % 12 or 12}:{30 if target_1h.minute >= 30 else 0:02d} {'AM' if target_1h.hour < 12 else 'PM'}"
            today = dt_date.today().isoformat()
            
            risk_appts = Appointment.query.join(Slot).filter(
                Slot.date == today,
                Slot.time_label == target_time_1h,
                Appointment.risk_flag == 'high',
                Appointment.status == 'waiting'
            ).all()
            for ra in risk_appts:
                print(f"🚨 HIGH RISK REMINDER: Call {ra.patient.name} at {ra.patient.phone} for {ra.slot.time_label} appointment.")

            # Normal Reminders (30 mins before)
            target = now + timedelta(minutes=30)
            target_time_h = target.hour % 12 or 12
            target_ampm = 'AM' if target.hour < 12 else 'PM'
            for m in [0, 30]:
                tl = f"{target_time_h}:{m:02d} {target_ampm}"
                slots = Slot.query.filter_by(date=today, time_label=tl).all()
                for slot in slots:
                    appts = Appointment.query.filter_by(slot_id=slot.id, reminder_sent=False).filter(
                        Appointment.status.in_(['waiting','called'])).all()
                    for a in appts:
                        p = Patient.query.get(a.patient_id)
                        doc = Doctor.query.get(slot.doctor_id)
                        print(f"📱 REMINDER: {p.name} - Token #{a.token_number} at {slot.time_label} with {doc.name}")
                        a.reminder_sent = True
            db.session.commit()

    scheduler = BackgroundScheduler()
    scheduler.add_job(func=check_reminders, trigger='interval', seconds=60)
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())

    return app

if __name__ == '__main__':
    app = create_app()
    socketio.run(app, debug=True, port=5000, use_reloader=True)

