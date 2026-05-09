"""Tests for the gateway SurfaceAdapter interface and TelegramAdapter.

Scope: structural prep only — the adapter refactor must not change
runtime behavior. These tests cover:

- :class:`TelegramAdapter` satisfies the :class:`SurfaceAdapter` Protocol.
- :class:`TelegramAdapter.receive` delegates to ``poller.run_poll_iteration``.
- :class:`TelegramAdapter.send` delegates to ``api.send_message``.
- The daemon's default ``poll_fn`` drives every registered adapter once
  per tick (so a custom adapter list is honoured).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from metasphere.gateway import daemon as gw_daemon
from metasphere.gateway.adapter import SurfaceAdapter
from metasphere.gateway.adapters.telegram import TelegramAdapter
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_telegram_adapter_implements_surface_adapter_protocol():
    adapter = TelegramAdapter()
    assert isinstance(adapter, SurfaceAdapter)
    assert adapter.surface_type == "telegram"


def test_surface_type_is_class_attribute():
    """``surface_type`` is set on the class so the daemon can look it up
    without instantiating an adapter (e.g. when reporting which surfaces
    are wired)."""
    assert TelegramAdapter.surface_type == "telegram"


# ---------------------------------------------------------------------------
# TelegramAdapter — receive() delegates to poller
# ---------------------------------------------------------------------------


def test_telegram_adapter_receive_calls_run_poll_iteration():
    adapter = TelegramAdapter()
    with patch(
        "metasphere.gateway.adapters.telegram.poller.run_poll_iteration",
        return_value=3,
    ) as run_poll:
        n = adapter.receive(timeout=5)

    assert n == 3
    run_poll.assert_called_once()
    kwargs = run_poll.call_args.kwargs
    assert kwargs["timeout"] == 5
    # on_error wired to the adapter's stored callback (None unless set).
    assert kwargs["on_error"] is None


def test_telegram_adapter_passes_handler_error_callback():
    """``on_handler_error`` from __init__ propagates to the poller's
    ``on_error`` so per-update failures keep getting logged."""
    callback = MagicMock()
    adapter = TelegramAdapter(on_handler_error=callback)
    with patch(
        "metasphere.gateway.adapters.telegram.poller.run_poll_iteration",
        return_value=0,
    ) as run_poll:
        adapter.receive()

    assert run_poll.call_args.kwargs["on_error"] is callback


# ---------------------------------------------------------------------------
# TelegramAdapter — send() delegates to api.send_message
# ---------------------------------------------------------------------------


def test_telegram_adapter_send_calls_api_send_message():
    adapter = TelegramAdapter()
    with patch(
        "metasphere.gateway.adapters.telegram.api.send_message"
    ) as send_message:
        adapter.send(12345, "hello")

    send_message.assert_called_once_with(12345, "hello")


# ---------------------------------------------------------------------------
# Daemon routes through registered adapters
# ---------------------------------------------------------------------------


def test_run_daemon_drives_registered_adapter_each_tick(tmp_paths: Paths):
    """When ``adapters=[fake]`` is passed, the daemon's default poll_fn
    must call ``fake.receive`` once per loop iteration. Proves the loop
    is no longer hard-coded to telegram."""
    fake = MagicMock(spec=SurfaceAdapter)
    fake.surface_type = "fake"
    fake.receive.return_value = 0

    iterations = {"n": 0}

    def stop():
        iterations["n"] += 1
        return iterations["n"] > 3

    with patch.object(gw_daemon, "ensure_session"), \
         patch.object(gw_daemon, "run_watchdog"):
        gw_daemon.run_daemon(
            tmp_paths,
            poll_interval=0.0,
            watchdog_interval=10_000.0,  # don't fire watchdog in this test
            stop=stop,
            adapters=[fake],
            sleep_fn=lambda s: None,
            time_fn=lambda: 0.0,
        )

    # Loop ran 3 iterations (stop returns True on the 4th call).
    assert iterations["n"] == 4
    assert fake.receive.call_count == 3


def test_run_daemon_drives_multiple_adapters_per_tick(tmp_paths: Paths):
    """Two registered adapters → both get their ``receive`` called every
    tick; counts sum into the daemon's poll-tick total."""
    a = MagicMock(spec=SurfaceAdapter)
    a.surface_type = "alpha"
    a.receive.return_value = 1
    b = MagicMock(spec=SurfaceAdapter)
    b.surface_type = "beta"
    b.receive.return_value = 2

    iterations = {"n": 0}

    def stop():
        iterations["n"] += 1
        return iterations["n"] > 2

    with patch.object(gw_daemon, "ensure_session"), \
         patch.object(gw_daemon, "run_watchdog"):
        gw_daemon.run_daemon(
            tmp_paths,
            poll_interval=0.0,
            watchdog_interval=10_000.0,
            stop=stop,
            adapters=[a, b],
            sleep_fn=lambda s: None,
            time_fn=lambda: 0.0,
        )

    assert a.receive.call_count == 2
    assert b.receive.call_count == 2


def test_run_daemon_default_adapters_includes_telegram(tmp_paths: Paths):
    """Smoke test: when no ``adapters`` are passed, the default list
    contains a TelegramAdapter so production behavior is unchanged."""
    defaults = gw_daemon._default_adapters()

    assert len(defaults) == 1
    assert isinstance(defaults[0], TelegramAdapter)
    assert defaults[0].surface_type == "telegram"


def test_poll_once_routes_through_telegram_adapter():
    """``_poll_once`` is the default ``poll_fn`` and must drive the
    telegram adapter (not call the poller directly), so additional
    adapters added to ``_default_adapters`` are also driven."""
    with patch(
        "metasphere.gateway.adapters.telegram.poller.run_poll_iteration",
        return_value=7,
    ) as run_poll:
        n = gw_daemon._poll_once(timeout=2)

    assert n == 7
    run_poll.assert_called_once()
    assert run_poll.call_args.kwargs["timeout"] == 2
