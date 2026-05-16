"""Tests for ``metasphere agent`` — list/status/spawn/wake CLI surface.

The list-tree behavior (KNOWN_ISSUES "Agent tree doesn't look like a
tree") groups persistent agents by project: global agents (empty
project sidecar) under a ``global/`` header, then each project's
agents under ``<project>/`` alphabetically. Within each bucket
agents are sorted by name.
"""

from __future__ import annotations

import pytest

from metasphere.cli import agents as cli_agents
from metasphere.paths import Paths


def _make_agent(d, *, mission: str = "m", project_sidecar: str = ""):
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text(mission)
    (d / "scope").write_text("/")
    (d / "parent").write_text("@orchestrator")
    (d / "status").write_text("spawned")
    (d / "spawned_at").write_text("2026-04-07T00:00:00Z")
    if project_sidecar:
        (d / "project").write_text(project_sidecar)


def test_list_groups_global_first_then_projects(tmp_paths: Paths, capsys, monkeypatch):
    """Global agents (empty project) print under ``global/``; project-
    scoped agents print under ``<project>/`` headers, alphabetically."""
    # Force session_alive() False so output uses the dormant marker.
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)

    # Two global agents
    _make_agent(tmp_paths.agents / "@orchestrator")
    _make_agent(tmp_paths.agents / "@julian")
    # Two project-scoped agents under distinct projects
    _make_agent(tmp_paths.project_agents_dir("worldwire") / "@worldwire-eng")
    _make_agent(tmp_paths.project_agents_dir("worldwire") / "@worldwire-lead")
    _make_agent(tmp_paths.project_agents_dir("recurse") / "@recurse-eng")

    rc = cli_agents._list()
    out, _ = capsys.readouterr()
    assert rc == 0

    lines = [ln for ln in out.splitlines() if ln.strip()]

    # Header first
    assert lines[0] == "Persistent agents (have MISSION.md):"

    # global/ bucket appears before project buckets
    assert "  global/" in lines
    assert "  recurse/" in lines
    assert "  worldwire/" in lines
    g_idx = lines.index("  global/")
    r_idx = lines.index("  recurse/")
    w_idx = lines.index("  worldwire/")
    assert g_idx < r_idx < w_idx, (
        f"Expected global < recurse < worldwire, got {g_idx} {r_idx} {w_idx}"
    )

    # Each agent prints with 4-space indent + dormant marker
    assert "    ○ @julian" in lines
    assert "    ○ @orchestrator" in lines
    assert "    ○ @recurse-eng" in lines
    assert "    ○ @worldwire-eng" in lines
    assert "    ○ @worldwire-lead" in lines


def test_list_omits_global_bucket_when_no_global_agents(
    tmp_paths: Paths, capsys, monkeypatch,
):
    """If every persistent agent is project-scoped, no ``global/``
    header should print — only the project buckets that actually have
    members."""
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    _make_agent(tmp_paths.project_agents_dir("worldwire") / "@worldwire-eng")

    rc = cli_agents._list()
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "global/" not in out
    assert "  worldwire/" in out
    assert "    ○ @worldwire-eng" in out


def test_list_project_filter_still_works_under_tree_layout(
    tmp_paths: Paths, capsys, monkeypatch,
):
    """``metasphere agent list <project>`` continues to filter to a
    single project. The bucket header still prints so the format is
    consistent across filtered/unfiltered views."""
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    _make_agent(tmp_paths.agents / "@orchestrator")
    _make_agent(tmp_paths.project_agents_dir("worldwire") / "@worldwire-eng")
    _make_agent(tmp_paths.project_agents_dir("recurse") / "@recurse-eng")

    rc = cli_agents._list(project_filter="worldwire")
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "@worldwire-eng" in out
    assert "@recurse-eng" not in out
    assert "@orchestrator" not in out


def test_list_no_persistent_agents_prints_message(
    tmp_paths: Paths, capsys, monkeypatch,
):
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    rc = cli_agents._list()
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "No persistent agents." in out


# ---------------------------------------------------------------------------
# spawn_main flag-shape rejection (mirrors df6812e project-init fix)
# ---------------------------------------------------------------------------

def test_spawn_main_help_prints_usage(capsys):
    rc = cli_agents.spawn_main(["--help"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "metasphere agent spawn" in out


def test_spawn_main_short_help_prints_usage(capsys):
    rc = cli_agents.spawn_main(["-h"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "metasphere agent spawn" in out


def test_spawn_main_rejects_flag_shaped_agent_name(
    tmp_paths: Paths, capsys, monkeypatch,
):
    """``metasphere agent spawn --typo /scope/ "task"`` previously
    persisted ``@--typo`` to disk — the same argv-leak class as the
    ghost ``--help`` project fixed in df6812e. The library-layer
    validator now refuses it; the CLI renders the ValueError as a
    clean stderr line + exit 2.
    """
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    rc = cli_agents.spawn_main(["--bogus", "/", "do thing", "@orchestrator"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err
    assert not (tmp_paths.agents / "@--bogus").exists()
    assert not (tmp_paths.agents / "--bogus").exists()


def test_agent_seed_rejects_flag_shaped_name(
    tmp_paths: Paths, capsys,
):
    """``metasphere agent seed --spec foo @--bogus`` previously
    seeded a persona stack under ``@--bogus/`` — same argv-leak class
    as spawn. seed_agent now validates names; the CLI renders the
    ValueError as a clean stderr line + exit 2.
    """
    # Create a minimal spec so the lookup succeeds before seeding.
    spec_dir = tmp_paths.project_root / "specs" / "researcher"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "config.md").write_text(
        "---\nname: researcher\nrole: researcher\ndescription: t\n"
        "sandbox: scoped\npersistent: true\n---\n"
    )
    (spec_dir / "SOUL.md").write_text("# {{agent_id}}\n")
    (spec_dir / "MISSION.md").write_text("# {{agent_id}}\n")

    rc = cli_agents.main(["seed", "--spec", "researcher", "@--bogus"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err
    assert not (tmp_paths.agents / "@--bogus").exists()
