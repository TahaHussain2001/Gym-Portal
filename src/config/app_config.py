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

JWT_SECRET = os.getenv("JWT_SECRET", "sthxtechnologies_super_secret_jwt_key_2026")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = 1440  # 24 hours

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./gym.db")

raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000")
ALLOWED_ORIGINS = [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

PASSWORD_REGEX = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
