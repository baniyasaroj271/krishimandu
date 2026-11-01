import csv
import io
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, or_

from extensions import db
from models import (
    Cart, CartItem, CheckoutOrder, CheckoutOrderItem, FavouriteFarmer,
    MarketPrice, MarketSync, Order, Product, User,
)
from security import role_required, save_upload

dashboard_bp = Blueprint("dashboard", __name__)


def _dashboard_context(user=None):
    from models import AuditLog

    latest = db.session.query(func.max(MarketPrice.price_date)).scalar()
    price_query = MarketPrice.query.filter_by(price_date=latest) if latest else MarketPrice.query.filter(False)
    trend_name = db.session.query(
        MarketPrice.product_name
    ).group_by(MarketPrice.product_name).order_by(
        func.count(MarketPrice.id).desc()
    ).first()
    trend_rows = MarketPrice.query.filter_by(
        product_name=trend_name[0] if trend_name else ""
    ).order_by(MarketPrice.price_date).all()
    products = Product.query.filter_by(is_active=True).order_by(
        Product.stock_quantity.desc()
    ).limit(6).all()
    latest_sync = MarketSync.query.order_by(MarketSync.started_at.desc()).first()

    if user:
        purchases = CheckoutOrder.query.filter_by(buyer_id=user.id).count()
        active_orders = CheckoutOrder.query.filter(
            CheckoutOrder.buyer_id == user.id,
            CheckoutOrder.status.not_in(("Delivered", "Cancelled", "Completed")),
        ).count()
        activities = AuditLog.query.filter_by(user_id=user.id).order_by(
            AuditLog.created_at.desc()
        ).limit(6).all()
    else:
        purchases = 0
        active_orders = 0
        activities = []

    return {
        "dashboard_page": True,
        "user_name": user.full_name if user else "there",
        "total_purchases": purchases,
        "available_products": Product.query.filter_by(is_active=True).count(),
        "active_orders": active_orders,
        "watchlist_alerts": 0,
        "price_labels": [row.price_date.strftime("%d %b") for row in trend_rows] if user else [],
        "price_values": [float(row.average_price or 0) for row in trend_rows] if user else [],
        "trend_product": trend_name[0] if user and trend_name else "Sign in to view prices",
        "product_labels": [product.english_name or product.name for product in products] if user else [],
        "product_values": [float(product.stock_quantity or 0) for product in products] if user else [],
        "recent_activity": activities,
        "latest_price_count": price_query.count(),
        "latest_sync": latest_sync,
    }


@dashboard_bp.get("/dashboard")
def dashboard_home():
    user = current_user if current_user.is_authenticated else None
    return render_template("dashboard.html", title="Dashboard", **_dashboard_context(user))


@dashboard_bp.get("/farmer/dashboard")
@login_required
@role_required("farmer")
def farmer():
    products = Product.query.filter_by(
        farmer_id=current_user.id
    ).order_by(Product.created_at.desc()).all()
    return render_template(
        "farmer_dashboard.html",
        products=products,
        active_products=[product for product in products if product.is_active],
    )


