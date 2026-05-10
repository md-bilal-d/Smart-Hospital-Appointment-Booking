from models import db, AuditLog
from flask import request, session

def log_action(action, description=None, target_type=None, target_id=None):
    user_id = session.get('full_user_id', 'anonymous')
    user_role = session.get('user_role', 'guest')
    ip = request.remote_addr
    
    log = AuditLog(
        user_id=user_id,
        user_role=user_role,
        action=action,
        description=description,
        target_type=target_type,
        target_id=target_id,
        ip_address=ip
    )
    db.session.add(log)
    db.session.commit()
