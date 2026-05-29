"""Tests for the project-capsule auto-inference fallback (B4).

When MISSION.md frontmatter declares no project, ``_render_project_capsule``
falls back to ``_infer_project_for_agent``: path layout first
(``projects/<P>/agents/@<agent>/``), then name prefix
(``@<project>-<role>`` + ``projects/<project>/`` exists).

Frontmatter remains the explicit override. Inference is single-project;
multi-project still requires ``projects: [a, b]`` in frontmatter.

Trigger: @worldwire-eng's MISSION.md had no ``project:`` key, so the
T1 capsule no-op'd and the agent couldn't see its project's LEARNINGS.
Julian directive 2026-05-29 22:32Z (msg-1780093638): don't require
frontmatter; infer from layout.
"""

from __future__ import annotations

from pathlib import Path

from metasphere import context as ctx
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Seed helpers — two layouts: root-scope agent dir vs project-nested.
# ---------------------------------------------------------------------------


def _seed_root_agent_mission(
    tmp_paths: Paths, agent: str, frontmatter: str = ""
) -> Path:
    """Agent at ``~/.metasphere/agents/@<id>/`` (no project nesting)."""
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    fm_block = ("---\n" + frontmatter + "---\n\n") if frontmatter else ""
    (d / "MISSION.md").write_text(
        fm_block + "# Mission\n\nbody\n", encoding="utf-8",
    )
    return d


def _seed_project_nested_agent_mission(
    tmp_paths: Paths, project: str, agent: str, frontmatter: str = "",
) -> Path:
    """Agent at ``~/.metasphere/projects/<P>/agents/@<id>/``."""
    d = tmp_paths.project_agent_dir(project, agent)
    d.mkdir(parents=True, exist_ok=True)
    fm_block = ("---\n" + frontmatter + "---\n\n") if frontmatter else ""
    (d / "MISSION.md").write_text(
        fm_block + "# Mission\n\nbody\n", encoding="utf-8",
    )
    return d


def _seed_project_files(
    tmp_paths: Paths, project: str, *, learnings: str = "", memory: str = "",
) -> Path:
    pdir = tmp_paths.projects / project
    pdir.mkdir(parents=True, exist_ok=True)
    if learnings:
        (pdir / "LEARNINGS.md").write_text(learnings, encoding="utf-8")
    if memory:
        (pdir / "MEMORY.md").write_text(memory, encoding="utf-8")
    return pdir


# ---------------------------------------------------------------------------
# Probe 1: project-nested agent dir, NO frontmatter → path inference.
# ---------------------------------------------------------------------------


def test_path_inference_project_nested_agent(tmp_paths: Paths):
    _seed_project_nested_agent_mission(tmp_paths, "worldwire", "@probe1")
    _seed_project_files(
        tmp_paths, "worldwire",
        learnings="hetzner tunnel 670s pg idle timeout.",
    )

    out = ctx._render_project_capsule(tmp_paths, "@probe1")

    assert "## Project: worldwire" in out
    assert "hetzner tunnel 670s pg idle timeout." in out


# ---------------------------------------------------------------------------
# Probe 2: root-scope agent, NO frontmatter, name prefix matches a
# registered project → name inference.
# ---------------------------------------------------------------------------


