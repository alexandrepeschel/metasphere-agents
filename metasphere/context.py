"""Per-turn context block builder.

Section order is load-bearing: the orchestrator's per-turn cognition
keys off the layout (status header → drift warning → telegram →
messages → tasks → events → FTS).

Pure stdlib. No third-party deps.
"""

from __future__ import annotations

import collections as _collections
import datetime as _dt
import hashlib
import json
import re
import subprocess
from pathlib import Path

from . import messages as _msgs
from . import tasks as _tasks
from .identity import resolve_agent_id
from .paths import Paths, rel_path, resolve

# Files baked into the REPL at session start. Order is irrelevant —
# sorted before concatenating for deterministic hashing.
_HARNESS_FILES = (
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/settings.local.json",
)

DEFAULT_SECTION_BUDGET = 2048


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def truncate_section(text: str, budget: int = DEFAULT_SECTION_BUDGET) -> str:
    """Cap a section to ``budget`` bytes, appending a truncation note."""
    if budget <= 0 or text is None:
        return ""
    data = text.encode("utf-8")
    if len(data) <= budget:
        return text
    cut = data[:budget].decode("utf-8", errors="ignore").rstrip()
    return cut + f"\n_(truncated: {len(data)} bytes total)_\n"


# ---------------------------------------------------------------------------
# Harness drift detector
# ---------------------------------------------------------------------------


def _existing_harness_files(base: Path) -> list[Path]:
    out: list[Path] = []
    for rel in _HARNESS_FILES:
        p = base / rel
        if p.is_file():
            out.append(p)
    return out


def harness_hash(paths: Paths) -> str:
    """Sha256 of the harness files the claude REPL actually baked in.

    Reads from ``paths.root`` (= ``~/.metasphere``) — the dir whose
    ``CLAUDE.md`` / ``.claude/settings*.json`` the claude CLI auto-
    loads when it starts a session. Previously used
    ``paths.project_root``, which diverged between the baseline writer
    (gateway daemon with ``METASPHERE_REPO_ROOT`` set to the source
    repo) and the reader (REPL whose CWD resolves to ``~/.metasphere``
    via ``git rev-parse`` fallback). That divergence produced a drift
    banner that could never clear — baseline always mismatched live.
    Rooting both to ``paths.root`` eliminates the env-hygiene class of
    bug entirely.

    Returns "" if no files exist.
    """
    files = _existing_harness_files(paths.root)
    if not files:
        return ""
    files_sorted = sorted(str(p) for p in files)
    h = hashlib.sha256()
    for fp in files_sorted:
        try:
            with open(fp, "rb") as f:
                h.update(f.read())
        except OSError:
            continue
    return h.hexdigest()


def _baseline_hash(paths: Paths) -> str:
    p = paths.state / "harness_hash_baseline"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


_STATUS_ICON = {
    _msgs.STATUS_UNREAD: "○",
    _msgs.STATUS_READ: "◐",
    _msgs.STATUS_REPLIED: "◑",
    _msgs.STATUS_COMPLETED: "●",
}


def _render_status_header(paths: Paths, agent: str) -> str:
    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    status = "unknown"
    sf = agent_dir / "status"
    if sf.is_file():
        try:
            status = sf.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            pass
    out = [f"# Metasphere Delta ({agent})", "", f"_Status: {status}_", ""]
    return "\n".join(out)


def _read_persona_body(path: Path) -> str:
    """Read a persona file; strip the leading H1 line; return the
    body. Returns ``""`` on missing file, OSError, or empty body
    after H1 stripping."""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _render_voice_capsule(paths: Paths, agent: str) -> str:
    """Inject the agent's full persona — SOUL / IDENTITY / USER — into
    every turn's context.

    No truncation: persona files are small and load-bearing. Pre-PR-B
    behaviour capped the capsule at 1500B / 40 lines and only loaded
    SOUL/VOICE, never IDENTITY or USER — so the kaomoji, warmth-marker,
    calm-intensity / thinking-companion lines, and full user-model sat
    on disk and never reached the model. Persona drift over time was
    the symptom.

    Each section is emitted iff its file exists. ``VOICE.md`` is a
    backward-compat alias for ``SOUL.md`` (older agents still have
    the file under the old name). The trailing pointer line is only
    emitted when at least one persona file landed.
    """
    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    soul_body = (
        _read_persona_body(agent_dir / "SOUL.md")
        or _read_persona_body(agent_dir / "VOICE.md")
    )
    identity_body = _read_persona_body(agent_dir / "IDENTITY.md")
    user_body = _read_persona_body(agent_dir / "USER.md")

    sections: list[str] = []
    if soul_body:
        sections.append("## Voice (who you are, how you sound)\n\n" + soul_body)
    if identity_body:
        sections.append("## Identity\n\n" + identity_body)
    if user_body:
        sections.append("## User-model (who you collaborate with)\n\n" + user_body)

    if not sections:
        return ""
    sections.append(
        f"_(Persona files at `{agent_dir}` + persona-index.md "
        f"for lazy-loadables.)_"
    )
    return "\n\n".join(sections) + "\n"