@dashboard_bp.route("/farmer/price-comparison", methods=["GET", "POST"])
@login_required
@role_required("farmer")
def farmer_price_comparison():
    products = Product.query.filter_by(farmer_id=current_user.id, is_active=True).order_by(Product.name).all()
    selected_id = request.values.get("product_id", type=int)
    selected = next((p for p in products if p.id == selected_id), products[0] if products else None)
    market = None
    costs = {"production": Decimal("0"), "packaging": Decimal("0"), "transportation": Decimal("0")}
    if selected:
        market = MarketPrice.query.filter(
            or_(MarketPrice.product_name.ilike(f"%{selected.name}%"),
                MarketPrice.product_name.ilike(f"%{selected.english_name or selected.name}%"))
        ).order_by(MarketPrice.price_date.desc()).first()
    if request.method == "POST":
        try:
            costs = {key: Decimal(request.form.get(key, "0") or "0") for key in costs}
        except InvalidOperation:
            flash("Enter valid non-negative cost values.", "error")
            costs = {key: Decimal("0") for key in costs}
        if any(value < 0 for value in costs.values()):
            flash("Costs cannot be negative.", "error")
            costs = {key: Decimal("0") for key in costs}
    total_cost = sum(costs.values(), Decimal("0"))
    quantity = selected.stock_quantity if selected and selected.stock_quantity > 0 else Decimal("1")
    break_even = total_cost / quantity
    farmer_price = selected.price_per_unit if selected else None
    difference = farmer_price - market.average_price if farmer_price and market and market.average_price is not None else None
    difference_percent = (difference / market.average_price * 100) if difference is not None and market.average_price else None
    suggested_low = max(break_even, market.min_price) if market else break_even
    suggested_high = market.max_price if market else break_even * Decimal("1.15")
    return render_template("farmer_price_comparison.html", products=products, selected=selected, market=market,
                           costs=costs, total_cost=total_cost, break_even=break_even,
                           farmer_price=farmer_price, difference=difference,
                           difference_percent=difference_percent, suggested_low=suggested_low,
                           suggested_high=suggested_high)


@dashboard_bp.route("/farmer/products/new", methods=["GET", "POST"])
@login_required
@role_required("farmer")
def add_product():
    if request.method == "POST":
        try:
            price = Decimal(request.form.get("price_per_unit", ""))
            stock = Decimal(request.form.get("stock_quantity", ""))
            minimum = Decimal(request.form.get("minimum_order_quantity", "1"))
        except InvalidOperation:
            flash("Enter valid numeric values for price and stock.", "error")
        else:
            name = request.form.get("name", "").strip()
            category = request.form.get("category", "").strip()
            harvest = _parse_date(request.form.get("harvest_date"))
            if not name or not category or price <= 0 or stock < 0 or minimum <= 0 or not harvest:
                flash("Complete the product fields with valid values.", "error")
            else:
                db.session.add(Product(
                    farmer_id=current_user.id, name=name, category=category,
                    produce_type=request.form.get("produce_type", "").strip(),
                    english_name=request.form.get("english_name", "").strip(),
                    nepali_name=request.form.get("nepali_name", "").strip(),
                    image_path=save_upload(request.files.get("image"), f"product-{current_user.id}"),
                    variety=request.form.get("variety", "").strip(),
                    quality_grade=request.form.get("quality_grade", "").strip(),
                    unit=request.form.get("unit", "kg").strip(),
                    price_per_unit=price, stock_quantity=stock,
                    minimum_order_quantity=minimum, harvest_date=harvest,
                    farm_location=request.form.get("farm_location", "").strip(),
                    description=request.form.get("description", "").strip(),
                    is_active=True, is_approved=True,
                ))
                db.session.commit()
                flash("Product added to your inventory.", "success")
                return redirect(url_for("dashboard.farmer"))
    return render_template("product_form.html", title="Add product", product=None, today=date.today())


def _parse_date(value):
    try:
        parsed = datetime.strptime(value or "", "%Y-%m-%d").date()
        return parsed if parsed <= date.today() else None
    except ValueError:
        return None


