import time
import uuid
import logging
import json
from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

logger = logging.getLogger("sthxtechnologies-observability")

class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Request & Correlation ID Generation
        request_id = request.headers.get("X-Request-ID") or f"req-{uuid.uuid4().hex[:12]}"
        correlation_id = request.headers.get("X-Correlation-ID") or f"corr-{uuid.uuid4().hex[:12]}"
        
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id
        
        start_time = time.time()
        
        try:
            response = await call_next(request)
            process_time = time.time() - start_time
            
            # Slow Request / Query Warning (> 500ms)
            if process_time > 0.5:
                logger.warning(json.dumps({
                    "event": "SLOW_REQUEST_DETECTED",
                    "request_id": request_id,
                    "correlation_id": correlation_id,
                    "method": request.method,
                    "url": str(request.url.path),
                    "duration_ms": round(process_time * 1000, 2),
                    "client_ip": request.client.host if request.client else "127.0.0.1"
                }))

            response.headers["X-Request-ID"] = request_id
            response.headers["X-Correlation-ID"] = correlation_id
            response.headers["X-Response-Time"] = f"{round(process_time * 1000, 2)}ms"
            return response
            
        except Exception as exc:
            process_time = time.time() - start_time
            logger.error(json.dumps({
                "event": "UNHANDLED_EXCEPTION",
                "request_id": request_id,
                "correlation_id": correlation_id,
                "method": request.method,
                "url": str(request.url.path),
                "error": str(exc),
                "duration_ms": round(process_time * 1000, 2)
            }))
            raise exc

def setup_health_routes(app):
    @app.get("/health", tags=["Observability"])
    @app.get("/api/health", tags=["Observability"])
    def health_check():
        return {
            "status": "healthy",
            "service": "STHX Technologies Gym Management Portal",
            "timestamp": time.time()
        }

    @app.get("/liveness", tags=["Observability"])
    @app.get("/api/liveness", tags=["Observability"])
    def liveness_check():
        return {"status": "alive"}

    @app.get("/readiness", tags=["Observability"])
    @app.get("/api/readiness", tags=["Observability"])
    def readiness_check():
        from src.models.db_models import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db.close()
            return {"status": "ready", "database": "connected"}
        except Exception as e:
            db.close()
            raise HTTPException(status_code=503, detail=f"Database readiness check failed: {str(e)}")
