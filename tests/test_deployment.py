"""Configuration and storage adapter checks; no external services are called."""

import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from app import Listing, create_app, database_uri_from_environment, db
from image_storage import BlobImageStorage, LocalImageStorage


class DeploymentConfigTest(unittest.TestCase):
    def test_sqlite_without_database_url(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(database_uri_from_environment(), "sqlite:///lobo_exchange.db")

    def test_postgres_url_forms_use_psycopg(self):
        for prefix in ("postgres://", "postgresql://", "postgresql+psycopg://"):
            with self.subTest(prefix=prefix), patch.dict(os.environ, {
                "DATABASE_URL": prefix + "user:password@host/db?sslmode=require",
            }, clear=True):
                self.assertEqual(
                    database_uri_from_environment(),
                    "postgresql+psycopg://user:password@host/db?sslmode=require",
                )

    def test_vercel_requires_production_configuration(self):
        with patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SECRET_KEY"):
                create_app()
        with patch.dict(os.environ, {"VERCEL": "1", "SECRET_KEY": "configured"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL"):
                create_app()
        with patch.dict(os.environ, {
            "VERCEL": "1", "SECRET_KEY": "configured",
            "DATABASE_URL": "postgresql://user:password@host/db",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BLOB_READ_WRITE_TOKEN"):
                create_app()

    def test_local_upload_mode_and_static_assets(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            app = create_app({
                "TESTING": True,
                "SECRET_KEY": "test-secret",
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "UPLOAD_FOLDER": str(Path(temp) / "uploads"),
            })
            self.assertIsInstance(app.extensions["image_storage"], LocalImageStorage)
            static_response = app.test_client().get("/static/style.css")
            self.assertEqual(static_response.status_code, 200)
            static_response.close()
            with app.app_context():
                db.session.remove()
                db.drop_all()
                db.engine.dispose()


class BlobWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "UPLOAD_FOLDER": str(Path(self.temp.name) / "uploads"),
            "BLOB_READ_WRITE_TOKEN": "mock-token",
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
        return re.search(rb'name="csrf_token" value="([a-f0-9]+)"', response.data).group(1).decode()

    def image(self):
        data = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(data, format="PNG")
        data.seek(0)
        return data

    @patch("vercel.blob.sync.delete_blob")
    @patch("vercel.blob.sync.put_blob")
    def test_cloud_image_renders_and_is_deleted(self, put_blob, delete_blob):
        self.assertIsInstance(self.app.extensions["image_storage"], BlobImageStorage)
        put_blob.side_effect = lambda path, body, **kwargs: SimpleNamespace(
            url="https://test-store.public.blob.vercel-storage.com/" + path,
        )
        self.client.post("/register", data={
            "csrf_token": self.csrf("/register"),
            "email": "seller@unm.edu", "password": "password123",
        })
        response = self.client.post("/listings/new", data={
            "csrf_token": self.csrf("/listings/new"),
            "title": "Lamp", "description": "Good condition", "category": "Furniture",
            "price": "25", "image": (self.image(), "lamp.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(put_blob.call_count, 1)
        self.assertEqual(put_blob.call_args.kwargs["access"], "public")
        with self.app.app_context():
            listing = db.session.query(Listing).one()
            image_url = listing.image_filename
            listing_id = listing.id
        self.assertIn(image_url.encode(), response.data)
        self.assertIn(image_url.encode(), self.client.get("/").data)
        self.assertFalse((Path(self.app.config["UPLOAD_FOLDER"]) / "listings").exists())

        delete_response = self.client.post(f"/listings/{listing_id}/delete", data={
            "csrf_token": self.csrf(f"/listings/{listing_id}"),
        }, follow_redirects=True)
        self.assertIn(b"Listing deleted.", delete_response.data)
        delete_blob.assert_called_once_with(image_url)
        with self.app.app_context():
            self.assertEqual(db.session.query(Listing).count(), 0)

    @patch("vercel.blob.sync.delete_blob")
    def test_blob_deletion_ignores_unrelated_url(self, delete_blob):
        storage = self.app.extensions["image_storage"]
        storage.delete("https://other.example.com/listings/" + "a" * 32 + ".png")
        storage.delete("https://test-store.public.blob.vercel-storage.com/other/" + "a" * 32 + ".png")
        delete_blob.assert_not_called()


if __name__ == "__main__":
    unittest.main()
