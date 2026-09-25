"""Public exceptions raised by :mod:`codex_cloud_api`."""


class CodexCloudError(Exception):
    """Base class for all library errors."""


class ConfigurationError(CodexCloudError):
    """A client option or request argument is invalid."""


class UnsafeDestinationError(ConfigurationError):
    """Credentials would be sent to an untrusted destination."""


class AuthenticationError(CodexCloudError):
    """Credentials could not be obtained or decoded."""


class TokenRefreshError(AuthenticationError):
    """An OAuth token refresh failed."""


class NetworkError(CodexCloudError):
    """The HTTP transport failed before a response was received."""


class HTTPResponseError(CodexCloudError):
    """An HTTP response has an error status."""

    def __init__(self, response):
        self.response = response
        super().__init__(f"HTTP {response.status} for {response.method} {response.url}")


# Compatibility aliases for early users of the single-file utility.
CodexApiError = CodexCloudError
AuthError = AuthenticationError
RefreshError = TokenRefreshError
