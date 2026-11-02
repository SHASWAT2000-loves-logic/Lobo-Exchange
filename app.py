"""Lobo Exchange: a small local marketplace prototype."""

import io
import os
import re
import secrets
from decimal import Decimal, InvalidOperation
from pathlib import Path

from flask import (
    Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory,
    session, url_for,
)
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from PIL import Image, UnidentifiedImageError
from sqlalchemy import Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from ai_service import AISuggestionError, analyze_listing_safety, generate_listing_suggestions
from image_storage import BlobImageStorage, LocalImageStorage, listing_image_url


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

CATEGORIES = ("Books", "Electronics", "Furniture", "Clothing", "School Supplies", "Other")
UNM_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@unm\.edu$", re.IGNORECASE)
IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}
IMAGE_MIME_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "GIF": "image/gif"}
AI_ERROR_MESSAGE = "AI suggestions are unavailable right now. You can continue creating the listing manually."
AI_BUSY_MESSAGE = "The AI service is busy right now. Please try again in a few minutes. You can continue creating the listing manually."
AI_LIMIT_MESSAGE = "The free AI request limit has been reached. Please try again later. You can continue creating the listing manually."
AI_SETUP_MESSAGE = "Add a Groq API key to .env and restart the app to use AI suggestions. You can continue creating the listing manually."
AI_CONNECTION_MESSAGE = "The app could not connect to Groq. Check internet access and try again. You can continue creating the listing manually."
AI_AUTH_MESSAGE = "Groq rejected the API key. Check GROQ_API_KEY in .env and restart the app. You can continue creating the listing manually."


def read_listing_image(image):
    """Read and verify an uploaded listing photo once."""
    if not image or not image.filename:
        raise ValueError("No image selected")
    try:
        image_bytes = image.read()
        with Image.open(io.BytesIO(image_bytes)) as opened:
            image_format = opened.format
            opened.verify()
        if image_format not in IMAGE_FORMATS:
            raise ValueError("Unsupported image format")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid listing image") from exc
    return image_bytes, image_format