_MISSION_BYTE_CAP = 1024
_MISSION_LINE_CAP = 30

# Per-file 2KB cap: LEARNINGS/MEMORY are the on-disk knowledge base
# and denser than mission. Section-level enforcement is delegated to
# the outer ``truncate_section`` call in ``build_context`` (default
# 2KB budget), which is the load-bearing cap — no per-section knob
# needed here.
_PROJECT_FILE_BYTE_CAP = 2048

# Inbox sample size for query-signal extraction. Capped at 5 because
# older messages (a) score weakly and (b) inflate stopword noise.
_PROJECT_QUERY_INBOX_SAMPLE = 5

# Token shape: starts alpha, ≥3 chars total. Strips short noise
# ("a", "pg", "to") and anything beginning with a digit/symbol.
_PROJECT_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{2,}")

# Hardcoded stopword set — small (~30) to keep tokenization cheap and
# avoid pulling NLTK / spacy for a feature that runs every turn.
_PROJECT_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "this", "that", "with", "from",
    "but", "not", "you", "your", "have", "has", "had", "will", "would",
    "should", "can", "could", "into", "than", "then", "what", "when",
    "where", "which", "who", "why", "how", "all", "any", "out", "yes",
})

# Reserve at the tail of a per-file render for the "N more entries"
# footer. ~80 bytes covers the largest plausible footer phrasing
# without crowding actual content.
_PROJECT_FOOTER_RESERVE = 80


def _render_mission_capsule(paths: Paths, agent: str) -> str:
    """Inject the agent's MISSION.md so persistent agents know their
    purpose every turn. Capped to ~1KB / 30 lines."""
    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    mission_file = agent_dir / "MISSION.md"
    if not mission_file.is_file():
        return ""
    try:
        lines = mission_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    body_lines = lines[1 : _MISSION_LINE_CAP + 1]
    body = "\n".join(body_lines)
    data = body.encode("utf-8")[:_MISSION_BYTE_CAP]
    body = data.decode("utf-8", errors="ignore").rstrip()
    if not body:
        return ""
    return f"## Mission\n\n{body}\n"


def _infer_project_for_agent(
    paths: Paths, agent: str, agent_dir: Path
) -> str | None:
    """Auto-infer a single project association from the agent's dir
    layout when MISSION.md frontmatter declares nothing.

    Resolution chain (frontmatter is handled by the caller; this is
    the fallback ONLY when frontmatter is absent):

    1. Path inference: agent home at
       ``~/.metasphere/projects/<P>/agents/@<agent>/`` → ``<P>``.
    2. Name-prefix inference: ``@<project>-<role>`` (first dash
       split) AND ``~/.metasphere/projects/<project>/`` exists →
       ``<project>``.

    Returns ``None`` when neither matches. Inference yields at most
    one project — multi-project agents must declare via
    ``projects: [a, b]`` frontmatter (explicit beats implicit).
    """
    try:
        if (
            agent_dir.parent.name == "agents"
            and agent_dir.parent.parent.parent == paths.projects
        ):
            project = agent_dir.parent.parent.name
            if (paths.projects / project).is_dir():
                return project
    except Exception:
        pass

    name = agent.lstrip("@")
    if "-" in name:
        prefix = name.split("-", 1)[0]
        if prefix and (paths.projects / prefix).is_dir():
            return prefix

    return None


def _tokenize_query(text: str) -> set[str]:
    """Lowercase token set with stopwords dropped. Returns empty for
    empty input. Token shape per ``_PROJECT_TOKEN_RE`` (alpha-led, ≥3
    chars)."""
    if not text:
        return set()
    out: set[str] = set()
    for tok in _PROJECT_TOKEN_RE.findall(text):
        low = tok.lower()
        if low not in _PROJECT_STOPWORDS:
            out.add(low)
    return out


