"""Errors shared by the REST API and the MCP server. Each carries an HTTP status and a stable code."""
from __future__ import annotations


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra


class BadRequest(AppError):
    status, code = 400, "bad_request"


class Unauthorized(AppError):
    status, code = 401, "unauthorized"


class TooManyAttempts(AppError):
    status, code = 429, "too_many_attempts"


class Forbidden(AppError):
    status, code = 403, "forbidden"


class NotFound(AppError):
    status, code = 404, "not_found"


class Conflict(AppError):
    status, code = 409, "conflict"


class NeedsConfirmation(AppError):
    status, code = 409, "needs_confirmation"


class QuotaExceeded(AppError):
    status, code = 413, "quota_exceeded"


class TooLarge(AppError):
    status, code = 413, "too_large"


class Paused(AppError):
    status, code = 503, "ingest_paused"


class Unavailable(AppError):
    status, code = 503, "unavailable"
