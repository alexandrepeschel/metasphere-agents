"""Tests for B5 — relevance-selected project memory rendering.

B4 (b245aa3) shipped the project-capsule auto-inference fallback, so
``@worldwire-eng``'s capsule now resolves to project ``worldwire``
without MISSION.md frontmatter. But canonical end-to-end (Hetzner
tunnel 670s repro) still failed because ``LEARNINGS.md`` is ~38KB
while the per-file budget is 2KB, and the head-of-file truncator
buried the load-bearing entry ~15KB deep. Inference was correct;
selection was dumb.

B5 replaces head-of-file byte truncation with entry-aware relevance
selection: parse markdown ``##``/``###`` entries, score by overlap
with a turn-fresh query signal (recent inbox + MISSION body), and
greedy-fill within budget — most relevant first.

Julian directive 2026-05-29 22:53Z (msg-1780095440): "Yeah can we be
smarter and grep the files above the truncation limit?"
"""

from __future__ import annotations

from pathlib import Path

from metasphere import context as ctx
from metasphere import messages as _msgs
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_project_nested_agent_mission(
    tmp_paths: Paths, project: str, agent: str, mission_body: str = "body",
) -> Path:
    d = tmp_paths.project_agent_dir(project, agent)
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text(
        f"# Mission\n\n{mission_body}\n", encoding="utf-8",
    )
    return d


def _seed_root_agent_mission(
    tmp_paths: Paths, agent: str, mission_body: str = "body",
) -> Path:
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text(
        f"# Mission\n\n{mission_body}\n", encoding="utf-8",
    )
    return d


def _seed_project_file(
    tmp_paths: Paths, project: str, filename: str, content: str,
) -> Path:
    pdir = tmp_paths.projects / project
    pdir.mkdir(parents=True, exist_ok=True)
    fpath = pdir / filename
    fpath.write_text(content, encoding="utf-8")
    return fpath


def _seed_inbox_message(
    tmp_paths: Paths, body: str, target: str = "@worldwire-eng",
) -> None:
    """Inject an unread !task into the agent's canonical inbox.

    Uses ``send_message`` with ``wake=False`` so we don't try to
    inject into a tmux session that doesn't exist under test."""
    _msgs.send_message(
        target=target,
        label="!task",
        body=body,
        from_agent="@test-sender",
        paths=tmp_paths,
        wake=False,
    )


# ===========================================================================
# Unit: _parse_markdown_entries
# ===========================================================================


def test_parse_markdown_entries_basic():
    text = (
        "## First\n\nbody one\n\n"
        "### Sub\n\nbody two\n\n"
        "## Third\n\nbody three\n"
    )
    out = ctx._parse_markdown_entries(text)
    headers = [h for h, _b in out]
    bodies = [b for _h, b in out]
    assert headers == ["## First", "### Sub", "## Third"]
    assert bodies == ["body one", "body two", "body three"]


def test_parse_markdown_entries_empty_body():
    text = "## First\n\n## Second\n\nbody\n"
    out = ctx._parse_markdown_entries(text)
    assert out == [("## First", ""), ("## Second", "body")]


def test_parse_markdown_entries_no_headers():
    # Flat content → single ("", body) tuple. Preserves pre-B5
    # behavior for unstructured files.
    text = "just some prose\nwith multiple lines\n"
    out = ctx._parse_markdown_entries(text)
    assert out == [("", "just some prose\nwith multiple lines")]


def test_parse_markdown_entries_h1_preamble_kept_as_anon_entry():
    # Document-level H1 is NOT an entry boundary (## / ### only).
    # H1 + intro paragraph becomes the preamble entry; subsequent
    # ## entries follow.
    text = (
        "# Doc title\n\nintro paragraph\n\n"
        "## Real entry\n\nentry body\n"
    )
    out = ctx._parse_markdown_entries(text)
    assert out[0] == ("", "# Doc title\n\nintro paragraph")
    assert out[1] == ("## Real entry", "entry body")


def test_parse_markdown_entries_empty_input():
    assert ctx._parse_markdown_entries("") == []
    assert ctx._parse_markdown_entries("\n\n") == []


# ===========================================================================
# Unit: _extract_query_signal
# ===========================================================================


