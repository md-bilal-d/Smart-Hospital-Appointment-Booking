"""
MediSlot - Database Initialization & Seed Script
Creates tables, seeds 7 doctors, 4 departments, and generates slots for next 7 days.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db, Doctor, Department, Slot, Patient
from datetime import date, timedelta
import bcrypt


def seed():
    app = create_app()
    with app.app_context():
        db.drop_all()
        db.create_all()
        print("✅ Tables created.")

        # --- Departments ---
        departments = [
            Department(name='Cardiology', description='Heart and cardiovascular system specialists'),
            Department(name='Orthopedics', description='Bone, joint and muscle care experts'),
            Department(name='Dermatology', description='Skin, hair and nail treatment specialists'),
            Department(name='Neurology', description='Brain and nervous system specialists'),
        ]
        db.session.add_all(departments)
        db.session.commit()
        print("✅ 4 departments seeded.")

        # --- Doctors ---
        doctors = [
            Doctor(name='Dr. Arjun Sharma', specialization='Cardiologist', experience_years=12,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=AS&backgroundColor=0369a1',
                   department_id=1, is_available=True),
            Doctor(name='Dr. Priya Menon', specialization='Orthopedic Surgeon', experience_years=9,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=PM&backgroundColor=7c3aed',
                   department_id=2, is_available=True),
            Doctor(name='Dr. Ravi Patel', specialization='Dermatologist', experience_years=7,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=RP&backgroundColor=059669',
                   department_id=3, is_available=True),
            Doctor(name='Dr. Neha Gupta', specialization='Neurologist', experience_years=15,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=NG&backgroundColor=dc2626',
                   department_id=4, is_available=True),
            Doctor(name='Dr. Sanjay Reddy', specialization='Cardiologist', experience_years=10,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=SR&backgroundColor=d97706',
                   department_id=1, is_available=True),
            Doctor(name='Dr. Anita Desai', specialization='Orthopedic Surgeon', experience_years=8,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=AD&backgroundColor=0891b2',
                   department_id=2, is_available=True),
            Doctor(name='Dr. Vikram Singh', specialization='Dermatologist', experience_years=11,
                   profile_pic_url='https://api.dicebear.com/9.x/initials/svg?seed=VS&backgroundColor=4f46e5',
                   department_id=3, is_available=True),
        ]
        db.session.add_all(doctors)
        db.session.commit()
        print("✅ 7 doctors seeded.")

        # --- Slots for next 7 days ---
        time_labels = []
        for hour in range(9, 17):  # 9 AM to 4:30 PM
            for minute in [0, 30]:
                h = hour % 12 or 12
                ampm = 'AM' if hour < 12 else 'PM'
                time_labels.append(f"{h}:{minute:02d} {ampm}")

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
        print(f"✅ {slot_count} slots generated for next 7 days.")

        # --- Demo patient ---
        pw = bcrypt.hashpw('patient123'.encode(), bcrypt.gensalt()).decode()
        demo = Patient(name='Demo Patient', phone='9876543210', email='patient@demo.com', password_hash=pw)
        db.session.add(demo)
        db.session.commit()
        print("✅ Demo patient created (patient@demo.com / patient123)")

        print("\n🏥 MediSlot database initialized successfully!")
        print("   Admin login: admin / admin123")
        print("   Patient login: patient@demo.com / patient123")


if __name__ == '__main__':
    seed()
