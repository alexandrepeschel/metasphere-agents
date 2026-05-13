# Known Issues

Living document. Add issues as they're discovered, strike them when fixed (keep
the line — history is signal). Newest at top of each section.

## Format
```
- [ ] **short title** — one-line description
      Where: file:line or component
      Repro: how to see it
      Notes: hypotheses, related tasks
```

---

## Critical (breaks core flow)

- [x] **`messages` script aborts silently when unread count is 0** — context hook prints `Error loading messages`.
      Where: `scripts/messages:171` — `((unread++))` returns exit 1 on first increment from 0, trips `set -e`.
      Fix: replaced with `unread=$((unread + 1))` (a6809ac+).
      Notes: classic bash gotcha. Audit complete (@explorer 2026-04-07): only other offender was `scripts/metasphere-agent` (9 sites in `doctor` + `tree` subtree-stats), all converted to `var=$((var + 1))`. No other `((var++))`/`((var--))`/`let` increments under `set -e` in scripts/.

## High

- [x] **Fractal spawning auto-exec missing** — `metasphere-spawn` now launches child detached via `nohup claude -p ... --dangerously-skip-permissions`, writes `pid` and `output.log` in agent dir, opt-out via `METASPHERE_SPAWN_NO_EXEC=1`. (Fixed in this session.)
      Related task: `fractal-spawning-any-agent-can-spawn-sub-agents-20260406`

- [x] **Persistent agent idle GC** — three layers landed:
      `agents.py` cold-starts on next wake when idle > `METASPHERE_STALE_SESSION_THRESHOLD_SEC` (default 7200s/2h);
      gateway daemon reaps fully-dormant sessions at `dormancy_max_idle_seconds` (default 86400s/24h);
      ephemerals reaped at `ephemeral_idle_max_seconds` (default 1800s/30min).
      Decision-as-shipped: warm session for ≤2h, cold-start beyond that, hard-reap at 24h.
      Verified live: @explorer kill at idle=14429s on 2026-05-07T12:00:59Z.

- [x] **Spawned child in `-p` mode doesn't engage tools** — child process runs and exits cleanly but only prints "Done." with no tool calls. The harness markdown as the entire `-p` prompt is too descriptive / not action-imperative enough. Headless claude treats it as a doc, not a task.
      Where: `scripts/metasphere-spawn` harness template + invocation strategy
      Repro: spawn @smoke-test with a "send message back" task — process exits 0, status updates, but no message sent back.
      Hypotheses: (a) need to append an explicit "BEGIN. Execute the task now using bash." imperative at the end of the harness, (b) headless mode may need `--allowedTools "Bash,Read,Write,Edit"` explicitly, (c) the SPIRAL/Communication sections describe machinery without saying "do this now".
      Resolved (@explorer 2026-05-09): the bash-era bug doesn't reproduce on the Python spawn path. `bin/metasphere-spawn` is gone; `metasphere agent spawn` lives in `metasphere/agents.py::spawn_agent` (line 339), invoking `claude -p <harness> --dangerously-skip-permissions` against `templates/agent-harness.md`. The new harness opens with imperative framing ("First thing: read your SOUL", followed by a concrete Communication / Task System / Tools surface) rather than the descriptive SPIRAL/Communication block from the bash era. Live evidence today (2026-05-09): two ephemerals completed multi-tool work end-to-end — `eph-bootstrap-frontend-repo` ran `gh repo create`, `pnpm dlx shadcn`, multiple commits + push (output a513286, see daily/2026-05-09.md 15:20Z), and `eph-author-frontend-brainstorming` shipped 5 commits authoring docs/brainstorming/01-04 + README. Tool engagement is reliable on the current path. The original `scripts/metasphere-spawn` script and bash harness no longer exist on disk.

- [x] **Telegram send wrapper chokes on markdown chars** — `send_message()` defaulted to `parse_mode=Markdown` and used `-d "text=$text"` (no urlencode). Fixed: default parse_mode is now empty (plain text), always uses `--data-urlencode`. Markdown is opt-in via the third arg. (Fixed this session.)

