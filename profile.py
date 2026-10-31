from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from models import FarmerProfile, User
from security import audit, normalize_phone, save_upload, valid_password

profile_bp = Blueprint("profile", __name__, url_prefix="/profile")


@profile_bp.route("", methods=["GET", "POST"])
@login_required
def profile():
    farmer = current_user.farmer_profile
    business = current_user.business_profile
    if request.method == "POST":
        form = request.form
        phone = normalize_phone(form.get("phone"))
        duplicate = User.query.filter(User.phone == phone, User.id != current_user.id).first()
        if not form.get("full_name", "").strip() or duplicate:
            flash("Enter a valid name and a unique phone number.", "error")
        else:
            current_user.full_name = form["full_name"].strip()
            current_user.phone = phone
            current_user.address = form.get("address", "").strip()
            picture = save_upload(request.files.get("profile_picture"), f"profile-{current_user.id}")
            if picture:
                current_user.profile_picture = picture
            if farmer:
                farmer.farm_name = form.get("farm_name", "").strip()
                farmer.farming_location = form.get("farming_location", "").strip()
                farmer.product_categories = form.get("product_categories", "").strip()
            elif business:
                business.business_name = form.get("business_name", "").strip()
                business.business_type = form.get("business_type", "").strip()
                business.purchase_location = form.get("purchase_location", "").strip()
                business.shop_address = form.get("shop_address", "").strip()
            audit("profile_updated", current_user)
            db.session.commit()
            flash("Profile updated.", "success")
            return redirect(url_for("profile.profile"))
    return render_template("profile.html", farmer=farmer, business=business)


@profile_bp.post("/password")
@login_required
def change_password():
    if not current_user.check_password(request.form.get("current_password", "")):
        flash("Current password is incorrect.", "error")
    elif request.form.get("new_password") != request.form.get("confirm_password") or not valid_password(request.form.get("new_password", "")):
        flash("New passwords must match and meet the strength requirements.", "error")
    else:
        current_user.password_hash = User.hash_password(request.form["new_password"])
        audit("password_changed", current_user)
        db.session.commit()
        flash("Password changed successfully.", "success")
    return redirect(url_for("profile.profile"))

