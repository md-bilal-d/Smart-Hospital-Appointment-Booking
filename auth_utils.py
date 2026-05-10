from functools import wraps
from flask import session, redirect, url_for, flash, request
from models import Patient, Staff

def role_required(allowed_roles):
    if isinstance(allowed_roles, str):
        allowed_roles = [allowed_roles]
        
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_role' not in session:
                flash('Please login to access this page.', 'error')
                return redirect(url_for('auth.login'))
            
            if session['user_role'] not in allowed_roles:
                flash('Unauthorized access.', 'error')
                return redirect(url_for('auth.unauthorized'))
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def get_current_user():
    if 'user_id' not in session:
        return None
    
    role = session.get('user_role')
    uid = session.get('user_id')
    
    if role == 'patient':
        return Patient.query.get(uid)
    else:
        return Staff.query.get(uid)
