import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    phone = db.Column(db.String(30), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="farmer")
    district = db.Column(db.String(80), nullable=False)
    municipality = db.Column(db.String(100), nullable=False)
    ward = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    profile_picture = db.Column(db.String(255))
    failed_login_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime(timezone=True))
    last_login_at = db.Column(db.DateTime(timezone=True))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    farmer_profile = db.relationship("FarmerProfile", uselist=False, cascade="all, delete-orphan")
    business_profile = db.relationship("BusinessProfile", uselist=False, cascade="all, delete-orphan")

    @staticmethod
    def hash_password(password):
        return generate_password_hash(password, method="scrypt")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def is_locked(self):
        return self.locked_until and self.locked_until > utcnow()


class FarmerProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    farm_name = db.Column(db.String(150), nullable=False)
    farming_location = db.Column(db.String(255), nullable=False)
    product_categories = db.Column(db.String(500), nullable=False)


class BusinessProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    business_name = db.Column(db.String(150), nullable=False)
    business_type = db.Column(db.String(100), nullable=False)
    purchase_location = db.Column(db.String(255), nullable=False)
    shop_address = db.Column(db.String(255))


class PasswordResetToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True))

    @staticmethod
    def create(user_id, minutes):
        raw = secrets.token_urlsafe(48)
        token = PasswordResetToken(
            user_id=user_id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            expires_at=utcnow() + timedelta(minutes=minutes),
        )
        return token, raw

    def valid(self, raw):
        return not self.used_at and self.expires_at > utcnow() and secrets.compare_digest(
            self.token_hash, hashlib.sha256(raw.encode()).hexdigest()
        )


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    event = db.Column(db.String(100), nullable=False)
    ip_address = db.Column(db.String(45))
    details = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    produce_type = db.Column(db.String(20), nullable=True)
    english_name = db.Column(db.String(150), nullable=True)
    nepali_name = db.Column(db.String(150), nullable=True)
    image_path = db.Column(db.String(255), nullable=True)
    variety = db.Column(db.String(120), nullable=True)
    quality_grade = db.Column(db.String(50), nullable=True)
    unit = db.Column(db.String(30), nullable=False, default="kg")
    price_per_unit = db.Column(db.Numeric(12, 2), nullable=False)
    stock_quantity = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    minimum_order_quantity = db.Column(db.Numeric(12, 2), nullable=True)
    harvest_date = db.Column(db.Date, nullable=True)
    farm_location = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    low_stock_threshold = db.Column(db.Numeric(12, 2), nullable=False, default=10)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_approved = db.Column(db.Boolean, nullable=True, default=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    orders = db.relationship("Order", backref="product", lazy=True)


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    buyer_name = db.Column(db.String(120), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Pending")
    negotiation_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at = db.Column(db.DateTime(timezone=True))


class MarketPrice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(150), nullable=False, index=True)
    market_name = db.Column(db.String(150), nullable=False, default="Kalimati")
    unit = db.Column(db.String(30), nullable=False, default="kg")
    min_price = db.Column(db.Numeric(12, 2), nullable=False)
    max_price = db.Column(db.Numeric(12, 2), nullable=False)
    average_price = db.Column(db.Numeric(12, 2), nullable=True)
    category = db.Column(db.String(30), nullable=True)
    source_date = db.Column(db.Date, nullable=True)
    price_date = db.Column(db.Date, nullable=False, index=True)
    source_url = db.Column(db.String(500), nullable=True)
    synced_at = db.Column(db.DateTime(timezone=True), nullable=True)


class FavouriteFarmer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    __table_args__ = (db.UniqueConstraint("reseller_id", "farmer_id", name="uq_favourite_farmer"),)


class Cart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    items = db.relationship("CartItem", backref="cart", cascade="all, delete-orphan")


class CartItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cart_id = db.Column(db.Integer, db.ForeignKey("cart.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    product = db.relationship("Product")
    __table_args__ = (db.UniqueConstraint("cart_id", "product_id", name="uq_cart_product"),)


class CheckoutOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(32), unique=True, nullable=False, default=lambda: f"KD-{uuid.uuid4().hex[:12].upper()}")
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    delivery_address = db.Column(db.String(255), nullable=False)
    contact_number = db.Column(db.String(30), nullable=False)
    payment_method = db.Column(db.String(30), nullable=False)
    subtotal = db.Column(db.Numeric(12, 2), nullable=False)
    delivery_charge = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    items = db.relationship("CheckoutOrderItem", backref="checkout_order", cascade="all, delete-orphan")


class CheckoutOrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("checkout_order.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    product_name = db.Column(db.String(150), nullable=False)
    unit = db.Column(db.String(30), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    unit_price = db.Column(db.Numeric(12, 2), nullable=False)
    total_price = db.Column(db.Numeric(12, 2), nullable=False)


class Negotiation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    buyer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    farmer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    proposed_price = db.Column(db.Numeric(12, 2), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Pending")
    buyer_accepted = db.Column(db.Boolean, nullable=False, default=False)
    farmer_accepted = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    product = db.relationship("Product")
    messages = db.relationship("NegotiationOffer", backref="negotiation", cascade="all, delete-orphan")


class NegotiationOffer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    negotiation_id = db.Column(db.Integer, db.ForeignKey("negotiation.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=False)
    quantity = db.Column(db.Numeric(12, 2), nullable=False)
    note = db.Column(db.String(500))
    status = db.Column(db.String(20), nullable=False, default="Pending")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
