# templates/

Boilerplate copied into a freshly-spawned agent or installed into
`~/.metasphere/` on first install. Three sibling surfaces, each
serving a different stage of the lifecycle.

## `agent-harness.md`

Render template for **ephemeral one-shot agents** spawned via
`metasphere agent spawn` (the headless `claude -p` path). Contains:

- The agent's role and scope as the first thing it reads
- The same operational primer as the project `CLAUDE.md` (messages
  CLI, tasks CLI, completion protocol, message labels, task
  priorities)
- A pointer back to the parent agent
- The "Use the harness, evolve the harness" reminder

If you change how spawned ephemerals bootstrap (e.g. add a new
mandatory step at startup), edit this file. Existing agents won't
be re-templated, but every new spawn picks it up.

## `agents/<role>/`

Per-**role** runtime guidelines copied into a **persistent agent**'s
home (`~/.metasphere/agents/<id>/AGENTS.md`) when `metasphere agent
seed --spec <name>` runs. The seeder reads the spec's `role:` field
(from `specs/<name>/config.md`) and copies the matching
`templates/agents/<role>/AGENTS.md` — see
`metasphere/specs.py::_find_agents_md_template`. Current set:

- `critic/` — review-gate agents (seeded by spec `reviewer`)
- `designer/` — UX / visual-direction agents (seeded by spec `designer`)
- `eng/` — implementation agents (seeded by spec `implementer`)
- `explorer/` — autonomous exploration agents (seeded by spec `monitor`)
- `lead/` — team-lead agents (seeded by spec `planner`)
- `researcher/` — research / investigation agents (seeded by spec `researcher`)
- `orchestrator/` — orchestrator personae (seeded by `install.sh`, no spec)

`metasphere update`'s drift-check resolves an agent's role from its
config sidecar and matches against `templates/agents/<role>/` (see
`metasphere/update.py::_agent_role`).

Adding a new spec:
1. Create `specs/<name>/` with `SOUL.md`, `MISSION.md`, and
   `config.md` (frontmatter declares `name:`, `role:`, `sandbox:`).
   `list_specs()` discovers it via directory scan — no Python edit.
2. If the spec's `role:` is new, create
   `templates/agents/<role>/AGENTS.md` for the runtime guidelines.
   Existing roles reuse the shared `AGENTS.md` so behavior can
   evolve in one place across all specs sharing the role.

## `install/`

Installed into `~/.metasphere/` once at `install.sh` first-run.
Files here ship to every fresh host. Current contents:

- `CLAUDE.md` — the user-facing manual that lands in
  `~/.metasphere/CLAUDE.md` (harness etiquette, agent-runtime
  surface).
- `ADDRESSBOOK.yaml.template` — bootstrap stub for the local
  agent/user/project address book.
- `projects/CLAUDE.md.template` + `projects/USER.md.template` —
  rendered into each new project's home by
  `metasphere project init` (see `metasphere/specs.py:200` and
  `metasphere/project.py`).

Edits here only affect new installs (or new projects, for the
`projects/` subtree). Existing hosts keep whatever lives at
`~/.metasphere/CLAUDE.md` already; re-installs do not clobber
operator edits.
