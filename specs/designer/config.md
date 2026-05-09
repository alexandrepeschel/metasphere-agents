---
name: designer
role: designer
description: UX verdicts on CLI, help text, AGENTS.md templates, and docs surfaces
sandbox: scoped
persistent: true
---

## Triggers

- On `message.task` with label `design-review`: review the named surface for UX clarity and consistency
- On `team.invoke` with action `review-ux`: produce a UX verdict on the current diff
