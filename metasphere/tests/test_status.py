"""Tests for metasphere.status.summary()."""

from __future__ import annotations

from metasphere import status, tasks as t


def test_summary_reports_task_count(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@tester")
    t.create_task("Alpha", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.create_task("Beta", "!high", tmp_paths.scope, tmp_paths.project_root)

    out = status.summary()

    assert "Tasks: 2 active" in out
    assert "Tasks: (unavailable)" not in out


def test_summary_tasks_zero_when_empty(tmp_paths):
    out = status.summary()
    assert "Tasks: 0 active" in out


def test_summary_surfaces_exception_diagnostic(tmp_paths, monkeypatch):
    """Bug-masking guard: a broken subsystem must surface the exception
    inline, not silently render as `(unavailable)`. The original
    `list_tasks` TypeError (KNOWN_ISSUES.md, fixed 2026-04-24) sat
    masked for weeks because the bare handler ate the diagnostic.
    """

    def boom(*_args, **_kwargs):
        raise TypeError("simulated wiring breakage")

    monkeypatch.setattr("metasphere.tasks.list_tasks", boom)

    out = status.summary()

    assert "Tasks: (unavailable: TypeError: simulated wiring breakage)" in out
