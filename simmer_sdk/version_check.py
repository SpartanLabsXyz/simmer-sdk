"""
SDK version-check helper.

Called once from SimmerClient.__init__ to ask the server whether this SDK
version is ok / deprecated / blocked and emit a DeprecationWarning when the
server says so.

Design constraints:
- Fail-quiet on any network error (SDK must not hard-fail at startup because
  our server is briefly unreachable).
- Cache result for the lifetime of the client (no re-check per API call).
- No extra RTT on every cold start — the check is fire-and-forget from the
  caller's perspective (called synchronously but swallows all exceptions).
"""
from __future__ import annotations

import logging
import warnings

logger = logging.getLogger(__name__)

VERSION_CHECK_PATH = "/api/sdk/version-check"


def check_server_version_compatibility(
    base_url: str,
    sdk_version: str,
    session,  # requests.Session — already configured with auth headers
) -> None:
    """Hit the server's version-check endpoint and emit a warning if needed.

    Emits ``DeprecationWarning`` (stacklevel=4 so the warning points at the
    caller's ``SimmerClient(...)`` line) when status is ``deprecated`` or
    ``blocked``.

    Never raises — all exceptions are caught and logged at DEBUG level so the
    SDK never hardens into a startup failure.

    Args:
        base_url:    Client base URL (no trailing slash), e.g.
                     "https://api.simmer.markets".
        sdk_version: The SDK's own ``__version__`` string.
        session:     Configured ``requests.Session`` to reuse for the call.
    """
    url = f"{base_url.rstrip('/')}{VERSION_CHECK_PATH}"
    try:
        resp = session.get(url, params={"sdk_version": sdk_version}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("SDK version check failed (network/parse error) — ignoring: %s", exc)
        return

    status = data.get("status", "ok")
    message = data.get("message", "")

    if status in ("deprecated", "blocked"):
        warnings.warn(message, DeprecationWarning, stacklevel=4)
        logger.debug("SDK version check result: %s — %s", status, message)
