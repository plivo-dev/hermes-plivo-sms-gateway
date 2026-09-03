"""Plivo inbound-webhook signature verification (Messaging-API V3, "MA-V3").

The canonical string matches the verified Plivo MA-V3 algorithm, which was
reverse-engineered from a real captured Plivo inbound webhook and confirmed
to reproduce the ``X-Plivo-Signature-MA-V3`` header exactly. It matches
Plivo's own ``validate_v3_signature`` construction. Pure stdlib
(hmac/hashlib/base64).

Plivo signs inbound MESSAGING webhooks with ``X-Plivo-Signature-MA-V3`` and
puts the nonce in ``X-Plivo-Signature-V3-Nonce``. The plain
``X-Plivo-Signature-V3`` header is the voice-style signature and is not used
here.

Canonical POST string that is HMAC-SHA256'd with the auth token and base64'd:

    base + "?" + [sortedQuery + "."]? + sortedBodyParams + "." + nonce

``sortedBodyParams`` concatenates ``key + value`` with no separator for the
POST form keys sorted ascending. The sort is by KEY, not by the concatenated
string. ``sortedQuery`` applies only when the signed URL carries a query
string, and consists of its params sorted by key, joined as ``key=value``
with ``&``, followed by a ``.`` separator. It is omitted entirely when the
URL has no query.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from urllib.parse import parse_qsl

MA_V3_SIGNATURE_HEADER = "X-Plivo-Signature-MA-V3"
MA_V3_NONCE_HEADER = "X-Plivo-Signature-V3-Nonce"


def _sorted_query_string(query: str) -> str:
    pairs = parse_qsl(query, keep_blank_values=True)
    pairs.sort(key=lambda kv: kv[0])
    return "&".join(f"{key}={value}" for key, value in pairs)


def _sorted_params_string(form: dict) -> str:
    # Sort by KEY, then concatenate key+value with no separator.
    return "".join(f"{key}{form.get(key, '')}" for key in sorted(form))


def _split_url_query(url: str) -> tuple[str, str]:
    without_fragment = url.split("#", 1)[0]
    if "?" in without_fragment:
        base, query = without_fragment.split("?", 1)
        return base, query
    return without_fragment, ""


def build_ma_v3_payload(url: str, params: dict, nonce: str) -> str:
    """Build the exact canonical string Plivo signs for a POST messaging webhook."""
    base, query = _split_url_query(url)
    query_segment = f"{_sorted_query_string(query)}." if query else ""
    return f"{base}?{query_segment}{_sorted_params_string(params)}.{nonce}"


def compute_ma_v3_signature(auth_token: str, url: str, params: dict, nonce: str) -> str:
    """Return the base64 HMAC-SHA256 signature for a messaging webhook."""
    payload = build_ma_v3_payload(url, params, nonce)
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def verify_ma_v3_signature(
    auth_token: str,
    url: str,
    params: dict,
    nonce: str,
    signature: str,
) -> bool:
    """Constant-time compare of the provided signature against our computation.

    ``signature`` may be a single header value or a comma-joined list of
    candidate values, because Plivo can send more than one signature header.
    Matching any one candidate passes. Returns False on any missing input so
    the webhook handler can uniformly reject with a 403.
    """
    if not auth_token or not signature or not nonce:
        return False
    computed = compute_ma_v3_signature(auth_token, url, params, nonce)
    candidates = [c.strip() for c in str(signature).split(",") if c.strip()]
    return any(hmac.compare_digest(computed, candidate) for candidate in candidates)
