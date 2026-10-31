import secrets
import smtplib
from datetime import timedelta
from email.message import EmailMessage

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from extensions import db, limiter
from models import BusinessProfile, FarmerProfile, PasswordResetToken, User, utcnow
from security import ROLE_DASHBOARDS, audit, normalize_phone, parse_roles, valid_password

auth_bp = Blueprint("auth", __name__)


def _dashboard():
    return redirect(url_for(ROLE_DASHBOARDS.get(current_user.role, "auth.login")))


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return _dashboard()
    if request.method == "POST":
        form = request.form
        role = parse_roles(form.get("role"))
        phone = normalize_phone(form.get("phone"))
        required = ["email", "phone", "password", "location"]
        if not role or any(not form.get(field, "").strip() for field in required):
            flash("Please enter your email, phone number, password and location.", "error")
        elif not valid_password(form["password"]):
            flash("Password must contain at least 6 characters.", "error")
        elif User.query.filter_by(email=form["email"].strip().lower()).first() or User.query.filter_by(phone=phone).first():
            flash("An account with that email or phone already exists.", "error")
        else:
            user = User(full_name=form["email"].strip().lower().split("@")[0],
                        email=form["email"].strip().lower(),
                        phone=phone, password_hash=User.hash_password(form["password"]), role=role,
                        district="", municipality="", ward="", address=form["location"].strip())
            db.session.add(user)
            db.session.flush()
            audit("registration_success", user)
            db.session.commit()
            flash("Registration successful. Please sign in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return _dashboard()
    if request.method == "POST":
        identifier = request.form.get("identifier", "").strip()
        user = User.query.filter((User.email == identifier.lower()) | (User.phone == normalize_phone(identifier))).first()
        if not user:
            audit("login_failed", details="unknown_identifier")
            db.session.commit()
            flash("Invalid credentials.", "error")
        elif user.is_locked():
            audit("login_throttled", user)
            db.session.commit()
            flash("Too many failed attempts. Please try again later.", "error")
        elif not user.check_password(request.form.get("password", "")):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= current_app.config["MAX_LOGIN_ATTEMPTS"]:
                user.locked_until = utcnow() + timedelta(minutes=current_app.config["LOCKOUT_MINUTES"])
            audit("login_failed", user)
            db.session.commit()
            flash("Invalid credentials.", "error")
        else:
            user.failed_login_attempts = 0
            user.locked_until = None
            user.last_login_at = utcnow()
            audit("login_success", user)
            db.session.commit()
            session.clear()
            login_user(user, remember=request.form.get("remember") == "on", fresh=True)
            return _dashboard()
    return render_template("auth/login.html")


@auth_bp.post("/logout")
@login_required
def logout():
    audit("logout", current_user)
    db.session.commit()
    logout_user()
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("auth.login"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    if request.method == "POST":
        user = User.query.filter_by(email=request.form.get("email", "").strip().lower()).first()
        if user:
            token, raw = PasswordResetToken.create(user.id, current_app.config["RESET_TOKEN_MINUTES"])
            db.session.add(token)
            audit("password_reset_requested", user)
            db.session.commit()
            _send_reset_email(user.email, raw)
        flash("If that email is registered, password-reset instructions have been sent.", "success")
        return redirect(url_for("auth.forgot_password"))
    return render_template("auth/forgot_password.html")


def _send_reset_email(recipient, raw_token):
    host = current_app.config.get("SMTP_HOST")
    if not host:
        current_app.logger.warning("SMTP is not configured; reset delivery skipped.")
        return
    link = url_for("auth.reset_password", token=raw_token, _external=True)
    message = EmailMessage()
    message["Subject"] = "KrishiDirect password reset"
    message["From"] = current_app.config["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(f"Use this link within {current_app.config['RESET_TOKEN_MINUTES']} minutes: {link}")
    with smtplib.SMTP(host, current_app.config["SMTP_PORT"], timeout=10) as smtp:
        smtp.starttls()
        smtp.login(current_app.config["SMTP_USERNAME"], current_app.config["SMTP_PASSWORD"])
        smtp.send_message(message)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    import hashlib
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    reset = PasswordResetToken.query.filter_by(token_hash=token_hash).first()
    if not reset or not reset.valid(token):
        flash("This reset link is invalid or expired.", "error")
        return redirect(url_for("auth.forgot_password"))
    if request.method == "POST":
        password = request.form.get("password", "")
        if password != request.form.get("confirm_password") or not valid_password(password):
            flash("Password must contain at least 6 characters.", "error")
        else:
            user = User.query.get(reset.user_id)
            user.password_hash = User.hash_password(password)
            reset.used_at = utcnow()
            audit("password_reset_completed", user)
            db.session.commit()
            flash("Password updated. Please sign in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", token=token)
