"""CLI: ``metasphere session``.

    session list
    session info <@agent>
    session attach <@agent>
    session stop <@agent>
    session restart <@agent> [reason]
    session send <@agent> <message>
    session exit-self
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

from metasphere.agents import session_alive
from metasphere.events import log_event
from metasphere.gateway.session import _tmux
from metasphere.session import (
    _resolve_session,
    attach_to,
    list_sessions,
    restart_session,
    send_to_session,
    session_info,
    stop_session,
)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args

    if cmd in ("list", "ls"):
        rows = list_sessions()
        if not rows:
            print("(no metasphere sessions)")
            return 0
        for s in rows:
            mark = "●" if s.attached else "○"
            print(f"{mark} {s.agent:24} {s.name:32} windows={s.windows}")
        return 0

    if cmd == "info":
        if not rest:
            print("usage: session info <@agent>", file=sys.stderr)
            return 2
        s = session_info(rest[0])
        if not s:
            print(f"no session: {rest[0]}", file=sys.stderr)
            return 1
        print(f"name:     {s.name}")
        print(f"agent:    {s.agent}")
        print(f"windows:  {s.windows}")
        print(f"created:  {s.created}")
        print(f"attached: {s.attached}")
        return 0

    if cmd == "attach":
        if not rest:
            print("usage: session attach <@agent>", file=sys.stderr)
            return 2
        return attach_to(rest[0])

    if cmd == "stop":
        if not rest:
            print("usage: session stop <@agent>", file=sys.stderr)
            return 2
        ok = stop_session(rest[0])
        if ok:
            print(f"stopped {rest[0]}")
        else:
            print(f"no session for {rest[0]}", file=sys.stderr)
            return 1
        return 0

    if cmd == "restart":
        if not rest:
            print("usage: session restart <@agent> [reason]", file=sys.stderr)
            return 2
        agent = rest[0]
        reason = " ".join(rest[1:]) if len(rest) > 1 else "CLI restart"
        ok = restart_session(agent, reason)
        if ok:
            print(f"restarting {agent}: {reason}")
        else:
            print(f"no session for {agent}", file=sys.stderr)
            return 1
        return 0

    if cmd == "send":
        if len(rest) < 2:
            print("usage: session send <@agent> <message>", file=sys.stderr)
            return 2
        agent = rest[0]
        message = " ".join(rest[1:])
        ok = send_to_session(agent, message)
        if ok:
            print(f"sent to {agent}")
        else:
            print(f"no session for {agent}", file=sys.stderr)
            return 1
        return 0

    if cmd in ("exit-self", "exit_self"):
        # Schedule a /exit into the caller's tmux pane via a detached
        # background process. Resolves the caller from
        # $METASPHERE_AGENT_ID and mirrors the C-c x2 + C-u + /exit +
        # Enter x2 sequence used by ``gateway.session.restart_agent_session``.
        #
        # Why detached: this command is itself running in the caller's
        # pane (via the agent's Bash tool). Sending C-c via tmux
        # send-keys to that same pane delivers SIGINT to the pane's
        # foreground process — claude — which propagates the interrupt
        # to its child (this metasphere CLI process). Calling the
        # send-keys sequence inline would kill THIS process before
        # ``/exit`` could be delivered, leaving claude alive and the
        # pane zombied at "Interrupted · What should Claude do
        # instead?" (observed 2026-05-03 across all 4 research-monitor
        # cron fires). A detached child with start_new_session=True
        # survives the parent's death; a short pre-sleep gives the
        # caller's Bash tool time to return cleanly before the C-c
        # arrives.
        #
        # Behavior split downstream of /exit:
        # - Persistent agents (respawn loop running in pane shell):
        #   /exit kills claude → respawn loop spins fresh claude →
        #   watchdog injects continuation prompt. Pane stays alive.
        # - Ephemeral cron-fired agents (no respawn loop): /exit
        #   kills claude → pane idles at shell prompt. The
        #   ``reap_ephemeral_idle`` step in the lifecycle daemon
        #   completes cleanup within the configured threshold
        #   (default 30 min).
        caller = os.environ.get("METASPHERE_AGENT_ID")
        if not caller:
            print("Error: $METASPHERE_AGENT_ID not set", file=sys.stderr)
            return 1
        target = _resolve_session(caller)
        if not session_alive(target):
            print(
                f"Error: no live tmux session for {caller} "
                f"(resolved to {target}). exit-self only applies to "
                f"agents running in tmux; headless ``claude -p`` "
                f"ephemerals exit on their own.",
                file=sys.stderr,
            )
            return 1
        t = shlex.quote(target)
        # 2.5s pre-sleep: long enough for this Bash tool to return and
        # claude to begin emitting its final assistant text; short
        # enough that the agent's pane is freed promptly. The sleep
        # only delays the kill, not the agent — Stop hook fires
        # naturally on turn end either way.
        keystrokes_sh = (
            f"sleep 2.5; "
            f"tmux send-keys -t {t} C-c; sleep 0.3; "
            f"tmux send-keys -t {t} C-c; sleep 0.3; "
            f"tmux send-keys -t {t} C-u; sleep 0.2; "
            f"tmux send-keys -t {t} -l -- /exit; sleep 0.3; "
            f"tmux send-keys -t {t} Enter; sleep 0.4; "
            f"tmux send-keys -t {t} Enter"
        )
        subprocess.Popen(  # noqa: S603 — fixed argv, no shell=True
            ["bash", "-c", keystrokes_sh],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            log_event(
                "agent.exit_self",
                f"{caller} queued /exit for own session {target}",
                agent=caller,
                meta={"session": target},
            )
        except Exception:
            pass
        print(f"queued /exit for {target} ({caller}) in 2.5s")
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
