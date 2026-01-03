"""Custom middleware for the FastAPI application."""

import time
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger()


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Middleware for logging HTTP requests using structlog.

    GET requests are logged at DEBUG level, other methods at INFO level.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process request and log access information.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware/route handler.

        Returns:
            The HTTP response.
        """
        start_time = time.monotonic()

        response = await call_next(request)

        duration_ms = int((time.monotonic() - start_time) * 1000)

        log_data = {
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }

        # GET requests go to DEBUG, others to INFO
        if request.method == "GET":
            logger.debug("http_request", **log_data)
        else:
            logger.info("http_request", **log_data)

        return response
