import base64
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from ai_service import AISuggestionError, ListingSuggestions, generate_listing_suggestions
from app import CATEGORIES, Listing, create_app, db


def sample_photo():
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(data, format="PNG")
    data.seek(0)
    return data


class AISuggestionFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(Path(self.temp.name) / "uploads"),
        })
        self.client = self.app.test_client()
        token = self.csrf("/register")
        self.client.post("/register", data={
            "csrf_token": token,
            "email": "student@unm.edu",
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
        match = re.search(rb'name="csrf_token" value="([a-f0-9]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_suggestions_return_editable_values_without_publishing(self):
        suggestions = {
            "title": "Desk lamp",
            "description": "Small blue desk lamp.",
            "category": "Furniture",
            "estimated_price_low": "8.00",
            "estimated_price_high": "14.00",
        }
        form = self.client.get("/listings/new").data
        self.assertIn(b'id="generate-ai"', form)
        self.assertIn(b'id="estimated-price-low"', form)
        self.assertIn(b'listing_ai.js', form)

        with patch("app.generate_listing_suggestions", return_value=suggestions) as mocked:
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (sample_photo(), "lamp.png"),
            }, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), suggestions)
        self.assertEqual(mocked.call_args.args[1:], ("image/png", CATEGORIES))
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)

        # The user's edited values, not the AI response, are published only on submit.
        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": "Edited lamp title",
            "description": "My own description.",
            "category": "Electronics",
            "price": "12.50",
            "estimated_price_low": "7.00",
            "estimated_price_high": "15.00",
            "image": (sample_photo(), "lamp.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            listing = db.session.query(Listing).one()
            self.assertEqual(listing.title, "Edited lamp title")
            self.assertEqual(listing.category, "Electronics")
            self.assertEqual(str(listing.price), "12.50")

    def test_suggestion_request_includes_current_seller_fields(self):
        suggestions = {
            "title": "Office chair",
            "description": "Office chair barely used for two weeks.",
            "category": "Furniture",
            "estimated_price_low": "75.00",
            "estimated_price_high": "110.00",
        }
        with patch("app.generate_listing_suggestions", return_value=suggestions) as mocked:
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (sample_photo(), "chair.png"),
                "title": "Office chair",
                "description": "Office chair barely used for 2 weeks.",
                "category": "Furniture",
                "price": "50.00",
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_args.kwargs["seller_input"], {
            "title": "Office chair",
            "description": "Office chair barely used for 2 weeks.",
            "category": "Furniture",
            "price": "50.00",
        })

    def test_api_failure_keeps_manual_listing_available(self):
        with patch("app.generate_listing_suggestions", side_effect=AISuggestionError("timeout")):
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (sample_photo(), "lamp.png"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 503)
        self.assertIn("continue creating the listing manually", response.get_json()["error"])
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)

        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": "Manual lamp",
            "description": "Works well.",
            "category": "Furniture",
            "price": "10.00",
            "image": (sample_photo(), "lamp.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Manual lamp", response.data)

    def test_invalid_image_does_not_call_ai_service(self):
        with patch("app.generate_listing_suggestions") as mocked:
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (io.BytesIO(b"bad image"), "bad.png"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 400)
        mocked.assert_not_called()

    def test_busy_model_explains_retry_without_publishing(self):
        with patch("app.generate_listing_suggestions", side_effect=AISuggestionError("busy", reason="busy")):
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (sample_photo(), "lamp.png"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 503)
        self.assertIn("AI service is busy right now", response.get_json()["error"])
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)

    def test_missing_groq_key_gives_setup_message(self):
        with patch("app.generate_listing_suggestions", side_effect=AISuggestionError("missing", reason="missing_key")):
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (sample_photo(), "lamp.png"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Groq API key", response.get_json()["error"])

    def test_connection_failure_explains_problem(self):
        with patch("app.generate_listing_suggestions", side_effect=AISuggestionError("connection", reason="connection")):
            response = self.client.post("/listings/suggest", data={
                "csrf_token": self.csrf("/listings/new"),
                "image": (sample_photo(), "lamp.png"),
            }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 503)
        self.assertIn("could not connect to Groq", response.get_json()["error"])


class AIServiceValidationTest(unittest.TestCase):
    def test_schema_closes_extra_properties_for_groq_strict_mode(self):
        self.assertIs(ListingSuggestions.model_json_schema()["additionalProperties"], False)

    def test_missing_key_fails_before_any_api_call(self):
        with patch.dict(os.environ, {"GROQ_API_KEY": ""}), patch("ai_service.Groq") as client:
            with self.assertRaises(AISuggestionError):
                generate_listing_suggestions(b"image bytes", "image/png", CATEGORIES)
            client.assert_not_called()

    def test_malformed_groq_data_is_rejected(self):
        malformed = {
            "title": "Lamp",
            "description": "Blue lamp",
            "category": "Not a marketplace category",
            "estimated_price_low": 20,
            "estimated_price_high": 10,
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only", "GROQ_MODEL": "test-model"}):
            with patch("ai_service.Groq") as client:
                completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(malformed)))])
                client.return_value.__enter__.return_value.chat.completions.create.return_value = completion
                with self.assertRaises(AISuggestionError):
                    generate_listing_suggestions(b"image bytes", "image/png", CATEGORIES)
                call = client.return_value.__enter__.return_value.chat.completions.create.call_args
                self.assertEqual(call.kwargs["model"], "test-model")
                self.assertIs(call.kwargs["response_format"]["json_schema"]["strict"], True)
                self.assertEqual(call.kwargs["response_format"]["json_schema"]["schema"]["properties"]["category"]["enum"], list(CATEGORIES))
                image_url = call.kwargs["messages"][0]["content"][1]["image_url"]["url"]
                self.assertTrue(image_url.startswith("data:image/png;base64,"))
                self.assertEqual(base64.b64decode(image_url.split(",", 1)[1]), b"image bytes")

    def test_valid_groq_response_is_validated_and_formatted(self):
        valid = {
            "title": "  Desk lamp  ",
            "description": "  Blue lamp.  ",
            "category": "Furniture",
            "estimated_price_low": 8,
            "estimated_price_high": 14.5,
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                completion = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(valid)))])
                client.return_value.__enter__.return_value.chat.completions.create.return_value = completion
                result = generate_listing_suggestions(b"image bytes", "image/png", CATEGORIES)
        self.assertEqual(result, {
            "title": "Desk lamp",
            "description": "Blue lamp.",
            "category": "Furniture",
            "estimated_price_low": "8.00",
            "estimated_price_high": "14.50",
        })

    def test_prompt_preserves_seller_facts_without_claiming_visual_verification(self):
        suggestion = {
            "title": "Black mesh office chair",
            "description": "Black mesh office chair, barely used for two weeks.",
            "category": "Furniture",
            "estimated_price_low": 75,
            "estimated_price_high": 110,
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                client.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(suggestion)))],
                )
                result = generate_listing_suggestions(
                    b"image bytes", "image/png", CATEGORIES,
                    seller_input={
                        "title": "Office chair",
                        "description": "Office chair barely used for 2 weeks.",
                        "category": "Furniture",
                        "price": "50.00",
                    },
                )
                prompt = client.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("Office chair barely used for 2 weeks.", prompt)
        self.assertIn('"price": "50.00"', prompt)
        self.assertIn("seller-provided item facts as primary", prompt)
        self.assertIn("Do not claim that seller-provided facts were visually verified", prompt)
        self.assertIn("Write the description as natural listing copy", prompt)
        self.assertIn("Avoid phrases such as 'the seller notes'", prompt)
        self.assertIn("barely used for two weeks", result["description"])

    def test_generated_description_removes_third_person_seller_attribution(self):
        suggestion = {
            "title": "Black mesh office chair",
            "description": (
                "Black mesh office chair with cushioned arms. "
                "The seller notes the chair is only three weeks old and in like-new condition."
            ),
            "category": "Furniture",
            "estimated_price_low": 75,
            "estimated_price_high": 110,
        }
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                client.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(suggestion)))],
                )
                result = generate_listing_suggestions(b"image bytes", "image/png", CATEGORIES)
        self.assertIn("The chair is only three weeks old and in like-new condition.", result["description"])
        self.assertNotIn("the seller notes", result["description"].lower())

    def test_sdk_503_is_classified_as_busy(self):
        failure = RuntimeError("high demand")
        failure.status_code = 503
        with patch.dict(os.environ, {"GROQ_API_KEY": "test-only"}):
            with patch("ai_service.Groq") as client:
                client.return_value.__enter__.return_value.chat.completions.create.side_effect = failure
                with self.assertRaises(AISuggestionError) as caught:
                    generate_listing_suggestions(b"image bytes", "image/png", CATEGORIES)
        self.assertEqual(caught.exception.reason, "busy")


if __name__ == "__main__":
    unittest.main()