def _extract_query_signal(paths: Paths, agent: str) -> set[str]:
    """Pull keyword tokens that approximate "what is the agent thinking
    about right now". Sources, cheapest first:

    1. Last ``_PROJECT_QUERY_INBOX_SAMPLE`` messages from the agent's
       inbox (recent !task / !info / !query bodies — high signal:
       the words the team is using to describe the immediate work).
    2. The agent's MISSION.md body — slow-changing, but anchors the
       agent's domain ("worldwire", "tunnel", "pipeline", etc.).

    Returns the union as a stopword-filtered token set. Empty when
    both sources are empty — caller falls back to head-of-file.

    No ``view=True`` on the inbox read: this is a context build, not
    a user-facing inbox listing, and we don't want to flip messages
    from unread → read as a side effect of rendering."""
    parts: list[str] = []
    try:
        msgs = _msgs.collect_inbox(paths.scope, paths.project_root)
    except Exception:
        msgs = []
    for m in msgs[:_PROJECT_QUERY_INBOX_SAMPLE]:
        if m.body:
            parts.append(m.body)

    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    mission_file = agent_dir / "MISSION.md"
    if mission_file.is_file():
        try:
            parts.append(mission_file.read_text(encoding="utf-8"))
        except OSError:
            pass

    return _tokenize_query(" ".join(parts))


def _parse_markdown_entries(text: str) -> list[tuple[str, str]]:
    """Walk markdown text, yield ``(header_line, body)`` tuples at
    ``##`` or ``###`` boundaries.

    The migration ephemerals that wrote per-project LEARNINGS/MEMORY
    files used those headers as entry boundaries; we use the same
    boundaries on read so each entry is an atomic unit (no mid-entry
    cuts in selection).

    Content before the first ``##``/``###`` header is emitted as a
    single tuple with ``header=""`` so file-level H1s / preambles
    aren't silently dropped. Empty bodies are preserved (header-only
    entries are valid). The header line is kept verbatim — callers
    render it as-is."""
    if not text:
        return []
    entries: list[tuple[str, str]] = []
    current_header: str | None = None
    current_body: list[str] = []

    def _flush() -> None:
        if current_header is None:
            body = "\n".join(current_body).strip()
            if body:
                entries.append(("", body))
            return
        body = "\n".join(current_body).strip()
        entries.append((current_header, body))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("### ") or stripped.startswith("## "):
            _flush()
            current_header = line
            current_body = []
        else:
            current_body.append(line)
    _flush()
    return entries


def _entry_render_cost(header: str, body: str) -> int:
    """Bytes for an entry rendered as ``header\\n\\nbody``."""
    if header and body:
        return len((header + "\n\n" + body).encode("utf-8"))
    return len((header or body).encode("utf-8"))


def _format_entry(header: str, body: str) -> str:
    if header and body:
        return header + "\n\n" + body
    return header or body


def _rank_entries(
    entries: list[tuple[str, str]],
    query: set[str],
    budget: int,
) -> tuple[list[tuple[str, str]], int]:
    """Rank ``entries`` by ``|query ∩ entry_tokens|`` and greedy-fill
    within ``budget`` bytes.

    Returns ``(rendered, omitted)`` where ``rendered`` is the chosen
    subset in score-descending order (most relevant first — orch wants
    load-bearing entries at the TOP of the section so outer truncation
    can't bury them again) and ``omitted`` counts entries dropped for
    score-0 OR budget reasons.

    Skips entries with ``score == 0`` (relevance path only keeps
    matches; the cold-start path handles zero-signal selection). Skips
    entries that exceed ``budget`` alone — no mid-entry cuts."""
    if not entries or budget <= 0 or not query:
        return [], len(entries)

    scored: list[tuple[int, int, str, str]] = []
    for idx, (header, body) in enumerate(entries):
        tokens = _tokenize_query(header + "\n" + body)
        score = len(query & tokens)
        scored.append((score, idx, header, body))
    scored.sort(key=lambda s: (-s[0], s[1]))

    rendered: list[tuple[str, str]] = []
    used = 0
    sep = len("\n\n".encode("utf-8"))
    for score, _idx, header, body in scored:
        if score == 0:
            break
        cost = _entry_render_cost(header, body)
        cost += sep if rendered else 0
        if used + cost > budget:
            continue
        rendered.append((header, body))
        used += cost
    omitted = len(entries) - len(rendered)
    return rendered, omitted


def _fit_head_entries(
    entries: list[tuple[str, str]], budget: int,
) -> tuple[list[tuple[str, str]], int]:
    """Cold-start / no-match fit: walk entries in document order and
    take until ``budget`` runs out. Skips entries too large to fit
    alone rather than stopping — keeps the early-skip → later-fit
    behavior consistent with the rank path."""
    if not entries or budget <= 0:
        return [], len(entries)
    rendered: list[tuple[str, str]] = []
    used = 0
    sep = len("\n\n".encode("utf-8"))
    for header, body in entries:
        cost = _entry_render_cost(header, body)
        cost += sep if rendered else 0
        if used + cost > budget:
            continue
        rendered.append((header, body))
        used += cost
    return rendered, len(entries) - len(rendered)


