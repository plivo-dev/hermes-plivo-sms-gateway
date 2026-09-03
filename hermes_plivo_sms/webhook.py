"""Webhook server hosting and inbound request validation for the SMS channel.

Hermes provides no shared inbound HTTP surface for platform plugins, so each
webhook-based adapter binds its own aiohttp server, exactly as the bundled
SMS adapter does. The request-validation step is a pure function over the
request headers and the parsed form so it stays testable without a running
server.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, Optional

from .signature import (
    MA_V3_NONCE_HEADER,
    MA_V3_SIGNATURE_HEADER,
    verify_ma_v3_signature,
)

logger = logging.getLogger(__name__)

# Plivo webhook payloads are small, so 64 KiB is generous.
MAX_BODY_BYTES = 65_536


def verify_request(
    auth_token: str,
    webhook_url: str,
    headers: Mapping[str, str],
    params: Mapping[str, str],
) -> bool:
    """Validate the MA-V3 signature carried on an inbound webhook request.

    ``headers`` is any case-insensitive mapping (aiohttp's request headers
    qualify). Returns False when either MA-V3 header is absent or the
    signature does not match.
    """
    signature = headers.get(MA_V3_SIGNATURE_HEADER, "")
    nonce = headers.get(MA_V3_NONCE_HEADER, "")
    if not signature or not nonce:
        logger.warning("[plivo_sms] rejected: missing MA-V3 signature/nonce headers")
        return False
    if not verify_ma_v3_signature(auth_token, webhook_url, dict(params), nonce, signature):
        logger.warning("[plivo_sms] rejected: invalid MA-V3 signature")
        return False
    return True


class WebhookServer:
    """Owns the aiohttp application lifecycle for the message webhook."""

    def __init__(self, host: str, port: int, path: str, handler: Callable) -> None:
        self._host = host
        self._port = port
        self._path = path
        self._handler = handler
        self._runner: Optional[Any] = None

    async def start(self) -> None:
        from aiohttp import web

        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_post(self._path, self._handler)
        app.router.add_get("/health", lambda _request: web.Response(text="ok"))
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(
            "[plivo_sms] webhook server listening on %s:%d (path %s)",
            self._host,
            self._port,
            self._path,
        )

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
