"""Plivo SMS platform adapter for the Hermes gateway: glue + server lifecycle.

Receives inbound SMS in real time on a self-hosted webhook and sends outbound
replies via the Plivo Messages REST API. Each inbound phone number gets its
own Hermes session, so the agent holds an independent two-way conversation per
sender. The structure mirrors Hermes' bundled SMS platform adapter.

Configuration lives in the plugin envelope: :mod:`.setup` resolves the
public URL and wires the Plivo side, :mod:`.webhook` hosts the server and
validates requests, :mod:`.schemas` parses inbound payloads, and
:mod:`.signature` implements MA-V3.

Credentials and config (environment variables):
  - PLIVO_AUTH_ID                 Plivo account auth id
  - PLIVO_AUTH_TOKEN              Plivo account auth token (also verifies MA-V3)
  - PLIVO_PHONE_NUMBER            E.164 sender number replies are sent from
  - PLIVO_SMS_PUBLIC_URL          public https base (optional, else tunnel)
  - PLIVO_SMS_AUTO_WIRE           "false" disables the automatic Plivo wiring
  - PLIVO_SMS_WEBHOOK_PORT        listen port (default 8090)
  - PLIVO_SMS_WEBHOOK_HOST        listen host (default 127.0.0.1)
  - PLIVO_SMS_INSECURE_NO_SIGNATURE  "true" disables signature validation (dev only)
  - PLIVO_SMS_ALLOWED_USERS       comma-separated E.164 numbers allowed to talk
  - PLIVO_SMS_ALLOW_ALL_USERS     "true"/"false"
  - PLIVO_SMS_HOME_CHANNEL        number for cron / notification delivery
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.platforms.helpers import redact_phone, strip_markdown

from . import schemas, setup, webhook
from .setup import API_BASE, MESSAGE_PATH, get_scoped_secret

logger = logging.getLogger(__name__)

MAX_SMS_LENGTH = 1600  # ~10 SMS segments
DEFAULT_WEBHOOK_PORT = 8090
DEFAULT_WEBHOOK_HOST = "127.0.0.1"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def check_plivo_requirements() -> bool:
    """Dependency + credential check the gateway runs before instantiation."""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return bool(
        get_scoped_secret("PLIVO_AUTH_ID") and get_scoped_secret("PLIVO_AUTH_TOKEN")
    )


class PlivoSmsAdapter(BasePlatformAdapter):
    """Plivo SMS <-> Hermes gateway adapter (inbound webhook + outbound REST)."""

    MAX_MESSAGE_LENGTH = MAX_SMS_LENGTH

    def __init__(self, config: PlatformConfig):
        platform = Platform("plivo_sms")
        super().__init__(config=config, platform=platform)
        self._auth_id: str = get_scoped_secret("PLIVO_AUTH_ID", "") or ""
        self._auth_token: str = get_scoped_secret("PLIVO_AUTH_TOKEN", "") or ""
        self._from_number: str = _env("PLIVO_PHONE_NUMBER")
        self._webhook_port: int = int(
            _env("PLIVO_SMS_WEBHOOK_PORT", str(DEFAULT_WEBHOOK_PORT))
        )
        self._webhook_host: str = _env("PLIVO_SMS_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST)
        self._webhook_url: str = ""  # public URL Plivo signs, set at connect
        self._server: Optional[webhook.WebhookServer] = None
        self._tunnel_proc: Optional[Any] = None
        self._wire_task: Optional[asyncio.Task] = None
        self._http_session = None  # type: Optional[Any]

    # ------------------------------------------------------------------
    # Lifecycle: connect / disconnect
    # ------------------------------------------------------------------

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        import aiohttp

        if not self._from_number:
            msg = "[plivo_sms] PLIVO_PHONE_NUMBER not set, cannot send replies"
            logger.error(msg)
            self._set_fatal_error("plivo_sms_missing_phone_number", msg, retryable=False)
            return False

        if self._server is None:
            self._server = webhook.WebhookServer(
                self._webhook_host, self._webhook_port, MESSAGE_PATH, self._handle_webhook
            )
            await self._server.start()

        insecure_no_sig = (
            _env("PLIVO_SMS_INSECURE_NO_SIGNATURE").lower() == "true"
        )

        # Autonomous configuration. Resolving the base is quick (a configured
        # value, or the tunnel-URL read capped at 20 seconds), so it fits the
        # gateway's 30-second connect() budget. The slow tail (DNS propagation
        # for a fresh tunnel hostname, then the Plivo wiring with retries)
        # runs in a tracked background task that disconnect() cancels.
        base, proc = await setup.resolve_public_base(self._webhook_port)
        if base:
            self._tunnel_proc = proc
            self._webhook_url = f"{base}{MESSAGE_PATH}"
            self._wire_task = asyncio.create_task(
                self._finish_wiring(base, spawned=proc is not None)
            )
        elif not insecure_no_sig:
            msg = (
                "[plivo_sms] no public URL: set PLIVO_SMS_PUBLIC_URL (a public "
                "host or named tunnel) or install cloudflared. The public URL "
                "is required for MA-V3 signature validation. For local "
                "development without validation, set "
                "PLIVO_SMS_INSECURE_NO_SIGNATURE=true (NOT for production)."
            )
            logger.error(msg)
            self._set_fatal_error("plivo_sms_no_public_url", msg, retryable=True)
            return False
        else:
            logger.warning(
                "[plivo_sms] PLIVO_SMS_INSECURE_NO_SIGNATURE=true and no public "
                "URL. Signature validation is DISABLED, so any client that can "
                "reach port %d can inject messages. Do NOT use this in production.",
                self._webhook_port,
            )

        self._http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            trust_env=True,
        )
        self._running = True
        logger.info(
            "[plivo_sms] ready on %s:%d, webhook %s, from %s",
            self._webhook_host,
            self._webhook_port,
            self._webhook_url or "(no public URL)",
            redact_phone(self._from_number),
        )
        return True

    async def _finish_wiring(self, base: str, *, spawned: bool) -> None:
        try:
            if spawned:
                await setup.wait_for_dns(base)
            await setup.auto_wire(base)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - network failures
            logger.error("[plivo_sms] background wiring failed: %s", exc)

    async def disconnect(self) -> None:
        if self._wire_task is not None and not self._wire_task.done():
            self._wire_task.cancel()
        self._wire_task = None
        if self._tunnel_proc is not None:
            with contextlib.suppress(Exception):
                self._tunnel_proc.terminate()
            self._tunnel_proc = None
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        if self._server is not None:
            await self._server.stop()
            self._server = None
        self._running = False
        logger.info("[plivo_sms] disconnected")

    # ------------------------------------------------------------------
    # Outbound: send / get_chat_info / formatting
    # ------------------------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        import aiohttp

        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted)
        last_result = SendResult(success=True)

        url = f"{API_BASE}/{self._auth_id}/Message/"
        auth = aiohttp.BasicAuth(self._auth_id, self._auth_token)

        session = self._http_session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            trust_env=True,
        )
        try:
            for chunk in chunks:
                body = {
                    "src": self._from_number,
                    "dst": chat_id,
                    "text": chunk,
                    "type": "sms",
                }
                try:
                    async with session.post(url, json=body, auth=auth) as resp:
                        payload = await resp.json()
                        if resp.status >= 400:
                            error_msg = payload.get("error", str(payload))
                            logger.error(
                                "[plivo_sms] send failed to %s: %s %s",
                                redact_phone(chat_id),
                                resp.status,
                                error_msg,
                            )
                            return SendResult(
                                success=False,
                                error=f"Plivo {resp.status}: {error_msg}",
                            )
                        message_uuid = payload.get("message_uuid")
                        if isinstance(message_uuid, list):
                            message_uuid = message_uuid[0] if message_uuid else ""
                        last_result = SendResult(
                            success=True, message_id=message_uuid or ""
                        )
                except Exception as exc:
                    logger.error(
                        "[plivo_sms] send error to %s: %s", redact_phone(chat_id), exc
                    )
                    return SendResult(success=False, error=str(exc))
        finally:
            if not self._http_session and session:
                await session.close()

        return last_result

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    def format_message(self, content: str) -> str:
        """Strip markdown, because SMS renders it as literal characters."""
        return strip_markdown(content)

    # ------------------------------------------------------------------
    # Inbound Plivo webhook handler
    # ------------------------------------------------------------------

    async def _handle_webhook(self, request):
        from aiohttp import web

        try:
            content_length = request.content_length
            if content_length is not None and content_length > webhook.MAX_BODY_BYTES:
                return web.Response(status=413)
            raw = await request.read()
            if len(raw) > webhook.MAX_BODY_BYTES:
                return web.Response(status=413)
            form = schemas.parse_form_body(raw)
        except Exception as exc:
            logger.error("[plivo_sms] webhook parse error: %s", exc)
            return web.Response(status=400)

        # Validate the Plivo MA-V3 signature unless explicitly disabled.
        if self._webhook_url and not webhook.verify_request(
                self._auth_token,
                self._webhook_url,
                request.headers,
                schemas.flatten_form(form),
            ):
                return web.Response(status=403)

        inbound = schemas.extract_inbound(form)
        if inbound is None:
            return web.Response(text="", status=200)

        # Echo prevention: ignore messages from our own number.
        if inbound.from_number == self._from_number:
            logger.debug(
                "[plivo_sms] ignoring echo from own number %s",
                redact_phone(inbound.from_number),
            )
            return web.Response(text="", status=200)

        logger.info(
            "[plivo_sms] inbound from %s -> %s: %s",
            redact_phone(inbound.from_number),
            redact_phone(inbound.to_number),
            inbound.text[:80],
        )

        source = self.build_source(
            chat_id=inbound.from_number,
            chat_name=inbound.from_number,
            chat_type="dm",
            user_id=inbound.from_number,
            user_name=inbound.from_number,
        )
        event = MessageEvent(
            text=inbound.text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=inbound.form,
            message_id=inbound.message_uuid,
        )

        # Non-blocking: Plivo expects a fast 200 while the agent works.
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

        return web.Response(text="", status=200)


# ----------------------------------------------------------------------
# Plugin glue
# ----------------------------------------------------------------------


def _build_adapter(config):
    """adapter_factory: construct a PlivoSmsAdapter from a PlatformConfig."""
    return PlivoSmsAdapter(config)


def _is_connected(config) -> bool:
    """Connected when Plivo credentials are present."""
    try:
        import hermes_cli.gateway as gateway_mod

        return bool((gateway_mod.get_env_value("PLIVO_AUTH_ID") or "").strip())
    except Exception:
        return bool((os.getenv("PLIVO_AUTH_ID") or "").strip())


def _send_sms_sync(auth_id: str, auth_token: str, src: str, dst: str, text: str) -> str:
    """Blocking stdlib POST to the Plivo Messages API. Returns the message UUID.

    Used only by the standalone sender, which runs out of process without an
    adapter instance or an aiohttp session.
    """
    url = f"{API_BASE}/{auth_id}/Message/"
    creds = base64.b64encode(f"{auth_id}:{auth_token}".encode("ascii")).decode("ascii")
    body = json.dumps({"src": src, "dst": dst, "text": text, "type": "sms"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Basic {creds}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Plivo HTTP {exc.code}: {raw or exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Plivo connection error: {exc.reason}") from exc
    message_uuid = payload.get("message_uuid")
    if isinstance(message_uuid, list):
        message_uuid = message_uuid[0] if message_uuid else ""
    return message_uuid or ""


async def _standalone_send(
    pconfig,
    chat_id,
    message,
    *,
    thread_id=None,
    media_files=None,
    force_document=False,
):
    """Out-of-process SMS delivery via the Plivo Messages REST API.

    Implements the ``standalone_sender_fn`` contract, mirroring the bundled
    SMS adapter's ``_standalone_send``, so cron and notification delivery can
    reach a Plivo number without a live adapter instance. The stdlib client
    is sync, so run it off the event loop.
    """
    auth_id = get_scoped_secret("PLIVO_AUTH_ID", "") or ""
    auth_token = get_scoped_secret("PLIVO_AUTH_TOKEN", "") or ""
    src = _env("PLIVO_PHONE_NUMBER")
    if not (auth_id and auth_token and src):
        return {"error": "Plivo credentials or PLIVO_PHONE_NUMBER not set"}
    try:
        message_uuid = await asyncio.to_thread(
            _send_sms_sync, auth_id, auth_token, src, chat_id, strip_markdown(message)
        )
        return {
            "success": True,
            "platform": "plivo_sms",
            "chat_id": chat_id,
            "message_id": message_uuid,
        }
    except Exception as exc:
        return {"error": f"Plivo send failed: {exc}"}


def register(ctx) -> None:
    """Register the Plivo SMS platform adapter with the gateway."""
    ctx.register_platform(
        name="plivo_sms",
        label="SMS (Plivo)",
        adapter_factory=_build_adapter,
        check_fn=check_plivo_requirements,
        is_connected=_is_connected,
        required_env=["PLIVO_AUTH_ID", "PLIVO_AUTH_TOKEN", "PLIVO_PHONE_NUMBER"],
        install_hint="pip install hermes-plivo-sms",
        allowed_users_env="PLIVO_SMS_ALLOWED_USERS",
        allow_all_env="PLIVO_SMS_ALLOW_ALL_USERS",
        cron_deliver_env_var="PLIVO_SMS_HOME_CHANNEL",
        standalone_sender_fn=_standalone_send,
        max_message_length=MAX_SMS_LENGTH,
        pii_safe=True,
        emoji="📩",
        allow_update_command=True,
    )
