# 🏥 MediSlot: Smart Hospital Appointment Booking System

MediSlot is a premium, high-performance healthcare scheduling platform designed to eliminate hospital wait times. Featuring a stunning **glassmorphic UI**, real-time queue tracking, and an advanced doctor analytics dashboard, MediSlot brings modern digital excellence to medical management.

---

## ✨ Key Features

### 👨‍⚕️ Doctor Portal (Premium Overhaul)
- **Advanced Analytics**: 5 real-time charts (Chart.js) tracking queue distribution, weekly volume, hourly load, and performance efficiency.
- **Queue Management**: Live "Call Next" system with Socket.IO integration and "No Show" tracking.
- **Glassmorphic Design**: Premium dark-themed dashboard with glowing animations and interactive stats.
- **Digital Prescriptions**: Secure clinical notes and prescription upload system.

### 👤 Patient Experience
- **Smart Booking**: Department-wise specialist discovery with real-time slot availability.
- **Live Token System**: Instant token generation with dynamic ETA and queue position tracking.
- **Personalized Profile**: Comprehensive visit history and health records management.
- **Secure OTP Login**: Multi-factor authentication for patient data protection.

### 🛠️ Administrative Control (RBAC)
- **Role-Based Access**: Specialized views for Super Admins, Receptionists, and Doctors.
- **Resource Management**: Master control over departments, doctors, and appointment slots.
- **Audit Logs**: Comprehensive logging of all system activities for security and compliance.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.8+
- Node.js (for Tailwind/CSS processing if applicable)
- SQLite (Default) or PostgreSQL

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/md-bilal-d/Smart-Hospital-Appointment-Booking.git
cd Smart-Hospital-Appointment-Booking

# Install dependencies
pip install -r requirements.txt
```

### 3. Initialize Database
Re-seed the database to set up the default doctors (Dr. Daniya, Dr. Nisarga, etc.) and departments.
```bash
python init_db.py
```

### 4. Run the Application
```bash
python app.py
```
Visit `http://localhost:5000` to access the portal.

---

## 🩺 Seeded Doctor Portals
*Password for all test accounts:* `doctor123`

| Doctor | Specialization | Email |
| :--- | :--- | :--- |
| **Dr. Daniya** | Cardiologist | `daniya@medislot.com` |
| **Dr. Nisarga** | Pediatrician | `nisarga@medislot.com` |
| **Dr. Faiza** | Orthopedic Surgeon | `faiza@medislot.com` |
| **Dr. Subiya** | Dermatologist | `subiya@medislot.com` |
| **Dr. Mizba** | Neurologist | `mizba@medislot.com` |
| **Dr. Sheema** | General Physician | `sheema@medislot.com` |

---

## 🎨 Technology Stack
- **Backend**: Flask, SQLAlchemy, Flask-SocketIO
- **Frontend**: Vanilla JS, Chart.js, TailwindCSS (Utility styles), CSS3 (Glassmorphism)
- **Real-time**: WebSockets (Socket.IO)
- **Security**: Bcrypt Hashing, Role-Based Access Control (RBAC), OTP Simulation

---

## 📄 License
This project is for demonstration purposes. All rights reserved.
