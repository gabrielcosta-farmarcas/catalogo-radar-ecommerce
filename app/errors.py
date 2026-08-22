class AppError(Exception):
    """Erro de negócio com código estável para o frontend tratar."""

    def __init__(self, message: str, code: str = "app_error", status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = None


class NotFoundError(AppError):
    def __init__(self, message: str, code: str = "not_found"):
        super().__init__(message, code=code, status_code=404)


class ConflictError(AppError):
    def __init__(self, message: str, code: str = "conflict"):
        super().__init__(message, code=code, status_code=409)


class DependencyUnavailable(AppError):
    def __init__(self, message: str, code: str = "dependency_unavailable"):
        super().__init__(message, code=code, status_code=503)


class ValidationAppError(AppError):
    def __init__(self, message: str, details=None, code: str = "validation_error"):
        super().__init__(message, code=code, status_code=422)
        self.details = details
