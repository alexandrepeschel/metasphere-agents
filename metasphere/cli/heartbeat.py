"""CLI for the heartbeat daemon."""

from __future__ import annotations

DESCRIPTION = "Per-tick heartbeat: one-shot or long-running daemon."

USAGE = """\
Usage: metasphere heartbeat [<command>] [args...]

Commands:
  (no args)                    One-shot tick (alias for `once`).
  once                         One-shot tick.
  check                        One-shot tick.
  daemon [<interval-seconds>]  Run forever; default interval 30s.

Options:
  --invoke-agent               Inject the per-turn context block into
                               every active agent on each tick. Equivalent
                               to setting HEARTBEAT_INVOKE_AGENT=true in
                               the environment.

Runs as the `metasphere-heartbeat.service` systemd user unit with
HEARTBEAT_INVOKE_AGENT=true so agent REPLs receive the ~5-minute
context refresh.
"""


import os
import sys

from metasphere.heartbeat import heartbeat_daemon, heartbeat_once
from metasphere.paths import resolve


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0

    invoke_agent = os.environ.get("HEARTBEAT_INVOKE_AGENT", "").lower() == "true"
    if "--invoke-agent" in args:
        invoke_agent = True
        args = [a for a in args if a != "--invoke-agent"]

    paths = resolve()

    if not args or args[0] in ("once", "check"):
        heartbeat_once(paths, invoke_agent=invoke_agent)
        return 0

    if args[0] == "daemon":
        interval = 30
        if len(args) > 1:
            try:
                interval = int(args[1])
            except ValueError:
                print(f"invalid interval: {args[1]}", file=sys.stderr)
                return 2
        heartbeat_daemon(
            paths,
            interval_seconds=interval,
            invoke_agent=invoke_agent,
        )
        return 0

    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
