"""Tests for ``metasphere session exit-self``.

Phase H: exit-self schedules ``/exit`` for the caller's own tmux pane.
Replaces the deferred-command marker path, which couldn't fire on
empty REPL panes (cron-fired single-shot sessions emit no Stop hook).

The kill sequence (C-c x2 + C-u + ``/exit`` literal + Enter x2) runs
inside a detached background process spawned via ``subprocess.Popen``
with ``start_new_session=True``. Running it inline would be fatal:
``tmux send-keys C-c`` against the caller's own pane delivers SIGINT
to claude, which propagates to its currently-running Bash tool — i.e.
this metasphere CLI process — killing it before ``/exit`` can be
delivered. Detaching survives the parent's death.
"""

from __future__ import annotations

from unittest.mock import patch

from metasphere.cli import session as cli_session


def _agent_record(name: str, project: str = ""):
    """Minimal AgentRecord stand-in for resolver tests."""
    from metasphere.agents import AgentRecord

    return AgentRecord(
        name=name,
        scope="",
        parent="",
        status="",
        spawned_at="",
        project=project,
    )


def test_exit_self_schedules_kill_via_detached_subprocess(monkeypatch):
    """Happy path: agent set, session alive → a detached Popen fires
    the C-c x2 + /exit + Enter x2 sequence. The main process MUST NOT
    call ``_tmux`` directly — that would self-interrupt this CLI
    process before /exit could land in the caller's pane.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker-cron-1")

    popen_calls: list[dict] = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            popen_calls.append({"args": args, "kwargs": kwargs})

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-worker-cron-1",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session._tmux",
        side_effect=AssertionError(
            "exit-self must NOT call _tmux from the main process — that "
            "would self-interrupt the caller's Bash tool. Use a "
            "detached subprocess instead."
        ),
    ), patch(
        "metasphere.cli.session.subprocess.Popen", _FakePopen
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    assert len(popen_calls) == 1, f"expected one detached Popen, got {popen_calls}"
    call = popen_calls[0]

    # Detached: must use start_new_session so the child survives parent death.
    assert call["kwargs"].get("start_new_session") is True, (
        "Popen must set start_new_session=True so the kill sequence "
        f"survives the parent metasphere CLI exiting. kwargs={call['kwargs']}"
    )

    # Argv shape: ["bash", "-c", "<script>"]
    assert call["args"][:2] == ["bash", "-c"]
    script = call["args"][2]

    # The script must target the resolved session name and include the
    # full restart_agent_session-style sequence.
    assert "metasphere-worker-cron-1" in script
    assert "C-c" in script
    assert "C-u" in script
    assert "/exit" in script
    assert "Enter" in script
    # /exit must be sent with -l -- so flags inside the payload aren't
    # parsed by tmux.
    assert "-l -- /exit" in script
    # Pre-sleep delays the kill so the caller's Bash tool can return.
    assert "sleep" in script


def test_exit_self_no_agent_env_returns_1(monkeypatch, capsys):
    """No $METASPHERE_AGENT_ID → exit code 1 + stderr message, no kill spawn."""
    monkeypatch.delenv("METASPHERE_AGENT_ID", raising=False)

    with patch(
        "metasphere.cli.session.subprocess.Popen",
        side_effect=AssertionError("Popen must not be called"),
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "METASPHERE_AGENT_ID" in err


def test_exit_self_headless_no_tmux_returns_1(monkeypatch, capsys):
    """Agent has no live tmux session (headless ``claude -p``) →
    exit code 1, clean stderr, no crash, no kill spawn."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@headless-spawn")

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-headless-spawn",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=False
    ), patch(
        "metasphere.cli.session.subprocess.Popen",
        side_effect=AssertionError("Popen must not be called"),
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "@headless-spawn" in err
    assert "metasphere-headless-spawn" in err


def test_exit_self_emits_agent_exit_self_event(monkeypatch):
    """Successful exit-self appends an ``agent.exit_self`` record so the
    silent-success path is observable in the events log. Without this
    emit, a cron-fired session that exits cleanly leaves no trace
    between ``agent.session`` (start) and the next reap sweep.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker-cron-1")

    recorded: list[dict] = []

    def _fake_log_event(type_, message, *, agent=None, meta=None, **_kw):
        recorded.append(
            {"type": type_, "message": message, "agent": agent, "meta": meta or {}}
        )

    class _FakePopen:
        def __init__(self, *_args, **_kwargs):
            pass

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-worker-cron-1",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session.subprocess.Popen", _FakePopen
    ), patch(
        "metasphere.cli.session.log_event", side_effect=_fake_log_event
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    exit_evts = [r for r in recorded if r["type"] == "agent.exit_self"]
    assert len(exit_evts) == 1, f"expected one agent.exit_self event, got {recorded}"
    evt = exit_evts[0]
    assert evt["agent"] == "@worker-cron-1"
    assert evt["meta"].get("session") == "metasphere-worker-cron-1"


def test_exit_self_event_emit_failure_does_not_break_exit(monkeypatch):
    """If ``log_event`` raises (disk full, permissions, etc), the actual
    kill spawn must still complete and the call must still return 0 —
    observability is best-effort, the kill is load-bearing.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker-cron-1")

    spawned: list[bool] = []

    class _FakePopen:
        def __init__(self, *_args, **_kwargs):
            spawned.append(True)

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-worker-cron-1",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session.subprocess.Popen", _FakePopen
    ), patch(
        "metasphere.cli.session.log_event",
        side_effect=OSError("disk full"),
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    assert spawned, "kill spawn must run even when log_event fails"


def test_exit_self_resolves_project_scoped_agent(monkeypatch):
    """Project-scoped agents must resolve to the project-prefixed session
    name, not the bare ``session_name_for`` form. Regression mirrors the
    bug class fixed in 107c792 for ``_check_deferred_command``'s resolver.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@accelerator-programs")

    popen_calls: list[dict] = []

    class _FakePopen:
        def __init__(self, args, **kwargs):
            popen_calls.append({"args": args, "kwargs": kwargs})

    rec = _agent_record("@accelerator-programs", project="research")

    # Drive the real ``_resolve_session`` so the project lookup is exercised.
    with patch(
        "metasphere.session.list_agents", return_value=[rec]
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session.subprocess.Popen", _FakePopen
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    assert popen_calls, "expected one detached Popen for the kill"
    script = popen_calls[0]["args"][2]
    expected = "metasphere-research-accelerator-programs"
    assert expected in script, (
        f"detached kill script must target project-scoped session "
        f"{expected!r}; got script={script!r}"
    )
    # Verify the bare (un-prefixed) session name is NOT what we send to.
    bare_pattern = "tmux send-keys -t metasphere-accelerator-programs "
    assert bare_pattern not in script, (
        "detached kill script must not target the bare session name "
        "(regression: 04-28 project-scope resolver bug)"
    )
