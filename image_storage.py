"""Local or Vercel Blob storage for public listing photos."""

import re
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from flask import url_for


LOCAL_IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.(?:jpg|png|webp|gif)")
BLOB_IMAGE_PATH = re.compile(r"/listings/[0-9a-f]{32}\.(?:jpg|png|webp|gif)")
BLOB_HOST = re.compile(r"[a-z0-9-]+\.public\.blob\.vercel-storage\.com")


def is_listing_blob_url(value):
    """Accept only URLs shaped like this app's public listing photos."""
    try:
        parts = urlsplit(value)
        return bool(
            parts.scheme == "https"
            and parts.hostname
            and BLOB_HOST.fullmatch(parts.hostname)
            and not parts.port
            and not parts.username
            and not parts.password
            and BLOB_IMAGE_PATH.fullmatch(parts.path)
            and not parts.query
            and not parts.fragment
        )
    except (TypeError, ValueError):
        return False


def listing_image_url(image_reference):
    """Render both existing local filenames and new public Blob URLs."""
    if is_listing_blob_url(image_reference):
        return image_reference
    return url_for("uploaded_image", filename=image_reference)


class LocalImageStorage:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)

    def save(self, image_bytes, extension, content_type):
        filename = f"{uuid4().hex}{extension}"
        (self.folder / filename).write_bytes(image_bytes)
        return filename

    def delete(self, image_reference):
        if not LOCAL_IMAGE_NAME.fullmatch(image_reference):
            return
        upload_dir = self.folder.resolve()
        image_path = upload_dir / image_reference
        if image_path.is_file() and not image_path.is_symlink() and image_path.resolve().parent == upload_dir:
            image_path.unlink()


class BlobImageStorage:
    def save(self, image_bytes, extension, content_type):
        from vercel.blob.sync import put_blob

        pathname = f"listings/{uuid4().hex}{extension}"
        blob = put_blob(pathname, image_bytes, access="public", content_type=content_type)
        if not is_listing_blob_url(blob.url) or urlsplit(blob.url).path != f"/{pathname}":
            raise ValueError("Blob storage returned an invalid listing image URL")
        return blob.url

    def delete(self, image_reference):
        if not is_listing_blob_url(image_reference):
            return
        from vercel.blob.sync import delete_blob

        delete_blob(image_reference)