def test_name_prefix_inference_root_scope_agent(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@worldwire-probe2")
    _seed_project_files(
        tmp_paths, "worldwire",
        memory="ricardo owns Louvain inheritance.",
    )

    out = ctx._render_project_capsule(tmp_paths, "@worldwire-probe2")

    assert "## Project: worldwire" in out
    assert "ricardo owns Louvain inheritance." in out


# ---------------------------------------------------------------------------
# Probe 3: name prefix doesn't match any project dir → no capsule.
# ---------------------------------------------------------------------------


def test_name_prefix_inference_no_match(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@no-project-match")
    # No project dirs exist beyond tmp_paths default.

    out = ctx._render_project_capsule(tmp_paths, "@no-project-match")

    assert out == ""


# ---------------------------------------------------------------------------
# Probe 4: explicit frontmatter wins over path inference.
# ---------------------------------------------------------------------------


def test_frontmatter_overrides_path_inference(tmp_paths: Paths):
    # Agent dir says "worldwire" (path inference would pick it), but
    # frontmatter declares "customproject" — frontmatter wins.
    _seed_project_nested_agent_mission(
        tmp_paths, "worldwire", "@probe4",
        frontmatter="project: customproject\n",
    )
    _seed_project_files(
        tmp_paths, "worldwire", learnings="should-not-render",
    )
    _seed_project_files(
        tmp_paths, "customproject", learnings="this-must-render",
    )

    out = ctx._render_project_capsule(tmp_paths, "@probe4")

    assert "## Project: customproject" in out
    assert "this-must-render" in out
    assert "## Project: worldwire" not in out
    assert "should-not-render" not in out


# ---------------------------------------------------------------------------
# Probe 5: explicit frontmatter wins over name-prefix inference.
# ---------------------------------------------------------------------------


def test_frontmatter_overrides_name_inference(tmp_paths: Paths):
    _seed_root_agent_mission(
        tmp_paths, "@worldwire-probe5",
        frontmatter="project: writing\n",
    )
    _seed_project_files(
        tmp_paths, "worldwire", learnings="inferred-but-should-not-render",
    )
    _seed_project_files(
        tmp_paths, "writing", memory="this-must-render",
    )

    out = ctx._render_project_capsule(tmp_paths, "@worldwire-probe5")

    assert "## Project: writing" in out
    assert "this-must-render" in out
    assert "## Project: worldwire" not in out


# ---------------------------------------------------------------------------
# Probe 6: multi-project frontmatter still works after the fallback
# is plumbed in (regression check on T1 list-projects path).
# ---------------------------------------------------------------------------


def test_multi_project_frontmatter_still_works(tmp_paths: Paths):
    _seed_root_agent_mission(
        tmp_paths, "@multi", frontmatter="projects: [a, b]\n",
    )
    _seed_project_files(tmp_paths, "a", learnings="a-learnings")
    _seed_project_files(tmp_paths, "b", memory="b-memory")

    out = ctx._render_project_capsule(tmp_paths, "@multi")

    assert "## Project: a" in out
    assert "a-learnings" in out
    assert "## Project: b" in out
    assert "b-memory" in out


# ---------------------------------------------------------------------------
# Probe 7: name-prefix uses FIRST dash split. ``@a-b-c`` with both
# ``a`` and ``a-b`` registered should resolve to ``a`` per the
# documented contract — first-dash split is what the helper does.
# ---------------------------------------------------------------------------


def test_name_prefix_first_dash_split(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@a-b-c")
    _seed_project_files(tmp_paths, "a", learnings="picked-first-segment")
    _seed_project_files(tmp_paths, "a-b", learnings="should-not-pick")

    out = ctx._render_project_capsule(tmp_paths, "@a-b-c")

    assert "## Project: a" in out
    assert "picked-first-segment" in out
    assert "## Project: a-b" not in out


# ---------------------------------------------------------------------------
# Probe 8: name prefix without a matching project dir → no-op.
# ---------------------------------------------------------------------------


def test_inference_skips_when_project_dir_missing(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@ghost-probe")
    # projects/ghost/ does NOT exist.

    out = ctx._render_project_capsule(tmp_paths, "@ghost-probe")

    assert out == ""


# ---------------------------------------------------------------------------
# Helper-level unit coverage for _infer_project_for_agent.
# ---------------------------------------------------------------------------


def test_infer_returns_none_for_unprojected_root_agent(tmp_paths: Paths):
    agent_dir = _seed_root_agent_mission(tmp_paths, "@solo")
    assert ctx._infer_project_for_agent(
        tmp_paths, "@solo", agent_dir,
    ) is None


def test_infer_returns_project_for_nested_agent(tmp_paths: Paths):
    _seed_project_files(tmp_paths, "worldwire")
    agent_dir = _seed_project_nested_agent_mission(
        tmp_paths, "worldwire", "@inner",
    )
    assert ctx._infer_project_for_agent(
        tmp_paths, "@inner", agent_dir,
    ) == "worldwire"


def test_infer_path_dominates_name_prefix(tmp_paths: Paths):
    # Agent dir layout says "writing"; name prefix says "worldwire".
    # Path wins.
    _seed_project_files(tmp_paths, "writing")
    _seed_project_files(tmp_paths, "worldwire")
    agent_dir = _seed_project_nested_agent_mission(
        tmp_paths, "writing", "@worldwire-eng",
    )
    assert ctx._infer_project_for_agent(
        tmp_paths, "@worldwire-eng", agent_dir,
    ) == "writing"


def test_infer_strips_at_prefix(tmp_paths: Paths):
    # The agent identifier is sometimes passed without the leading @.
    _seed_project_files(tmp_paths, "worldwire")
    agent_dir = _seed_root_agent_mission(tmp_paths, "@worldwire-probe")
    assert ctx._infer_project_for_agent(
        tmp_paths, "worldwire-probe", agent_dir,
    ) == "worldwire"