- [x] **`~/.metasphere/bin/` was copies, not symlinks** — install.sh `cp`s scripts into bin, so any repo edit silently failed to take effect until reinstall. This invalidated all prior fixes in this session until the symlink conversion. Fixed: converted all 22 scripts in `~/.metasphere/bin/` to symlinks pointing at `$REPO/scripts/$name`. Backup at `~/.metasphere/bin.backup-20260407/`. install.sh should be updated to symlink by default.
      Follow-up: patch `install.sh` to use `ln -sf` instead of `cp` for the bin install step.

- [x] **Telegram slash commands still point at openclaw** — `/inbox`, `/tasks`, `/schedule`, `/help` haven't been re-registered with BotFather for metasphere.
      Related task: `register-slash-commands-with-botfather-setmycommands-20260406`
      Resolved (@explorer 2026-05-09): `metasphere/gateway/daemon.py` calls `register_bot_commands()` on every boot (commit 4af3622, 2026-04-08), publishing `BOT_COMMANDS_MANIFEST` from `metasphere/telegram/commands.py` via the Telegram `setMyCommands` API. Live verify against the bot: 15 commands registered, including `/tasks`, `/schedule`, `/help` plus the metasphere-correct surface (`/agents`, `/team`, `/specs`, `/send`, `/projects`, `/messages`, `/spot`, `/events`, `/memory`, `/ping`, `/session`, `/status`). `/inbox` was retired in favour of `/messages` for the canonical name; the typed `/inbox` still routes via the dispatch table for any cached autocomplete. The 2026-04-06 task lives under `.tasks/completed/`.

- [x] **Tasks not properly cleaned up** — completed/stale tasks linger in `.tasks/active/`.
      Where: `scripts/tasks` (move-on-done logic missing or broken?)
      Repro: 1 found by @explorer 2026-04-07: `@ux-tester/ux-tester-lifecycle` was status:completed but lived in `.tasks/active/completed/ux-tester-lifecycle.md` (manually moved to `.tasks/completed/`). Suggests `tasks done` did move it but resolved the destination relative to `.tasks/active/` instead of `.tasks/`. Worth a one-line fix in scripts/tasks before the Python rewrite lands so live data stays clean.
      Also found: 5 active tasks with `/` in their id (e.g. `installsh-detect-.../metasphere-files-...`) created nested subdirs in `.tasks/active/`. Same root cause as `fix-tasks-slug-sanitization-for-slash-chars-20260406`.
      Related task: `audit-agent-ephemerality--cleanup-20260406`
      Resolved (@explorer 2026-05-09): the bash-era bug went away with the Python rewrite. `metasphere/tasks.py::complete_task` (line 539) moves `active/<id>.md` → `archive/YYYY-MM-DD/<id>.md`; legacy `completed/` remains a read-only fallback in `_find_task_file` (line 410). The slash-in-slug subsidiary was closed separately by the `Task slug sanitization` bullet below. On-disk verification: zero `status: completed` files in any `*/tasks/active/` dir across spot's metasphere tree, the agents repo, and project scopes. Coverage: `test_complete_task_archives_to_dated_dir`, `test_find_task_includes_archive_and_legacy_completed`, `test_list_includes_archive_when_completed_requested`.

## Normal

- [ ] **Bare-name `scope` in `spawn_ephemeral` resolves under project_root** — passing a bare project name as `scope_path` (e.g. `"writing"`, `"metasphere-agents"`) takes the `else` branch in `_resolve_scope` and becomes `<project_root>/<bare-name>`, so the agent's scope sidecar + the scaffold mkdir at `agents.py:358-361` land inside the spawning project's working tree instead of the intended sibling project. Result: empty `.tasks/{active,completed}` + `.messages/{inbox,outbox}` stubs accumulate in-repo, and any tasks/messages the agent emits route to the wrong scope.
      Where: `metasphere/agents.py:289` (`_resolve_scope`) — the `else: s = project_root / scope_path` branch can't disambiguate "bare project name" from "subdirectory of this project."
      Repro: spawn an ephemeral with `scope="writing"` (or any registered project name) from inside `metasphere-agents`; observe `<repo>/writing/.tasks/active/` etc. get created.
      Cleanup: empty stubs at `metasphere-agents/{metasphere-agents,writing}/` removed by @explorer 2026-05-13 (rmdir, gitignored, no-op for git).
      Fix candidate: when `scope_path` contains no `/`, consult the project registry first; if it names a registered project, return that project's absolute path. Falls back to the current relative semantics otherwise. Add a test covering both ambiguity directions (bare name that IS a project vs bare name that ISN'T).
      Notes: lower-urgency than it looks — the empty stubs are gitignored, and there's no current evidence of an agent actually committing work to a wrong-scope path (grep of `~/.metasphere/agents/*/scope` found zero stale references to the cleaned dirs). But the semantics are wrong and a future bigger refactor will trip on it.

