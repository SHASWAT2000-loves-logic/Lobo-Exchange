import io
import re
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app import Listing, User, create_app, db


class MarketplaceFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(Path(self.temp.name) / "uploads"),
        })
        self.client = self.app.test_client()

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

    def test_registration_and_listing_flow(self):
        response = self.client.post("/register", data={
            "csrf_token": self.csrf("/register"),
            "email": "student@example.com",
            "password": "password123",
        })
        self.assertIn(b"@unm.edu", response.data)
        with self.app.app_context():
            self.assertEqual(db.session.query(User).count(), 0)

        response = self.client.post("/register", data={
            "csrf_token": self.csrf("/register"),
            "email": "Student@UNM.edu",
            "password": "password123",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Your account is ready", response.data)
        with self.app.app_context():
            user = db.session.query(User).one()
            self.assertEqual(user.email, "student@unm.edu")
            self.assertNotEqual(user.password_hash, "password123")

        image_data = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_data, format="PNG")
        image_data.seek(0)
        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": "Calculus textbook",
            "description": "Used for one semester.",
            "category": "Books",
            "price": "18.50",
            "image": (image_data, "book.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Calculus textbook", response.data)
        self.assertIn(b"$18.50", response.data)

        with self.app.app_context():
            listing = db.session.query(Listing).one()
            self.assertEqual(listing.seller.email, "student@unm.edu")
            self.assertTrue((Path(self.app.config["UPLOAD_FOLDER"]) / listing.image_filename).is_file())
            image_url = f"/uploads/{listing.image_filename}"
        image_response = self.client.get(image_url)
        self.assertEqual(image_response.status_code, 200)
        image_response.close()
        self.assertIn(b"Calculus textbook", self.client.get("/").data)

        self.client.post("/logout", data={"csrf_token": self.csrf("/")})
        self.assertEqual(self.client.get("/listings/new").status_code, 302)
        response = self.client.post("/login", data={
            "csrf_token": self.csrf("/login"),
            "email": "student@unm.edu",
            "password": "password123",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Post a listing", response.data)

    def test_rejects_invalid_image_and_missing_csrf(self):
        self.client.post("/register", data={
            "csrf_token": self.csrf("/register"),
            "email": "student@unm.edu",
            "password": "password123",
        })
        self.assertEqual(self.client.post("/logout").status_code, 400)
        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": "Fake image",
            "description": "Testing upload validation",
            "category": "Other",
            "price": "5.00",
            "image": (io.BytesIO(b"not an image"), "fake.png"),
        }, content_type="multipart/form-data")
        self.assertIn(b"Upload a JPEG", response.data)
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)


class ListingDeletionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(Path(self.temp.name) / "uploads"),
        })
        self.client = self.app.test_client()
        self.register("owner@unm.edu")
        self.listing_id, self.image_path = self.create_listing("Owner's desk lamp")
        self.delete_url = f"/listings/{self.listing_id}/delete"

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

    def register(self, email):
        response = self.client.post("/register", data={
            "csrf_token": self.csrf("/register"),
            "email": email,
            "password": "password123",
        })
        self.assertEqual(response.status_code, 302)

    def create_listing(self, title):
        image_data = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_data, format="PNG")
        image_data.seek(0)
        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": title,
            "description": "Works well.",
            "category": "Furniture",
            "price": "15.00",
            "image": (image_data, "lamp.png"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            listing = db.session.query(Listing).filter_by(title=title).one()
            image_path = Path(self.app.config["UPLOAD_FOLDER"]) / listing.image_filename
            self.assertTrue(image_path.is_file())
            return listing.id, image_path

    def test_owner_can_delete_listing_and_image(self):
        detail = self.client.get(f"/listings/{self.listing_id}")
        self.assertIn(b"Delete listing", detail.data)
        self.assertIn(b"Are you sure you want to delete this listing?", detail.data)
        self.assertIn(b'method="post"', detail.data)
        self.assertEqual(self.client.get(self.delete_url).status_code, 405)

        response = self.client.post(self.delete_url, data={
            "csrf_token": self.csrf(f"/listings/{self.listing_id}"),
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Listing deleted.", response.data)
        self.assertEqual(response.request.path, "/")
        with self.app.app_context():
            self.assertIsNone(db.session.get(Listing, self.listing_id))
        self.assertFalse(self.image_path.exists())

    def test_non_owner_cannot_delete_listing(self):
        self.client.post("/logout", data={"csrf_token": self.csrf("/")})
        self.register("other@unm.edu")
        detail = self.client.get(f"/listings/{self.listing_id}")
        self.assertNotIn(b"Delete listing", detail.data)

        response = self.client.post(self.delete_url, data={
            "csrf_token": self.csrf("/"),
        })
        self.assertEqual(response.status_code, 403)
        with self.app.app_context():
            self.assertIsNotNone(db.session.get(Listing, self.listing_id))
        self.assertTrue(self.image_path.is_file())

    def test_deleted_listing_disappears_from_browse_and_detail(self):
        other_id, other_image = self.create_listing("Keep this chair")
        self.client.post(self.delete_url, data={
            "csrf_token": self.csrf(f"/listings/{self.listing_id}"),
        })
        browse = self.client.get("/")
        self.assertNotIn(b"Owner&#39;s desk lamp", browse.data)
        self.assertIn(b"Keep this chair", browse.data)
        self.assertEqual(self.client.get(f"/listings/{self.listing_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/listings/{other_id}").status_code, 200)
        self.assertTrue(other_image.is_file())

    def test_unsafe_image_path_is_not_deleted(self):
        outside_image = Path(self.temp.name) / "outside.png"
        outside_image.write_bytes(b"keep me")
        with self.app.app_context():
            listing = db.session.get(Listing, self.listing_id)
            listing.image_filename = "../outside.png"
            db.session.commit()
        response = self.client.post(self.delete_url, data={
            "csrf_token": self.csrf("/"),
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(outside_image.is_file())


if __name__ == "__main__":
    unittest.main()
