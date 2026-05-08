"""``metasphere status`` — pure-Python system status summary."""

from __future__ import annotations

import sys


DESCRIPTION = "Print a single-screen system summary."

USAGE = """\
Usage: metasphere status

Print a single-screen summary of the running system: tmux agent
sessions (grouped by liveness), active task count, enabled cron job
count, initialized projects, and orchestrator liveness.

Takes no arguments.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    from metasphere.status import summary
    sys.stdout.write(summary() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