- [x] **Memory maintenance not encoded in CLAUDE.md** — there's no explicit protocol telling the orchestrator when to prune `LEARNINGS.md`, rotate `HEARTBEAT.md`, summarize old daily logs, etc.
      Fix: "Memory Hygiene" section landed in CLAUDE.md with a file/cadence/action table covering LEARNINGS, HEARTBEAT, MISSION, SOUL/IDENTITY, daily logs.

- [x] **Daemon status accuracy** — `metasphere status` reports stale/wrong session state.
      Related task: `fix-metasphere-daemon-status-accuracy-20260406`
      Progress (@explorer 2026-04-24): one case resolved — `Tasks: (unavailable)` was a silent TypeError in `status.py:26` (call to `list_tasks(project_root)` missing the `scope` arg, masked by `except Exception`). Fixed, regression test in `tests/test_status.py`. Full suite pass.
      Resolved (@explorer 2026-05-09): orchestrator-idle accuracy was the remaining symptom. `gateway.session.session_health` was reading tmux `session_activity`, which only advances on keystrokes — so an unattended REPL processing pasted prompts read as idle for hours. Live example: `idle 131040s` (~36h) while the orchestrator's `last_active` sidecar was 2h fresh. Fix: `session_health` now prefers the `<paths.agents>/@orchestrator/last_active` sidecar (refreshed by every hook signal — UserPromptSubmit, Stop, telegram-inject, heartbeat-tick), falling back to tmux `session_activity` only when the sidecar is missing. Matches the signal `reap_dormant` already uses. Coverage: `test_session_health_prefers_orchestrator_sidecar` in `test_gateway.py`. Live verify: idle dropped from 131040s → 66s post-fix, advancing in step with heartbeat ticks instead of keystrokes.

- [x] **Task slug sanitization** — slashes in titles produce broken slugs/paths.
      Fix: `tasks.slugify()` replaces `/` with `-` and strips punctuation; covered by `test_slugify_replaces_slashes`. Live tasks dir has no nested-dir leaks as of 2026-04-24.

## Low

- [x] **Agent tree doesn't look like a tree** — `metasphere agents` flat list, no hierarchy.
      Related task: `make-agent-tree-actually-look-like-a-tree-20260406`
      Resolved (@explorer 2026-05-09): `metasphere agent list` now groups persistent agents by project. Global agents (empty project sidecar) print under a `global/` header first; project-scoped agents print under per-project headers (`worldwire/`, `recurse/`, …) alphabetically; agents within each bucket sorted by name. Implementation in `metasphere/cli/agents.py::_list`; coverage in `test_cli_agents.py` (4 cases: bucket order, global-omitted-when-empty, filter under tree layout, empty-state message). The `--project <name>` filter continues to work and renders the same shape with one bucket. Header line preserved so existing screen scrapers still match.

- [x] **Stale agents in registry** — `~/.metasphere/agents/` contains agents from old sessions (`@coding-integration`, `@coding-simple`, `@main`, `@night`, `@research-gather`, `@research-synthesize`, `@smoke-test`) with no GC.
      Resolved (@explorer 2026-05-08): all 7 named agents are gone from the registry. Ephemeral GC now ships in `metasphere/consolidate.py::_gc_ephemeral_agents` — agents without `MISSION.md`/`persona-index.md` are reaped once their tmux+pid go cold; persistent personas are exempt by design.

---

## Test Coverage Gaps (CLI e2e)

**Superseded (2026-05-08)** — this checklist tracked end-to-end coverage of
pre-Python-rewrite bash scripts. The Python CLI surface (`metasphere/cli/*.py`)
has unit-test coverage in `metasphere/tests/test_cli_*.py`; the listed bash
scripts are either retired or thin shims that delegate to Python. The original
checklist is preserved in git history (see commits prior to 2026-05-10); do
not interpret it as open work.
