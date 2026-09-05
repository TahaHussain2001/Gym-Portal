import logging
import json
from datetime import datetime
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError, IntegrityError, OperationalError

logger = logging.getLogger("sthxtechnologies-error")

def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")

def create_error_response(status_code: int, error_code: str, message: str, details=None, request: Request = None) -> JSONResponse:
    request_id = get_request_id(request) if request else "unknown"
    payload = {
        "success": False,
        "error": {
            "code": error_code,
            "message": message,
            "details": details or []
        },
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat()
    }
    return JSONResponse(status_code=status_code, content=payload)

from starlette.exceptions import HTTPException as StarletteHTTPException

def setup_global_exception_handlers(app):
    @app.exception_handler(HTTPException)
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        error_code = f"HTTP_{exc.status_code}"
        return create_error_response(
            status_code=exc.status_code,
            error_code=error_code,
            message=str(exc.detail),
            request=request
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        formatted_errors = []
        for err in exc.errors():
            field = " -> ".join([str(p) for p in err.get("loc", []) if p != "body"])
            msg = err.get("msg", "Invalid field value")
            formatted_errors.append({"field": field, "issue": msg})

        return create_error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_code="VALIDATION_ERROR",
            message="Request input validation failed. Please check specified fields.",
            details=formatted_errors,
            request=request
        )

    @app.exception_handler(IntegrityError)
    async def db_integrity_exception_handler(request: Request, exc: IntegrityError):
        logger.error(f"[DB INTEGRITY ERROR] {exc}")
        return create_error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code="DATABASE_INTEGRITY_VIOLATION",
            message="Database integrity rule constraint violation (e.g. duplicate key or foreign key error).",
            request=request
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
        logger.error(f"[SQLALCHEMY ERROR] {exc}")
        return create_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="DATABASE_ERROR",
            message="A database execution error occurred. Transaction rolled back safely.",
            request=request
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error(f"[UNHANDLED EXCEPTION] {exc}", exc_info=True)
        return create_error_response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred. Our engineering team has been notified.",
            request=request
        )
