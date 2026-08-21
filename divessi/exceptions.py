class DivessiException(Exception):
    """Base class for every error raised by this package."""


class AuthenticationException(DivessiException):
    """Raised when the API does not hand back a token for the credentials."""


class TokenException(AuthenticationException):
    """Raised when a call is answered with an authentication failure.

    The server signals this inside an HTTP 200 body, typically
    {"status": 401, "authenticated": false, ...}, so it has to be detected
    by inspecting the payload rather than the status code.
    """


class ApiException(DivessiException):
    """Raised when a request fails or the response is not usable JSON."""


class UnknownCommandException(ApiException):
    """Raised when the server answers HTTP 404, its reply to a "what" it
    does not recognise."""


class TimeoutException(ApiException):
    """Raised when the server does not answer in time.

    Seen in streaks when the API rate limits a client: after a burst of
    requests it simply stops answering for several minutes.
    """
