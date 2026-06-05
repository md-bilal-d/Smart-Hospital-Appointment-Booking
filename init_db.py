"""
MediSlot - Database Initialization & Seed Script
Creates tables, seeds 7 doctors, 4 departments, and generates slots for next 7 days.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from models import db, Doctor, Department, Slot, Patient, Staff, Review, ReminderLog, ReminderPreference, Article, Appointment, Ward, Bed, TelehealthMessage
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
        
        # Seed additional demo patients for rich reviews community
        patients = [
            Patient(name='Sarah Jenkins', phone='9876543211', email='sarah@demo.com', password_hash=patient_pw, role='patient'),
            Patient(name='Michael Chang', phone='9876543212', email='michael@demo.com', password_hash=patient_pw, role='patient'),
            Patient(name='Emily Rodriguez', phone='9876543213', email='emily@demo.com', password_hash=patient_pw, role='patient'),
            Patient(name='David Kim', phone='9876543214', email='david@demo.com', password_hash=patient_pw, role='patient'),
        ]
        db.session.add_all(patients)
        db.session.flush()

        # Seed reviews and historical appointments
        import random
        feedback_options = {
            1: [
                "Doctor was extremely late and dismissive of my concerns.",
                "Terrible experience. Felt rushed and the clinic was unorganized."
            ],
            2: [
                "Long wait time and very brief consultation. Expected more care.",
                "Hard to get in touch with. The doctor did not explain the prescription clearly."
            ],
            3: [
                "Average consultation. The doctor was polite but very quick.",
                "Decent service, but the waiting room was crowded and delayed."
            ],
            4: [
                "Very professional doctor. Explained the diagnosis clearly and suggested a practical care plan.",
                "Great experience. Dr. was caring and patient. Will recommend.",
                "Detailed consultation and helpful advice. Very satisfied."
            ],
            5: [
                "Absolutely stellar care! The doctor went above and beyond to make me feel comfortable and listened to.",
                "Incredibly knowledgeable and compassionate specialist. Highly recommended!",
                "Outstanding treatment. The best healthcare experience I have ever had.",
                "Brilliant doctor. Understood my health history immediately and gave excellent guidance."
            ]
        }

        # Create historical appointments (seen) and attach reviews
        # Let's seed for each doctor 2-3 reviews
        review_count = 0
        all_patients = [demo] + patients
        for doctor in doctors:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            for p_idx, patient in enumerate(all_patients[1:4]): # Use Sarah, Michael, Emily
                time_lbl = time_labels[p_idx % len(time_labels)]
                hist_slot = Slot(
                    doctor_id=doctor.id,
                    date=yesterday,
                    time_label=time_lbl,
                    max_capacity=3,
                    is_blocked=False
                )
                db.session.add(hist_slot)
                db.session.flush()

                appt = Appointment(
                    patient_id=patient.id,
                    slot_id=hist_slot.id,
                    token_number=p_idx + 1,
                    status='seen',
                    appointment_type='normal',
                    notes='Routine health checkup',
                    consultation_mode='In-Person'
                )
                db.session.add(appt)
                db.session.flush()

                # Customize ratings slightly based on doctor
                if doctor.name == 'Dr. Daniya':
                    rating = random.choice([5, 5, 4]) # super highly rated
                elif doctor.name == 'Dr. Subiya':
                    rating = random.choice([3, 4, 4, 5]) # average
                else:
                    rating = random.choice([4, 5, 5])
                    
                feedback = random.choice(feedback_options[rating])
                
                review = Review(
                    appointment_id=appt.id,
                    patient_id=patient.id,
                    doctor_id=doctor.id,
                    rating=rating,
                    feedback=feedback
                )
                db.session.add(review)
                review_count += 1

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

        # --- Wards & Beds Seeding ---
        wards = [
            Ward(name='Intensive Care Unit (ICU)', type='icu', total_beds=6, cost_per_day=1200.0),
            Ward(name='General Medicine Ward', type='general', total_beds=10, cost_per_day=300.0),
            Ward(name='Pediatrics Ward', type='pediatric', total_beds=8, cost_per_day=450.0),
            Ward(name='Premium Deluxe Suite', type='deluxe', total_beds=8, cost_per_day=950.0),
        ]
        db.session.add_all(wards)
        db.session.flush()

        # Generate beds for each ward
        bed_count = 0
        from datetime import datetime as dt_class
        
        # Occupancy details
        occupancy_map = {
            'Intensive Care Unit (ICU)': {1: (patients[0].id, dt_class.utcnow() - timedelta(days=2))},
            'General Medicine Ward': {3: (patients[1].id, dt_class.utcnow() - timedelta(days=4))},
            'Premium Deluxe Suite': {2: (patients[2].id, dt_class.utcnow() - timedelta(days=1))},
        }

        for ward in wards:
            prefix = 'ICU' if ward.type == 'icu' else 'GEN' if ward.type == 'general' else 'PED' if ward.type == 'pediatric' else 'DLX'
            for b_num in range(1, ward.total_beds + 1):
                bed_num_str = f"{prefix}-{b_num:02d}"
                status = 'available'
                patient_id = None
                admitted_at = None
                expected_discharge = None
                
                if ward.name in occupancy_map and b_num in occupancy_map[ward.name]:
                    p_id, adm_date = occupancy_map[ward.name][b_num]
                    status = 'occupied'
                    patient_id = p_id
                    admitted_at = adm_date
                    expected_discharge = adm_date + timedelta(days=5)
                elif ward.type == 'icu' and b_num == 4:
                    status = 'maintenance'
                elif ward.type == 'general' and b_num == 8:
                    status = 'maintenance'
                    
                bed = Bed(
                    ward_id=ward.id,
                    bed_number=bed_num_str,
                    status=status,
                    patient_id=patient_id,
                    admitted_at=admitted_at,
                    expected_discharge=expected_discharge
                )
                db.session.add(bed)
                bed_count += 1
                
        db.session.flush()
        print(f"Seeded {len(wards)} wards and {bed_count} beds successfully.")

        db.session.commit()

        print("\nMediSlot database initialized successfully!")
        print("   Super Admin: admin@medislot.com / admin123")
        print("   Doctors seeded: Dr. Daniya, Dr. Nisarga, Dr. Faiza, Dr. Subiya, Dr. Mizba, Dr. Sheema")
        print("   Password for all doctors: doctor123")
        print("   Patient: patient@demo.com / patient123")


if __name__ == '__main__':
    seed()

