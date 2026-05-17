"""
MediSlot - Database Initialization & Seed Script
Creates tables, seeds 7 doctors, 4 departments, and generates slots for next 7 days.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db, Doctor, Department, Slot, Patient, Staff, Review
from datetime import date, timedelta
from utils import generate_time_labels
import bcrypt


def seed():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        print("Tables created.")

        # --- Departments ---
        departments = [
            Department(name='Cardiology', description='Heart and cardiovascular system specialists'),
            Department(name='Orthopedics', description='Bone, joint and muscle care experts'),
            Department(name='Dermatology', description='Skin, hair and nail treatment specialists'),
            Department(name='Neurology', description='Brain and nervous system specialists'),
            Department(name='General Medicine', description='General health care for adults'),
            Department(name='ENT', description='Ear, Nose and Throat specialists'),
            Department(name='Gynecology', description='Female reproductive system health'),
            Department(name='Pediatrics', description='Medical care for children'),
        ]
        db.session.add_all(departments)
        db.session.commit()
        print(f"{len(departments)} departments seeded.")

        hashed_pw = bcrypt.hashpw('doctor123'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # --- Doctors ---
        doctors = [
            Doctor(name='Dr. Daniya', email='daniya@medislot.com', password_hash=hashed_pw, is_active=True, specialization='Cardiologist', experience_years=12,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=DD&backgroundColor=0369a1',
                   department_id=1, is_available=True),
            Doctor(name='Dr. Nisarga', email='nisarga@medislot.com', password_hash=hashed_pw, is_active=True, specialization='Pediatrician', experience_years=14,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=DN&backgroundColor=f59e0b',
                   department_id=8, is_available=True),
            Doctor(name='Dr. Faiza', email='faiza@medislot.com', password_hash=hashed_pw, is_active=True, specialization='Orthopedic Surgeon', experience_years=9,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=DF&backgroundColor=7c3aed',
                   department_id=2, is_available=True),
            Doctor(name='Dr. Subiya', email='subiya@medislot.com', password_hash=hashed_pw, is_active=True, specialization='Dermatologist', experience_years=7,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=DS&backgroundColor=059669',
                   department_id=3, is_available=True),
            Doctor(name='Dr. Mizba', email='mizba@medislot.com', password_hash=hashed_pw, is_active=True, specialization='Neurologist', experience_years=15,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=DM&backgroundColor=dc2626',
                   department_id=4, is_available=True),
            Doctor(name='Dr. Sheema', email='sheema@medislot.com', password_hash=hashed_pw, is_active=True, specialization='General Physician', experience_years=10,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=DS&backgroundColor=d97706',
                   department_id=5, is_available=True),
        ]
        db.session.add_all(doctors)
        db.session.commit()
        print(f"{len(doctors)} doctors seeded.")

        # --- Slots for next 7 days ---
        time_labels = generate_time_labels()

        today = date.today()
        slot_count = 0
        for d in range(7):
            current_date = (today + timedelta(days=d)).isoformat()
            for doctor in doctors:
                for tl in time_labels:
                    slot = Slot(
                        doctor_id=doctor.id,
                        date=current_date,
                        time_label=tl,
                        max_capacity=3,
                        is_blocked=False
                    )
                    db.session.add(slot)
                    slot_count += 1
        db.session.commit()
        print(f"{slot_count} slots generated for next 7 days.")

        # --- Staff & Patients ---
        admin_pw = bcrypt.hashpw('admin123'.encode(), bcrypt.gensalt()).decode()
        super_admin = Staff(name='Super Admin', email='admin@medislot.com', password_hash=admin_pw, role='super_admin')
        receptionist = Staff(name='Receptionist One', email='receptionist@medislot.com', password_hash=admin_pw, role='receptionist')
        
        # Link doctor accounts
        doc_staff_list = []
        for i, doc in enumerate(doctors):
            doc_staff_list.append(Staff(
                name=doc.name, 
                email=doc.email, 
                password_hash=hashed_pw, 
                role='doctor', 
                doctor_id=doc.id
            ))
        
        db.session.add_all([super_admin, receptionist] + doc_staff_list)
        
        patient_pw = bcrypt.hashpw('patient123'.encode(), bcrypt.gensalt()).decode()
        demo = Patient(name='Demo Patient', phone='9876543210', email='patient@demo.com', password_hash=patient_pw, role='patient')
        db.session.add(demo)
        db.session.commit()

        print("\nMediSlot database initialized successfully!")
        print("   Super Admin: admin@medislot.com / admin123")
        print("   Doctors seeded: Dr. Daniya, Dr. Nisarga, Dr. Faiza, Dr. Subiya, Dr. Mizba, Dr. Sheema")
        print("   Password for all doctors: doctor123")
        print("   Patient: patient@demo.com / patient123")


if __name__ == '__main__':
    seed()

