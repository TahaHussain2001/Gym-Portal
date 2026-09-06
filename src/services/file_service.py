import os
import re
import uuid
import logging
import requests
from typing import Tuple, Optional
from fastapi import UploadFile, HTTPException
from src.config.app_config import (
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_STORAGE_BUCKET
)

logger = logging.getLogger("sthxtechnologies-file")

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB

MAGIC_BYTES_MAP = {
    b"\xFF\xD8\xFF": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"RIFF": "image/webp"
}

FORBIDDEN_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".sh", ".php", ".py", ".js", ".html", ".htm",
    ".dll", ".so", ".vbs", ".ps1", ".jar", ".msi", ".cgi", ".pl"
}

MANAGED_CATEGORIES = {"profile-avatars", "gym-logos", "backgrounds", "avatars", "logos"}

class FileSecurityService:
    @staticmethod
    def validate_file_signature(content: bytes) -> str:
        for magic, mime in MAGIC_BYTES_MAP.items():
            if content.startswith(magic):
                return mime
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        raise HTTPException(
            status_code=400,
            detail="File security validation failed: Invalid file header signature or unapproved file format."
        )

    @staticmethod
    def validate_file_content(file: UploadFile, content: bytes) -> Tuple[str, str]:
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="No file uploaded.")

        if ".." in file.filename or "/" in file.filename or "\\" in file.filename:
            raise HTTPException(status_code=400, detail="Path traversal attempt detected in filename.")

        raw_filename = os.path.basename(file.filename)

        _, ext = os.path.splitext(raw_filename)
        ext = ext.lower().strip()

        if ext in FORBIDDEN_EXTENSIONS or ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File extension '{ext}' is forbidden.")

        if file.content_type and file.content_type.lower() not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=400, detail=f"MIME type '{file.content_type}' is not allowed.")

        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"File size exceeds 5MB limit ({len(content) / (1024 * 1024):.2f}MB)."
            )

        detected_mime = FileSecurityService.validate_file_signature(content)
        return detected_mime, ext

    @staticmethod
    def ensure_bucket_exists(bucket_name: str = SUPABASE_STORAGE_BUCKET) -> bool:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
            return False

        url = f"{SUPABASE_URL}/storage/v1/bucket"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "application/json"
        }
        try:
            res = requests.post(
                url,
                headers=headers,
                json={"id": bucket_name, "name": bucket_name, "public": True},
                timeout=10
            )
            return res.status_code in [200, 201, 400, 409]
        except Exception as e:
            logger.warning(f"Bucket existence check error: {e}")
            return False

    @staticmethod
    def upload_to_supabase(content: bytes, storage_path: str, mime_type: str) -> str:
        bucket = SUPABASE_STORAGE_BUCKET or "gym-assets"
        base_url = SUPABASE_URL

        if not base_url or not SUPABASE_SERVICE_ROLE_KEY:
            logger.warning("[MOCK/DEV] Supabase credentials not set. Returning mock storage URL.")
            return f"{base_url or 'https://mock-supabase.co'}/storage/v1/object/public/{bucket}/{storage_path}"

        FileSecurityService.ensure_bucket_exists(bucket)

        upload_url = f"{base_url}/storage/v1/object/{bucket}/{storage_path}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": mime_type,
            "x-upsert": "true"
        }

        try:
            res = requests.post(upload_url, headers=headers, data=content, timeout=15)
            if res.status_code in [200, 201]:
                public_url = f"{base_url}/storage/v1/object/public/{bucket}/{storage_path}"
                logger.info(f"Successfully uploaded asset to Supabase Storage: {storage_path}")
                return public_url
            else:
                logger.error(f"Supabase Storage API upload error ({res.status_code}): {res.text}")
                raise HTTPException(status_code=500, detail=f"Failed to upload asset to Supabase Storage: HTTP {res.status_code}")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Exception during Supabase Storage upload: {e}")
            raise HTTPException(status_code=500, detail="Failed to connect to Supabase Storage service.")

    @staticmethod
    def delete_managed_object(object_url_or_path: str) -> bool:
        if not object_url_or_path or not isinstance(object_url_or_path, str):
            return False

        if "sthx_technologies_logo" in object_url_or_path or "portal_background" in object_url_or_path:
            return False

        bucket = SUPABASE_STORAGE_BUCKET or "gym-assets"
        base_url = SUPABASE_URL

        rel_path = ""
        if f"/storage/v1/object/public/{bucket}/" in object_url_or_path:
            rel_path = object_url_or_path.split(f"/storage/v1/object/public/{bucket}/")[-1]
        elif object_url_or_path.startswith(tuple(f"{cat}/" for cat in MANAGED_CATEGORIES)):
            rel_path = object_url_or_path

        if not rel_path:
            return False

        category = rel_path.split("/")[0]
        if category not in MANAGED_CATEGORIES:
            return False

        if not base_url or not SUPABASE_SERVICE_ROLE_KEY:
            logger.info(f"[MOCK/DEV] Would delete Supabase Storage managed object: {rel_path}")
            return True

        delete_url = f"{base_url}/storage/v1/object/{bucket}"
        headers = {
            "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Content-Type": "application/json"
        }

        try:
            res = requests.delete(delete_url, headers=headers, json={"prefixes": [rel_path]}, timeout=10)
            return res.status_code in [200, 201, 204]
        except Exception as e:
            logger.warning(f"Failed to delete old Supabase Storage object {rel_path}: {e}")
            return False

    @staticmethod
    async def validate_and_save_upload(file: UploadFile, upload_dir: str = "static/uploads") -> str:
        content = await file.read()
        mime_type, ext = FileSecurityService.validate_file_content(file, content)

        category = "profile-avatars"
        if "logo" in upload_dir.lower() or "branding" in upload_dir.lower():
            category = "gym-logos"
        elif "background" in upload_dir.lower() or "pref" in upload_dir.lower():
            category = "backgrounds"

        storage_path = f"{category}/assets/{uuid.uuid4().hex}{ext}"
        return FileSecurityService.upload_to_supabase(content, storage_path, mime_type)

    @staticmethod
    async def validate_and_upload_scoped(file: UploadFile, category: str, entity_id: str) -> str:
        content = await file.read()
        mime_type, ext = FileSecurityService.validate_file_content(file, content)
        safe_entity_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(entity_id))
        storage_path = f"{category}/{safe_entity_id}/{uuid.uuid4().hex}{ext}"
        return FileSecurityService.upload_to_supabase(content, storage_path, mime_type)

