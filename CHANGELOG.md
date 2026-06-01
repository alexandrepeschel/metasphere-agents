# Changelog

All notable changes to Metasphere Agents will be documented here.

---

## 2026-05-29 to 2026-05-30 — per-project memory architecture shipped end-to-end + wake/discovery reliability fixes + audit-docs filter refinements

Nineteen non-bump commits across 48h. Three coherent themes: (a) per-project memory + learnings architecture landed in eight feature PRs plus three docs/cleanup follow-ups, replacing the per-agent LEARNINGS/MEMORY pool with a shared per-project file pool; (b) two reliability fixes on the messages surface (cross-scope `msg read`, wake-banner truncation on long task bodies) plus a feature that auto-escalates undelivered high-priority wakes to session-respawn; (c) three small `audit-docs` filter refinements that cut self-referential and same-day false positives the operator was stripping by hand.

### Per-project memory + learnings architecture

Driven by an operator directive 2026-05-29 + spec at `~/.metasphere/agents/@orchestrator/artifacts/2026-05-29-per-project-memory-learnings-spec.md`. The shipped layer reads each agent's relevant `~/.metasphere/projects/<P>/LEARNINGS.md` + `MEMORY.md` per turn, recency-ranks entries inside a section budget, and ships a footer pointer so agents can grep older content directly. Three phases landed across nineteen hours.

