"""TelegramAdapter — :class:`SurfaceAdapter` wrapping the telegram poller.

Wraps :func:`metasphere.telegram.poller.run_poll_iteration` (inbound) and
:func:`metasphere.telegram.api.send_message` (outbound) so the gateway
daemon can drive telegram through the generic adapter contract.

This is purely structural: behavior matches the pre-adapter daemon loop
exactly. Per-update handler errors are still routed to ``log_event`` via
the same ``on_handler_error`` callback the daemon installed before.
"""

from __future__ import annotations

from typing import Callable, Optional

from ...telegram import api, poller
from ..adapter import SurfaceAdapter


class TelegramAdapter:
    """Adapter for Telegram bot transport.

    Args:
        on_handler_error: callback invoked when ``handle_update`` raises
            for a single update. Signature ``(update, exc) -> None``.
            Best-effort: the offset still advances so the failing update
            is not re-driven.
    """

    surface_type: str = "telegram"

    def __init__(
        self,
        on_handler_error: Optional[Callable[[poller.Update, Exception], None]] = None,
    ) -> None:
        self._on_handler_error = on_handler_error

    def receive(self, timeout: int = 1) -> int:
        return poller.run_poll_iteration(
            timeout=timeout,
            on_error=self._on_handler_error,
        )

    def send(self, chat_id: int, text: str) -> None:
        api.send_message(chat_id, text)


__all__ = ["TelegramAdapter", "SurfaceAdapter"]
