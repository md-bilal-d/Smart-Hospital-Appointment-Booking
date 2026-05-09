"""MediSlot - Smart Hospital Appointment Booking System"""
import eventlet
eventlet.monkey_patch()

from flask import Flask
from flask_login import LoginManager
from models import db, Patient, Doctor
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from extensions import socketio

login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config.from_pyfile('config.py')
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    @login_manager.user_loader
    def load_user(user_id):
        if user_id and user_id.startswith('patient_'):
            pid = int(user_id.split('_')[1])
            return Patient.query.get(pid)
        return None

    from routes_auth import auth_bp
    from routes_patient import patient_bp
    from routes_admin import admin_bp
    from routes_doctor import doctor_bp
    from routes_api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(patient_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(doctor_bp)
    app.register_blueprint(api_bp)

    socketio.init_app(app, cors_allowed_origins="*")

    @app.errorhandler(404)
    def not_found(e):
        from flask import render_template
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        from flask import render_template
        return render_template('500.html'), 500

    # APScheduler for appointment reminders
    def check_reminders():
        with app.app_context():
            from datetime import datetime, timedelta, date as dt_date
            from models import Appointment, Slot
            now = datetime.now()
            target = now + timedelta(minutes=30)
            today = dt_date.today().isoformat()
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
    socketio.run(app, debug=True, port=5000, use_reloader=False)
