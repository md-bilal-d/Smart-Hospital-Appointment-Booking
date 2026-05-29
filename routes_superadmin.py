from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from auth_utils import role_required
from models import db, Staff, AuditLog, Appointment, Doctor, Department, Article
from audit_logger import log_action
import bcrypt

superadmin_bp = Blueprint('superadmin', __name__)

@superadmin_bp.route('/superadmin')
@role_required('super_admin')
def dashboard():
    staff_members = Staff.query.all()
    return render_template('admin/superadmin.html', staff=staff_members)

@superadmin_bp.route('/superadmin/add-staff', methods=['POST'])
@role_required('super_admin')
def add_staff():
    name = request.form.get('name')
    email = request.form.get('email')
    password = request.form.get('password')
    role = request.form.get('role')
    doctor_id = request.form.get('doctor_id')
    
    if Staff.query.filter_by(email=email).first():
        flash('Email already exists.', 'error')
        return redirect(url_for('superadmin.dashboard'))
    
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    new_staff = Staff(
        name=name, 
        email=email, 
        password_hash=pw_hash, 
        role=role, 
        doctor_id=doctor_id if doctor_id else None
    )
    db.session.add(new_staff)
    db.session.commit()
    
    log_action('Staff Created', f'Created staff: {name} ({role})', target_type='staff', target_id=new_staff.id)
    flash('Staff member added successfully.', 'success')
    return redirect(url_for('superadmin.dashboard'))

@superadmin_bp.route('/superadmin/deactivate/<int:staff_id>', methods=['POST'])
@role_required('super_admin')
def deactivate_staff(staff_id):
    staff = Staff.query.get_or_404(staff_id)
    staff.is_active = False
    db.session.commit()
    log_action('Staff Deactivated', f'Deactivated staff: {staff.name}', target_type='staff', target_id=staff.id)
    flash('Staff member deactivated.', 'success')
    return redirect(url_for('superadmin.dashboard'))

@superadmin_bp.route('/admin/audit-log')
@role_required('super_admin')
def audit_log():
    page = request.args.get('page', 1, type=int)
    role_filter = request.args.get('role')
    action_filter = request.args.get('action')
    
    query = AuditLog.query
    if role_filter:
        query = query.filter_by(user_role=role_filter)
    if action_filter:
        query = query.filter(AuditLog.action.ilike(f"%{action_filter}%"))
        
    logs = query.order_by(AuditLog.timestamp.desc()).paginate(page=page, per_page=20)
    return render_template('admin/audit_log.html', logs=logs)

@superadmin_bp.route('/superadmin/articles')
@role_required('super_admin')
def manage_articles():
    articles = Article.query.order_by(Article.created_at.desc()).all()
    return render_template('admin/admin_articles.html', articles=articles)

@superadmin_bp.route('/superadmin/articles/new', methods=['POST'])
@role_required('super_admin')
def add_article():
    from flask import session
    title = request.form.get('title')
    content = request.form.get('content')
    category = request.form.get('category', 'Health Tip')
    is_published = request.form.get('is_published') == 'on'

    staff_id = session.get('staff_id')
    if not staff_id:
        flash('Unauthorized', 'error')
        return redirect(url_for('superadmin.manage_articles'))

    article = Article(
        title=title,
        content=content,
        category=category,
        author_id=staff_id,
        is_published=is_published
    )
    db.session.add(article)
    db.session.commit()
    log_action('Article Created', f'Created article: {title}', target_type='article', target_id=article.id)
    flash('Article added successfully.', 'success')
    return redirect(url_for('superadmin.manage_articles'))

@superadmin_bp.route('/superadmin/articles/<int:article_id>/edit', methods=['POST'])
@role_required('super_admin')
def edit_article(article_id):
    article = Article.query.get_or_404(article_id)
    article.title = request.form.get('title')
    article.content = request.form.get('content')
    article.category = request.form.get('category')
    article.is_published = request.form.get('is_published') == 'on'
    
    db.session.commit()
    log_action('Article Edited', f'Edited article: {article.title}', target_type='article', target_id=article.id)
    flash('Article updated successfully.', 'success')
    return redirect(url_for('superadmin.manage_articles'))

@superadmin_bp.route('/superadmin/articles/<int:article_id>/delete', methods=['POST'])
@role_required('super_admin')
def delete_article(article_id):
    article = Article.query.get_or_404(article_id)
    db.session.delete(article)
    db.session.commit()
    log_action('Article Deleted', f'Deleted article: {article.title}', target_type='article', target_id=article_id)
    flash('Article deleted successfully.', 'success')
    return redirect(url_for('superadmin.manage_articles'))