def _render_project_file(
    path: Path, query: set[str], budget: int,
) -> str:
    """Read ``path`` and return the relevance-ranked, entry-aware body
    suitable for inclusion under a ``### LEARNINGS`` or ``### MEMORY``
    subheading.

    Selection order:

    1. **Relevance path** — non-empty query AND at least one entry
       scores > 0: render top-scoring entries in score-descending
       order until ``budget`` is hit. Append a footer if entries were
       dropped.
    2. **Cold-start / no-match fallback** — query is empty OR every
       entry scored 0: render head-of-file entries in document order
       until ``budget`` is hit. Footer added when entries are dropped.

    Files with no ``##``/``###`` headers (e.g. flat prose) fall back
    to byte-truncated head, preserving pre-B5 behavior on
    unstructured content."""
    if not path.is_file() or budget <= 0:
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.strip():
        return ""

    entries = _parse_markdown_entries(text)
    if not entries:
        data = text.strip().encode("utf-8")[:budget]
        return data.decode("utf-8", errors="ignore").rstrip()

    # Reserve room for the truncation footer so omissions don't push
    # actual content past the budget edge.
    effective = max(budget - _PROJECT_FOOTER_RESERVE, 1)

    rendered: list[tuple[str, str]] = []
    omitted = len(entries)
    if query:
        rendered, omitted = _rank_entries(entries, query, effective)

    if not rendered:
        rendered, omitted = _fit_head_entries(entries, effective)

    if not rendered:
        return ""

    parts = [_format_entry(h, b) for h, b in rendered]
    if omitted > 0:
        noun = "entry" if omitted == 1 else "entries"
        parts.append(f"_({omitted} more {noun} omitted.)_")
    return "\n\n".join(parts)


