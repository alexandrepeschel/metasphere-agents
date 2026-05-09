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

- [ ] **Spawned child in `-p` mode doesn't engage tools** — child process runs and exits cleanly but only prints "Done." with no tool calls. The harness markdown as the entire `-p` prompt is too descriptive / not action-imperative enough. Headless claude treats it as a doc, not a task.
      Where: `scripts/metasphere-spawn` harness template + invocation strategy
      Repro: spawn @smoke-test with a "send message back" task — process exits 0, status updates, but no message sent back.
      Hypotheses: (a) need to append an explicit "BEGIN. Execute the task now using bash." imperative at the end of the harness, (b) headless mode may need `--allowedTools "Bash,Read,Write,Edit"` explicitly, (c) the SPIRAL/Communication sections describe machinery without saying "do this now".
      Next: try option (a) + (b) together.

- [x] **Telegram send wrapper chokes on markdown chars** — `send_message()` defaulted to `parse_mode=Markdown` and used `-d "text=$text"` (no urlencode). Fixed: default parse_mode is now empty (plain text), always uses `--data-urlencode`. Markdown is opt-in via the third arg. (Fixed this session.)

- [x] **`~/.metasphere/bin/` was copies, not symlinks** — install.sh `cp`s scripts into bin, so any repo edit silently failed to take effect until reinstall. This invalidated all prior fixes in this session until the symlink conversion. Fixed: converted all 22 scripts in `~/.metasphere/bin/` to symlinks pointing at `$REPO/scripts/$name`. Backup at `~/.metasphere/bin.backup-20260407/`. install.sh should be updated to symlink by default.
      Follow-up: patch `install.sh` to use `ln -sf` instead of `cp` for the bin install step.

- [ ] **Telegram slash commands still point at openclaw** — `/inbox`, `/tasks`, `/schedule`, `/help` haven't been re-registered with BotFather for metasphere.
      Related task: `register-slash-commands-with-botfather-setmycommands-20260406`

- [x] **Tasks not properly cleaned up** — completed/stale tasks linger in `.tasks/active/`.
      Where: `scripts/tasks` (move-on-done logic missing or broken?)
      Repro: 1 found by @explorer 2026-04-07: `@ux-tester/ux-tester-lifecycle` was status:completed but lived in `.tasks/active/completed/ux-tester-lifecycle.md` (manually moved to `.tasks/completed/`). Suggests `tasks done` did move it but resolved the destination relative to `.tasks/active/` instead of `.tasks/`. Worth a one-line fix in scripts/tasks before the Python rewrite lands so live data stays clean.
      Also found: 5 active tasks with `/` in their id (e.g. `installsh-detect-.../metasphere-files-...`) created nested subdirs in `.tasks/active/`. Same root cause as `fix-tasks-slug-sanitization-for-slash-chars-20260406`.
      Related task: `audit-agent-ephemerality--cleanup-20260406`
      Resolved (@explorer 2026-05-09): the bash-era bug went away with the Python rewrite. `metasphere/tasks.py::complete_task` (line 539) moves `active/<id>.md` → `archive/YYYY-MM-DD/<id>.md`; legacy `completed/` remains a read-only fallback in `_find_task_file` (line 410). The slash-in-slug subsidiary was closed separately by the `Task slug sanitization` bullet below. On-disk verification: zero `status: completed` files in any `*/tasks/active/` dir across spot's metasphere tree, the agents repo, and project scopes. Coverage: `test_complete_task_archives_to_dated_dir`, `test_find_task_includes_archive_and_legacy_completed`, `test_list_includes_archive_when_completed_requested`.

## Normal

- [x] **Memory maintenance not encoded in CLAUDE.md** — there's no explicit protocol telling the orchestrator when to prune `LEARNINGS.md`, rotate `HEARTBEAT.md`, summarize old daily logs, etc.
      Fix: "Memory Hygiene" section landed in CLAUDE.md with a file/cadence/action table covering LEARNINGS, HEARTBEAT, MISSION, SOUL/IDENTITY, daily logs.

- [ ] **Daemon status accuracy** — `metasphere status` reports stale/wrong session state.
      Related task: `fix-metasphere-daemon-status-accuracy-20260406`
      Progress (@explorer 2026-04-24): one case resolved — `Tasks: (unavailable)` was a silent TypeError in `status.py:26` (call to `list_tasks(project_root)` missing the `scope` arg, masked by `except Exception`). Fixed, regression test in `tests/test_status.py`. Full suite pass. Session-state accuracy is the remaining original symptom — still open.

- [x] **Task slug sanitization** — slashes in titles produce broken slugs/paths.
      Fix: `tasks.slugify()` replaces `/` with `-` and strips punctuation; covered by `test_slugify_replaces_slashes`. Live tasks dir has no nested-dir leaks as of 2026-04-24.

## Low

- [ ] **Agent tree doesn't look like a tree** — `metasphere agents` flat list, no hierarchy.
      Related task: `make-agent-tree-actually-look-like-a-tree-20260406`

- [x] **Stale agents in registry** — `~/.metasphere/agents/` contains agents from old sessions (`@coding-integration`, `@coding-simple`, `@main`, `@night`, `@research-gather`, `@research-synthesize`, `@smoke-test`) with no GC.
      Resolved (@explorer 2026-05-08): all 7 named agents are gone from the registry. Ephemeral GC now ships in `metasphere/consolidate.py::_gc_ephemeral_agents` — agents without `MISSION.md`/`persona-index.md` are reaped once their tmux+pid go cold; persistent personas are exempt by design.

---

## Test Coverage Gaps (CLI e2e)

**Superseded (2026-05-08)** — this checklist tracked end-to-end coverage of
pre-Python-rewrite bash scripts. The Python CLI surface (`metasphere/cli/*.py`)
has unit-test coverage in `metasphere/tests/test_cli_*.py`; the bash scripts
listed below are either retired or now thin shims that delegate to Python.
Lines preserved as historical record; do not interpret as open work.

Each script needs an end-to-end pass. Mark `[x]` when verified, `[!]` when broken.

- [ ] `messages` (send/reply/done/read/tree/status)
- [ ] `tasks` (new/start/update/done/list)
- [ ] `metasphere` (status/ls/agents/watch)
- [ ] `metasphere-spawn` (full lifecycle, child auto-exec)
- [ ] `metasphere-context` (hook output, all sections)
- [ ] `metasphere-events` (log/list/filter)
- [ ] `metasphere-agent` (activity, identity)
- [ ] `metasphere-fts` (CAM search)
- [ ] `metasphere-heartbeat`
- [ ] `metasphere-identity`
- [ ] `metasphere-migrate`
- [ ] `metasphere-posthook` (Stop hook → telegram routing)
- [ ] `metasphere-project`
- [ ] `metasphere-schedule` (cron port)
- [ ] `metasphere-session`
- [ ] `metasphere-telegram` (send)
- [ ] `metasphere-telegram-groups`
- [ ] `metasphere-telegram-stream`
- [ ] `metasphere-tmux-submit`
- [ ] `metasphere-trace`
- [ ] `metasphere-git-hooks`
- [ ] `metasphere-gateway`
