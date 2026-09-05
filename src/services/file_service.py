import os
import uuid
import logging
from typing import Tuple
from fastapi import UploadFile, HTTPException

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

class FileSecurityService:
    @staticmethod
    def validate_file_signature(content: bytes) -> str:
        for magic, mime in MAGIC_BYTES_MAP.items():
            if content.startswith(magic):
                return mime
        # Special check for WEBP (starts with RIFF and contains WEBP at byte 8)
        if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
            return "image/webp"
        raise HTTPException(
            status_code=400,
            detail="File security validation failed: Invalid file header signature or unapproved file format."
        )

    @staticmethod
    async def validate_and_save_upload(file: UploadFile, upload_dir: str = "static/uploads") -> str:
        if not file or not file.filename:
            raise HTTPException(status_code=400, detail="No file uploaded.")

        # 1. Filename Sanitization & Path Traversal Protection
        raw_filename = os.path.basename(file.filename)
        _, ext = os.path.splitext(raw_filename)
        ext = ext.lower().strip()

        if ext in FORBIDDEN_EXTENSIONS or ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File extension '{ext}' is forbidden.")

        # 2. MIME Type Validation
        if file.content_type and file.content_type.lower() not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=400, detail=f"MIME type '{file.content_type}' is not allowed.")

        # 3. Read Content & Max Size Validation
        content = await file.read()
        if len(content) > MAX_FILE_SIZE_BYTES:
            raise HTTPException(status_code=400, detail=f"File size exceeds 5MB limit ({len(content) / (1024 * 1024):.2f}MB).")

        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

        # 4. File Header Signature (Magic Bytes) Validation
        FileSecurityService.validate_file_signature(content)

        # 5. Secure Target File Creation
        os.makedirs(upload_dir, exist_ok=True)
        unique_name = f"{uuid.uuid4().hex}{ext}"
        target_path = os.path.abspath(os.path.join(upload_dir, unique_name))

        # Absolute Path Traversal Check
        abs_upload_dir = os.path.abspath(upload_dir)
        if not target_path.startswith(abs_upload_dir):
            raise HTTPException(status_code=400, detail="Path traversal attempt detected.")

        with open(target_path, "wb") as f:
            f.write(content)

        relative_url = f"/{upload_dir}/{unique_name}".replace("\\", "/")
        return relative_url
