import io
import json
import os
import re
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from ai_service import analyze_listing_safety
from app import Listing, create_app, db


def sample_photo():
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(data, format="PNG")
    data.seek(0)
    return data


def mocked_groq_signals(signals):
    completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(signals)))])
    return completion


class SafetyServiceTest(unittest.TestCase):
    def analyze_with_signals(self, signals, **overrides):
        values = {
            "title": "Desk lamp",
            "description": "A used desk lamp in good working order.",
            "category": "Furniture",
            "price": Decimal("15.00"),
            "image_bytes": b"image bytes",
            "mime_type": "image/png",
        }
        values.update(overrides)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                request = client.return_value.__enter__.return_value.chat.completions.create
                request.return_value = mocked_groq_signals(signals)
                result = analyze_listing_safety(**values)
                kwargs = request.call_args.kwargs
        return result, kwargs

    def test_normal_listing_has_no_major_warning(self):
        result, call = self.analyze_with_signals({
            "suspicious_language": False,
            "spam_like": False,
            "image_personal_info": False,
        }, estimated_price_low="10.00", estimated_price_high="20.00")
        self.assertEqual(result["risk_level"], "low")
        self.assertEqual(result["warnings"], [])
        self.assertFalse(result["ai_unavailable"])
        self.assertIs(call["response_format"]["json_schema"]["strict"], True)
        self.assertEqual(call["response_format"]["json_schema"]["name"], "listing_safety_signals")

    def test_suspicious_and_spam_like_listing_warns_without_accusation(self):
        result, _ = self.analyze_with_signals({
            "suspicious_language": True,
            "spam_like": True,
            "image_personal_info": False,
        }, description="Pay immediately to reserve. Buy now buy now buy now!")
        self.assertEqual(len(result["warnings"]), 2)
        self.assertTrue(any("caution" in item for item in result["warnings"]))
        self.assertTrue(any("spam" in item for item in result["warnings"]))
        self.assertNotIn("definitely", " ".join(result["warnings"] + result["reasons"]).lower())

    def test_personal_information_is_flagged_and_redacted_from_prompt(self):
        description = "Call 505-555-0123 or me@example.com. Pickup at 123 Main St. Student ID: 123456789."
        result, call = self.analyze_with_signals({
            "suspicious_language": False,
            "spam_like": False,
            "image_personal_info": True,
        }, description=description)
        combined = " ".join(result["warnings"])
        for label in ("phone number", "email address", "street address", "student ID", "photo"):
            self.assertIn(label, combined)
        prompt = call["messages"][0]["content"][0]["text"]
        for private_value in ("505-555-0123", "me@example.com", "123 Main St", "123456789"):
            self.assertNotIn(private_value, prompt)

    def test_price_far_from_ai_guidance_warns(self):
        result, _ = self.analyze_with_signals({
            "suspicious_language": False,
            "spam_like": False,
            "image_personal_info": False,
        }, price=Decimal("100.00"), estimated_price_low="10.00", estimated_price_high="20.00")
        self.assertTrue(any("price" in item for item in result["warnings"]))
        self.assertFalse(result["ai_unavailable"])

    def test_current_edited_description_flags_phone_number(self):
        result, _ = self.analyze_with_signals({
            "suspicious_language": False,
            "spam_like": False,
            "image_personal_info": False,
        }, description="Call me at 505-555-0123 if interested.")
        self.assertTrue(any("phone number" in warning for warning in result["warnings"]))

    def test_current_edited_description_flags_advance_payment(self):
        result, _ = self.analyze_with_signals({
            "suspicious_language": False,
            "spam_like": False,
            "image_personal_info": False,
        }, description="Need payment immediately. Zelle only. Pay before pickup.")
        self.assertTrue(any("caution" in warning for warning in result["warnings"]))


class SafetyWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(Path(self.temp.name) / "uploads"),
        })
        self.client = self.app.test_client()
        self.client.post("/register", data={
            "csrf_token": self.csrf("/register"),
            "email": "safety@unm.edu",
            "password": "password123",
        })

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()
            db.engine.dispose()
        self.temp.cleanup()

    def csrf(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        token = re.search(rb'name="csrf_token" value="([a-f0-9]+)"', response.data)
        self.assertIsNotNone(token)
        return token.group(1).decode()

    def test_ai_failure_still_allows_manual_publish(self):
        form = self.client.get("/listings/new").data
        self.assertIn(b'data-analyze-url=', form)
        self.assertIn(b'id="safety-publish"', form)
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                client.return_value.__enter__.return_value.chat.completions.create.side_effect = RuntimeError("offline")
                response = self.client.post("/listings/analyze", data={
                    "csrf_token": self.csrf("/listings/new"),
                    "title": "Manual lamp",
                    "description": "Call 505-555-0123 for details.",
                    "category": "Furniture",
                    "price": "12.00",
                    "image": (sample_photo(), "lamp.png"),
                }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["ai_unavailable"])
        self.assertTrue(any("phone number" in item for item in response.get_json()["warnings"]))
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)

        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": "Manual lamp",
            "description": "A used lamp.",
            "category": "Furniture",
            "price": "12.00",
            "image": (sample_photo(), "lamp.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 1)

    def test_review_uses_edited_fields_and_original_ai_price_range(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                request = client.return_value.__enter__.return_value.chat.completions.create
                request.return_value = mocked_groq_signals({
                    "suspicious_language": False,
                    "spam_like": False,
                    "image_personal_info": False,
                })
                response = self.client.post("/listings/analyze", data={
                    "csrf_token": self.csrf("/listings/new"),
                    "title": "Edited mattress title",
                    "description": "Call me at 505-555-0123 if interested. Need payment immediately. Zelle only. Pay before pickup.",
                    "category": "Furniture",
                    "price": "1000.00",
                    "estimated_price_low": "900.00",
                    "estimated_price_high": "1100.00",
                    "original_estimated_price_low": "300.00",
                    "original_estimated_price_high": "400.00",
                    "image": (sample_photo(), "mattress.png"),
                }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)
        warnings = " ".join(response.get_json()["warnings"])
        self.assertIn("phone number", warnings)
        self.assertIn("caution", warnings)
        self.assertIn("asking price", warnings)
        prompt = request.call_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("Edited mattress title", prompt)
        self.assertIn("Asking price: $1000.00", prompt)
        self.assertIn("Need payment immediately", prompt)
        self.assertNotIn("505-555-0123", prompt)
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)


if __name__ == "__main__":
    unittest.main()
