"""Autonomous configuration: public-URL provisioning + Plivo message-URL wiring.

Runs from the plugin's ``connect()`` so the operator configures only
credentials and a phone number. The plugin does the rest at every start.

Public URL, hybrid resolution order:
  1. ``PLIVO_SMS_PUBLIC_URL``, an https base such as a server host or a named
     Cloudflare tunnel. This is the stable production path.
  2. ``PLIVO_WEBHOOK_URL``, the legacy full webhook URL from the combined
     plugin. Its host is reused as the base.
  3. Otherwise spawn a Cloudflare quick tunnel (``cloudflared tunnel --url``)
     and read the assigned ``trycloudflare.com`` URL back. This is the
     zero-setup development path, and the URL rotates per run.

Plivo wiring is an idempotent check-before-set at every start: find or create
the Plivo application named ``hermes-plivo-sms-<number>``, keyed by the
digits-only phone number, point its message_url at
this plugin's webhook path, and attach the phone number. Re-pointing happens
automatically when the public URL changes. Disable with
``PLIVO_SMS_AUTO_WIRE=false``.

Timing matters. The gateway caps ``connect()`` at 30 seconds, so only the
tunnel-URL read (capped at 20 seconds) happens inline. The slow tail, waiting
for the fresh tunnel hostname to resolve in DNS and then wiring Plivo with
retries, runs in a background task that ``disconnect()`` cancels. Plivo
validates the message_url's domain at write time and rejects hostnames that
do not resolve yet, hence the DNS wait and the retry on "valid url" errors.

Secrets are read with the profile-scoped pattern used by the bundled
platform plugins, scoped store first with an ``os.environ`` fallback on the
default profile.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

APP_NAME_PREFIX = "hermes-plivo-sms"
API_BASE = "https://api.plivo.com/v1/Account"
MESSAGE_PATH = "/plivo/sms/message"
_TUNNEL_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

try:  # Hermes runtime: profile-scoped secret store, same pattern as the bundled sms adapter
    from agent.secret_scope import UnscopedSecretError as _UnscopedSecretError
    from agent.secret_scope import get_secret as _scoped_get_secret
except Exception:  # pragma: no cover - importable outside a Hermes runtime
    _UnscopedSecretError = KeyError  # type: ignore[assignment,misc]

    def _scoped_get_secret(name, default=None):  # type: ignore[no-redef]
        return os.getenv(name, default)


def get_scoped_secret(name: str, default: Optional[str] = None) -> Optional[str]:
    """Scope-aware credential read with the default-profile env fallback.

    Secondary profiles construct their adapters under a profile secret scope,
    where the scoped read is authoritative. The default profile constructs
    unscoped, where a bare ``get_secret`` would raise, so fall back to the
    environment there.
    """
    try:
        val = _scoped_get_secret(name, default)
    except _UnscopedSecretError:
        val = os.getenv(name)
    return val if val is not None else default


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def app_name_for(number: str) -> str:
    """Plivo application name keyed by the digits-only phone number.

    Each number maps to exactly one deterministic application, so re-wiring a
    given number updates its own app and never collides with the app for a
    different number.
    """
    return f"{APP_NAME_PREFIX}-{re.sub(r'[^0-9]', '', number)}"


def _normalize_base(url: str) -> str:
    """Reduce any configured URL form to a bare https base with no path."""
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not url.startswith("http"):
        url = "https://" + url
    parsed = urlparse(url)
    return f"https://{parsed.netloc}"


async def resolve_public_base(port: int) -> Tuple[Optional[str], Optional[Any]]:
    """Resolve the public https base URL. Returns (base_url, tunnel_process).

    ``tunnel_process`` is non-None only when a quick tunnel was spawned, and
    the caller owns terminating it on disconnect.
    """
    configured = _env("PLIVO_SMS_PUBLIC_URL")
    if configured:
        base = _normalize_base(configured)
        logger.info("[plivo_sms] public URL (configured): %s", base)
        return base, None

    legacy = _env("PLIVO_WEBHOOK_URL")
    if legacy:
        base = _normalize_base(legacy)
        logger.info("[plivo_sms] public URL (from legacy PLIVO_WEBHOOK_URL): %s", base)
        return base, None

    return await _spawn_quick_tunnel(port)


async def _spawn_quick_tunnel(port: int) -> Tuple[Optional[str], Optional[Any]]:
    """Launch a Cloudflare quick tunnel and read back its assigned URL."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error(
            "[plivo_sms] no public URL configured and cloudflared is not installed. "
            "Set PLIVO_SMS_PUBLIC_URL (a public host or named tunnel), or install "
            "cloudflared for a zero-setup development tunnel."
        )
        return None, None

    async def _read_url() -> Optional[str]:
        while True:
            line = await proc.stderr.readline()  # type: ignore[union-attr]
            if not line:
                return None
            m = _TUNNEL_URL_RE.search(line.decode(errors="replace"))
            if m:
                return m.group(0)

    try:
        url = await asyncio.wait_for(_read_url(), timeout=20)
    except asyncio.TimeoutError:
        url = None
    if not url:
        logger.error("[plivo_sms] quick tunnel did not report a URL, terminating it")
        proc.terminate()
        return None, None

    # Keep draining stderr so the tunnel process never blocks on a full pipe,
    # and log if it exits. This is development-grade supervision, and a
    # production deployment uses a stable URL instead.
    async def _drain() -> None:
        while True:
            line = await proc.stderr.readline()  # type: ignore[union-attr]
            if not line:
                break
        logger.warning(
            "[plivo_sms] quick tunnel process exited (rc=%s), so the public URL is "
            "dead until the gateway restarts. Use PLIVO_SMS_PUBLIC_URL for a stable "
            "deployment.",
            proc.returncode,
        )

    # Held on the process object, because a bare create_task can be collected
    # mid-await and the drain would stop reporting a dead tunnel.
    proc._drain_task = asyncio.create_task(_drain())
    logger.info("[plivo_sms] public URL (quick tunnel, testing-grade): %s", url)
    return url, proc


