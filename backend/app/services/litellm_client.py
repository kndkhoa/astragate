"""
LiteLLM Proxy HTTP client.

Thin async wrapper around the internal LiteLLM Proxy service. AstraGate calls
LiteLLM exclusively via this module — direct provider calls are forbidden by
design (Requirement 1 AC1, AC4).

Responsibilities:
  - Inject ``Authorization: Bearer {LITELLM_MASTER_KEY}`` on every request.
  - ``post_chat(body)`` — non-streaming chat completion with a 30s timeout.
    Raises :class:`LiteLLMTimeoutError` on timeout (Requirement 1 AC6) and
    :class:`LiteLLMHTTPError` on non-2xx responses.
  - ``stream_chat(body)`` — async iterator of raw SSE bytes for streaming
    responses (Requirement 1 AC5). The connection is held open for the life
    of the iterator.
  - ``get_models()`` — list models exposed by LiteLLM (Requirement 13 AC3).

Requirement 1: AC1, AC2, AC4, AC6
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Optional

import httpx

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

#: Hard upper bound on a single LiteLLM call. The router itself is configured
#: with a 25s timeout (see ``litellm/litellm_config.yaml``); AstraGate adds a
#: small buffer so the router has room to surface its own timeout error.
DEFAULT_TIMEOUT_SECONDS: float = 30.0

#: Endpoint paths on the LiteLLM Proxy service.
CHAT_COMPLETIONS_PATH = "/v1/chat/completions"
EMBEDDINGS_PATH = "/v1/embeddings"
MODELS_PATH = "/v1/models"


# ── Exceptions ────────────────────────────────────────────────────────────────


class LiteLLMError(Exception):
    """Base class for all LiteLLM client errors."""


class LiteLLMTimeoutError(LiteLLMError):
    """
    Raised when a LiteLLM call exceeds the client timeout.

    The gateway maps this to HTTP 504 for the customer (Requirement 1 AC6).
    """


class LiteLLMHTTPError(LiteLLMError):
    """
    Raised when LiteLLM returns a non-2xx HTTP response.

    Attributes:
        status_code: HTTP status code returned by LiteLLM.
        body: Raw response body (truncated to 2KB for log safety).
    """

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body[:2048]
        super().__init__(
            f"LiteLLM returned HTTP {status_code}: {self.body}"
        )


# ── Client ────────────────────────────────────────────────────────────────────


class LiteLLMClient:
    """
    Async HTTP client for the internal LiteLLM Proxy service.

    Construct with no arguments to pick up ``LITELLM_URL`` and
    ``LITELLM_MASTER_KEY`` from the global settings singleton. Tests can
    inject a custom ``transport`` (e.g. ``httpx.MockTransport``) or pre-built
    ``client`` to avoid real network I/O.

    The client deliberately does not pool a single ``httpx.AsyncClient``
    instance across calls. LiteLLM is a sibling container on the internal
    Docker network, and the small overhead of a per-call client keeps the
    code simple and avoids lifecycle bugs across reload/restart.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        master_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_url = (base_url or settings.LITELLM_URL).rstrip("/")
        self.master_key = master_key or settings.LITELLM_MASTER_KEY
        self.timeout = timeout
        self._transport = transport

    # ── Public API ─────────────────────────────────────────────────────────

    async def post_chat(self, body: dict) -> dict:
        """
        Non-streaming chat completion call.

        Args:
            body: OpenAI-compatible request body. Must NOT have ``stream=True``.

        Returns:
            Parsed JSON response from LiteLLM.

        Raises:
            LiteLLMTimeoutError: If the call exceeds ``self.timeout``.
            LiteLLMHTTPError: If LiteLLM returns a non-2xx response.
            LiteLLMError: For other transport / decode failures.
        """
        return await self._post_json(CHAT_COMPLETIONS_PATH, body)

    async def stream_chat(self, body: dict) -> AsyncIterator[bytes]:
        """
        Streaming chat completion call. Yields raw SSE bytes.

        The caller is responsible for relaying these bytes to the customer
        verbatim (e.g. via FastAPI's ``StreamingResponse``). The HTTP
        connection is held open for the duration of the iterator and torn
        down on completion or exception.

        Args:
            body: OpenAI-compatible request body. ``stream`` should be True.

        Yields:
            Raw SSE chunks exactly as emitted by LiteLLM.

        Raises:
            LiteLLMTimeoutError: If the connection / read times out.
            LiteLLMHTTPError: If LiteLLM returns a non-2xx response before
                streaming starts.
            LiteLLMError: For other transport failures.
        """
        client = self._build_client()
        try:
            async with client:
                try:
                    async with client.stream(
                        "POST", CHAT_COMPLETIONS_PATH, json=body
                    ) as response:
                        if response.status_code >= 400:
                            # Surface error before yielding any bytes so the
                            # gateway can map to a real HTTP status.
                            raw = await response.aread()
                            text = raw.decode("utf-8", errors="replace")
                            logger.warning(
                                "litellm_stream_http_error",
                                status_code=response.status_code,
                                path=CHAT_COMPLETIONS_PATH,
                            )
                            raise LiteLLMHTTPError(response.status_code, text)
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                yield chunk
                except httpx.TimeoutException as exc:
                    logger.warning(
                        "litellm_stream_timeout",
                        path=CHAT_COMPLETIONS_PATH,
                        timeout=self.timeout,
                    )
                    raise LiteLLMTimeoutError(
                        f"LiteLLM stream timed out after {self.timeout}s"
                    ) from exc
                except LiteLLMError:
                    raise
                except httpx.HTTPError as exc:
                    logger.error(
                        "litellm_stream_transport_error",
                        path=CHAT_COMPLETIONS_PATH,
                        error=str(exc),
                    )
                    raise LiteLLMError(f"LiteLLM stream transport error: {exc}") from exc
        finally:
            # AsyncClient's __aexit__ already closed it on the happy path; this
            # just guards against the case where _build_client succeeded but
            # entering the context manager failed.
            if not client.is_closed:
                await client.aclose()

    async def get_models(self) -> list:
        """
        Fetch the list of models exposed by LiteLLM.

        Returns the ``data`` array from the OpenAI-compatible
        ``/v1/models`` response, or an empty list if the response has no
        ``data`` key.

        Raises:
            LiteLLMTimeoutError: On timeout.
            LiteLLMHTTPError: On non-2xx response.
            LiteLLMError: For other transport / decode failures.
        """
        payload = await self._get_json(MODELS_PATH)
        if isinstance(payload, dict):
            data = payload.get("data", [])
            return data if isinstance(data, list) else []
        if isinstance(payload, list):
            return payload
        return []

    # ── Internal Helpers ──────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.master_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_client(self) -> httpx.AsyncClient:
        """Create a fresh AsyncClient with auth headers and timeout applied."""
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": self.timeout,
            "headers": self._headers(),
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def _post_json(self, path: str, body: dict) -> dict:
        try:
            async with self._build_client() as client:
                response = await client.post(path, json=body)
        except httpx.TimeoutException as exc:
            logger.warning(
                "litellm_request_timeout",
                path=path,
                timeout=self.timeout,
            )
            raise LiteLLMTimeoutError(
                f"LiteLLM call to {path} timed out after {self.timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "litellm_transport_error",
                path=path,
                error=str(exc),
            )
            raise LiteLLMError(f"LiteLLM transport error: {exc}") from exc

        return self._handle_response(response, path)

    async def _get_json(self, path: str) -> Any:
        try:
            async with self._build_client() as client:
                response = await client.get(path)
        except httpx.TimeoutException as exc:
            logger.warning(
                "litellm_request_timeout",
                path=path,
                timeout=self.timeout,
            )
            raise LiteLLMTimeoutError(
                f"LiteLLM call to {path} timed out after {self.timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            logger.error(
                "litellm_transport_error",
                path=path,
                error=str(exc),
            )
            raise LiteLLMError(f"LiteLLM transport error: {exc}") from exc

        return self._handle_response(response, path)

    @staticmethod
    def _handle_response(response: httpx.Response, path: str) -> Any:
        if response.status_code >= 400:
            body = response.text
            logger.warning(
                "litellm_http_error",
                status_code=response.status_code,
                path=path,
            )
            raise LiteLLMHTTPError(response.status_code, body)
        try:
            return response.json()
        except ValueError as exc:
            logger.error(
                "litellm_invalid_json",
                path=path,
                content_type=response.headers.get("content-type"),
            )
            raise LiteLLMError(f"LiteLLM returned non-JSON body: {exc}") from exc


# ── Module-level convenience client ───────────────────────────────────────────
#
# Most callers use the global ``settings`` singleton, so a shared client is
# convenient. Tests construct their own LiteLLMClient with a MockTransport.

_default_client: Optional[LiteLLMClient] = None


def get_client() -> LiteLLMClient:
    """Return the lazily-constructed default LiteLLMClient."""
    global _default_client
    if _default_client is None:
        _default_client = LiteLLMClient()
    return _default_client


async def post_chat(body: dict) -> dict:
    """Module-level shortcut: ``LiteLLMClient().post_chat(body)``."""
    return await get_client().post_chat(body)


def stream_chat(body: dict) -> AsyncIterator[bytes]:
    """Module-level shortcut: ``LiteLLMClient().stream_chat(body)``."""
    return get_client().stream_chat(body)


async def get_models() -> list:
    """Module-level shortcut: ``LiteLLMClient().get_models()``."""
    return await get_client().get_models()
