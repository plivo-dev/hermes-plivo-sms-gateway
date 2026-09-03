"""Plivo SMS channel plugin for Hermes.

Registers the ``plivo_sms`` platform, a real-time two-way SMS gateway. Plivo
posts each inbound message to a webhook hosted by the plugin, every sender
number gets its own agent session, and replies go out through the Plivo
Messages API.

``register(ctx)`` is the single entry point called by the Hermes plugin
loader, whether this package is installed via the ``hermes_agent.plugins``
pip entry point or dropped into ``~/.hermes/plugins/``.
"""

from __future__ import annotations

PLUGIN_ID = "plivo_sms"


def register(ctx) -> None:
    """Register the Plivo SMS platform with the Hermes plugin context.

    Lazy import so this package stays importable outside a Hermes runtime,
    because the adapter imports ``gateway.*``. The gateway calls ``register``
    inside Hermes.
    """
    from .adapter import register as _register

    _register(ctx)