- **T1 seeder side — frontmatter declares projects, capsule renders them.** `_parse_frontmatter` (specs.py) gained inline-list parsing so `MISSION.md` can declare `projects: [a, b]`; scalar `project: foo` still works. Stays dependency-free (no PyYAML). New `_render_project_capsule` (context.py) reads each declared project's LEARNINGS+MEMORY and concats into one capsule, with per-file + per-section byte caps. (`a04b3a7` #117)
- **T2 cold-start migration nudge — operators get a one-line prompt, no auto-migrate.** New `_render_project_migration_nudge` enumerates project tokens from `~/.metasphere/projects/` subdirs and word-boundary-matches them against the agent's LEARNINGS+MEMORY; if any hit, the capsule emits a single-line nudge suggesting a migration ephemeral. Sentinel at `<agent_dir>/state/migration_nudge_seen` throttles the nudge. Scope-cut at msg-1780061622 dropped the dedicated migrate CLI — nudge-only. Per operator directive: don't auto-migrate — that's destructive, just nudge. (`2140100` #120)
- **Dead-code cleanup from T1+T2 attestation.** `_PROJECT_SECTION_BYTE_CAP = 8192` was introduced in #117 but never bound (outer `truncate_section` already trims at `DEFAULT_SECTION_BUDGET = 2048`, so the inner 8KB cap could only ever apply if a caller passed budget > 8192 — no callsite does). Dropped the constant + cap-check entirely; chose this over raising `DEFAULT_SECTION_BUDGET` because the outer truncate is the load-bearing cap by design. Plus a singular→plural fix on the migration nudge text. (`c4f1b8e` #122)
- **B4 auto-inference — frontmatter becomes explicit-override, not required.** Trigger: a worldwire-eng agent's MISSION.md predates the per-project memory layer and has no `project:` key, so the T1 capsule no-op'd and the agent couldn't see worldwire's LEARNINGS. Per operator directive 2026-05-29: frontmatter shouldn't be required. Resolution chain extended to: (1) MISSION.md frontmatter override; (2) path inference (`~/.metasphere/projects/<P>/agents/@<agent>/` → `<P>`); (3) name-prefix inference (`@<project>-<role>` + `projects/<project>/` exists). Multi-project agents still go through frontmatter. (`b245aa3` #124)
- **B5 relevance-rank entries inside the budget (later replaced).** Canonical 670s tunnel end-to-end on a worldwire-eng agent still failed after B4: the real LEARNINGS.md is ~38KB and the head-of-file byte-truncator buried the load-bearing entry past the 2KB cap. Replaced `_read_capped`'s slice with entry-aware relevance: parse atomic `(header, body)` entries at ##/### boundaries, tokenize last 5 inbox bodies + MISSION.md body into a stopword-filtered query set, score by `|query ∩ entry_tokens|`, greedy-fill within budget in score-desc order. (`c21e1a7` #125)
- **B6 replace relevance-rank with recency-sort + always-include file-path pointer.** Operator directive 2026-05-29: query-based ranking was over-engineered, wake-banner timing made the inbox signal unreliable, recency is predictable. Agents who need older content grep the file directly via the new footer pointer. Deleted `_extract_query_signal`, `_tokenize_query`, `_rank_entries`, the stopwords, the inbox-touching code path, and `_fit_head_entries`. New `_sort_entries_by_recency` parses ISO date from each header, sorts dated entries newest-first, undated entries land after in original document order. Footer pointer to the on-disk file is always present so the agent has a path even when budget displaces everything. (`eac2bfc` #126)
- **B7 central `teams.yaml` config — drop name-prefix string-match inference entirely.** The same 2026-05-29 directive flagged the brittleness: the B4 name-prefix branch first-dash-splits `@<a-b>-<c>` to `<a-b>`, which makes `@polymarket-agents-research` resolve to "polymarket" (no such project) rather than "polymarket-agents". Quote: "matching the agent identity by string matching is brittle. We could have a central config file where agent teams are registered." [ATTRIBUTED EXAMPLE — original operator directive] New `metasphere/teams.py` loader + lookup against `~/.metasphere/teams.yaml` (schema: `agents.<name>.projects: [...]`). Graceful-degrades on every failure mode so the resolution chain falls through to path inference. Mtime-keyed cache, O(1) once warm; operator edits land within one tick. `templates/install/teams.yaml` ships a canonical roster with schema notes. (`5a7f29a` #127)
- **B8 runtime nudge — AGENTS.md tells agents to grep the file when asked about something outside the recency window.** Closes the behavioral gap from orch's a worldwire-eng agent re-test: capsule rendered correctly but the agent didn't grep when asked about an entry outside the budget — it answered "not in memory" and fell back to auto-memory. Operator directive 2026-05-30: runtime nudge in AGENTS.md, not directive-grep — "I don't want them to always grep that could dilute their reasoning." Adds an identical "## Project memory store" section to every role template (critic, designer, eng, explorer, lead, orchestrator, researcher) with the three-part frame: capsule = recency lens, LEARNINGS.md / MEMORY.md at the footer path = primary memory store, auto-memory = last resort. (`c56d4c6` #128)
- **Repo-level docs alignment — README + install-templates + reference docs.** Operator directive 2026-05-30. README "Agent memory" bullet expanded into "Per-project memory" + "Agent memory" so first-time readers see the team-shared layer first; brief Memory section replaced with a layered walkthrough (per-project = LEARNINGS lessons + MEMORY facts → agent-level → auto-memory) including sample footer pointer + three-step resolution chain. (`611141d` #129; preceded by the earlier README-only landing `da9da71`)
- **Researcher template class-level discipline.** Side fix landed mid-tranche: A researcher weekly digest was wrong because the agent's MISSION.md baked a stale canonical path: a workspace clone frozen weeks earlier had the same directory shape as the live source, so reads from it silently returned "no recent files" instead of erroring. Adds a "Canonical source verification" section to `templates/agents/researcher/AGENTS.md` mandating `git -C PATH remote -v` + `git -C PATH log -1 --format=%cI` on every read window for git-backed sources, mtime check for non-git. (`e38be6e` #121)

### Messages — cross-scope `msg read` + wake-banner truncation + auto-escalation for high-priority labels

- **`metasphere msg read msg-XXX` works for archived messages, not just inbox.** Reproducer 2026-05-29: msg-1780061828-3630629 (T1 lead→eng dispatch, completed + archived ~4 minutes later) returned "not found" from `msg read` despite the archived file existing on disk. Bisect (per critic's hypothesis (3) at msg-1780066659): `archive_message` introduced inbox→archive moves but never extended the discovery surface; PR #10's canonical-layout refactor replaced the per-scope walk with `_canonical_inbox_dirs` (still inbox-only); H4 inbox-index added `_index_lookup` that exists()-checks the cached path but never refreshes when archive_message moves the file. Post-archive `msg read` falls off both the fast path (stale index entry filtered out by exists()) and the slow path (walk only covers inbox dirs). Fix: new `_find_message_anywhere(msg_id, paths)` walks inbox → archive → completed; called from `msg read`. (`3f15ff7` #119)
- **Wake-banner truncation on long task bodies.** Reproductions 2026-05-29: msg-1780061828 (a ~3KB T1 dispatch via `metasphere agent wake @metasphere-eng "<body>"`). Eng's pane came up to an empty `❯` prompt and the inbox !task stayed `read_at:` blank — the Claude Code TUI's bracketed-paste handler silently dropped the multi-KB payload, partial body landed in the input box, C-m committed the empty submit. msg-1780062587 hit the same class. The bootstrap-pointer workaround we'd been using (short wake → recipient outbox file → body) confirms the bug is in wake-delivery, not message-send. New `_prepare_wake_banner` helper persists bodies over `_WAKE_BANNER_BODY_THRESHOLD = 512` bytes via `messages.send_message(..., wake=False)` and substitutes a short pointer banner of the form `[task] Long body persisted as msg-XXX from <sender>. Read via: metasphere msg read msg-XXX`. Both warm-session and cold-start branches of `wake_persistent` go through the helper; on send_message failure we fall back to inline submission. (`3ab38b5` #118)
- **Auto-escalate undelivered high-priority wakes to session-respawn.** Trigger: a writing-lead agent's !task sat 7h unread before the operator noticed. When `send_message`'s best-effort `wake_recipient_if_live` inject returns `delivered=False` (dormant session, deferred typing, unresolvable target) and the label is `!task` / `!urgent` / `!query`, escalate to `agents.wake_persistent`. The `delivered` bool was added for #106 but was discarded at the send_message call site, so high-priority work could sit unread for hours on a nominally-alive but unresponsive recipient. Operator greenlight 2026-05-29 on `!task` + `!urgent`; widened to include `!query` shortly after — `!query` semantically requires a response, silent unread defeats the contract. `!info` / `!done` / `!reply` intentionally stay on heartbeat cadence (async status flow where REPL pickup on the next turn is the correct semantic). Guards on self-send, project recipient, @user sender, and `wake=False` sends. (`7bc3efe` #123)

### audit-docs filter refinements

Three small filters that cut false positives previously stripped by hand on every CHANGELOG promotion (visible as the "Five non-bump commits" / "Six non-bump commits" framing in the last four entries).

- **Skip `docs(*)` commits from staleness flagging — they ARE the doc update.** 2026-05-28 audit's `docs(changelog): ... canonical-name drift sweep ...` tripped the `canonical` keyword on the very commit that documented the canonical-name sweep. Self-referential false positive. Now skip records whose classified type is `docs` before running the keyword/path checks. Live verify on metasphere-agents: flag count 2 → 1. (`5faa957`)
- **Filter the auto-version-bump commits emitted by `bump-minor.yml`.** Every merge produces a `chore: bump version X.Y.Z → A.B.C [skip ci]` commit carrying zero CHANGELOG signal. 2026-05-29 audit reported 10 commits, 7 of which were bumps diluting the Chores bucket. Filter at the `_run_audit` boundary, after the documented-shas filter and before classification. Tolerates the bot shape (unicode arrow + `[skip ci]`) and the manual shape (ascii `->`, no marker). Subjects merely mentioning "version" or "bump" don't match — the regex anchors on the `^chore:\s*bump version\s+<semver>\s+(→|->)\s+<semver>` prefix. (`80d643b`)
- **Filter SHAs already cited in the newest CHANGELOG entry.** The UTC-midnight floor in `_normalize_since` is a deliberate choice (so a CHANGELOG entry written early on day N still captures commits that land later that same day — symptom 2026-04-15, see the existing docstring). But the side-effect is that a same-day end-of-day entry — the typical shape after a tranche of fixes land in a single afternoon — gets every one of those commits re-reported by the next audit, because their commit-time is `>= YYYY-MM-DD 00:00:00Z`. Read the SHAs the newest CHANGELOG entry already cites (every entry uses the `` (`abc1234`) `` convention) and filter those out. Observed: 2026-05-28 audit reported 7 commits, of which 5 were the entry I'd just written documenting them; with the filter, 3. (`ff3977a`, USAGE doc in `e647c39`)

### Docs

- `3f4a345` — `agent seed --force` semantics paragraph that was hand-edited into `docs/CLI.md` in fd9af16 (2026-05-25) finally got lifted into the source USAGE string in `metasphere/cli/agents.py`. `metasphere docs` had been regenerating without it for five days; `test_docs_check_passes_against_committed_reference` was red the whole time. Symmetric fix; byte-identical to the prior committed CLI.md.

Audit report: `metasphere audit-docs --project metasphere-agents` (run 2026-05-30).

---

## 2026-05-28 — canonical-name drift sweep across CLI surfaces + one audit-docs date-pick fix

Five non-bump commits in a single day, all narrow. Four close out the last surfaces still naming the legacy bare `messages` / `tasks` console-scripts after the unified-CLI cutover; the fifth fixes a silent over-report in the audit-docs newest-date extractor that was making every audit re-list the prior tranche's commits.

- **`cli/messages` validation hints** — `_USAGE_HINTS` and four `Usage:` printlines in `metasphere/cli/messages.py` still named the legacy `messages send …` form. The deprecation shim in `cli/_shims.py:42` warns and forwards to `metasphere msg`, so the only effect was that error messages pointed the user at the legacy surface they just got warned away from. Caught while the explorer cron itself hit the surface error (`Use: messages send @target !label "message"`) trying to ping `!info`. `test_cli_messages.py:33-35` already accepted both forms, so no test change needed. (`fa8243b`)
- **`cli/audit-docs` newest-date extractor picks end-of-range** — the regex looked for the first ISO date on a `##` header and returned that, so `## 2026-05-25 to 2026-05-27 — title` was scanned as starting at 2026-05-25. The 2026-05-28 audit was consequently reporting 25 commits since 2026-05-25, of which 18 were already covered by the closure stanza shipped on 2026-05-27. End-date pick makes the same audit report 7 commits since 2026-05-27 — the actual delta. Regression in `test_changelog_newest_date_range_returns_end`. (`72108db`)
- **harness/team canonical names in ephemeral scaffolding** — the `templates/agent-harness.md` render-template + the `/team` slash command's render block both still gave spawned ephemerals example `messages send` / `tasks new` invocations. New ephemerals would copy-paste those into their first turn and hit the same shim-warn surface. Replaced with `metasphere msg send` / `metasphere task new` throughout. (`226c4d5`)
- **`cli/tasks` validation hints** — same shape of fix as `fa8243b` but for the task surface: `_USAGE_HINTS` plus the `Usage:` printlines under `cmd_tasks_*` all named the legacy bare `tasks` console-script. Existing tests accepted both forms; no test-side edit. (`2815ebb`)
- **`schedule.dispatch_command` pre-wake recognizes canonical `msg send`** — the `_extract_messages_send_target` parser that decides whether to cold-start a dormant persistent agent before running a `command`-kind cron only matched the legacy bare-binary `messages send @X …` shape. A future `command`-kind cron using the canonical `metasphere msg send @X !task …` (or the bare `msg` shim) would skip pre-wake and write the inbox notice into a dormant session the gateway never injects into — the exact accumulate-unread silent-failure mode the original `test_dispatch_command_pre_wakes_messages_send_task_target` regression was guarding against. Verified by parsing the live `~/.metasphere/schedule/jobs.json` (45 jobs total, 24 `command`-kind, none currently use `msg send`) that no production cron hits the gap today; fix is forward-looking. Three new test cases (canonical bare, canonical full-path, negative for `metasphere msg read`); 14/14 scoped tests pass. (`2d35880`)

Audit report: `metasphere audit-docs --project metasphere-agents` (run 2026-05-28).

---

## 2026-05-25 to 2026-05-27 — post-cutover doc-rot closure + small correctness fixes

Seven non-bump commits in the trailing 48h. Three of them close out the "per-turn hook named by command, not module path" sweep that started with 22250e8 in the prior tranche; the rest are small but real correctness fixes (worktree-safe `metasphere docs`, never-raises contract on telegram attachments, dead skip-guard removal). Themes:

- **Per-turn-hook + templates/agents/ doc-rot, grep-verified-zero** — the 2026-05-23→25 tranche fixed the CLAUDE.md + install.sh + .claude/settings.json wording for the per-turn hook (`metasphere hooks context|posthook` is the wiring; the Python module path is incidental and not what install.sh writes), but three surfaces escaped the sweep. `fd9af16` corrected CLAUDE.md:26 + the `agent seed` docstring (the source for `docs/CLI.md`) to name `templates/agents/<role>/` rather than `<type>/<spec>/` and to point at `metasphere agent seed --spec` as the materialization moment (not "when an agent of that type is spawned"). `79b7b9c` rewrote the per-turn-hook line in all seven role AGENTS.md templates (critic, designer, eng, explorer, lead, orchestrator, researcher) from `metasphere.cli.context` to `` `metasphere hooks context` ``. `e54584a` closed two remaining hits in `docs/OPS.md` (the Reproducible alert demo + the Posthook fail-closed prose). The trailing grep across `docs/` + `templates/` returns zero module-path references for the per-turn hook; remaining mentions of `metasphere.cli.context` / `metasphere.posthook` live in source under `metasphere/` and in tests (mock targets, legitimate).
- **`metasphere docs` default output resolves from cwd, not package `__file__`** — running `metasphere docs` from a worktree silently wrote into the main checkout's `docs/CLI.md` because `_repo_root()` resolved from the editable-installed package path. Default now walks up from `Path.cwd()` looking for a dir carrying both `pyproject.toml` and `metasphere/__init__.py` (a strong fingerprint for a metasphere-agents checkout) and falls back to the `__file__`-based root only when cwd is outside any checkout. Three regression tests in `test_cli_docs.py` cover cwd-in-repo / cwd-outside-repo / end-to-end-write-lands-in-invoking-worktree. (`2d96735`)
- **`telegram.attachments.download_attachment` honors never-raises contract on missing token** — the docstring promised never to raise, but `api._config()` raises `RuntimeError` when no bot token is resolvable, leaking past the existing `OSError`/`URLError`/`ValueError` catch. Symptom: tests that strip both `TELEGRAM_BOT_TOKEN` + `METASPHERE_TELEGRAM_BOT_TOKEN` env vars + redirect `HOME` had been failing on clean-env runners since 2026-05-19, and the production poller would crash instead of rendering an attachment-misconfig note. Fix: `RuntimeError` joins the catch arm; missing-config returns the standard error-as-data `DownloadedAttachment(path=None, error="config: ...")` so the context block can flag the misconfig without dropping the rest of the inbound update. Test-side stub for the four existing download/render cases (`stub_token_env` autouse fixture in `test_attachments.py`) landed first as `6f3c498` so CI went green again; carved-out callsite fix shipped as `8f59068` with its own regression covering both env vars stripped.
- **Stale `USER.md.template` skip-guards removed from `test_specs`** — five tests carried a defensive `pytest.skip("USER.md.template not yet shipped")` guard whose template landed in `9fe0f66` on 2026-04-30. The guards were dead code for ~4 weeks, and the "no template" path is already explicitly covered by `test_seed_agent_user_md_no_template_leaves_unset` (monkeypatches the lookup to None). Net 15 deletions across 5 test bodies + helper; 20/20 tests in `test_specs.py` still pass. (`e151343`)

Audit report: `metasphere audit-docs --project metasphere-agents` (run 2026-05-27).

---

## 2026-05-23 to 2026-05-25 — flag-leak follow-on + helper consolidation + scope-registry fallback

Six non-bump commits after the 2026-05-22 "closed end-to-end" call. Two more flag-leak surfaces surfaced (third was caught at the same time), the four-of-a-kind helper got pulled into one place, and a 2026-05-13 scope-resolution fix turned out to miss a class of projects. Themes:

- **Three more flag-shape leaks** — the prior tranche's "closed end-to-end" call held for the read-side audit, but three write-side lookups still silently accepted dash-prefixed positionals: `hooks git install/uninstall/status PATH` (478be54), `restart <agent>` (8d7d794), and `project rename/delete/show/members/move/migrate <name>` (1e7381f). All three now reject flag-shape with rc=2 + a usage hint instead of falling through to a misleading lookup error. Each commit ships its own regression test (test_cli_git_hooks, test_cli_restart, test_cli_project_rename).
- **Flag-shape rejector consolidated** — after the third lookalike patch, the pattern was stable enough to centralize: `metasphere/cli/_argv.py::reject_flag_shape(value, op, *, command, what, usage)` replaces the inline helper in four surfaces (cli/restart, cli/project, cli/git_hooks, cli/session). Seven surfaces (messages, tasks, consolidate, trace, heartbeat, schedule, agents) keep their local variants — their signatures legitimately differ (3-arg rc=1, task-id sanitizer, combined int+negative validation, separate agent-name validator). The `_argv.py` module docstring records the carve-out so the next contributor doesn't re-converge. Tests in `test_cli_argv.py` cover the shared helper end-to-end; per-surface tests still exist for the call-site wiring. (e8e62ac)
- **`_resolve_scope` registry fallback** — b549761 (2026-05-13) fixed bare-name scope path-doubling by consulting `~/.metasphere/projects/<name>/project.json`, but projects registered before PR #10 (canonical project-dir move) or with their `project.json` still in-repo had no canonical file. `spawn(scope="worldwire")` from inside metasphere-agents fell through to `<repo>/worldwire/` and seeded an empty `.tasks/.messages` stub on every spawn. Fix: when canonical project.json is missing, consult `~/.metasphere/projects.json` directly and use the registry entry's `path`. Canonical project.json still wins when both signals exist. Live cleanup: removed the `worldwire/` and `ephemeral/` stub dirs the bug had been accumulating. Two new `TestResolveScope` cases cover the registry-fallback path and canonical-precedence-when-both-exist. (50a86a9)
- **Post-bash-rewrite doc rot in scripts/ + hooks** — three load-bearing repo docs still described the pre-2026-04-14 cutover world: CLAUDE.md's `scripts/` line described "bash shims that delegate to Python" (only `metasphere-reaper` survives); CLAUDE.md's per-turn-hook line named module paths (`metasphere.cli.context`, `metasphere.posthook`) that install.sh hasn't written since cutover (actual paths are `<venv>/bin/metasphere hooks context|posthook`, rewritten by `metasphere update::_sync_hook_paths` on relocate); `.claude/settings.json` `_comment` pointed at the deleted `scripts/metasphere-context` / `scripts/metasphere-posthook`; install.sh:386 comment still said "legacy bash kept in scripts/ for reference". Single-concern doc-only commit — `pytest metasphere/tests/` was green pre-edit (1451 passed, 4 deselected, 213s wall) and the edits don't touch code. (22250e8)

Audit report: `metasphere audit-docs --project metasphere-agents` (run 2026-05-25).

---

## 2026-05-17 to 2026-05-22 — flag-leak closure + posthook fidelity

Short follow-on tranche after the 2026-05-17 backfill. 22 non-bump commits, dominated by the read-side closure of the argv flag-leak audit and a small posthook+breadcrumb correctness pass. Themes:

- **Flag-leak audit closed end-to-end** — the prior tranche's "saturated" call was tranche-shaped, not pattern-shaped. A grep-verified-zero sweep across every read-side CLI leaf surfaced 9 more silent-drop surfaces (`ls`, `status`, `version`, `trace list`, `project list`, `schedule list`, `telegram groups list`, `session info`, etc.) and the closing patch (`27b7791`) rejects unknown trailing args across all of them. Write-side counterparts followed: `tasks new` (a82f47c), `tasks start/update/done/describe/show` (2cc1084), `accounts add/switch` (0573b6f), `session info/attach/stop/restart/send` (4586822), `project member add/remove` (c0d2296), `consolidate run` + `trace prune/list` + `schedule/heartbeat daemon` (d51d613, eabb0f8, be1e73c), `agent list/status/specs/wake` (27dccc4), `agent spawn` (f54cb83). Env-var counterpart in `agents.py` hardens `METASPHERE_STALE_SESSION_THRESHOLD_SEC` against non-int + negative values (76514cd) — a bad env var previously either crashed every CLI invocation or silently corrupted wake-stale logic.
- **Posthook captures pre-tool text in multi-entry turns** — `extract_last_assistant_text()` returned only the final JSONL assistant entry's text blocks; when a turn had text before tool calls (entry N) followed by tool results and more text (entry N+2), only N+2 reached Telegram and the pre-tool explanation was silently dropped (2d940b3).
- **Breadcrumb counter skips auto-injections** — heartbeat injections landing during a multi-tool turn inflated the Stop-time user-message count by +1, tripping the fail-closed gate and suppressing Telegram forward (ff26e22). Extended to the two other auto-injectors that share the same shape (restart-wake + agent-wake) for the same mid-turn race (36226b6).
- **Harness-vs-instance scrub, continuation** — three more passes ahead of the public flip: instance name dropped from `for_cwd` docstring example (bded011), instance identifiers scrubbed from test comments + docs (fb354dd), operator-host identifier stripped from a `KNOWN_ISSUES.md` verification note (9f6b3d6).
- **README sync** — agent-spawn signature (positional A/R/A contract fields, no more `--scope/--task/--sandbox` flags) + sandbox-level docs brought current with shipped CLI (b13cd9d).

Audit report: `metasphere audit-docs --project metasphere-agents` (run 2026-05-22).

---

## 2026-04-16 to 2026-05-17 — public-readiness hardening

Month of incremental hardening ahead of the public flip. 357 commits across CLI ergonomics, session lifecycle, consolidation, observability, and the harness-vs-instance scrub. Per-commit history: `git log --since=2026-04-16 --oneline`. Themes:

- **CLI argv-leak class fully closed** — flag-shaped values (`--help`, `@--bogus`, `-x`) were silently accepted as positional names across `msg send/reply/done/read/status`, `tasks new/assign/move`, `agent spawn/seed`, `schedule enable/disable`, `telegram groups`, and `project` (df6812e, 206c14b, 0782a12, 79675b4, e4a57a2, a52c91a, 15c5a3a, e8e2ab2, 70dd6b3, f5350ab). All surfaces now print a clean error + usage hint instead of writing corrupt frontmatter or raising `FileNotFoundError`.
- **Structured CLI docs** — each handler declares `DESCRIPTION` + `USAGE` constants; `docs/CLI.md` is now auto-generated by `metasphere docs` and gated against drift via `metasphere docs --check` (949a556). Templates/PROJECTS/CLAUDE installer docs synced to the canonical surface (b3d6d75, 8e3344a, 44c0795, 938c9da).
- **Session lifecycle hardening** — `metasphere session exit-self` primitive lands as synchronous `/exit` (8ad4894, d16a3c5), wired into 12 cron payloads (23a8edf) with per-job opt-out (a0e3ee1); `exit-self` now uses `tmux kill-session` so it can't self-interrupt the inject (90f68f7, 342baa4); ephemeral-idle reaper at 30min default; `session_health` reads orchestrator `last_active` sidecar instead of stale tmux `session_activity` (67837c9); cold-start on next wake when idle > `STALE_SESSION_THRESHOLD` (2h default).
- **Project scoping** — `_resolve_scope` consults the project registry before falling through to project-relative paths (b549761); CLI session ops resolve project-scoped agents (8f723e6, a5dc6f0, a5c0b10); `metasphere project rename` lands (f5b3c17); shared cross-agent project artifacts dir under `~/.metasphere/projects/<name>/shared/` (3d279bc).
- **Status + observability surface** — `metasphere status` surfaces systemd daemon health for `metasphere-gateway/heartbeat/schedule` (56a33a5) and replaces bare `(unavailable)` with exception diagnostics (d0c4a78); `metasphere logs` gained `update`/`posthook`/`reaper` subcommands (3dae986, bf2375e, ee6cea7), a freshness mtime header (fafd3d4), and an index view when no service is given (b531414).
- **Consolidate auto-archive** — terminal info labels (`!ack`, `!vet-result`, `!standby`, …) auto-archive once read (b936c81); generic read+silent auto-archive with `REQUIRED_ACTION_LABELS` opt-out (e75fc17); `!urgent` archives after ladder + 7d read (7f476b8); `!done` notification ping-loop closed (957eab4).
- **Failsafe + Anthropic credentials** — `metasphere failsafe` auto-rotates the active OAuth credential on rate-limit detection (03a0080), refuses to rotate when the live file is unmanaged (a1f4c25), and attributes detection to the agent that caught it (42694eb); `metasphere accounts` manages the credential profile symlink (696e8fa).
- **Voice-note transcription** — opt-in `faster-whisper` transcription stitched into the existing `[attachments]` block; voice / audio / video_note / audio-MIME documents transcribe locally and append the text to the agent's injected payload (444b422, #115).
- **Gateway surface adapter** — Telegram coupling extracted into a `SurfaceAdapter` interface so additional surfaces can plug in without rewriting the daemon (19d0b9f); `_poll_once` binds the adapter list once at `run_daemon` boot (475f69b); group `@-mention` wake + `ensure_session` on addressed messages (386641a).
- **Schedule** — `agent_id`-driven routing replaces 5 hardcoded prefix branches (82a5749); `dispatch_command` diagnostic surfaces instead of silent `exited 1:` (eebe5ed); silent wake-inject failures surfaced across schedule/messages/agents (a616cd7).
- **Templates + persona scaffold** — designer-role AGENTS.md (a30997c); project-level `USER.md` template seeded + symlinked into agent dirs (a606a76, 9fe0f66); USER persona block injected untruncated every turn (0308786); template drift warned + opt-in `metasphere update --templates` (f716c72).
- **Test pollution + sandboxing** — three-pass session-end pollution detector hardens against real-home leaks during tests (carried forward from PR #5/#8); autouse fixtures redirect 8 home-relative module constants per test.
- **Harness-vs-instance scrub** — runtime/comment instance leaks scrubbed in three batch passes (2e6db1d, be19488, cdf762b) ahead of the public flip; operator-host identifiers removed from shipped paths.
- **Auto-versioning** — patch bumps automated via `.github/workflows/bump-minor.yml` on every merge to `main` (143+ `chore: bump version` commits in-period). `[skip ci]` opts a merge out.

Audit report: `metasphere audit-docs --project metasphere-agents` (run 2026-05-17). 80 README staleness flags surfaced — README sweep is a follow-up.

---

## 2026-04-15 — PR #12: README + CLI regression bundle

Re-implemented three CLI subcommands that the README promised but had no Python implementation after the bash→Python port; updated README to match current reality; added architecture section with routing diagram; backfilled this CHANGELOG.

- **`metasphere daemon start|stop|restart|status`** — thin wrapper over `systemctl --user` for gateway / heartbeat / schedule. One-line-per-service output; inactive (rc=3) reported but not failed. (commit 2)
- **`metasphere logs [gateway|heartbeat|schedule|events] [-f] [--lines N]`** — tail-and-follow over `~/.metasphere/logs/*.log` and dated `~/.metasphere/events/*.jsonl`. Events JSONL pretty-printed. (commit 3)
- **`metasphere config telegram`** — interactive setup wizard (getMe validation + chat-id auto-discovery via getUpdates) or non-interactive `--token --chat-id`. Writes `~/.metasphere/config/telegram.env` (chmod 0600) + `~/.metasphere/config/telegram_chat_id`. (commit 4)
- **README doc fixes**: `tasks` / `messages` examples switched to `metasphere task` / `metasphere msg`; project `--member` syntax clarified (`@name:role:persistent` 3-part); Telegram slash-command list synced to actual `BOT_COMMANDS_MANIFEST`; OpenClaw migration section replaced with `migrate-project-dirs`. (commit 1)
- **README architecture section** (mermaid routing diagram + canonical per-project layout spec). (commit 5)

## 2026-04-15 — PR #11: Project-paths cleanup + consolidator routing + paused terminal

The maintainer flagged that the operator view was seeing 7-8 STALE→escalated-user events per 15-min cycle from worldwire tasks. Bundled five related fixes:

- **Removed PR #10 migration bridges** — `load_project` / `save_project` are now canonical-only (no in-repo read fallback, no dual-write).
- **Dropped unused `project_root` params** on `_find_task_file`, `scan_active_tasks`, `scan_inbox_messages`.
- **Consolidator routes pings to `@<project>-lead`** before `task.assignee` (maintainer directive). New `_route_ping_target` resolves `project → registered lead member → agent id`; falls back to assignee if no lead.
- **`VERDICT_PAUSED`** — `status: paused` now classifies terminal before the stale window check. `apply_verdict` treats it like BLOCKED / ACTIVE (noop, no ping, no archive).
- Deployed clean; 13 PAUSED→noop, 0 escalations on the next consolidate cycle.

## 2026-04-15 — PR #10: Messages / changelog / learnings migration

Follow-up to PR #9 using the `Project` abstraction. Everything project-scoped now lives under `~/.metasphere/projects/<name>/`:

- `.messages/inbox/` + `.messages/outbox/` routed via `_canonical_messages_dir(scope, paths)`; old per-scope nested inbox walk replaced with "one inbox per project + global bucket".
- `project_changelog` / `project_learnings` write to canonical `.changelog/` / `.learnings/`.
- `_ensure_scaffold` creates canonical dirs (in-repo `.metasphere/` stays as a lightweight marker).
- Migration subcommand `metasphere migrate-project-dirs --what {messages,changelog,learnings,all}` exercises the moves.

## 2026-04-15 — PR #9: Canonical tasks layout

Fixed the root cause behind `metasphere task done <id>` raising `FileNotFoundError`: tasks lived at `<repo>/.tasks/` but the CLI was looking under `~/.metasphere/`. Added a typed `Project` dataclass with `tasks_dir(paths)` / `messages_dir(paths)` / `changelog_dir(paths)` / `learnings_dir(paths)` + `Project.for_name()` / `Project.for_cwd()` / `Project.global_scope()` factories. Tasks now routed through `_canonical_tasks_dirs(paths)` — every registered project's `.tasks/` plus the global bucket at `~/.metasphere/tasks/`. Added `metasphere migrate-project-dirs` subcommand with `--what tasks` (idempotent; refuses on conflict).

## 2026-04-15 — PR #8: Extended test-pollution guard + autouse sandbox

PR #5's `b'BYTES:'`-only signature guard missed the 2026-04-15 Fix 1 leak where 41 fake task `.md` files and 64 stream JSONL lines landed in real `~/.metasphere/`. Three-pass session-end detector: signature match (pass 1); any new file with a pollution extension (.md/.lock/.jsonl/.bin) under a guarded subdir (pass 2); stream-content allow-listing the operator's real chat_id against a regex over the `"chat":{"id":N}` shape (pass 3). Autouse fixture redirects METASPHERE_DIR + 8 home-relative module constants + 8 function `__defaults__` tuples per test — closes the ignored-Paths-arg loophole that bypassed env monkeypatch.

## 2026-04-15 — PR #5: Session-scoped pollution guard (signature-based)

First defensive layer after PR #3's fake `BYTES:biggest.bin` fixture leaked into `~/.metasphere/attachments/`. `pytest_sessionstart` snapshots the real-home file set; `pytest_sessionfinish` fails the suite if any new file with that exact head appeared. Signature-based so live gateway/heartbeat/schedule daemons writing to real dirs during a test run don't false-positive.

## 2026-04-14 — PRs #6 + #7: Single Telegram handler, single poller

- **PR #6** — extracted per-update handling into `metasphere/telegram/handler.py`. Before, `metasphere/cli/telegram.py::_handle_update` had the attachments/archive/debug-log logic but the production `metasphere-gateway` systemd service ran `metasphere/gateway/daemon.py::_poll_once`, which silently filtered `if u.text and u.chat_id is not None` and dropped every photo. Both call sites now route through `handler.handle_update`. Per-update try/except so a handler exception advances the offset instead of re-driving.
- **PR #7** — collapsed three parallel poll loops (`cli/telegram.py::cmd_poll` + `cmd_once` + `heartbeat.py` combined-daemon thread) into `telegram/poller.py::run_poll_iteration`. Deleted the CLI poll subcommands, the `--with-telegram-poll` heartbeat flag, and the `HEARTBEAT_WITH_TELEGRAM_POLL` env var. Gateway daemon is now the single production poller.

## 2026-04-14 — PRs #3 + #4: Telegram attachments + debug instrumentation

- **PR #3** — poller now parses every top-level media object (photo array + any dict with `file_id` — document, audio, video, voice, video_note, animation, sticker), calls `getFile`, downloads bytes to `~/.metasphere/attachments/<message_id>/`, renders an `[attachments]` block appended to the injected orchestrator payload. No MIME-type filter; Claude decides what to do with each file. Photo thumbnails pick largest; filenames sanitized to `[A-Za-z0-9._-]`.
- **PR #4** — JSONL debug log at `~/.metasphere/state/telegram_debug.log` records `post_parse` / `early_return` / `archive_error` / `pre_inject` for each update. Archiver errors caught and logged instead of killing the inject path. Autouse sandbox fixture in `test_telegram.py` prevents future `attachments/` pollution.

## 2026-04-14 — PR #2: Consolidator exempts persistent agents from liveness GC

`_gc_ephemeral_agents` keyed persistence on `MISSION.md` alone, but bootstrap writes `persona-index.md` → `SOUL.md` → `MISSION.md` sequentially. 9 newly-bootstrapped persistent personas got reaped mid-bootstrap as "dead". Widened the skip predicate to `MISSION.md OR persona-index.md` in both `_is_persistent_agent` and `_gc_ephemeral_agents`. Ephemerals unaffected.

## 2026-04-08 to 2026-04-13 — Python port + lifecycle hardening

Pre-PR era: incremental commits through the bash → Python port and associated hardening. Highlights:

- **Python CLI cutover**: Legacy bash `scripts/*` retired; `metasphere <subcmd>` is the canonical entry point. Context hook, posthook (Stop), task/message/update modules all ported. Unified `metasphere` binary; individual script symlinks removed.
- **Telegram bridge hardening**: HTML parse_mode + bold rendering; mobile-first card layout for `/tasks` + `/schedule`; `/session restart`, `/projects`, `/schedule` slash commands; ack-reaction flow (👀 → 👍 on orchestrator reply); `setMessageReaction` retries; telegram-groups non-interactive setup.
- **Task lifecycle refactor**: `consolidate.py` STALE/ACTIVE/BLOCKED/UNOWNED/DONE verdicts; dated archive buckets; `tasks require project+assignee`; `--project <name>` filter; `task describe` verb; `task list --condensed` for all-projects view.
- **Scheduler / daemon polish**: schedule daemon hardening; cron-fire research monitors; heartbeat model-binding audit; agent `--model` flag; persistent vs ephemeral agent model.
- **Directives + broadcast channel**: `DIRECTIVES.yaml` as a broadcast channel; `metasphere agent verify @name`.

Full commit-level history: `git log --since=2026-04-07 --until=2026-04-14 --oneline --first-parent main`.

---

## [2026-04-07T00:37:00Z] — Telegram Bridge + CAM Integration

**Context:** Human-in-the-loop via Telegram; user can intervene on every turn.

**Changes:**
- Created `metasphere-telegram` - Bot command handler (/status, /inbox, /tasks, /send, /cam)
- Created `metasphere-telegram-stream` - Stream archival + CAM indexing
- Created `metasphere-heartbeat` - Proactive monitoring daemon
- Updated `metasphere-context` to inject last Telegram message first
- Telegram messages archived to `~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`
- Messages indexed into CAM for searchable history

**Architecture:**
```
User (Telegram) ←→ metasphere-telegram-stream ←→ Agent Mesh
                          ↓
                    CAM (searchable)
                          ↓
                    Context Injection
```

**Features:**
- Bidirectional: human → agents, agents → human
- Last message always in agent context (user can intervene)
- Proactive notifications for urgent messages, blocked agents
- Stream archived locally + indexed to CAM

**Files created:**
- `scripts/metasphere-telegram`
- `scripts/metasphere-telegram-stream`
- `scripts/metasphere-heartbeat`

---

## [2026-04-07T00:20:00Z] — Self-Evolution Bootstrap

**Context:** Rewrote claude.md for operational self-evolution; session continued from context compaction.

**Changes:**
- Rewrote `claude.md` from specification doc to operational instructions
  - Added Evolution Loop based on Karpathy's AutoResearch pattern
  - Added SPIRAL cognitive loop documentation
  - Added Quick Reference for scripts, labels, priorities
  - Added Self-evolution protocol
- Updated @orchestrator identity files at `~/.metasphere/agents/@orchestrator/`
  - `LEARNINGS.md`: Session insights (fractal scoping, file-based coordination, hooks)
  - `MISSION.md`: Clear success criteria with phase checkboxes
  - `HEARTBEAT.md`: Current operational status
- Fixed hook path in `.claude/settings.json` (was pointing to wrong directory)
- Verified all scripts working: messages, tasks, metasphere-spawn, metasphere-context

**Learnings captured:**
1. Karpathy's AutoResearch: tight feedback loops > extensive planning
2. Fractal scoping with upward visibility creates natural information flow
3. File-based coordination beats API calls (git-friendly, inspectable, durable)
4. Context injection via hooks gives agents immediate awareness

**Files touched:** `claude.md`, `CHANGELOG.md`, `.claude/settings.json`, `~/.metasphere/agents/@orchestrator/*`

---

## [2026-04-06T23:30:00Z] — Renamed to Metasphere Agents

**Context:** Project renamed for clarity and installability on any machine.

**Changes:**
- Renamed from fractal-agents to metasphere-agents
- Runtime directory: `~/.metasphere/`
- Added installation instructions to claude.md
- Updated all CLI commands to use `metasphere-` prefix
- Prepared for GitHub remote at julianfleck/metasphere-agents

**Impact:** Project is now installable on any VM/computer.

**Files touched:** `claude.md`, `overview.yaml`, `CHANGELOG.md`

---

## [2026-04-06T23:15:00Z] — Added Git Versioning Backbone

**Context:** Git requested as backbone for tracking agent developments across machines.

**Changes:**
- Added comprehensive Git integration section to claude.md
- Defined auto-commit triggers (session_complete, summary_updated, decision_made, task_completed)
- Added git hooks for agent coordination (post-commit notifications)
- Specified merge strategies for concurrent agent work
- Integrated with CAM's existing GitHub sync mechanism

**Impact:** Enables full audit trail of agent activity with cross-machine sync.

**Files touched:** `claude.md`

---

## [2026-04-06T23:13:47Z] — Initial Project Bootstrap

**Context:** Anthropic cut OpenClaw API access; need lightweight replacement using Claude Code.

**Changes:**
- Created `claude.md` with full architecture specification
- Documented SPIRAL agentic loop (Sample → Pursue → Integrate → Reflect → Abstract → Loop)
- Defined virtual filesystem structure for agent/memory coordination
- Integrated Collective Agent Memory (CAM) for knowledge substrate
- Added Claude Code hook patterns (SessionStart, PreToolUse, Stop)
- Created directory structure (docs/, input/, .claude/)
- Wrote initial research notes with external sources
- Created `overview.yaml` project ledger

**Impact:** Project now has solid architectural foundation for MVP development.

**Files touched:** `claude.md`, `overview.yaml`, `docs/research/2026-04-06/01-initial-research.md`

---

## Research Sources

- [Multi-Agent Systems & AI Orchestration Guide 2026](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)
- [collective-agent-memory](https://github.com/julianfleck/collective-agent-memory) (CAM architecture)
