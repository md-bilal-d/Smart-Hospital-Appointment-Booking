import os
SECRET_KEY = os.environ.get('SECRET_KEY', 'medislot-secret-key-2026')
SQLALCHEMY_DATABASE_URI = 'sqlite:///medislot.db'
SQLALCHEMY_TRACK_MODIFICATIONS = False
ADMIN_USER = 'admin'
ADMIN_PASS = 'admin123'
