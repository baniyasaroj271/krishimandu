import re
import secrets
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for
from flask_login import current_user
from flask_wtf.csrf import generate_csrf
from werkzeug.utils import secure_filename

from extensions import db
from models import AuditLog, User

ROLE_DASHBOARDS = {
    "admin": "dashboard.admin",
    "farmer": "dashboard.farmer",
    "customer": "dashboard.dashboard_home",
}


def csrf_token():
    return generate_csrf()


def audit(event, user=None, details=""):
    db.session.add(AuditLog(user_id=getattr(user or current_user, "id", None),
                            event=event, ip_address=request.remote_addr, details=details[:500]))


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.path))
            if current_user.role not in roles:
                flash("You are not authorized to access that page.", "error")
                return redirect(url_for(ROLE_DASHBOARDS.get(current_user.role, "auth.login")))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def valid_password(password):
    return isinstance(password, str) and len(password) >= 6


def normalize_phone(phone):
    return re.sub(r"[^\d+]", "", phone or "")


def allowed_file(filename):
    from flask import current_app
    return "." in filename and filename.rsplit(".", 1)[1].lower() in current_app.config["ALLOWED_IMAGE_EXTENSIONS"]


def save_upload(file, prefix):
    if not file or not file.filename or not allowed_file(file.filename):
        return None
    import os
    from flask import current_app
    extension = secure_filename(file.filename).rsplit(".", 1)[1].lower()
    name = f"{prefix}-{secrets.token_urlsafe(12)}.{extension}"
    file.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


def parse_roles(form_role):
    return form_role if form_role in {"farmer", "reseller", "customer"} else None
