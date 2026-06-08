from flask import request, session
from models import db, Appointment, Slot, AuditLog

class AuditLogger:
    def log_action(self, action, details=""):
        user_id = session.get('user_id', 'Anonymous')
        user_name = session.get('user_name', 'System')
        user_role = session.get('user_role', 'guest')
        ip_address = request.remote_addr if request else '127.0.0.1'
        
        log = AuditLog(
            user_id=str(user_id),
            user_name=user_name,
            user_role=user_role,
            action=action,
            details=details,
            ip_address=ip_address
        )
        db.session.add(log)
        db.session.commit()

audit_logger = AuditLogger()

def send_password_reset_email(recipient_email, reset_url):
    """Send password reset email using Flask‑Mail configuration."""
    from flask_mail import Message, Mail
    from flask import current_app, render_template
    mail = Mail()
    subject = _("Password Reset Request")
    html_body = render_template('reset_password_email.html', reset_url=reset_url)
    msg = Message(subject=subject,
                  recipients=[recipient_email],
                  html=html_body,
                  sender=current_app.config.get('MAIL_DEFAULT_SENDER'))
    mail.send(msg)


def predict_no_show(patient_id):
    """
    Calculates no-show risk based on patient history.
    Logic: (count of no_show status) / (total past appointments)
    If no_show_rate > 0.3 OR patient has 0 past appointments → high risk
    """
    past_appts = Appointment.query.filter_by(patient_id=patient_id).filter(
        Appointment.status.in_(['seen', 'no_show'])
    ).all()
    
    total = len(past_appts)
    if total == 0:
        return 'high' # New patients are considered high risk for now
    
    no_shows = sum(1 for a in past_appts if a.status == 'no_show')
    rate = no_shows / total
    
    if rate > 0.3:
        return 'high'
    elif rate > 0.1:
        return 'medium'
    else:
        return 'low'

def generate_time_labels(start_hour=9, end_hour=17, interval=30):
    """Generates a list of time labels like ['9:00 AM', '9:30 AM', ...]"""
    labels = []
    for hour in range(start_hour, end_hour):
        for minute in range(0, 60, interval):
            h = hour % 12 or 12
            ampm = 'AM' if hour < 12 else 'PM'
            labels.append(f"{h:02d}:{minute:02d} {ampm}")
    return labels
