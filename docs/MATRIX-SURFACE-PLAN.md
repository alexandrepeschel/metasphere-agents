# Build brief: Matrix surface (17 Nov 2026)

Goal: Alex talks to @orchestrator from Element (Matrix) instead of Telegram. Telegram keeps working as a fallback until Matrix has run cleanly for a week.

## Why
Telegram strips formatting Alex pastes (numbered lists) and its topics make clutter worse. Matrix is open, has proper markdown, replies, threads, voice messages and a first-class bot API.

## What exists
- Surface-agnostic routing: `docs/multi-surface-routing.md`. Every inbound is `(surface_id, chat_id)`; adapters implement `SurfaceAdapter` in `metasphere/gateway/adapter.py`.
- Two adapters to copy from: `metasphere/gateway/adapters/telegram.py` (+ `metasphere/telegram/`: api, poller, handler, routing, inject, attachments, commands) and `metasphere/gateway/adapters/slack.py` (+ `metasphere/slack/`, ~1,100 lines). Slack is the smaller template; Telegram has the features to match.
- Tests: `metasphere/tests/test_slack_*.py`, `test_gateway_adapter.py`.

## Build
1. `metasphere/matrix/` with `api.py` (client-server API over plain HTTPS: login, `/sync` long-poll with `since` token persisted, `m.room.message` send with `format: org.matrix.custom.html` + markdown body, media upload for documents and voice `m.audio`), `poller.py`, `handler.py`, `routing.py`. Prefer `requests` over adding matrix-nio unless E2EE is needed (it is not: the bot's DM room with Alex is created unencrypted).
2. `metasphere/gateway/adapters/matrix.py` registered with surface_id `matrix`.
3. Inbound parity with Telegram: text, reply-to quoting (`m.relates_to.m.in_reply_to` -> the same `↩ replying to` line Telegram inject produces), reactions (`m.reaction` -> same reaction line), voice/audio and file attachments downloaded like `metasphere/telegram/attachments.py`.
4. Outbound: `metasphere message send --surface matrix --chat-id <room_id>`; `--surface auto` already resolves to the surface Alex last used. Add a `metasphere matrix send` CLI mirroring `metasphere telegram send` incl. `--voice` and documents.
5. End-of-turn text forwarding (the posthook that forwards the orchestrator's final text to Telegram) must follow `--surface auto`.
6. Rewire prompts: 58 job prompts/scripts under `~/.metasphere/projects`, scheduler jobs and `~/writers-room/clay` hardcode `--surface telegram --chat-id 11714502`, `tg-html` or `metasphere-telegram send`. Replace with `--surface auto` (or a `metasphere message send --to alex` alias) so they follow Alex. Do not break `tg-html` callers: make it surface-aware or keep it as a Telegram-only path.

## Secrets and config
- Bot account: `~/.metasphere/secrets/matrix-clay.json` (keys: account, homeserver, login, password, user_id). Log in once, store the access token beside it (`matrix-clay.token`, mode 600), never print the password.
- Alex's Matrix ID: ask Alex (pending). The bot creates a DM room, invites him, stores the room_id in config.

## Done means
- Unit tests for api/poller/handler/routing pass, existing suites still pass.
- Live smoke test: Alex sends a numbered list and a voice note from Element; both arrive in the orchestrator intact; the reply arrives in Element with the list rendered.
- Gateway restart survives (sync token persisted, no replay of old messages).
- Commit to this fork (`alex-fork`), never to Julian's upstream. Restart daemons after (`metasphere daemon restart`).
