import os
import time
import logging
from collections import defaultdict
from fastapi import Request, HTTPException, status

logger = logging.getLogger("sthxtechnologies-ratelimit")

try:
    import redis
    REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
    redis_client.ping()
    HAS_REDIS = True
    logger.info(f"Connected to Redis rate limiter at {REDIS_HOST}:{REDIS_PORT}")
except Exception:
    redis_client = None
    HAS_REDIS = False
    logger.info("Redis not available; using in-memory Sliding Window Rate Limiter")

class SlidingWindowRateLimiter:
    def __init__(self, limit: int, window_seconds: int, name: str = "default"):
        self.limit = limit
        self.window_seconds = window_seconds
        self.name = name
        self.in_memory_store = defaultdict(list)

    def check(self, request: Request):
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.time()
        key = f"ratelimit:{self.name}:{client_ip}"

        if HAS_REDIS and redis_client:
            try:
                pipeline = redis_client.pipeline()
                pipeline.zremrangebyscore(key, 0, now - self.window_seconds)
                pipeline.zadd(key, {str(now): now})
                pipeline.zcard(key)
                pipeline.expire(key, self.window_seconds + 5)
                results = pipeline.execute()
                current_count = results[2]

                if current_count > self.limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail=f"Too many requests for {self.name}. Please try again later."
                    )
                return
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Redis check failed: {e}, falling back to in-memory")

        # In-memory sliding window fallback
        timestamps = [t for t in self.in_memory_store[key] if t > (now - self.window_seconds)]
        if len(timestamps) >= self.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many requests for {self.name}. Please try again later."
            )
        timestamps.append(now)
        self.in_memory_store[key] = timestamps

login_limiter = SlidingWindowRateLimiter(limit=5, window_seconds=60, name="login")
otp_limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60, name="otp")
reset_limiter = SlidingWindowRateLimiter(limit=3, window_seconds=60, name="password_reset")
register_limiter = SlidingWindowRateLimiter(limit=5, window_seconds=600, name="register")
refresh_limiter = SlidingWindowRateLimiter(limit=10, window_seconds=60, name="refresh_token")

def limit_auth_requests(request: Request):
    path = request.url.path.lower()
    if "login" in path:
        login_limiter.check(request)
    elif "otp" in path or "code" in path:
        otp_limiter.check(request)
    elif "forgot" in path or "reset" in path:
        reset_limiter.check(request)
    elif "register" in path:
        register_limiter.check(request)
    elif "refresh" in path:
        refresh_limiter.check(request)
    else:
        login_limiter.check(request)
