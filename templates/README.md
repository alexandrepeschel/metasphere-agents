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

## `agents/<type>/`

Per-agent-type templates installed into a **persistent agent**'s
home (`~/.metasphere/agents/<id>/`) when `metasphere agent seed
--spec <type>` runs. One subdirectory per spec — current set:

- `critic/` — review-gate agents
- `designer/` — UX / visual-direction agents
- `eng/` — implementation agents
- `explorer/` — autonomous exploration agents
- `lead/` — team-lead agents
- `orchestrator/` — orchestrator personae
- `researcher/` — research / investigation agents

Each contains the role's `AGENTS.md` (runtime rules / standing
behavior). Matched on spec at update time by
`metasphere update`'s drift-check (see `metasphere/update.py`).

Adding a new spec: create `templates/agents/<type>/AGENTS.md`, then
list the spec in `metasphere/specs.py` so `metasphere agent specs`
+ `metasphere agent seed --spec <type>` resolve it.

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
