import csv
import os
from datetime import date
from pathlib import Path
from decimal import Decimal

from flask import Flask, redirect, send_from_directory, url_for
from sqlalchemy import inspect, text
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from config import Config
from extensions import csrf, db, limiter, login_manager


def create_app(test_config=None):
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        from models import User
        return db.session.get(User, int(user_id))

    from auth import auth_bp
    from dashboard import dashboard_bp
    from profile import profile_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(profile_bp)

    @app.get("/")
    def index():
        return redirect(url_for("dashboard.dashboard_home"))

    @app.get("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.context_processor
    def inject_security_values():
        from security import csrf_token
        return {"csrf_token": csrf_token}

    @app.cli.command("init-db")
    def init_db():
        from models import User
        db.create_all()
        email = os.getenv("ADMIN_EMAIL")
        password = os.getenv("ADMIN_PASSWORD")
        if email and password and not User.query.filter_by(email=email.lower()).first():
            phone = os.getenv("ADMIN_PHONE", "+9779800000000")
            db.session.add(User(
                full_name="KrishiDirect Administrator",
                email=email.lower(),
                phone=phone,
                password_hash=User.hash_password(password),
                role="admin",
                district="",
                municipality="",
                ward="",
                address="KrishiDirect administration",
            ))
            db.session.commit()
        print("Database initialized.")

    @app.cli.command("seed-market-prices")
    def seed_market_prices():
        from models import MarketPrice

        source = Path(app.root_path) / "data" / "kalimati_prices.csv"
        imported = 0
        with source.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                price_date = date.fromisoformat(row["price_date"])
                existing = MarketPrice.query.filter_by(
                    product_name=row["product_name"],
                    market_name=row.get("market_name", "Kalimati"),
                    price_date=price_date,
                ).first()
                if existing:
                    continue
                db.session.add(MarketPrice(
                    product_name=row["product_name"],
                    market_name=row.get("market_name", "Kalimati"),
                    unit=row.get("unit", "kg"),
                    min_price=Decimal(row["min_price"]),
                    max_price=Decimal(row["max_price"]),
                    average_price=Decimal(row["average_price"]),
                    category=row.get("category") or None,
                    source_date=price_date,
                    price_date=price_date,
                    source_url="https://kalimatimarket.gov.np/",
                ))
                imported += 1
        db.session.commit()
        print(f"Imported {imported} Kalimati market price rows.")

    with app.app_context():
        from models import User  # noqa: F401
        db.create_all()
        _ensure_farmer_account()
        _add_missing_columns()
        _remove_verification_schema()

    if app.config["KALIMATI_SYNC_ENABLED"] and not app.testing:
        scheduler = BackgroundScheduler(daemon=True)
        from kalimati import sync_daily_prices
        scheduler.add_job(
            sync_daily_prices,
            "interval",
            minutes=app.config["KALIMATI_SYNC_INTERVAL_MINUTES"],
            args=[app],
            id="kalimati-daily-sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        app.extensions["kalimati_scheduler"] = scheduler

    return app


def _ensure_farmer_account():
    from models import User

    email = "farmers@gmail.com"
    if User.query.filter_by(email=email).first():
        return
    db.session.add(User(
        full_name="KrishiMandu Farmer",
        email=email,
        phone="+9779800000001",
        password_hash=User.hash_password("password"),
        role="farmer",
        district="",
        municipality="",
        ward="",
        address="Nepal",
    ))
    db.session.commit()


def _add_missing_columns():
    additions = {
        "product": {
            "produce_type": "VARCHAR(20)", "english_name": "VARCHAR(150)",
            "nepali_name": "VARCHAR(150)", "image_path": "VARCHAR(255)",
            "variety": "VARCHAR(120)", "quality_grade": "VARCHAR(50)",
            "minimum_order_quantity": "NUMERIC(12,2)", "harvest_date": "DATE",
            "farm_location": "VARCHAR(255)", "description": "TEXT",
            "is_approved": "BOOLEAN",
        },
        "order": {"negotiation_note": "VARCHAR(500)"},
        "negotiation": {"buyer_accepted": "BOOLEAN", "farmer_accepted": "BOOLEAN"},
        "market_price": {
            "average_price": "NUMERIC(12,2)", "category": "VARCHAR(30)",
            "source_date": "DATE", "source_url": "VARCHAR(500)",
            "synced_at": "DATETIME",
            "product_name_ne": "VARCHAR(150)", "product_name_en": "VARCHAR(150)",
            "market_date_bs": "VARCHAR(40)", "market_date_ad": "DATE",
            "fetched_at": "DATETIME", "sync_status": "VARCHAR(20)",
            "source_identity": "VARCHAR(180)",
        },
    }
    inspector = inspect(db.engine)
    preparer = db.engine.dialect.identifier_preparer
    for table, columns in additions.items():
        existing_names = {column["name"] for column in inspector.get_columns(table)}
        for column, sql_type in columns.items():
            if column not in existing_names:
                table_name = preparer.quote(table)
                column_name = preparer.quote(column)
                db.session.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}"))
    db.session.commit()


def _remove_verification_schema():
    inspector = inspect(db.engine)
    preparer = db.engine.dialect.identifier_preparer

    verification_table = "verification"
    if verification_table in inspector.get_table_names():
        db.session.execute(text(
            f"DROP TABLE {preparer.quote(verification_table)}"
        ))

    legacy_columns = {
        "user": ("email_verified", "phone_verified", "verification_status"),
        "farmer_profile": ("documents",),
        "business_profile": ("documents",),
    }
    for table, columns in legacy_columns.items():
        table_columns = {
            column["name"] for column in inspect(db.engine).get_columns(table)
        }
        for column in columns:
            if column in table_columns:
                db.session.execute(text(
                    f"ALTER TABLE {preparer.quote(table)} DROP COLUMN "
                    f"{preparer.quote(column)}"
                ))
    db.session.commit()


app = create_app()

if __name__ == "__main__":
    ssl_context = "adhoc" if os.getenv("DEV_HTTPS", "0") == "1" else None
    app.run(host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "5001")),
            ssl_context=ssl_context)