class User(UserMixin, db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    listings: Mapped[list["Listing"]] = relationship(back_populates="seller")


class Listing(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    image_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    seller_id: Mapped[int] = mapped_column(db.ForeignKey("user.id"), nullable=False)
    seller: Mapped[User] = relationship(back_populates="listings")


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


def database_uri_from_environment():
    """Use psycopg for both modern and legacy PostgreSQL URL forms."""
    uri = os.environ.get("DATABASE_URL", "").strip()
    if uri.startswith("postgres://"):
        return "postgresql+psycopg://" + uri[len("postgres://"):]
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg://" + uri[len("postgresql://"):]
    return uri or "sqlite:///lobo_exchange.db"


def create_app(test_config=None):
    app = Flask(__name__, static_folder="public/static", static_url_path="/static")
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-prototype-only-change-me"),
        SQLALCHEMY_DATABASE_URI=database_uri_from_environment(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(Path(app.instance_path) / "uploads"),
        BLOB_READ_WRITE_TOKEN=os.environ.get("BLOB_READ_WRITE_TOKEN", ""),
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)

    on_vercel = bool(os.environ.get("VERCEL") or os.environ.get("VERCEL_ENV"))
    if not app.config["TESTING"]:
        if (on_vercel or os.environ.get("DATABASE_URL")) and not os.environ.get("SECRET_KEY"):
            raise RuntimeError("SECRET_KEY must be set for production.")
        if on_vercel and not os.environ.get("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL must be set on Vercel.")
        if on_vercel and not app.config["BLOB_READ_WRITE_TOKEN"]:
            raise RuntimeError("BLOB_READ_WRITE_TOKEN must be set on Vercel.")

    storage = (BlobImageStorage()
               if app.config["BLOB_READ_WRITE_TOKEN"]
               else LocalImageStorage(app.config["UPLOAD_FOLDER"]))
    app.extensions["image_storage"] = storage
    db.init_app(app)
    login_manager.init_app(app)
    with app.app_context():
        db.create_all()

    @app.context_processor
    def template_helpers():
        def csrf_token():
            if "csrf_token" not in session:
                session["csrf_token"] = secrets.token_hex(32)
            return session["csrf_token"]

        return {"csrf_token": csrf_token, "categories": CATEGORIES,
                "listing_image_url": listing_image_url}

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            expected = session.get("csrf_token", "")
            submitted = request.form.get("csrf_token", "")
            if not expected or not secrets.compare_digest(expected, submitted):
                abort(400, description="Invalid form token. Refresh the page and try again.")

    @app.route("/")
    def browse():
        listings = db.session.execute(db.select(Listing).order_by(Listing.id.desc())).scalars().all()
        return render_template("browse.html", listings=listings)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("browse"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            if not UNM_EMAIL.fullmatch(email):
                flash("Register with a valid @unm.edu email address.", "danger")
            elif len(password) < 8:
                flash("Password must be at least 8 characters.", "danger")
            elif db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none():
                flash("That email is already registered.", "danger")
            else:
                user = User(email=email, password_hash=generate_password_hash(password))
                db.session.add(user)
                db.session.commit()
                login_user(user)
                flash("Your account is ready.", "success")
                return redirect(url_for("browse"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("browse"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = db.session.execute(db.select(User).filter_by(email=email)).scalar_one_or_none()
            if user and check_password_hash(user.password_hash, request.form.get("password", "")):
                login_user(user)
                return redirect(url_for("browse"))
            flash("Incorrect email or password.", "danger")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        return redirect(url_for("browse"))

    @app.route("/listings/new", methods=["GET", "POST"])
    @login_required
    def create_listing():
        if request.method == "POST":
            title = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            category = request.form.get("category", "")
            price_text = request.form.get("price", "").strip()
            image = request.files.get("image")
            try:
                price = Decimal(price_text)
                valid_price = price.is_finite() and Decimal("0") <= price <= Decimal("99999999.99") and price.as_tuple().exponent >= -2
            except InvalidOperation:
                valid_price = False

            if not (1 <= len(title) <= 120 and 1 <= len(description) <= 5000):
                flash("Add a title (up to 120 characters) and description (up to 5,000 characters).", "danger")
            elif category not in CATEGORIES:
                flash("Choose a category.", "danger")
            elif not valid_price:
                flash("Enter a valid price with no more than two decimal places.", "danger")
            elif not image or not image.filename:
                flash("Choose an image for your listing.", "danger")
            else:
                try:
                    image_bytes, image_format = read_listing_image(image)
                except ValueError:
                    flash("Upload a JPEG, PNG, WebP, or GIF image.", "danger")
                else:
                    try:
                        filename = storage.save(
                            image_bytes, IMAGE_FORMATS[image_format], IMAGE_MIME_TYPES[image_format],
                        )
                    except Exception:
                        app.logger.warning("Could not save listing image")
                        flash("Could not upload the listing image. Please try again.", "danger")
                        return render_template("create_listing.html")
                    listing = Listing(
                        title=title, description=description, category=category,
                        price=price, image_filename=filename, seller_id=current_user.id,
                    )
                    db.session.add(listing)
                    db.session.commit()
                    flash("Listing posted.", "success")
                    return redirect(url_for("listing_detail", listing_id=listing.id))
        return render_template("create_listing.html")

    @app.post("/listings/suggest")
    @login_required
    def suggest_listing():
        try:
            image_bytes, image_format = read_listing_image(request.files.get("image"))
        except ValueError:
            return jsonify(error=AI_ERROR_MESSAGE), 400
        seller_input = {
            "title": request.form.get("title", "").strip()[:120],
            "description": request.form.get("description", "").strip()[:5000],
            "category": request.form.get("category", "").strip(),
            "price": request.form.get("price", "").strip()[:30],
        }
        if seller_input["category"] not in CATEGORIES:
            seller_input["category"] = ""
        try:
            suggestions = generate_listing_suggestions(
                image_bytes, IMAGE_MIME_TYPES[image_format], CATEGORIES,
                seller_input=seller_input,
            )
        except AISuggestionError as exc:
            messages = {
                "busy": AI_BUSY_MESSAGE,
                "rate_limit": AI_LIMIT_MESSAGE,
                "missing_key": AI_SETUP_MESSAGE,
                "connection": AI_CONNECTION_MESSAGE,
                "auth": AI_AUTH_MESSAGE,
            }
            return jsonify(error=messages.get(exc.reason, AI_ERROR_MESSAGE)), 503
        return jsonify(suggestions)

    @app.post("/listings/analyze")
    @login_required
    def analyze_listing():
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "")
        try:
            price = Decimal(request.form.get("price", "").strip())
            valid_price = price.is_finite() and Decimal("0") <= price <= Decimal("99999999.99") and price.as_tuple().exponent >= -2
        except InvalidOperation:
            valid_price = False
        if not (1 <= len(title) <= 120 and 1 <= len(description) <= 5000 and category in CATEGORIES and valid_price):
            return jsonify(error="Complete the listing fields before reviewing."), 400
        try:
            image_bytes, image_format = read_listing_image(request.files.get("image"))
        except ValueError:
            return jsonify(error="Choose a valid listing photo before reviewing."), 400

        review = analyze_listing_safety(
            title, description, category, price,
            image_bytes, IMAGE_MIME_TYPES[image_format],
            request.form.get("original_estimated_price_low", ""),
            request.form.get("original_estimated_price_high", ""),
        )
        return jsonify(review)

    @app.get("/listings/<int:listing_id>")
    def listing_detail(listing_id):
        listing = db.get_or_404(Listing, listing_id)
        return render_template("listing_detail.html", listing=listing)

    @app.post("/listings/<int:listing_id>/delete")
    @login_required
    def delete_listing(listing_id):
        listing = db.get_or_404(Listing, listing_id)
        if listing.seller_id != current_user.id:
            abort(403)

        image_filename = listing.image_filename
        db.session.delete(listing)
        db.session.commit()

        try:
            storage.delete(image_filename)
        except Exception:
            app.logger.warning("Could not remove image for deleted listing %s", listing_id)

        flash("Listing deleted.", "success")
        return redirect(url_for("browse"))

    @app.get("/uploads/<path:filename>")
    def uploaded_image(filename):
        return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

    @app.errorhandler(413)
    def image_too_large(error):
        if request.path in (url_for("suggest_listing"), url_for("analyze_listing")):
            return jsonify(error=AI_ERROR_MESSAGE), 413
        return render_template("error.html", message="Upload an image smaller than 5 MB."), 413

    return app


app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
