from __future__ import annotations


class AppError(Exception):
    def __init__(self, detail: str, status_code: int = 500):
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class NotFoundError(AppError):
    def __init__(self, detail: str = "Not found"):
        super().__init__(detail, 404)


class AuthError(AppError):
    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(detail, 401)


class ForbiddenError(AppError):
    def __init__(self, detail: str = "Forbidden"):
        super().__init__(detail, 403)


class ValidationError(AppError):
    def __init__(self, detail: str = "Validation error"):
        super().__init__(detail, 422)


class GeminiError(AppError):
    def __init__(self, detail: str = "Gemini API error"):
        super().__init__(detail, 502)


class ExtractionError(AppError):
    def __init__(self, detail: str = "Extraction error"):
        super().__init__(detail, 422)


class JobError(AppError):
    def __init__(self, detail: str = "Job error"):
        super().__init__(detail, 500)
