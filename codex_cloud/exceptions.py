"""Public exception hierarchy."""

class CodexCloudError(Exception):
    def __init__(self, message: str, *, status: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.request_id = request_id

class AuthenticationError(CodexCloudError): pass
class AuthorizationError(CodexCloudError): pass
class TaskNotFound(CodexCloudError): pass
class EnvironmentNotFound(CodexCloudError): pass
class BackendUnavailable(CodexCloudError): pass
class SchemaDriftError(CodexCloudError): pass
class UnsupportedCapability(CodexCloudError): pass
class TaskConflictError(CodexCloudError): pass
class PollingTimeout(CodexCloudError): pass

class RateLimitError(CodexCloudError):
    def __init__(self, message: str, *, retry_after: float | None = None, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
