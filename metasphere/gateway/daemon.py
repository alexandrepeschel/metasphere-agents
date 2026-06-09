"""Gateway daemon: poll telegram, inject inbound messages, run watchdog.

This is the loop that ties session lifecycle, telegram polling, and the
watchdog together. It is intentionally bulletproof: every loop step is
wrapped in try/except so a single iteration failure cannot exit the
daemon. Every loop step is wrapped in try/except so a single iteration
failure cannot exit the process.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional

from ..events import log_event
from ..paths import Paths, resolve
from .adapter import SurfaceAdapter
from .adapters.telegram import TelegramAdapter
from .session import ensure_session, write_harness_hash_baseline
from .watchdog import run_watchdog


def _log_telegram_handler_error(u, exc) -> None:
    """Log a per-update handler failure to the ``@gateway`` event stream.

    Best-effort; never raises. Wired into :class:`TelegramAdapter` so the
    poller's ``on_error`` hook keeps emitting ``telegram.handle_error``
    events exactly as it did before the adapter refactor.
    """
    try:
        log_event(
            "telegram.handle_error",
            f"handle_update raised for update {u.update_id}: {exc}",
            agent="@gateway",
        )
    except Exception:
        pass


def _default_adapters() -> List[SurfaceAdapter]:
    """Adapters wired by default when ``run_daemon`` is called without an
    explicit list. Today: telegram only — additional surfaces register
    here as they land."""
    return [TelegramAdapter(on_handler_error=_log_telegram_handler_error)]


def _poll_once(
    timeout: int = 25,
    *,
    adapters: Optional[List[SurfaceAdapter]] = None,
) -> int:
    """Drive every adapter once; sum the inbound counts.

    Single source of truth for the daemon's per-tick polling. The
    daemon's outer loop binds its own adapter list (built once in
    ``run_daemon``) into a ``poll_fn`` that calls this helper —
    that way the same adapter instances drive every tick (matters
    for any adapter that holds connection state).

    ``timeout=25``: Telegram holds the connection open for up to 25s
    when there are no pending updates, returning immediately when a
    message arrives. Combined with no inter-poll sleep this gives
    sub-second message delivery in the happy path.

    ``adapters=None`` falls back to :func:`_default_adapters`. That
    branch keeps the bare ``gw_daemon._poll_once()`` test seam working
    for callers that don't care about instance identity (the existing
    photo-routing tests, which monkeypatch the poller/api layer
    underneath).
    """
    if adapters is None:
        adapters = _default_adapters()
    total = 0
    for adapter in adapters:
        total += adapter.receive(timeout=timeout)
    return total


def run_daemon(
    paths: Optional[Paths] = None,
    poll_interval: float = 0.5,
    watchdog_interval: float = 5.0,
    dormancy_interval: float = 300.0,
    dormancy_max_idle_seconds: int = 86400,
    *,
    adapters: Optional[List[SurfaceAdapter]] = None,
    stop: Optional[Callable[[], bool]] = None,
    poll_fn: Optional[Callable[[], int]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    time_fn: Optional[Callable[[], float]] = None,
    reap_dormant_fn: Optional[Callable[[Paths, int], list[str]]] = None,
    reap_crashed_fn: Optional[Callable[[Paths], list[str]]] = None,
    reap_ephemeral_idle_fn: Optional[Callable[[Paths, int], list[str]]] = None,
    ephemeral_idle_max_seconds: int = 1800,
) -> None:
    """Run the gateway daemon forever.

    ``adapters`` is the list of :class:`SurfaceAdapter` instances driven
    on each poll tick. ``None`` falls back to :func:`_default_adapters`
    (telegram only today). Pass an explicit list to register additional
    surfaces (web chat, email, webhook, …) alongside or instead of
    telegram. Ignored when ``poll_fn`` is also supplied — that path is
    the test seam.

    The injection points (``poll_fn``, ``sleep_fn``, ``time_fn``,
    ``stop``, ``reap_dormant_fn``, ``reap_crashed_fn``,
    ``reap_ephemeral_idle_fn``) exist for tests so a single iteration
    failure can be asserted to NOT exit the daemon. Production callers
    leave them at None and the daemon never returns.

    ``dormancy_interval`` is how often ``reap_dormant``, ``reap_crashed``,
    AND ``reap_ephemeral_idle`` are swept (default 5 min). They share
    the cadence because all three are session-hygiene sweeps with the
    same per-tick cost shape (O(N) over agent dirs / tmux sessions);
    splitting them would only duplicate the bookkeeping.
    ``dormancy_max_idle_seconds`` is the per-session idle TTL before a
    persistent agent is transitioned to ``dormant:`` status and its
    tmux session killed (default 24h). ``ephemeral_idle_max_seconds``
    is the shorter idle TTL applied to ephemeral (one-shot, no respawn
    loop) sessions whose claude REPL has exited but whose pane is
    sitting at a shell prompt (default 30 min).
    """
    paths = paths or resolve()
    if poll_fn is None:
        # Build the adapter list ONCE here (not per tick) so any adapter
        # holding connection / session state keeps it across iterations.
        # ``adapters`` lets a caller (CLI, future config) register additional
        # surfaces alongside telegram; ``None`` falls back to the default
        # list (telegram only, today). Test callers that pass ``poll_fn``
        # directly skip this branch entirely.
        adapter_list: List[SurfaceAdapter] = (
            adapters if adapters is not None else _default_adapters()
        )

        def poll_fn() -> int:
            return _poll_once(adapters=adapter_list)
    sleep_fn = sleep_fn or time.sleep
    time_fn = time_fn or time.time
    if reap_dormant_fn is None:
        from ..agents import reap_dormant as _reap_dormant

        def reap_dormant_fn(p: Paths, idle: int) -> list[str]:
            return _reap_dormant(p, max_idle_seconds=idle)
    if reap_crashed_fn is None:
        from ..agents import reap_crashed as _reap_crashed

        def reap_crashed_fn(p: Paths) -> list[str]:
            return _reap_crashed(p)
    if reap_ephemeral_idle_fn is None:
        from ..agents import reap_ephemeral_idle as _reap_ephemeral_idle

        def reap_ephemeral_idle_fn(p: Paths, idle: int) -> list[str]:
            return _reap_ephemeral_idle(p, max_idle_seconds=idle)

    # Refresh harness hash baseline at boot so an existing-on-startup
    # session uses the latest harness as its drift reference.
    try:
        write_harness_hash_baseline(paths)
    except Exception:
        pass

    try:
        ensure_session(paths)
    except Exception as e:
        try:
            log_event(
                "supervisor.daemon_error",
                f"ensure_session failed at boot: {e}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass

    # Republish slash command manifest to BotFather via setMyCommands.
    # This makes registration automatic on every daemon restart, so any
    # change to BOT_COMMANDS_MANIFEST takes effect by simply restarting
    # the gateway (which already happens after every code deploy).
    # Best-effort: a network blip must NOT block the daemon from booting.
    try:
        from ..telegram.commands import register_bot_commands

        register_bot_commands()
    except Exception as e:
        try:
            log_event(
                "supervisor.daemon_error",
                f"register_bot_commands failed at boot: {e}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass

    # so the watchdog fires on the first iteration. This is safe because
    # the daemon no longer flap-restarts; the 10s rate-limit marker inside
    # check_safety_hooks_confirmation is the defence-in-depth.
    last_watchdog = -float("inf")
    last_dormancy = -float("inf")
    while True:
        if stop is not None and stop():
            return

        # 1) Telegram poll. A failure here must NOT exit the daemon.
        try:
            poll_fn()
        except Exception as e:
            try:
                log_event(
                    "supervisor.daemon_error",
                    f"poll_fn raised: {e}",
                    agent="@daemon-supervisor",
                    paths=paths,
                )
            except Exception:
                pass

        # 2) Watchdog tick.
        now = time_fn()
        if now - last_watchdog >= watchdog_interval:
            try:
                run_watchdog(paths)
            except Exception as e:
                try:
                    log_event(
                        "supervisor.daemon_error",
                        f"run_watchdog raised: {e}",
                        agent="@daemon-supervisor",
                        paths=paths,
                    )
                except Exception:
                    pass
            last_watchdog = now

        # 3) Dormancy tick: sweep idle persistent agents on a longer
        # cadence than the watchdog (default 5 min). The per-sweep cost
        # is O(N) tmux probes where N = persistent agents alive, so 5
        # min is ample; finer cadence wastes cycles without catching
        # the 24h-idle transition any sooner.
        if now - last_dormancy >= dormancy_interval:
            try:
                reaped = reap_dormant_fn(paths, dormancy_max_idle_seconds)
                if reaped:
                    log_event(
                        "agent.dormant.reap",
                        f"reap_dormant transitioned {len(reaped)} agent(s): {reaped}",
                        agent="@daemon-supervisor",
                        meta={"agents": reaped},
                        paths=paths,
                    )
            except Exception as e:
                try:
                    log_event(
                        "supervisor.daemon_error",
                        f"reap_dormant raised: {e}",
                        agent="@daemon-supervisor",
                        paths=paths,
                    )
                except Exception:
                    pass
            # Crash sweep shares the dormancy cadence — silent-death
            # detection is the same shape of session-hygiene scan and
            # there's no reason to wake the daemon more often for it.
            # A failure here MUST NOT prevent reap_dormant from running
            # again next tick (and vice-versa) — hence the independent
            # try/except blocks rather than one wrapping both.
            try:
                crashed = reap_crashed_fn(paths)
                if crashed:
                    log_event(
                        "agent.crashed.reap",
                        f"reap_crashed transitioned {len(crashed)} agent(s): {crashed}",
                        agent="@daemon-supervisor",
                        meta={"agents": crashed},
                        paths=paths,
                    )
            except Exception as e:
                try:
                    log_event(
                        "supervisor.daemon_error",
                        f"reap_crashed raised: {e}",
                        agent="@daemon-supervisor",
                        paths=paths,
                    )
                except Exception:
                    pass
            # Ephemeral-idle sweep: kills tmux panes belonging to
            # one-shot agents whose claude REPL has exited (e.g. after
            # exit-self) and which would otherwise zombie indefinitely
            # — reap_dormant only handles persistent agents.
            try:
                ephemeral = reap_ephemeral_idle_fn(paths, ephemeral_idle_max_seconds)
                if ephemeral:
                    log_event(
                        "agent.ephemeral_idle.reap.batch",
                        f"reap_ephemeral_idle killed {len(ephemeral)} session(s): {ephemeral}",
                        agent="@daemon-supervisor",
                        meta={"sessions": ephemeral},
                        paths=paths,
                    )
            except Exception as e:
                try:
                    log_event(
                        "supervisor.daemon_error",
                        f"reap_ephemeral_idle raised: {e}",
                        agent="@daemon-supervisor",
                        paths=paths,
                    )
                except Exception:
                    pass
            last_dormancy = now

        # 4) Sleep.
        try:
            sleep_fn(poll_interval)
        except Exception:
            return
