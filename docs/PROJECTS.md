# Projects + persistent agent groups — design

> Status: shipped 2026-04-08; this document is preserved for design rationale.
>
> The per-project memory section below is current-as-shipped; the
> remainder is the original design proposal kept for context. For
> the operator-level walkthrough see `~/.metasphere/CLAUDE.md`
> (installed from `templates/install/CLAUDE.md`).

## Per-project memory

Every project carries two team-shared memory files alongside its
metadata:

| File | Holds |
|---|---|
| `~/.metasphere/projects/<name>/LEARNINGS.md` | What the team learned: incidents, lessons, debugging insights. |
| `~/.metasphere/projects/<name>/MEMORY.md` | What the team knows: facts, configs, references, ongoing state. |

These are the **primary memory store** for everyone on the team. The
per-turn context hook renders a recency-sorted window of both files
into each member's context block; the window's footer cites the
absolute on-disk path so an agent can `Read` or `grep` the full file
when uncertain.

### Capsule shape

The context hook (`metasphere/context.py::_render_project_capsule`)
emits, per resolved project:

```
## Project: <name>

### LEARNINGS

<newest entries by date desc, within byte budget>

_(N more entries omitted by recency. Full file: <absolute path> — Read or grep for older.)_

### MEMORY

<newest entries by date desc, within byte budget>

_(Full file: <absolute path> — Read or grep for older entries.)_
```

Recency sort anchors on a `YYYY-MM-DD` token in each entry's
`##` / `###` header; entries without a parseable date sort after
all dated entries in their original file order. Multi-project
agents get one section per project, split 60/40 LEARNINGS/MEMORY
within each project's slice of the section budget.

### Resolution: which projects an agent receives

Precedence, highest first:

1. **MISSION.md frontmatter** — `project: <name>` (scalar) or
   `projects: [a, b]` (list). Explicit override; supports
   multi-project.
2. **`~/.metasphere/teams.yaml`** — central agent→projects roster.
   Schema and operator notes live at the top of the shipped file
   (`templates/install/teams.yaml`).
3. **Path-nested location** — agents living under
   `~/.metasphere/projects/<P>/agents/@<id>/` resolve to `<P>`.
4. No capsule otherwise.