def test_extract_query_signal_from_inbox(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@worldwire-eng")
    _seed_inbox_message(tmp_paths, "Hetzner tunnel 670s pg timeout")
    tokens = ctx._extract_query_signal(tmp_paths, "@worldwire-eng")
    assert "hetzner" in tokens
    assert "tunnel" in tokens
    assert "timeout" in tokens
    # 'pg' is 2 chars → fails ≥3-char regex.
    assert "pg" not in tokens


def test_extract_query_signal_stopwords_dropped(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@stopword-probe")
    _seed_inbox_message(
        tmp_paths, "the and for this that with from but not",
        target="@stopword-probe",
    )
    tokens = ctx._extract_query_signal(tmp_paths, "@stopword-probe")
    # All inbox tokens were stopwords; only the MISSION body
    # ("body") survives. Stopwords MUST be absent from the result.
    for sw in ("the", "and", "for", "this", "that", "with"):
        assert sw not in tokens


def test_extract_query_signal_empty_inbox_and_no_mission(tmp_paths: Paths):
    # Agent dir exists but no MISSION.md, no inbox. Should not raise.
    tmp_paths.agent_dir("@ghost").mkdir(parents=True, exist_ok=True)
    tokens = ctx._extract_query_signal(tmp_paths, "@ghost")
    assert tokens == set()


def test_extract_query_signal_includes_mission_body(tmp_paths: Paths):
    _seed_root_agent_mission(
        tmp_paths, "@probe", mission_body="orchestrating pipelines daily",
    )
    tokens = ctx._extract_query_signal(tmp_paths, "@probe")
    assert "orchestrating" in tokens
    assert "pipelines" in tokens
    assert "daily" in tokens


# ===========================================================================
# Unit: _rank_entries
# ===========================================================================


def test_rank_entries_by_match_count():
    entries = [
        ("## A", "alpha only"),
        ("## B", "alpha beta together"),
        ("## C", "no match here"),
    ]
    query = {"alpha", "beta", "gamma"}
    rendered, omitted = ctx._rank_entries(entries, query, budget=10_000)
    # B scores 2, A scores 1, C scores 0 (skipped on relevance path).
    headers = [h for h, _b in rendered]
    assert headers == ["## B", "## A"]
    assert omitted == 1  # only C dropped


def test_rank_entries_budget_skips_overlarge():
    # Top-ranked entry is fat; lower-ranked is small. The fat one
    # exceeds budget alone → skipped; the small one fits.
    fat_body = "alpha " * 200  # ~1200 bytes
    entries = [
        ("## Fat", fat_body),           # score 1 but too big
        ("## Slim", "alpha"),           # score 1, fits
    ]
    query = {"alpha"}
    rendered, omitted = ctx._rank_entries(entries, query, budget=200)
    headers = [h for h, _b in rendered]
    assert "## Slim" in headers
    assert "## Fat" not in headers
    assert omitted == 1


def test_rank_entries_empty_query_returns_nothing():
    entries = [("## A", "body")]
    rendered, omitted = ctx._rank_entries(entries, query=set(), budget=1000)
    assert rendered == []
    assert omitted == 1


def test_rank_entries_zero_scores_only_returns_empty():
    entries = [("## A", "alpha"), ("## B", "beta")]
    rendered, omitted = ctx._rank_entries(
        entries, query={"unrelated"}, budget=1000,
    )
    assert rendered == []
    assert omitted == 2


# ===========================================================================
# Unit: _render_project_file — relevance + fallbacks
# ===========================================================================


def test_render_project_file_relevance_path(tmp_path: Path):
    # Five entries; query matches entry 3 deep in file. Without
    # relevance ranking, the head-of-file truncator would surface
    # entries 1-2 and bury 3.
    content = (
        "## Entry 1\n\nfirst entry filler text\n\n"
        "## Entry 2\n\nsecond entry filler text\n\n"
        "## Entry 3 Hetzner\n\nthe 670s tunnel issue lives here\n\n"
        "## Entry 4\n\nfourth entry filler text\n\n"
        "## Entry 5\n\nfifth entry filler text\n"
    )
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    query = {"hetzner", "tunnel"}
    out = ctx._render_project_file(path, query, budget=200)
    assert "Entry 3 Hetzner" in out
    assert "670s tunnel issue" in out


def test_render_project_file_cold_start_fallback(tmp_path: Path):
    # Each entry ~220 bytes; effective budget after footer reserve
    # (~80B) makes only ~1 entry fit so the footer surfaces.
    bulk = "filler word " * 18
    content = (
        f"## E1\n\n{bulk}\n\n"
        f"## E2\n\n{bulk}\n\n"
        f"## E3\n\n{bulk}\n"
    )
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, query=set(), budget=400)
    assert "## E1" in out
    assert "filler word" in out
    assert "omitted" in out  # footer present
    assert "2 more" in out


def test_render_project_file_no_match_fallback(tmp_path: Path):
    content = (
        "## E1\n\nfirst content\n\n"
        "## E2\n\nsecond content\n"
    )
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    # Query non-empty but zero overlap → falls back to head-of-file.
    out = ctx._render_project_file(
        path, query={"unrelated", "tokens"}, budget=200,
    )
    assert "## E1" in out
    assert "first content" in out


def test_render_project_file_no_omission_no_footer(tmp_path: Path):
    content = "## E1\n\nshort body\n"
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, query=set(), budget=2048)
    assert "## E1" in out
    assert "omitted" not in out
    assert "more" not in out.lower().split("entries")[-1] if "entries" in out else True


def test_render_project_file_missing_returns_empty(tmp_path: Path):
    path = tmp_path / "missing.md"
    assert ctx._render_project_file(path, query={"x"}, budget=2048) == ""


def test_render_project_file_unstructured_falls_back_to_byte_truncate(
    tmp_path: Path,
):
    # No ##/### headers → flat-prose mode (single anon entry).
    # Should still render without crashing.
    content = "flat prose without any markdown headers at all\n"
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, query=set(), budget=2048)
    assert "flat prose" in out


