import os

# Load .env file into os.environ if it exists
env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip("'\"")

ENVIRONMENT = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET or not JWT_SECRET.strip():
    if ENVIRONMENT in ["production", "prod"]:
        raise RuntimeError("JWT_SECRET environment variable is required in production environment.")
    JWT_SECRET = "sthxtechnologies_super_secret_jwt_key_2026_dev_only"

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 1440  # 24 hours


DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL or not DATABASE_URL.strip():
    raise RuntimeError("DATABASE_URL environment variable is required. Please specify your Supabase PostgreSQL connection string in .env")

DATABASE_URL = DATABASE_URL.strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)



raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000")
ALLOWED_ORIGINS = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

PASSWORD_REGEX = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"

# Supabase Storage Configuration
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
if not SUPABASE_URL and DATABASE_URL:
    try:
        import urllib.parse
        parsed = urllib.parse.urlparse(DATABASE_URL)
        if parsed.username and "." in parsed.username:
            proj_ref = parsed.username.split(".")[1]
            SUPABASE_URL = f"https://{proj_ref}.supabase.co"
    except Exception:
        pass

SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", "")).strip()
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "gym-assets").strip()

