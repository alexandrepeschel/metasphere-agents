"""CLI for git hook installation + per-event handlers."""

from __future__ import annotations


DESCRIPTION = "Install/uninstall git hook shims + per-event handlers."

USAGE = """\
Usage: metasphere hooks git <command> [args...]

Admin commands:
  install [path] [--dry-run]
                          Install the metasphere git hook shims into
                          [path]/.git/hooks/ (default: cwd).
  uninstall [path]        Remove metasphere shims from [path].
  status [path]           Print hook installation status for [path].

Event handlers (invoked by the installed shims, not by hand):
  pre-commit
  post-commit
  post-checkout <prev> <new> <flag>
  pre-push <remote> <url>
"""

import sys
from pathlib import Path

from metasphere.git_hooks import (
    HOOKS,
    handle_post_checkout,
    handle_post_commit,
    handle_pre_commit,
    handle_pre_push,
    hooks_status,
    install_hooks,
    uninstall_hooks,
)
from metasphere.paths import resolve


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if not args:
        sys.stderr.write(USAGE)
        return 2
    cmd, *rest = args
    paths = resolve()

    if cmd == "install":
        dry_run = "--dry-run" in rest
        rest = [a for a in rest if a != "--dry-run"]
        target = Path(rest[0]) if rest else Path.cwd()
        try:
            written = install_hooks(target, dry_run=dry_run)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        verb = "would install" if dry_run else "installed"
        print(f"{verb}: {', '.join(written)}")
        return 0

    if cmd == "uninstall":
        target = Path(rest[0]) if rest else Path.cwd()
        removed = uninstall_hooks(target)
        print(f"removed: {', '.join(removed) if removed else '(none)'}")
        return 0

    if cmd == "status":
        target = Path(rest[0]) if rest else Path.cwd()
        for hook, state in hooks_status(target).items():
            print(f"  {hook}: {state}")
        return 0

    if cmd == "pre-commit":
        return handle_pre_commit(paths=paths)
    if cmd == "post-commit":
        return handle_post_commit(paths=paths)
    if cmd == "post-checkout":
        prev = rest[0] if len(rest) > 0 else ""
        new = rest[1] if len(rest) > 1 else ""
        flag = rest[2] if len(rest) > 2 else "0"
        return handle_post_checkout(prev, new, flag, paths=paths)
    if cmd == "pre-push":
        remote = rest[0] if len(rest) > 0 else ""
        url = rest[1] if len(rest) > 1 else ""
        return handle_pre_push(remote, url, paths=paths)

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
