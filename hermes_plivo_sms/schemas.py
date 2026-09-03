"""Inbound Plivo SMS webhook payload shapes and parsing.

Plivo posts inbound messages as ``application/x-www-form-urlencoded`` with
``From``, ``To``, ``Text`` and ``MessageUUID`` fields. This module is pure
stdlib so the parsing layer is testable outside a Hermes runtime.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class InboundSms:
    """One inbound message extracted from a Plivo webhook post."""

    from_number: str
    to_number: str
    text: str
    message_uuid: str
    form: Dict[str, List[str]]


def parse_form_body(raw: bytes) -> Dict[str, List[str]]:
    """Decode a urlencoded form body into a multi-value dict."""
    return urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)


def flatten_form(form: Dict[str, List[str]]) -> Dict[str, str]:
    """Reduce a multi-value form to the first value per key.

    This flat view is what the MA-V3 signature is computed over and what the
    message fields are read from.
    """
    return {k: v[0] for k, v in form.items() if v}


def extract_inbound(form: Dict[str, List[str]]) -> Optional[InboundSms]:
    """Return the inbound message, or None when the post carries no message.

    Plivo also posts delivery reports and empty callbacks to the same URL, so
    a missing sender or empty body is not an error. The caller acknowledges
    those with a 200 and moves on.
    """
    params = flatten_form(form)
    from_number = params.get("From", "").strip()
    to_number = params.get("To", "").strip()
    text = params.get("Text", "").strip()
    if not from_number or not text:
        return None
    return InboundSms(
        from_number=from_number,
        to_number=to_number,
        text=text,
        message_uuid=params.get("MessageUUID", "").strip(),
        form=form,
    )