async def wait_for_dns(base_url: str, *, attempts: int = 30, delay: float = 3.0) -> bool:
    """Wait until the URL's hostname resolves.

    A brand-new trycloudflare hostname takes a while to propagate in DNS, and
    Plivo validates the message_url's domain at write time, so wiring straight
    after tunnel creation fails with "Must be a valid url". This runs in a
    background task, never inside ``connect()``, which the gateway caps at 30
    seconds.
    """
    host = base_url.split("//", 1)[1].split("/", 1)[0]
    loop = asyncio.get_running_loop()
    for _ in range(attempts):
        try:
            await loop.getaddrinfo(host, 443)
            return True
        except OSError:
            await asyncio.sleep(delay)
    logger.warning(
        "[plivo_sms] hostname %s still not resolving, wiring will rely on retries", host
    )
    return False


async def auto_wire(base_url: str) -> bool:
    """Point the Plivo side at this plugin. Idempotent and safe to run every start.

    Find or create the application ``hermes-plivo-sms-<number>``, keyed by
    the digits-only phone number, ensure its message_url is ``<base>/plivo/sms/message``, and ensure the configured
    phone number is attached to it. Skips with a log when disabled or when
    credentials or the number are missing, and inbound still works if the
    operator wired Plivo manually.
    """
    if _env("PLIVO_SMS_AUTO_WIRE", "true").lower() in ("false", "0", "no"):
        logger.info("[plivo_sms] auto-wire disabled (PLIVO_SMS_AUTO_WIRE=false)")
        return False
    auth_id = get_scoped_secret("PLIVO_AUTH_ID", "") or ""
    auth_token = get_scoped_secret("PLIVO_AUTH_TOKEN", "") or ""
    number = _env("PLIVO_PHONE_NUMBER").lstrip("+")
    if not (auth_id and auth_token and number):
        logger.warning(
            "[plivo_sms] auto-wire skipped: PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / "
            "PLIVO_PHONE_NUMBER not all set. Wire the number's message_url manually "
            "to %s%s",
            base_url, MESSAGE_PATH,
        )
        return False

    import aiohttp

    message_url = f"{base_url}{MESSAGE_PATH}"
    app_name = app_name_for(number)
    acct = f"{API_BASE}/{auth_id}"
    auth = aiohttp.BasicAuth(auth_id, auth_token)
    try:
        async with aiohttp.ClientSession(auth=auth) as http:
            # 1. Find or create the application. Plivo paginates the list
            # (20 per page), so page through it until the app is found or the
            # pages run out, because a single read would miss an app beyond
            # page one and create a duplicate on every start.
            app_id = None
            app_message_url = None
            limit = 20
            offset = 0
            while True:
                async with http.get(
                    f"{acct}/Application/", params={"limit": limit, "offset": offset}
                ) as r:
                    data = await r.json()
                objects = data.get("objects") or []
                for app in objects:
                    if app.get("app_name") == app_name:
                        app_id = str(app.get("app_id"))
                        app_message_url = app.get("message_url")
                        break
                meta = data.get("meta") or {}
                if app_id is not None or not meta.get("next") or not objects:
                    break
                offset += limit
            if app_id is None:
                created = None
                for attempt in range(3):
                    async with http.post(
                        f"{acct}/Application/",
                        json={
                            "app_name": app_name,
                            "message_url": message_url,
                            "message_method": "POST",
                        },
                    ) as r:
                        created = await r.json()
                        status = r.status
                    if status in (200, 201):
                        break
                    # DNS propagation race on a fresh tunnel hostname: Plivo
                    # validates the URL's domain, so back off and retry.
                    if "valid url" in str(created).lower() and attempt < 2:
                        logger.info(
                            "[plivo_sms] auto-wire: URL not accepted yet, retrying in 10s"
                        )
                        await asyncio.sleep(10)
                        continue
                    logger.error("[plivo_sms] auto-wire: app create failed: %s", created)
                    return False
                else:
                    logger.error(
                        "[plivo_sms] auto-wire: app create failed after retries: %s",
                        created,
                    )
                    return False
                app_id = str(created.get("app_id"))
                logger.info("[plivo_sms] auto-wire: created application %s", app_id)
            elif app_message_url != message_url:
                async with http.post(
                    f"{acct}/Application/{app_id}/",
                    json={"message_url": message_url, "message_method": "POST"},
                ) as r:
                    await r.read()
                logger.info("[plivo_sms] auto-wire: message_url updated -> %s", message_url)
            # 2. Ensure the number is attached to the application.
            async with http.get(f"{acct}/Number/{number}/") as r:
                num = await r.json()
            if r.status != 200:
                logger.error(
                    "[plivo_sms] auto-wire: number %s not found on this account", number
                )
                return False
            current_app = str(num.get("application") or "").rstrip("/").split("/")[-1]
            if current_app != app_id:
                async with http.post(f"{acct}/Number/{number}/", json={"app_id": app_id}) as r:
                    await r.read()
                logger.info(
                    "[plivo_sms] auto-wire: number %s attached to app %s", number, app_id
                )
            logger.info(
                "[plivo_sms] auto-wire complete: SMS to +%s -> %s", number, message_url
            )
            return True
    except Exception as exc:
        logger.error(
            "[plivo_sms] auto-wire failed (%s), wire Plivo manually to %s%s",
            exc, base_url, MESSAGE_PATH,
        )
        return False