Name-prefix string matching (`@<project>-<role>` → `<project>` via
dash-split) was tried briefly and removed: it failed on agents
whose project name itself contains a dash
(e.g. `@polymarket-agents-research` first-dash-splits to
`polymarket`, which doesn't match project `polymarket-agents`).
`teams.yaml` is the canonical replacement.

### Runtime discipline

Per-role `AGENTS.md` templates ship with a "Project memory store"
section telling agents how to read the capsule and when to consult
the underlying files directly:

- Reach for `MEMORY.md` for facts/configs/state.
- Reach for `LEARNINGS.md` for lessons/incidents.
- Don't reflex-grep on every project query (dilutes reasoning) —
  consult the file when the answer matters AND isn't in the
  agent's own memory AND isn't in the capsule.

The auto-memory layer at `~/.claude/projects/...` is
cross-conversation residue, secondary to the project files.

### Updating an agent's project membership

Operators have three knobs:

- Edit `MISSION.md` frontmatter for per-agent overrides
  (multi-project, custom project mapping).
- Edit `~/.metasphere/teams.yaml` for the default roster. Takes
  effect on the next per-turn tick (no restart).
- Move an agent's home dir to or from
  `~/.metasphere/projects/<P>/agents/` to flip the path-nested
  fallback. Rarely needed; teams.yaml is the cleaner path.

After updating a per-role `AGENTS.md` template, existing live
agents need a re-seed to pick up the new content:

```bash
metasphere agent seed --spec <spec-name> --force
```

`--force` is required to overwrite the existing per-agent file.

## What exists today

| Piece | State |
|---|---|
| `metasphere/project.py` | `init_project(name, path)` creates `.metasphere/project.json`, `.tasks/{active,completed}/`, `.messages/{inbox,outbox}/`, `.changelog/`, `.learnings/`, `shared/`. Registers in `~/.metasphere/projects.json`. |
| `metasphere project init [path]` / `list` / `changelog` / `learnings` | Working CLI subcommands. |
| `metasphere agent spawn` / `wake` | Spawns ephemeral agents (`claude -p`) and wakes persistent agents (tmux+REPL respawn loop). Persistence is detected by `MISSION.md` presence under `~/.metasphere/agents/@name/`. |
| `metasphere agent list` / `status` | Filters to persistent agents only. |
| Per-turn context injection | `metasphere hooks context` walks `.messages/` and `.tasks/` upward through scope. |

**Gaps the operator called out:**

1. No `members` field on a project — no way to bind a persistent agent group to a project.
2. No `goal` or `repo` fields on a project.
3. No interactive setup flow — `init` takes a path and that's it.
4. No `/project new` slash command.
5. The orchestrator has no way to surface "we are working in project X" in its per-turn context.

## Proposed schema (project.json v2)

```json
{
  "schema": 2,
  "name": "metasphere",
  "path": "<repo-root>",
  "created": "2026-04-07T22:00:00Z",
  "status": "active",
  "goal": "Self-improving multi-agent harness, dogfooding the harness on itself.",
  "repo": {
    "url": "git@github.com:youruser/metasphere-agents.git",
    "default_branch": "main",
    "managed_by_metasphere": true
  },
  "members": [
    {"id": "@orchestrator", "role": "lead",      "persistent": true},
    {"id": "@reviewer-quality", "role": "reviewer", "persistent": true},
    {"id": "@cli-unify",  "role": "developer", "persistent": false}
  ],
  "links": {
    "github_issues": "https://github.com/youruser/metasphere-agents/issues",
    "linear_team": null
  }
}
```

`schema: 1` (the current one-line `{name, path, created, status}`) loads under a compat shim — `members`, `goal`, `repo` default to empty/null and old projects keep working unchanged.

## Proposed Python API additions (`metasphere/project.py`)

```python
def init_project(
    name: Optional[str] = None,
    path: Optional[Path] = None,
    *,
    goal: Optional[str] = None,
    repo: Optional[str] = None,        # git URL; if set and path doesn't exist, clone it
    members: Optional[list[dict]] = None,
    paths: Optional[Paths] = None,
) -> Project: ...

def add_member(name_or_path: str | Path, agent_id: str, *,
               role: str = "contributor", persistent: bool = False,
               paths: Optional[Paths] = None) -> Project: ...

def remove_member(name_or_path: str | Path, agent_id: str, *,
                  paths: Optional[Paths] = None) -> Project: ...

def project_for_scope(scope: Path, paths: Optional[Paths] = None) -> Optional[Project]:
    """Walk upward from `scope` looking for `.metasphere/project.json`.
    Returns the nearest enclosing project, or None."""
    ...

def wake_members(name_or_path: str | Path, *,
                 paths: Optional[Paths] = None) -> list[str]:
    """Wake every persistent member of the project. Returns the list of
    agent ids that came up (skips already-alive ones)."""
    ...
```

`init_project` is the only signature change to existing API. New kwargs all default to None / empty so existing call sites keep working.

## Proposed CLI surface (`metasphere project ...`)

```
metasphere project new <name> [--path P] [--goal "..."] [--repo URL] [--member @x:role] ...
metasphere project init [path]                  # legacy, kept
metasphere project list                         # existing
metasphere project show [name]                  # NEW: full metadata + members + recent activity
metasphere project member add @agent --role lead [--persistent]
metasphere project member remove @agent
metasphere project members                      # list members of current/named project
metasphere project wake [name]                  # wake all persistent members
metasphere project changelog [name] [--since]   # existing
metasphere project learnings [name]             # existing
metasphere project for [path]                   # NEW: print enclosing project name (or empty), useful for context hook
```

Two-level subparser. `new` is the new richer constructor, `init` stays as the minimal one for scripts and tests.

## `/project` slash command

Lives at `.claude/commands/project.md` (claude-code reads commands from there). The slash command is just a prompt template — the wizard intelligence is the orchestrator's natural-language reasoning. Sketch:

```markdown
---
name: project
description: Create or work with a metasphere project
---

You have been invoked via the /project slash command. The user has typed:
$ARGUMENTS

If $ARGUMENTS is "new" (or empty), walk the user through setting up a
new project. Ask, in order:

1. Project name (required, kebab-case suggested)
2. Path on disk — default $PWD/<name>, but allow override. If they
   provide a git URL instead, treat it as `--repo` and clone into
   `<chosen-path>/<name>` (or wherever they specify).
3. Project goal (one sentence — what is this project trying to achieve)
4. Members — which persistent agents should belong to this project?
   Default: just @orchestrator. Offer to also add common roles
   (@reviewer, @researcher) that you can spawn now or later.
5. Optional: Linear team, GitHub issues URL, anything else they
   mention.

Then issue these commands in order, showing each one to the user
before running it:

  metasphere project new <name> --path <path> --goal "<goal>" [--repo <url>] [--member @x:role ...]
  metasphere project member add @agent --role <role> --persistent  # for each member
  metasphere project wake <name>                                    # bring up persistent members

Confirm completion: print the project show output and tell the user
how to enter the project scope (`cd <path>` or `metasphere ls <name>`).

If $ARGUMENTS is "list" or "show <name>", just run the corresponding
metasphere project subcommand and format the output for the user.

If $ARGUMENTS is "wake <name>", run `metasphere project wake <name>`
and report which agents came up.

If $ARGUMENTS is anything else, show this slash command's help and
the available metasphere project subcommands.
```

This is the right shape because (a) the wizard is conversational, not a series of `argparse` prompts, (b) the orchestrator can interpret partial input ("just call it `example-edge` and use the existing `<chosen-path>/example` repo, members are me and @researcher"), and (c) the slash command is dead simple — it's a prompt, not code.

## Per-turn context injection

Extend `metasphere/context.py` so the per-turn block includes a project header when the current scope is inside a project:

```
# Project: <name>
Goal: <one-sentence goal>
Members: @orchestrator (lead, alive), @reviewer-quality (reviewer, dormant)
Recent: <N tasks active, last commit <subject>>
```

Walks upward from `METASPHERE_SCOPE` looking for `.metasphere/project.json`, deserializes, and renders the header. Cheap; no new state.

This gives every agent that operates inside a project (orchestrator AND any spawned children whose scope is inside the project tree) implicit knowledge of which project they're in and who their teammates are.

## Design rationale (resolved as)

1. **Member roles**: should the role be free-form text, or should we have a small enum (`lead | developer | reviewer | researcher | contributor`)? Free-form is easier; an enum lets us key behaviors off it later (e.g. only `lead` can close milestones).
2. **Cross-project membership**: can `@reviewer-quality` be a persistent member of multiple projects at once? I assume yes (one tmux session, multiple project bindings) — confirm.
3. **Repo cloning**: when `--repo` is given and `--path` doesn't exist, do we `git clone` automatically, or just record the URL and let the user clone manually? I'd default to auto-clone with a confirmation prompt in the wizard.
4. **Project deletion / archive**: do we need `metasphere project archive <name>` for finished projects? Or is `status: archived` enough? I'd add the field but defer the CLI verb until we actually have an archived project.
5. **Linkage to MISSION.md**: when a project is created and a persistent member is added, should we auto-write a MISSION.md for that agent if one doesn't exist? Probably yes — it's the only way `metasphere agent wake` knows the agent is persistent. The wizard can pre-generate one with the project goal as a starting point.
6. **Daemon-level keepalive**: should there be a `metasphere project daemon` that keeps all persistent members of all active projects alive (re-waking dead tmux sessions)? Or do we lean on `metasphere agent wake` being idempotent and let humans/heartbeats trigger it? Defer until we have a project where it matters.

## Implementation tranche plan (when greenlit)

1. **Schema bump + compat shim** (1 commit): extend `project.json` to v2, add load/save with v1 fallback, add `goal` / `repo` / `members` / `links` fields. Tests: round-trip both schemas.
2. **Member API + CLI** (1 commit): `add_member` / `remove_member` / `wake_members` + `metasphere project member ...` subcommands. Tests: add, remove, wake (mocked tmux).
3. **`project new` constructor** (1 commit): the richer signature with `--goal --repo --member`. Optional auto-clone if `--repo` given and path doesn't exist. Tests: new with all flags, new without repo, new with repo + auto-clone (mocked git).
4. **`project_for_scope` + context injection** (1 commit): walk-up resolver, context.py header. Tests: nested project, no project, project at root.
5. **`/project` slash command** (1 commit): write `.claude/commands/project.md` with the wizard prompt above. No tests (it's a prompt).
6. **`metasphere project show`** (1 commit): pretty-print project metadata + members + recent activity. Tests: show by name, show by inferred-from-cwd.

6 commits, ~+400 LoC, ~+15 tests. Time budget: aggressive — most of this is glue around existing primitives.

## Things I am explicitly NOT proposing

- A new daemon. Keepalive is fine via the existing heartbeat + manual wake.
- A new storage format. Stick with `project.json` + `~/.metasphere/projects.json` registry. No SQLite, no key-value store.
- A web UI. Future possibility, not in scope.
- Auto-spawning members on every `metasphere project new`. Members are *registered* at create time; the user explicitly opts in to `wake` them. No surprise tmux sessions.
- Coupling project membership to message routing. Messages still flow through the fractal scope tree. Membership is metadata, not topology.
