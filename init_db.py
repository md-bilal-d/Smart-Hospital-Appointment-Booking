"""
MediSlot - Database Initialization & Seed Script
Creates tables, seeds 7 doctors, 4 departments, and generates slots for next 7 days.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db, Doctor, Department, Slot, Patient, Staff, Review, ReminderLog, ReminderPreference, Article
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
        db.session.flush()

        # --- Health Articles ---
        articles = [
            Article(
                title="5 Tips for a Healthy Heart",
                content="Maintaining a healthy heart is essential for overall well-being. Here are 5 tips:\n\n1. Eat a balanced diet rich in fruits, vegetables, and whole grains. Limit saturated fats, sodium, and added sugars.\n\n2. Exercise regularly, aiming for at least 30 minutes of moderate-intensity activity most days of the week. Walking, swimming, or cycling are great options.\n\n3. Manage stress through relaxation techniques like meditation, deep breathing, or yoga. Chronic stress can raise blood pressure and damage arteries.\n\n4. Avoid smoking and excessive alcohol consumption. Smoking doubles the risk of heart disease, and heavy drinking can lead to high blood pressure.\n\n5. Get regular check-ups with your cardiologist. Early detection of risk factors like high cholesterol or hypertension can prevent serious complications.",
                category="Health Tip",
                author_id=super_admin.id
            ),
            Article(
                title="MediSlot Now Offers Video Consultations",
                content="We are excited to announce that MediSlot now supports video consultations! You can now book appointments with your preferred doctors from the comfort of your home.\n\nHow it works:\n- Select 'Video Call' as your consultation mode when booking an appointment.\n- At your scheduled time, click the 'Join Video Call' button on your dashboard.\n- A secure, private video session will connect you directly with your doctor.\n\nVideo consultations are available across all departments and are ideal for follow-up visits, prescription renewals, and non-emergency consultations.\n\nFor emergencies, please visit the hospital in person or call our emergency helpline.",
                category="Announcement",
                author_id=super_admin.id
            ),
            Article(
                title="Understanding Blood Pressure: What Your Numbers Mean",
                content="Blood pressure is measured in two numbers: systolic (top number) and diastolic (bottom number).\n\nNormal: Less than 120/80 mmHg\nElevated: 120-129 / less than 80 mmHg\nHigh (Stage 1): 130-139 / 80-89 mmHg\nHigh (Stage 2): 140+ / 90+ mmHg\nCrisis: 180+ / 120+ mmHg — seek immediate medical attention.\n\nRegular monitoring is crucial. You can track your blood pressure using MediSlot's built-in Health Tracker on your patient dashboard. Our AI-powered insights will alert you if your readings fall outside the healthy range and recommend the right specialist.\n\nLifestyle changes like reducing salt intake, exercising regularly, and maintaining a healthy weight can significantly improve your blood pressure.",
                category="Health Tip",
                author_id=super_admin.id
            ),
            Article(
                title="New Department: Pediatrics Now Open!",
                content="MediSlot Hospital is proud to announce the opening of our new Pediatrics department!\n\nLed by Dr. Nisarga, our pediatrics team specializes in comprehensive healthcare for infants, children, and adolescents. Services include:\n\n- Routine health check-ups and vaccinations\n- Developmental assessments\n- Treatment of childhood illnesses\n- Nutritional guidance for growing children\n- Adolescent health and counseling\n\nYou can now book pediatric appointments through MediSlot's online booking system or visit us at the hospital. Use our Symptom Checker feature to determine if your child needs to see a pediatrician.",
                category="News",
                author_id=super_admin.id
            ),
            Article(
                title="The Importance of Mental Health in Daily Life",
                content="Mental health is just as important as physical health, yet it is often overlooked. Here are some key points to remember:\n\n1. Recognize the signs: Persistent sadness, anxiety, changes in sleep or appetite, and difficulty concentrating can all be indicators of mental health concerns.\n\n2. Talk about it: Breaking the stigma around mental health starts with open conversations. Don't hesitate to share your feelings with trusted friends, family, or professionals.\n\n3. Practice self-care: Regular exercise, adequate sleep, healthy eating, and mindfulness practices can significantly improve your mental well-being.\n\n4. Seek professional help: If symptoms persist, consult a mental health professional. Therapy and counseling are effective treatments for many conditions.\n\n5. Stay connected: Social isolation can worsen mental health. Make time for social activities and maintain meaningful relationships.\n\nRemember, seeking help is a sign of strength, not weakness. Our General Medicine department can provide referrals to mental health specialists.",
                category="Health Tip",
                author_id=super_admin.id
            ),
            Article(
                title="Flu Season Alert: How to Protect Yourself",
                content="Flu season is here, and it's important to take precautions to protect yourself and your loved ones.\n\nPrevention tips:\n- Get vaccinated: The flu vaccine is the most effective way to prevent influenza.\n- Wash your hands frequently with soap and water for at least 20 seconds.\n- Avoid touching your face, especially your eyes, nose, and mouth.\n- Cover your coughs and sneezes with a tissue or your elbow.\n- Stay home if you feel unwell to prevent spreading the virus.\n\nSymptoms to watch for:\n- Sudden onset of fever, chills, and body aches\n- Sore throat, cough, and runny or stuffy nose\n- Fatigue and headaches\n\nIf you experience severe symptoms such as difficulty breathing, persistent chest pain, or confusion, seek immediate medical attention. Book an appointment with our General Medicine department through MediSlot for flu consultations.",
                category="News",
                author_id=super_admin.id
            ),
        ]
        db.session.add_all(articles)

        db.session.commit()

        print("\nMediSlot database initialized successfully!")
        print("   Super Admin: admin@medislot.com / admin123")
        print("   Doctors seeded: Dr. Daniya, Dr. Nisarga, Dr. Faiza, Dr. Subiya, Dr. Mizba, Dr. Sheema")
        print("   Password for all doctors: doctor123")
        print("   Patient: patient@demo.com / patient123")


if __name__ == '__main__':
    seed()

