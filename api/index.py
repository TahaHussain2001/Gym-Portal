import os
import sys

# Prepend project root directory to sys.path so Vercel runtime can find main.py and src package
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from main import app as _app

async def app(scope, receive, send):
    if scope.get("type") == "http":
        headers = dict(scope.get("headers", []))
        # Vercel sends the original requested path in x-matched-path or x-forwarded-uri upon rewrite
        raw_path = headers.get(b"x-matched-path") or headers.get(b"x-forwarded-uri") or headers.get(b"x-original-uri")
        if raw_path:
            path_str = raw_path.decode("utf-8").split("?")[0]
            if path_str.startswith("/api"):
                scope["path"] = path_str
    await _app(scope, receive, send)

