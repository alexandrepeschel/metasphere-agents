"""Uniform --help/-h handling across metasphere CLI shims.

Every ``metasphere.cli.<name>`` entrypoint must accept ``--help``/``-h``
as the first argument and exit cleanly with rc=0, printing a usage
message. Regression target: previously messages/tasks/agents/heartbeat/
schedule/trace/session/project all treated ``--help`` as an unknown
command.
"""

from __future__ import annotations

import importlib

import pytest


CLI_MODULES = [
    "messages",
    "tasks",
    "agents",
    "heartbeat",
    "schedule",
    "trace",
    "session",
    "project",
    "version",
]


@pytest.mark.parametrize("name", CLI_MODULES)
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_cli_help_exits_zero(name, flag, tmp_paths, capsys):
    mod = importlib.import_module(f"metasphere.cli.{name}")
    rc = mod.main([flag])
    assert rc == 0, f"{name} {flag} returned {rc}"
    out = capsys.readouterr().out
    assert out.strip(), f"{name} {flag} printed nothing on stdout"


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_status_help_does_not_run_status(flag, tmp_paths, capsys):
    """``metasphere status --help`` must print help, not query tmux/tasks.

    Regression: ``_status`` previously ignored argv and unconditionally
    rendered the live system summary, surprising users who passed
    ``--help`` expecting a usage message.
    """
    from metasphere.cli import main as _main

    rc = _main._status([flag])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "Sessions:" not in out