@dashboard_bp.route("/farmer/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
@role_required("farmer")
def edit_product(product_id):
    product = Product.query.filter_by(id=product_id, farmer_id=current_user.id).first_or_404()
    if request.method == "POST":
        try:
            price = Decimal(request.form.get("price_per_unit", ""))
            stock = Decimal(request.form.get("stock_quantity", ""))
            minimum = Decimal(request.form.get("minimum_order_quantity", "1"))
        except InvalidOperation:
            flash("Enter valid numeric values.", "error")
        else:
            harvest = _parse_date(request.form.get("harvest_date"))
            if not request.form.get("name", "").strip() or price <= 0 or stock < 0 or minimum <= 0 or not harvest:
                flash("Complete the required fields with valid values.", "error")
            else:
                for field in ("name", "category", "produce_type", "english_name", "nepali_name",
                              "variety", "quality_grade", "unit", "farm_location", "description"):
                    setattr(product, field, request.form.get(field, "").strip())
                product.price_per_unit, product.stock_quantity = price, stock
                product.minimum_order_quantity, product.harvest_date = minimum, harvest
                image = save_upload(request.files.get("image"), f"product-{current_user.id}")
                if image:
                    product.image_path = image
                db.session.commit()
                flash("Product updated.", "success")
                return redirect(url_for("dashboard.inventory"))
    return render_template("product_form.html", title="Edit product", product=product, today=date.today())


@dashboard_bp.post("/farmer/products/<int:product_id>/delete")
@login_required
@role_required("farmer")
def delete_product(product_id):
    product = Product.query.filter_by(id=product_id, farmer_id=current_user.id).first_or_404()
    if Order.query.filter_by(product_id=product.id).first():
        product.is_active = False
    else:
        db.session.delete(product)
    db.session.commit()
    flash("Product listing deleted.", "success")
    return redirect(url_for("dashboard.inventory"))


@dashboard_bp.get("/farmer/inventory")
@login_required
@role_required("farmer")
def inventory():
    products = Product.query.filter_by(farmer_id=current_user.id).order_by(Product.created_at.desc()).all()
    return render_template("inventory.html", products=products, low_stock=[
        p for p in products if p.is_active and p.stock_quantity <= (p.low_stock_threshold or 10)
    ])


@dashboard_bp.post("/farmer/products/<int:product_id>/stock")
@login_required
@role_required("farmer")
def update_stock(product_id):
    product = Product.query.filter_by(id=product_id, farmer_id=current_user.id).first_or_404()
    try:
        stock = Decimal(request.form.get("stock_quantity", ""))
    except InvalidOperation:
        flash("Enter a valid stock quantity.", "error")
    else:
        if stock < 0:
            flash("Stock cannot be negative.", "error")
        else:
            product.stock_quantity = stock
            db.session.commit()
            flash(f"{product.name} stock updated.", "success")
    return redirect(url_for("dashboard.farmer"))


@dashboard_bp.post("/farmer/products/<int:product_id>/availability")
@login_required
@role_required("farmer")
def update_availability(product_id):
    product = Product.query.filter_by(id=product_id, farmer_id=current_user.id).first_or_404()
    product.is_active = request.form.get("available") == "1"
    db.session.commit()
    flash("Product availability updated.", "success")
    return redirect(url_for("dashboard.inventory"))


@dashboard_bp.get("/farmer/orders")
@login_required
@role_required("farmer")
def farmer_orders():
    orders = Order.query.filter_by(farmer_id=current_user.id).order_by(Order.created_at.desc()).all()
    return render_template("farmer_orders.html", orders=orders)


ORDER_STATUSES = {"Pending", "Accepted", "Negotiating", "Processing", "Ready for pickup",
                  "Dispatched", "Delivered", "Rejected", "Cancelled"}


@dashboard_bp.post("/farmer/orders/<int:order_id>/status")
@login_required
@role_required("farmer")
def update_order_status(order_id):
    order = Order.query.filter_by(id=order_id, farmer_id=current_user.id).first_or_404()
    status = request.form.get("status", "")
    if status not in ORDER_STATUSES:
        flash("Invalid order status.", "error")
    elif status == "Accepted" and order.status == "Pending":
        product = Product.query.filter_by(id=order.product_id).with_for_update().first()
        if not product or product.stock_quantity < order.quantity:
            flash("Not enough stock to accept this order.", "error")
        else:
            product.stock_quantity -= order.quantity
            order.status = status
            db.session.commit()
            flash("Order accepted and stock updated.", "success")
    else:
        order.status = status
        order.negotiation_note = request.form.get("negotiation_note", "").strip() or order.negotiation_note
        db.session.commit()
        flash("Order status updated.", "success")
    return redirect(url_for("dashboard.farmer_orders"))


@dashboard_bp.post("/farmer/orders/<int:order_id>/complete")
@login_required
@role_required("farmer")
def complete_order(order_id):
    order = Order.query.filter_by(id=order_id, farmer_id=current_user.id).first_or_404()
    if order.status == "Pending":
        order.status = "Completed"
        order.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Order marked as completed.", "success")
    return redirect(url_for("dashboard.farmer"))


@dashboard_bp.get("/marketplace")
@login_required
@role_required("admin", "reseller", "customer")
def marketplace():
    search = request.args.get("q", "").strip()
    category = request.args.get("category", "").strip()
    location = request.args.get("location", "").strip()
    sort = request.args.get("sort", "name")
    query = Product.query.filter(Product.is_active.is_(True), or_(Product.is_approved.is_(True), Product.is_approved.is_(None)))
    if search:
        query = query.filter(or_(Product.name.ilike(f"%{search}%"), Product.english_name.ilike(f"%{search}%"), Product.nepali_name.ilike(f"%{search}%")))
    if category:
        query = query.filter(Product.category.ilike(f"%{category}%"))
    if location:
        query = query.filter(Product.farm_location.ilike(f"%{location}%"))
    query = query.order_by(Product.price_per_unit if sort == "price" else Product.name)
    products = query.all()
    return render_template("marketplace.html", products=products, search=search, category=category, location=location, sort=sort)


@dashboard_bp.get("/marketplace/products/<int:product_id>")
@login_required
@role_required("reseller", "customer")
def product_detail(product_id):
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
    farmer = User.query.filter_by(id=product.farmer_id, role="farmer").first_or_404()
    return render_template("product_detail.html", product=product, farmer=farmer)


def _get_cart():
    cart = Cart.query.filter_by(buyer_id=current_user.id).first()
    if not cart:
        cart = Cart(buyer_id=current_user.id)
        db.session.add(cart)
        db.session.flush()
    return cart


@dashboard_bp.get("/cart")
@login_required
@role_required("reseller")
def cart():
    shopping_cart = _get_cart()
    subtotal = sum((item.quantity * item.product.price_per_unit for item in shopping_cart.items), Decimal("0"))
    delivery = Decimal("150") if shopping_cart.items else Decimal("0")
    return render_template("cart.html", cart=shopping_cart, subtotal=subtotal, delivery_charge=delivery, total=subtotal + delivery)


@dashboard_bp.post("/cart/products/<int:product_id>")
@login_required
@role_required("reseller")
def add_to_cart(product_id):
    product = Product.query.filter(
        Product.id == product_id, Product.is_active.is_(True),
        or_(Product.is_approved.is_(True), Product.is_approved.is_(None)),
    ).first_or_404()
    try:
        quantity = Decimal(request.form.get("quantity", ""))
    except InvalidOperation:
        flash("Enter a valid quantity.", "error")
        return redirect(url_for("dashboard.product_detail", product_id=product.id))
    minimum = product.minimum_order_quantity or Decimal("1")
    if quantity < minimum or quantity > product.stock_quantity:
        flash(f"Quantity must be at least {minimum} and within available stock.", "error")
    else:
        shopping_cart = _get_cart()
        item = CartItem.query.filter_by(cart_id=shopping_cart.id, product_id=product.id).first()
        new_quantity = quantity + (item.quantity if item else Decimal("0"))
        if new_quantity > product.stock_quantity:
            flash("The cart quantity exceeds current stock.", "error")
        elif item:
            item.quantity = new_quantity
            db.session.commit()
            flash("Cart quantity updated.", "success")
        else:
            db.session.add(CartItem(cart_id=shopping_cart.id, product_id=product.id, quantity=quantity))
            db.session.commit()
            flash("Product added to cart.", "success")
    return redirect(url_for("dashboard.product_detail", product_id=product.id))


@dashboard_bp.post("/cart/items/<int:item_id>/update")
@login_required
@role_required("reseller")
def update_cart_item(item_id):
    item = CartItem.query.join(Cart).filter(Cart.buyer_id == current_user.id, CartItem.id == item_id).first_or_404()
    try:
        quantity = Decimal(request.form.get("quantity", ""))
    except InvalidOperation:
        flash("Enter a valid quantity.", "error")
    else:
        minimum = item.product.minimum_order_quantity or Decimal("1")
        if quantity < minimum or quantity > item.product.stock_quantity:
            flash(f"Quantity must be at least {minimum} and within available stock.", "error")
        else:
            item.quantity = quantity
            db.session.commit()
            flash("Cart updated.", "success")
    return redirect(url_for("dashboard.cart"))


@dashboard_bp.post("/cart/items/<int:item_id>/remove")
@login_required
@role_required("reseller")
def remove_cart_item(item_id):
    item = CartItem.query.join(Cart).filter(Cart.buyer_id == current_user.id, CartItem.id == item_id).first_or_404()
    db.session.delete(item)
    db.session.commit()
    flash("Item removed from cart.", "success")
    return redirect(url_for("dashboard.cart"))


@dashboard_bp.route("/checkout", methods=["GET", "POST"])
@login_required
@role_required("reseller")
def checkout():
    shopping_cart = _get_cart()
    if not shopping_cart.items:
        flash("Your cart is empty.", "error")
        return redirect(url_for("dashboard.marketplace"))
    if request.method == "POST":
        address = request.form.get("delivery_address", "").strip()
        phone = request.form.get("contact_number", "").strip()
        payment = request.form.get("payment_method", "")
        if not address or not phone or payment not in {"cash_on_delivery", "bank_transfer"}:
            flash("Complete the delivery and payment details.", "error")
            return render_template("checkout.html", cart=shopping_cart)
        subtotal = Decimal("0")
        order_items = []
        for item in list(shopping_cart.items):
            product = Product.query.filter_by(id=item.product_id, is_active=True).with_for_update().first()
            if not product or item.quantity < (product.minimum_order_quantity or Decimal("1")) or item.quantity > product.stock_quantity:
                flash(f"{item.product.name} is no longer available in the requested quantity.", "error")
                db.session.rollback()
                return redirect(url_for("dashboard.cart"))
            unit_price = product.price_per_unit
            line_total = item.quantity * unit_price
            subtotal += line_total
            order_items.append((product, item.quantity, unit_price, line_total))
        delivery = Decimal("150")
        order = CheckoutOrder(
            buyer_id=current_user.id, delivery_address=address, contact_number=phone,
            payment_method=payment, subtotal=subtotal, delivery_charge=delivery,
            total_amount=subtotal + delivery,
        )
        db.session.add(order)
        db.session.flush()
        for product, quantity, unit_price, line_total in order_items:
            product.stock_quantity -= quantity
            db.session.add(CheckoutOrderItem(
                order_id=order.id, product_id=product.id, farmer_id=product.farmer_id,
                product_name=product.name, unit=product.unit, quantity=quantity,
                unit_price=unit_price, total_price=line_total,
            ))
        CartItem.query.filter_by(cart_id=shopping_cart.id).delete()
        db.session.commit()
        flash(f"Order {order.order_number} placed successfully.", "success")
        return redirect(url_for("dashboard.marketplace"))
    return render_template("checkout.html", cart=shopping_cart)


@dashboard_bp.post("/marketplace/products/<int:product_id>/contact")
@login_required
@role_required("reseller", "customer")
def contact_farmer(product_id):
    product = Product.query.filter_by(id=product_id, is_active=True).first_or_404()
    flash(f"Your contact request for {product.name} was sent to the farmer.", "success")
    return redirect(url_for("dashboard.product_detail", product_id=product.id))


@dashboard_bp.get("/farmers")
@login_required
@role_required("reseller")
def find_farmers():
    location = request.args.get("location", "").strip()
    product = request.args.get("product", "").strip()
    farmers = User.query.filter_by(role="farmer").all()
    if location:
        farmers = [f for f in farmers if location.lower() in (f.farmer_profile.farming_location if f.farmer_profile else f.address).lower()]
    if product:
        farmers = [f for f in farmers if any(product.lower() in (p.name or "").lower() for p in Product.query.filter_by(farmer_id=f.id, is_active=True))]
    return render_template("farmers.html", farmers=farmers, location=location, product=product)


@dashboard_bp.get("/farmers/<int:farmer_id>")
@login_required
@role_required("reseller")
def farmer_profile(farmer_id):
    farmer = User.query.filter_by(id=farmer_id, role="farmer").first_or_404()
    products = Product.query.filter_by(farmer_id=farmer.id, is_active=True).all()
    return render_template("farmer_profile.html", farmer=farmer, products=products)


@dashboard_bp.get("/marketplace/compare")
@login_required
@role_required("admin", "reseller", "customer")
def compare_prices():
    name = request.args.get("product", "").strip()
    products = Product.query.filter(Product.is_active.is_(True), or_(Product.is_approved.is_(True), Product.is_approved.is_(None)), Product.name.ilike(f"%{name}%")).all() if name else []
    official = MarketPrice.query.filter(MarketPrice.product_name.ilike(f"%{name}%")).order_by(MarketPrice.price_date.desc()).first() if name else None
    return render_template("compare_prices.html", products=products, official=official, name=name)


@dashboard_bp.get("/marketplace/prices")
@login_required
@role_required("admin", "reseller", "customer")
def market_prices():
    latest = db.session.query(func.max(MarketPrice.price_date)).scalar_subquery()
    prices = MarketPrice.query.filter(MarketPrice.price_date == latest).order_by(MarketPrice.product_name).all()
    return render_template("market_prices.html", prices=prices)


@dashboard_bp.get("/marketplace/price-history")
@login_required
@role_required("admin", "reseller", "customer")
def price_history():
    product = request.args.get("product", "").strip()
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    query = MarketPrice.query
    if product:
        query = query.filter(MarketPrice.product_name.ilike(f"%{product}%"))
    if start:
        query = query.filter(MarketPrice.price_date >= start)
    if end:
        query = query.filter(MarketPrice.price_date <= end)
    prices = query.order_by(MarketPrice.price_date).all()
    chart = [{"date": p.price_date.isoformat(), "min": float(p.min_price), "max": float(p.max_price), "average": float(p.average_price) if p.average_price is not None else None} for p in prices]
    return render_template("price_history.html", prices=prices, chart=chart, product=product, start=start, end=end)


@dashboard_bp.get("/api/market-prices")
@login_required
@role_required("admin", "reseller", "customer")
def market_prices_api():
    latest = db.session.query(func.max(MarketPrice.price_date)).scalar()
    prices = MarketPrice.query.filter_by(price_date=latest).order_by(MarketPrice.product_name).all() if latest else []
    return jsonify({
        "publication_date_bs": prices[0].market_date_bs if prices else None,
        "prices": [{
            "product_name": price.product_name,
            "unit": price.unit,
            "minimum": float(price.min_price),
            "maximum": float(price.max_price),
            "average": float(price.average_price) if price.average_price is not None else None,
            "category": price.category,
        } for price in prices],
    })


@dashboard_bp.get("/api/market-prices/history")
@login_required
@role_required("admin", "reseller", "customer")
def market_price_history_api():
    product = request.args.get("product", "").strip()
    query = MarketPrice.query
    if product:
        query = query.filter(MarketPrice.product_name.ilike(f"%{product}%"))
    rows = query.order_by(MarketPrice.price_date, MarketPrice.product_name).all()
    return jsonify({"history": [{
        "product_name": row.product_name,
        "date": row.price_date.isoformat(),
        "market_date_bs": row.market_date_bs,
        "minimum": float(row.min_price),
        "maximum": float(row.max_price),
        "average": float(row.average_price) if row.average_price is not None else None,
    } for row in rows]})


@dashboard_bp.get("/marketplace/price-fluctuation")
@login_required
@role_required("admin", "reseller", "customer")
def price_fluctuation():
    category = request.args.get("category", "").strip()
    query = MarketPrice.query.order_by(MarketPrice.product_name, MarketPrice.price_date)
    if category:
        query = query.filter_by(category=category)
    rows = query.all()
    changes = []
    previous = {}
    for row in rows:
        if row.average_price is None:
            continue
        old = previous.get(row.product_name)
        difference = row.average_price - old if old is not None else None
        changes.append((row, difference, (difference / old * 100) if old else None))
        previous[row.product_name] = row.average_price
    return render_template("price_fluctuation.html", changes=changes, category=category)


@dashboard_bp.route("/admin/market-prices/import", methods=["GET", "POST"])
@login_required
@role_required("admin")
def import_market_prices():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload or not upload.filename.lower().endswith(".csv"):
            flash("Upload a CSV exported from the official source.", "error")
        else:
            imported = 0
            for row in csv.DictReader(io.StringIO(upload.stream.read().decode("utf-8-sig"))):
                try:
                    price_date = datetime.strptime(row["publication_date"], "%Y-%m-%d").date()
                    minimum = Decimal(row["minimum_price"])
                    maximum = Decimal(row["maximum_price"])
                    average = Decimal(row["average_price"]) if row.get("average_price") else None
                except (KeyError, ValueError, InvalidOperation):
                    continue
                db.session.add(MarketPrice(
                    product_name=row["product_name"].strip(),
                    market_name=row.get("market_name", "Kalimati").strip(),
                    unit=row.get("unit", "kg").strip(),
                    min_price=minimum, max_price=maximum, average_price=average,
                    category=row.get("category", "").strip() or None,
                    price_date=price_date, source_date=price_date,
                    source_url="https://kalimatimarket.gov.np/price",
                    synced_at=datetime.now(timezone.utc),
                ))
                imported += 1
            db.session.commit()
            flash(f"Imported {imported} official price rows.", "success")
    sync_history = MarketSync.query.order_by(MarketSync.started_at.desc()).limit(20).all()
    return render_template("market_price_import.html", sync_history=sync_history)


@dashboard_bp.get("/admin/market-prices/sync-history")
@login_required
@role_required("admin")
def market_price_sync_history():
    sync_history = MarketSync.query.order_by(MarketSync.started_at.desc()).limit(100).all()
    return render_template("market_price_sync_history.html", sync_history=sync_history)


@dashboard_bp.post("/admin/market-prices/sync")
@login_required
@role_required("admin")
def sync_market_prices():
    from kalimati import sync_daily_prices

    result = sync_daily_prices()
    flash(
        f"Kalimati sync completed: {result.get('imported', 0)} new records."
        if result["status"] == "success"
        else f"Kalimati sync failed: {result.get('error', result['status'])}",
        "success" if result["status"] == "success" else "error",
    )
    return redirect(url_for("dashboard.import_market_prices"))


@dashboard_bp.get("/api/market-prices/status")
@login_required
@role_required("admin", "reseller", "customer")
def market_price_sync_status():
    sync = MarketSync.query.order_by(MarketSync.started_at.desc()).first()
    return jsonify({
        "status": sync.status if sync else "not_run",
        "started_at": sync.started_at.isoformat() if sync else None,
        "completed_at": sync.completed_at.isoformat() if sync and sync.completed_at else None,
        "publication_date_bs": sync.publication_date_bs if sync else None,
        "imported_count": sync.imported_count if sync else 0,
        "error": sync.error_message if sync else None,
    })


@dashboard_bp.post("/marketplace/products/<int:product_id>/order")
@login_required
@role_required("reseller")
def place_order(product_id):
    return add_to_cart(product_id)


@dashboard_bp.post("/marketplace/farmers/<int:farmer_id>/favourite")
@login_required
@role_required("reseller")
def toggle_favourite_farmer(farmer_id):
    farmer = User.query.filter_by(id=farmer_id, role="farmer").first_or_404()
    favourite = FavouriteFarmer.query.filter_by(
        reseller_id=current_user.id, farmer_id=farmer_id
    ).first()
    if favourite:
        db.session.delete(favourite)
        flash("Farmer removed from favourites.", "success")
    else:
        db.session.add(FavouriteFarmer(reseller_id=current_user.id, farmer_id=farmer.id))
        flash("Farmer saved to favourites.", "success")
    db.session.commit()
    return redirect(url_for("dashboard.marketplace", q=request.args.get("q", "")))


@dashboard_bp.get("/dashboard/admin")
@login_required
@role_required("admin")
def admin():
    return render_template(
        "dashboard.html",
        title="Dashboard",
        **_dashboard_context(current_user),
    )
