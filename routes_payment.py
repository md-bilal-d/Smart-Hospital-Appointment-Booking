"""
MediSlot - Payment Routes (Razorpay Integration)
Handles Razorpay order creation, checkout, verification, receipts and webhooks.
"""

import io
import hmac
import hashlib
from datetime import datetime
from fpdf import FPDF
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, current_app, send_file, jsonify
)
from flask_login import login_required, current_user
from models import db, Invoice, Payment, Patient, Appointment, Slot, Doctor
from utils import audit_logger

payment_bp = Blueprint('payment', __name__)


def _get_razorpay_client():
    """Lazily initialise a Razorpay client using app config."""
    import razorpay
    key_id = current_app.config.get('RAZORPAY_KEY_ID', '')
    key_secret = current_app.config.get('RAZORPAY_KEY_SECRET', '')
    return razorpay.Client(auth=(key_id, key_secret))


# ─── Checkout Page ───────────────────────────────────────────────────────────
@payment_bp.route('/payment/checkout/<int:invoice_id>')
@login_required
def checkout(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.invoices'))
    if invoice.status == 'paid':
        flash('This invoice is already paid.', 'info')
        return redirect(url_for('patient.invoices'))

    # Gather appointment details for the checkout card
    appt = invoice.appointment
    slot = Slot.query.get(appt.slot_id) if appt else None
    doctor = Doctor.query.get(slot.doctor_id) if slot else None

    razorpay_key = current_app.config.get('RAZORPAY_KEY_ID', '')
    return render_template(
        'payment_checkout.html',
        invoice=invoice,
        appt=appt,
        slot=slot,
        doctor=doctor,
        razorpay_key=razorpay_key,
    )


# ─── Create Razorpay Order (AJAX) ───────────────────────────────────────────
@payment_bp.route('/payment/create-order/<int:invoice_id>', methods=['POST'])
@login_required
def create_order(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    if invoice.patient_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403
    if invoice.status == 'paid':
        return jsonify({'error': 'Invoice already paid'}), 400

    try:
        client = _get_razorpay_client()
        amount_paise = int(invoice.amount * 100)  # Razorpay needs paise

        order_data = {
            'amount': amount_paise,
            'currency': 'INR',
            'receipt': f'inv_{invoice.id}',
            'notes': {
                'invoice_id': str(invoice.id),
                'patient_id': str(current_user.id),
                'patient_name': current_user.name,
            },
        }
        razorpay_order = client.order.create(data=order_data)

        # Save payment record
        payment = Payment(
            invoice_id=invoice.id,
            patient_id=current_user.id,
            razorpay_order_id=razorpay_order['id'],
            amount=invoice.amount,
            currency='INR',
            status='created',
        )
        db.session.add(payment)
        db.session.commit()

        return jsonify({
            'order_id': razorpay_order['id'],
            'amount': amount_paise,
            'currency': 'INR',
            'payment_id': payment.id,
        })

    except Exception as e:
        current_app.logger.error(f'Razorpay order creation failed: {e}')
        return jsonify({'error': str(e)}), 500


# ─── Verify Payment ─────────────────────────────────────────────────────────
@payment_bp.route('/payment/verify', methods=['POST'])
@login_required
def verify_payment():
    data = request.get_json() or request.form
    razorpay_order_id = data.get('razorpay_order_id')
    razorpay_payment_id = data.get('razorpay_payment_id')
    razorpay_signature = data.get('razorpay_signature')
    payment_id = data.get('payment_id', type=int) if hasattr(data, 'get') else data.get('payment_id')

    if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        return jsonify({'error': 'Missing payment details'}), 400

    payment = Payment.query.filter_by(razorpay_order_id=razorpay_order_id).first()
    if not payment:
        return jsonify({'error': 'Payment record not found'}), 404

    if payment.patient_id != current_user.id:
        return jsonify({'error': 'Unauthorized'}), 403

    # Verify signature
    try:
        client = _get_razorpay_client()
        params = {
            'razorpay_order_id': razorpay_order_id,
            'razorpay_payment_id': razorpay_payment_id,
            'razorpay_signature': razorpay_signature,
        }
        client.utility.verify_payment_signature(params)
    except Exception as e:
        payment.status = 'failed'
        db.session.commit()
        current_app.logger.error(f'Payment verification failed: {e}')
        return jsonify({'error': 'Payment verification failed', 'details': str(e)}), 400

    # Mark payment as successful
    payment.razorpay_payment_id = razorpay_payment_id
    payment.razorpay_signature = razorpay_signature
    payment.status = 'paid'
    payment.paid_at = datetime.utcnow()

    # Update invoice
    invoice = payment.invoice
    invoice.status = 'paid'
    invoice.paid_at = datetime.utcnow()

    db.session.commit()

    audit_logger.log_action(
        'Payment Completed',
        f'Patient {current_user.name} paid ₹{payment.amount} for Invoice #{invoice.id} via Razorpay (Txn: {razorpay_payment_id})',
    )

    return jsonify({
        'success': True,
        'redirect': url_for('payment.success', payment_id=payment.id),
    })


# ─── Success Page ────────────────────────────────────────────────────────────
@payment_bp.route('/payment/success/<int:payment_id>')
@login_required
def success(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    if payment.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.invoices'))

    invoice = payment.invoice
    appt = invoice.appointment
    slot = Slot.query.get(appt.slot_id) if appt else None
    doctor = Doctor.query.get(slot.doctor_id) if slot else None

    return render_template(
        'payment_success.html',
        payment=payment,
        invoice=invoice,
        appt=appt,
        slot=slot,
        doctor=doctor,
    )


# ─── PDF Receipt ─────────────────────────────────────────────────────────────
@payment_bp.route('/payment/receipt/<int:payment_id>/pdf')
@login_required
def download_receipt(payment_id):
    payment = Payment.query.get_or_404(payment_id)
    if payment.patient_id != current_user.id:
        flash('Unauthorized.', 'error')
        return redirect(url_for('patient.invoices'))

    invoice = payment.invoice
    appt = invoice.appointment
    slot = Slot.query.get(appt.slot_id) if appt else None
    doctor = Doctor.query.get(slot.doctor_id) if slot else None

    pdf = FPDF()
    pdf.add_page()

    # Header
    pdf.set_font('helvetica', 'B', 26)
    pdf.set_text_color(79, 70, 229)  # Indigo
    pdf.cell(0, 15, 'MediSlot Hospital', ln=True, align='C')
    pdf.set_font('helvetica', '', 11)
    pdf.set_text_color(100, 116, 139)
    pdf.cell(0, 8, 'Payment Receipt', ln=True, align='C')
    pdf.ln(6)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(10)

    def add_row(label, value):
        pdf.set_font('helvetica', 'B', 11)
        pdf.cell(60, 9, f'{label}:')
        pdf.set_font('helvetica', '', 11)
        pdf.cell(0, 9, str(value), ln=True)

    add_row('Receipt No', f'RCT-{payment.id:05d}')
    add_row('Transaction ID', payment.razorpay_payment_id or 'N/A')
    add_row('Order ID', payment.razorpay_order_id)
    pdf.ln(4)
    add_row('Patient Name', current_user.name)
    add_row('Patient Email', current_user.email)
    pdf.ln(4)
    if doctor:
        add_row('Doctor', doctor.name)
        add_row('Specialization', doctor.specialization)
    if slot:
        add_row('Appointment Date', slot.date)
        add_row('Time', slot.time_label)
    pdf.ln(4)
    add_row('Invoice ID', f'INV-{invoice.id:05d}')
    add_row('Description', invoice.description or 'Medical Consultation')

    pdf.ln(6)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(6)

    pdf.set_font('helvetica', 'B', 16)
    pdf.set_text_color(16, 185, 129)  # Emerald
    pdf.cell(0, 12, f'Amount Paid:  Rs. {payment.amount:,.2f}', ln=True, align='C')
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    pdf.set_font('helvetica', '', 10)
    pdf.set_text_color(100, 116, 139)
    paid_str = payment.paid_at.strftime('%d %b %Y, %I:%M %p') if payment.paid_at else 'N/A'
    pdf.cell(0, 8, f'Paid on: {paid_str}', ln=True, align='C')
    pdf.cell(0, 8, f'Payment Method: Razorpay ({payment.currency})', ln=True, align='C')

    pdf.ln(20)
    pdf.set_font('helvetica', 'I', 9)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 8, 'This is a computer-generated receipt and does not require a signature.', ln=True, align='C')
    pdf.cell(0, 8, f'Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}', ln=True, align='C')

    buffer = io.BytesIO(pdf.output())
    return send_file(
        buffer,
        as_attachment=True,
        download_name=f'MediSlot_Receipt_{payment.id}.pdf',
        mimetype='application/pdf',
    )


# ─── Razorpay Webhook (optional, for async confirmation) ────────────────────
@payment_bp.route('/payment/webhook', methods=['POST'])
def razorpay_webhook():
    """Handles Razorpay webhook events for payment.captured / payment.failed."""
    payload = request.get_data(as_text=True)
    signature = request.headers.get('X-Razorpay-Signature', '')
    secret = current_app.config.get('RAZORPAY_KEY_SECRET', '')

    # Verify webhook signature
    expected = hmac.new(
        secret.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return jsonify({'error': 'Invalid signature'}), 400

    import json
    event = json.loads(payload)
    event_type = event.get('event', '')

    if event_type == 'payment.captured':
        entity = event.get('payload', {}).get('payment', {}).get('entity', {})
        order_id = entity.get('order_id')
        payment_id_rp = entity.get('id')

        payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
        if payment and payment.status != 'paid':
            payment.razorpay_payment_id = payment_id_rp
            payment.status = 'paid'
            payment.paid_at = datetime.utcnow()
            payment.invoice.status = 'paid'
            payment.invoice.paid_at = datetime.utcnow()
            db.session.commit()

    elif event_type == 'payment.failed':
        entity = event.get('payload', {}).get('payment', {}).get('entity', {})
        order_id = entity.get('order_id')
        payment = Payment.query.filter_by(razorpay_order_id=order_id).first()
        if payment:
            payment.status = 'failed'
            db.session.commit()

    return jsonify({'status': 'ok'}), 200