def _render_project_capsule(paths: Paths, agent: str) -> str:
    """Inject per-project LEARNINGS+MEMORY for projects associated
    with the agent.

    Sources, in precedence order:

    1. ``project: <name>`` (scalar) or ``projects: [a, b]`` (list)
       in MISSION.md frontmatter — explicit override / escape hatch,
       and the only path that supports multi-project agents.
    2. When frontmatter declares nothing, infer a single project via
       :func:`_infer_project_for_agent` (dir layout, then name
       prefix). Lets agents whose MISSION.md predates the
       per-project memory layer pick up their project's
       LEARNINGS/MEMORY without an annotation pass (Julian
       directive 2026-05-29 22:32Z — msg-1780093638).

    Returns ``""`` when no source resolves any project, when no
    project file exists, or when MISSION.md is unreadable.
    """
    from .specs import _parse_frontmatter

    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    mission_file = agent_dir / "MISSION.md"
    if not mission_file.is_file():
        return ""
    try:
        text = mission_file.read_text(encoding="utf-8")
    except OSError:
        return ""
    fm = _parse_frontmatter(text)

    declared: list[str] = []
    scalar = fm.get("project")
    if isinstance(scalar, str) and scalar.strip():
        declared.append(scalar.strip())
    elif isinstance(scalar, list):
        declared.extend(p for p in scalar if isinstance(p, str) and p)
    plural = fm.get("projects")
    if isinstance(plural, list):
        declared.extend(p for p in plural if isinstance(p, str) and p)
    elif isinstance(plural, str) and plural.strip():
        declared.append(plural.strip())

    seen: set[str] = set()
    ordered: list[str] = []
    for p in declared:
        if p not in seen:
            seen.add(p)
            ordered.append(p)

    # Fallback runs only when frontmatter declared nothing. An agent
    # with explicit ``project: customproject`` keeps that even when
    # their dir layout or name would infer something different.
    if not ordered:
        inferred = _infer_project_for_agent(paths, agent, agent_dir)
        if inferred:
            ordered.append(inferred)

    if not ordered:
        return ""

    # Pull turn-fresh query signal once (inbox + MISSION body) and
    # share it across every project file. Cheap — both sources are
    # already mmapped/seeked in the same per-turn build pass.
    query = _extract_query_signal(paths, agent)

    # Divide the section budget across declared projects and split
    # 60/40 LEARNINGS/MEMORY within each. Under the canonical single-
    # project case (worldwire-eng), this gives LEARNINGS ~1228B and
    # MEMORY ~819B — both deep enough to land the load-bearing entry
    # under relevance ranking.
    per_project = max(_PROJECT_FILE_BYTE_CAP // max(len(ordered), 1), 512)
    learn_budget = int(per_project * 0.6)
    mem_budget = per_project - learn_budget

    sections: list[str] = []
    for p in ordered:
        proj_dir = paths.projects / p
        learnings = _render_project_file(
            proj_dir / "LEARNINGS.md", query, learn_budget,
        )
        memory = _render_project_file(
            proj_dir / "MEMORY.md", query, mem_budget,
        )
        parts: list[str] = []
        if learnings:
            parts.append("### LEARNINGS\n\n" + learnings)
        if memory:
            parts.append("### MEMORY\n\n" + memory)
        if parts:
            sections.append(f"## Project: {p}\n\n" + "\n\n".join(parts))

    if not sections:
        return ""

    return "\n\n".join(sections) + "\n"


def _render_project_migration_nudge(paths: Paths, agent: str) -> str:
    """Cold-start nudge for agents whose agent-level LEARNINGS/MEMORY
    contain entries that look project-specific.

    Per the per-project memory spec (Phase 1, section "Periodic check
    in the hook"): scan the residual agent-level pool for known project
    tokens; if matches found, surface a one-line nudge to spawn a
    migration ephemeral. Strictly nudge-only — the spec explicitly says
    "Don't auto-migrate — that's destructive."

    Project tokens are the registered project names under
    ``~/.metasphere/projects/`` (one subdir per project). Match
    semantic: case-insensitive word-boundary regex per token. The
    word-boundary form avoids spurious substring hits (``worldwire``
    would otherwise catch ``worldwireless`` and similar near-tokens).

    Sentinel cache at ``<agent_dir>/state/migration_nudge_seen`` stores
    a fingerprint of the LEARNINGS+MEMORY mtimes the last time we
    inspected. Subsequent calls suppress the nudge while the
    fingerprint matches — approximating "cache a flag once a session
    has been nudged" from the spec, extended to "until either source
    file is edited." Writing the sentinel on the no-match path too
    keeps the per-turn cost bounded for agents with nothing to migrate.

    Returns ``""`` when no agent-level files exist, no project tokens
    match, or the sentinel reports no change since last surfacing.
    """
    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    learnings = agent_dir / "LEARNINGS.md"
    memory = agent_dir / "MEMORY.md"
    files = [f for f in (learnings, memory) if f.is_file()]
    if not files:
        return ""

    try:
        current_fp = "|".join(
            f"{f.name}:{f.stat().st_mtime_ns}" for f in files
        )
    except OSError:
        return ""

    sentinel = agent_dir / "state" / "migration_nudge_seen"
    if sentinel.is_file():
        try:
            stored = sentinel.read_text(encoding="utf-8").strip()
        except OSError:
            stored = ""
        if stored == current_fp:
            return ""

    projects_dir = paths.projects
    tokens: list[str] = []
    if projects_dir.is_dir():
        try:
            for entry in sorted(projects_dir.iterdir()):
                if entry.is_dir() and not entry.name.startswith("."):
                    tokens.append(entry.name)
        except OSError:
            pass
    if not tokens:
        return ""

    matched_tokens: list[str] = []
    total_hits = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except OSError:
            continue
        for token in tokens:
            pat = re.compile(rf"\b{re.escape(token)}\b", re.IGNORECASE)
            hits = pat.findall(text)
            if hits:
                if token not in matched_tokens:
                    matched_tokens.append(token)
                total_hits += len(hits)

    def _persist_sentinel() -> None:
        try:
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text(current_fp, encoding="utf-8")
        except OSError:
            pass

    if not matched_tokens:
        _persist_sentinel()
        return ""

    proj_list = ", ".join(matched_tokens)
    noun = "entry" if total_hits == 1 else "entries"
    body = (
        "## Per-project memory migration\n\n"
        f"{total_hits} {noun} in your agent-level LEARNINGS/MEMORY "
        f"look project-specific (matches: {proj_list}). "
        "Spawn a migration ephemeral to clean up.\n"
    )
    _persist_sentinel()
    return body


def _render_child_reports(paths: Paths, agent: str) -> str:
    """Show pending child agent completion reports (max 5)."""
    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    reports_dir = agent_dir / "child_reports"
    if not reports_dir.is_dir():
        return ""
    try:
        files = sorted(reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return ""
    if not files:
        return ""
    out = [f"## Child Agent Reports ({len(files)} pending)", ""]
    for f in files[:5]:
        child = f.stem.split("-")[0]
        try:
            body = f.read_text(encoding="utf-8").strip()
        except OSError:
            body = "(unreadable)"
        out.append(f"### @{child}")
        out.append(body)
        out.append("")
    return "\n".join(out)


def _render_drift_warning(paths: Paths) -> str:
    baseline = _baseline_hash(paths)
    if not baseline:
        return ""
    live = harness_hash(paths)
    if not live or live == baseline:
        return ""
    hash_file = paths.state / "harness_hash_baseline"
    return (
        "## ⚠ Harness drift detected\n"
        "\n"
        "Your baked-in CLAUDE.md / .claude/settings / metasphere-context has changed\n"
        "since this REPL started. The new content is on disk but you're still running\n"
        "the old version in memory.\n"
        "\n"
        "If the change is relevant to what you're doing (e.g. updated workflow,\n"
        "new slash command, new safety rule), run `/exit` — the tmux pane will\n"
        "respawn a fresh REPL with the latest harness. Otherwise ignore this and\n"
        "the warning will keep appearing until you reload.\n"
        "\n"
        f"_(suppress: `echo $_live_hash > {hash_file}` to silence without reloading)_\n"
    )


_TELEGRAM_BYTE_CAP = 1024


def _render_telegram(paths: Paths, history: int = 3) -> str:
    """Render the recent telegram conversation.

    Reads from :func:`metasphere.telegram.archiver.telegram_context`
    directly (no subprocess). Caps at ``_TELEGRAM_BYTE_CAP`` bytes.
    """
    from .telegram.archiver import telegram_context

    try:
        body = telegram_context(history=history, base_dir=str(paths.telegram))
    except Exception:
        body = ""
    if not body.strip():
        return "## Telegram (recent conversation)\n(no recent messages)\n"
    data = body.encode("utf-8")[:_TELEGRAM_BYTE_CAP]
    return data.decode("utf-8", errors="ignore").rstrip() + "\n"


def _render_messages(paths: Paths) -> str:
    msgs = _msgs.collect_inbox(paths.scope, paths.project_root, view=True)
    unread = sum(1 for m in msgs if m.status == _msgs.STATUS_UNREAD)
    total = len(msgs)
    if total == 0:
        return "## Messages: No messages in scope\n"
    out = [
        f"## Messages ({unread} unread, {total} total)",
        f"## Scope: {rel_path(paths.scope, paths.project_root)}",
        "",
    ]
    for m in msgs:
        if m.status != _msgs.STATUS_UNREAD:
            continue
        icon = _STATUS_ICON.get(m.status, "?")
        reply = f" ↩ reply to {m.reply_to}" if m.reply_to else ""
        body_preview = " ".join(m.body.split())[:60]
        out.append(f"{icon} {m.label} from {m.from_} [{m.id}]{reply}")
        out.append(f"  {m.scope} | {m.created}")
        out.append(f"  {body_preview}")
        out.append("")
    return "\n".join(out) + "\n"


def _render_tasks(paths: Paths) -> str:
    items = _tasks.list_tasks(paths.scope, paths.project_root, include_completed=False)
    if not items:
        return "## Tasks: No active tasks in scope\n"
    out = [f"## Tasks ({len(items)} active)", ""]
    for t in items:
        icon = {
            "pending": "○",
            "in-progress": "◐",
            "blocked": "◼",
            "completed": "●",
        }.get(t.status, "?")
        suffix = f" → {t.assignee}" if t.assignee else ""
        out.append(f"{icon} {t.priority} {t.title} [{t.id}]{suffix}")
        out.append(f"  {t.scope} | {t.status}")
    return "\n".join(out) + "\n"


def _render_events(paths: Paths, n: int = 10) -> str:
    log = paths.events_log
    if not log.is_file():
        return "## Recent Events\n(no recent events)\n"
    try:
        with open(log, "r", encoding="utf-8") as f:
            # Constant memory single-pass tail.
            tail = list(_collections.deque(f, maxlen=n))
    except OSError:
        return "## Recent Events\n(no recent events)\n"
    out = ["## Recent Events", ""]
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("timestamp", "")
        typ = rec.get("type", "")
        msg = rec.get("message", "")
        out.append(f"- {ts} [{typ}] {msg}")
    return "\n".join(out) + "\n"


def _render_directives(paths: Paths) -> str:
    """Render broadcast directives from DIRECTIVES.yaml at project root."""
    from . import directives as _directives
    return _directives.render_directives(paths)


def _render_memory_fts(paths: Paths, agent: str) -> str:
    """Pull the memory section using CAM (primary) + token-overlap (fallback).

    Replaced the token-overlap-only path on 2026-04-17 because the
    near-static query (task file + project name) produced identical
    results every turn — operator-flagged the "noise" at 22:16Z.

    Now: HybridStrategy(CamStrategy + TokenOverlapStrategy) with a
    turn-varying signal injected into the query so ranking shifts.
    """
    from .memory import (
        AutoMemoryStrategy,
        CamStrategy,
        HybridStrategy,
        TokenOverlapStrategy,
        context_for as _memory_context_for,
    )

    out = ["## Memory Context (FTS)"]

    # Build query: static stem (task + project) + fresh signal (last event)
    agent_dir = paths.find_agent_dir(agent) or paths.agent_dir(agent)
    task_file = agent_dir / "task"
    query_parts: list[str] = []
    if task_file.is_file():
        try:
            query_parts.append(task_file.read_text(encoding="utf-8").strip())
        except OSError:
            pass
    query_parts.append(paths.project_root.name)

    # Fresh signal: most recent event message. This ensures the query
    # varies turn-to-turn so memory recall shifts with the agent's
    # recent activity rather than returning the same top-N every tick.
    fresh = _latest_event_message(paths)
    if fresh:
        query_parts.append(fresh)

    query = " ".join(p for p in query_parts if p).replace("\n", " ")
    query = " ".join(query.split())[:300] or agent

    # Auto-memory first (orchestrator's curated MEMORY.md memos —
    # highest signal, pure Python, fast), then CAM (historical Claude
    # session transcripts), then token-overlap as final fallback.
    strategies = [HybridStrategy([
        AutoMemoryStrategy(),
        CamStrategy(fast=True, timeout=2.0),
        TokenOverlapStrategy(paths),
    ])]
    body = _memory_context_for(
        query, budget_chars=2048, strategies=strategies,
    ).strip()
    if not body:
        body = "No relevant memory found."
    out.append(body)
    return "\n".join(out) + "\n"


def _latest_event_message(paths: Paths) -> str:
    """Return the message field of the most recent event, or ''."""
    from . import events as _events
    try:
        tail = _events.tail_events(1, paths=paths)
        if not tail or not tail.strip():
            return ""
        # tail_events returns "HH:MM:SSZ [type] @agent: message"
        # Extract everything after the first ": " as the message
        first_line = tail.strip().splitlines()[0]
        if ": " in first_line:
            return first_line.split(": ", 1)[1][:80]
        return first_line[:80]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


def _render_project(paths: Paths) -> str:
    """Project header for the per-turn context block.

    Walks upward from ``paths.scope`` to find the enclosing project; if
    found, emits name/goal/members/recent activity. Empty string when
    the current scope is not inside any project (keeps the turn block
    unchanged for non-project agents).
    """
    from . import project as _project
    from . import agents as _agents

    proj = _project.project_for_scope(paths.scope, paths=paths)
    if proj is None:
        return ""

    out = [f"## Project: {proj.name}"]
    if proj.goal:
        out.append(f"Goal: {proj.goal}")

    # Members with alive/dormant marker. Alive = tmux session exists.
    if proj.members:
        parts: list[str] = []
        for m in proj.members:
            marker = ""
            if m.persistent:
                # ``_resolve_session`` walks the agent registry so
                # project-scoped members (which is the common case in
                # @project context) are checked under their actual
                # ``metasphere-<project>-<agent>`` session name, not
                # the bare form that misses them.
                try:
                    from .session import _resolve_session
                    alive = _agents.session_alive(_resolve_session(m.id))
                except Exception:
                    alive = False
                marker = ", alive" if alive else ", dormant"
            parts.append(f"{m.id} ({m.role}{marker})")
        out.append("Members: " + ", ".join(parts))
    else:
        out.append("Members: (none)")

    # Scope line: show the project path + whether the agent is inside it.
    scope_inside = str(paths.scope).startswith(str(proj.path))
    scope_label = "(active)" if scope_inside else "(external)"
    out.append(f"Scope: {proj.path} {scope_label}")

    # Recent activity: count of active tasks + last commit subject with
    # timestamps so the agent can gauge freshness.
    from . import tasks as _tasks
    try:
        active = _tasks.list_tasks(Path(proj.path), paths.project_root,
                                   include_completed=False)
        task_n = len(active)
        # Most recent task update timestamp
        latest_update = ""
        for t in active:
            u = getattr(t, "updated", "") or ""
            if u > latest_update:
                latest_update = u
    except Exception:
        task_n = 0
        latest_update = ""
    last_commit = ""
    commit_ts = ""
    git_dir = Path(proj.path) / ".git"
    if git_dir.exists():
        try:
            res = subprocess.run(
                ["git", "-C", proj.path, "log", "-1",
                 "--pretty=%s|%aI"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            parts = res.stdout.strip().splitlines()[0].rsplit("|", 1) if res.stdout.strip() else [""]
            last_commit = parts[0]
            commit_ts = parts[1] if len(parts) > 1 else ""
        except (subprocess.SubprocessError, OSError, IndexError):
            pass
    activity = f"{task_n} tasks active"
    if latest_update:
        activity += f", latest task update: {latest_update[:16]}"
    if last_commit:
        ts_part = f" ({commit_ts[:16]})" if commit_ts else ""
        activity += f", last commit: {last_commit}{ts_part}"
    out.append(f"Recent: {activity}")

    # Shared artifacts: list top-level files (max 10) so agents see what
    # teammates have dropped in the cross-agent dir without having to ls.
    shared_dir = paths.projects / proj.name / "shared"
    if shared_dir.is_dir():
        try:
            entries = sorted(p.name for p in shared_dir.iterdir() if p.is_file())
        except OSError:
            entries = []
        if entries:
            shown = ", ".join(entries[:10])
            more = f" (+{len(entries) - 10} more)" if len(entries) > 10 else ""
            out.append(f"Shared: {shared_dir} — {shown}{more}")
        else:
            out.append(f"Shared: {shared_dir} (empty)")
    return "\n".join(out) + "\n"


_LAST_EDITED_NOISE = {
    "__pycache__", ".git", ".venv", "node_modules", ".metasphere",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".egg-info", ".eggs",
}
_LAST_EDITED_LIMIT = 10


def _render_last_edited_files(paths: Paths) -> str:
    """Show the most recently modified files under the project scope.

    Skipped when not inside a project (root-scope agents get no noise).
    """
    from . import project as _project

    proj = _project.project_for_scope(paths.scope, paths=paths)
    if proj is None or not proj.path:
        return ""
    proj_path = Path(proj.path)
    if not proj_path.is_dir():
        return ""

    candidates: list[tuple[float, str]] = []
    try:
        for entry in proj_path.rglob("*"):
            if not entry.is_file():
                continue
            # Skip noise directories
            parts = entry.relative_to(proj_path).parts
            if any(p in _LAST_EDITED_NOISE or p.endswith(".egg-info") for p in parts):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            rel = str(entry.relative_to(proj_path))
            candidates.append((mtime, rel))
    except OSError:
        return ""

    if not candidates:
        return ""

    candidates.sort(reverse=True)
    top = candidates[:_LAST_EDITED_LIMIT]

    out = [f"## Last Edited Files [{proj.name}]"]
    for mtime, rel in top:
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        out.append(f"  {rel} — {ts}")
    return "\n".join(out) + "\n"


SECTION_NAMES = (
    "status",
    "drift",
    "project",
    "telegram",
    "messages",
    "tasks",
    "events",
    "memory",
)


def build_context(paths: Paths | None = None, *, budget: int = DEFAULT_SECTION_BUDGET) -> str:
    """Assemble the per-turn context block. Section order is load-bearing."""
    paths = paths or resolve()
    agent = resolve_agent_id(paths)

    sections: list[str] = []

    # Host-health ALERT: goes at the TOP so the agent sees a zombie /
    # tmux / PID-headroom trip before any other context. Empty string
    # when nothing is tripped, which keeps the zero-impact invariant
    # on normal turns.
    try:
        from .gateway.monitoring import render_alert as _render_alert
        alert = _render_alert(paths)
    except Exception:
        alert = ""
    sections.append(truncate_section(alert, budget) if alert else "")

    sections.append(truncate_section(_render_status_header(paths, agent), budget))
    voice = _render_voice_capsule(paths, agent)
    sections.append(truncate_section(voice, budget) if voice else "")
    mission = _render_mission_capsule(paths, agent)
    sections.append(truncate_section(mission, budget) if mission else "")
    project_capsule = _render_project_capsule(paths, agent)
    sections.append(truncate_section(project_capsule, budget) if project_capsule else "")
    migration_nudge = _render_project_migration_nudge(paths, agent)
    sections.append(truncate_section(migration_nudge, budget) if migration_nudge else "")
    drift = _render_drift_warning(paths)
    sections.append(truncate_section(drift, budget) if drift else "")
    directives_block = _render_directives(paths)
    sections.append(truncate_section(directives_block, budget) if directives_block else "")
    project_block = _render_project(paths)
    sections.append(truncate_section(project_block, budget) if project_block else "")
    sections.append(truncate_section(_render_telegram(paths), budget))
    child_reports = _render_child_reports(paths, agent)
    sections.append(truncate_section(child_reports, budget) if child_reports else "")
    sections.append(truncate_section(_render_messages(paths), budget))
    sections.append(truncate_section(_render_tasks(paths), budget))
    sections.append(truncate_section(_render_events(paths), budget))
    last_edited = _render_last_edited_files(paths)
    sections.append(truncate_section(last_edited, budget) if last_edited else "")
    sections.append(truncate_section(_render_memory_fts(paths, agent), budget))

    return "\n".join(s for s in sections if s).rstrip() + "\n"