# ===========================================================================
# Integration: _render_project_capsule end-to-end
# ===========================================================================


def test_capsule_end_to_end_relevance_surfaces_deep_entry(tmp_paths: Paths):
    """Canonical Hetzner-670s repro.

    Seed: LEARNINGS.md with a deep "Hetzner tunnel 670s" entry padded
    by enough filler to push it past the 2KB byte cap. Seed inbox
    with a !task mentioning "Hetzner tunnel". Assert the deep entry
    appears in the capsule output — the entire point of B5.
    """
    _seed_project_nested_agent_mission(
        tmp_paths, "worldwire", "@worldwire-eng",
    )

    # Pad with many filler entries before the load-bearing one so
    # head-of-file truncation would clearly miss it. Each filler is
    # ~120 bytes; 20 fillers = ~2.4KB of head noise.
    fillers = "\n\n".join(
        f"## Filler {i}\n\nfiller content number {i} with assorted words"
        for i in range(20)
    )
    learnings = (
        fillers
        + "\n\n## 2026-05-29: Hetzner tunnel 670s\n\n"
        + "The PG tunnel from cortex.worldwire.sh to db drops after "
        + "670s due to a sysctl idle-conn-timeout on the bastion. "
        + "Fix: wrap asyncpg with autossh and tcp_keepalive=60.\n"
    )
    _seed_project_file(tmp_paths, "worldwire", "LEARNINGS.md", learnings)

    _seed_inbox_message(
        tmp_paths,
        "Investigate the Hetzner tunnel 670s drop. Repro under "
        "asyncpg + ssh tunnel.",
        target="@worldwire-eng",
    )

    out = ctx._render_project_capsule(tmp_paths, "@worldwire-eng")

    assert "## Project: worldwire" in out
    assert "Hetzner tunnel 670s" in out
    assert "670s" in out


def test_capsule_cold_start_renders_head_with_footer(tmp_paths: Paths):
    """Probe 1: blank wake context / no signal → head-of-file
    fallback + 'N more entries omitted.' footer."""
    _seed_project_nested_agent_mission(
        tmp_paths, "worldwire", "@no-signal", mission_body="",
    )
    # Each filler entry ~250 bytes; the per-project LEARNINGS budget
    # (~1228B − 80B footer reserve = ~1148B) only holds ~4 of them.
    # Enough entries to guarantee omissions across both files.
    bulk = "lorem ipsum dolor sit amet consectetur " * 6
    entries = "\n\n".join(
        f"## E{i}\n\n{bulk}" for i in range(20)
    )
    _seed_project_file(tmp_paths, "worldwire", "LEARNINGS.md", entries)

    out = ctx._render_project_capsule(tmp_paths, "@no-signal")

    assert "## Project: worldwire" in out
    assert "## E0" in out  # head-of-file path
    assert "omitted" in out  # footer present


def test_capsule_zero_match_renders_head(tmp_paths: Paths):
    """Probe 3: signal present, file has zero matches → head-of-file
    fallback."""
    _seed_project_nested_agent_mission(
        tmp_paths, "worldwire", "@zero-match",
    )
    _seed_inbox_message(
        tmp_paths, "completely unrelated topic words floating around",
        target="@zero-match",
    )
    entries = "\n\n".join(
        f"## E{i}\n\nentry body {i} with filler" for i in range(10)
    )
    _seed_project_file(tmp_paths, "worldwire", "LEARNINGS.md", entries)

    out = ctx._render_project_capsule(tmp_paths, "@zero-match")

    assert "## Project: worldwire" in out
    assert "## E0" in out  # head-of-file fallback


def test_capsule_t1_regression_explicit_frontmatter_still_works(
    tmp_paths: Paths,
):
    """Probe 5 regression: explicit ``project: <name>`` frontmatter
    (T1 path) still renders the section heading + entry."""
    agent_dir = tmp_paths.agent_dir("@t1-probe")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "MISSION.md").write_text(
        "---\nproject: worldwire\n---\n\n# Mission\n\nbody\n",
        encoding="utf-8",
    )
    _seed_project_file(
        tmp_paths, "worldwire", "LEARNINGS.md",
        "## Some entry\n\nrendered body\n",
    )

    out = ctx._render_project_capsule(tmp_paths, "@t1-probe")

    assert "## Project: worldwire" in out
    assert "## Some entry" in out
    assert "rendered body" in out


def test_capsule_b4_regression_path_inference_still_works(
    tmp_paths: Paths,
):
    """B4 path-inference fallback still hits the per-project files
    under the new render path."""
    _seed_project_nested_agent_mission(
        tmp_paths, "worldwire", "@b4-probe",
    )
    _seed_project_file(
        tmp_paths, "worldwire", "LEARNINGS.md",
        "## Entry\n\nhetzner tunnel content\n",
    )

    out = ctx._render_project_capsule(tmp_paths, "@b4-probe")

    assert "## Project: worldwire" in out
    assert "hetzner tunnel content" in out
