"""Credential failsafe: auto-rotate Anthropic OAuth profiles on rate-limit.

Probes the orchestrator's tmux pane for rate-limit signals before each
heartbeat injection.  If a signal is found and the cooldown period has
elapsed, rotates to the next available credential profile via the
``accounts`` module's helpers.

Public API
----------
``probe_and_rotate(session, paths) -> bool``
    Call this just before injecting a heartbeat.  Returns True if a
    rotation was performed.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Rate-limit detection patterns
# ---------------------------------------------------------------------------

# Strings that appear in the claude-code TUI when the account is rate-limited.
# Conservative set — prefer false-negatives over false-positives.
_RATE_LIMIT_PATTERNS: tuple[str, ...] = (
    # claude-code / anthropic API errors
    "rate_limit_error",
    "RateLimitError",
    "429 Too Many Requests",
    "Too Many Requests",
    # Claude.ai plan-level limits
    "usage limit",
    "Usage limit",
    "usage_limit_error",
    "reached your limit",
    "reached its limit",
    "Claude.ai usage limit",
    # Generic anthropic SDK messages
    "rate limited",
    "Rate limited",
)

# How many tail lines from the pane to inspect.
_PANE_TAIL_LINES = 60

# Minimum seconds between two consecutive rotations (per process).
# Prevents flip-flopping if the pane still shows a stale error.
_COOLDOWN_SECONDS = 600  # 10 minutes

# ---------------------------------------------------------------------------
# Cooldown state (in-process; resets on daemon restart)
# ---------------------------------------------------------------------------

_last_rotation_ts: float = float("-inf")  # -inf ensures first call always passes cooldown


def _cooldown_elapsed() -> bool:
    return (time.monotonic() - _last_rotation_ts) >= _COOLDOWN_SECONDS


def _mark_rotated() -> None:
    global _last_rotation_ts
    _last_rotation_ts = time.monotonic()


# ---------------------------------------------------------------------------
# Pane capture
# ---------------------------------------------------------------------------


def _capture_pane(session: str) -> str:
    """Return the visible pane text for *session*, or '' on failure."""
    tmux = "tmux"
    try:
        r = subprocess.run(
            [tmux, "capture-pane", "-p", "-t", session],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _pane_has_rate_limit(pane_text: str) -> bool:
    """Return True if any rate-limit pattern appears in *pane_text*."""
    tail = "\n".join(pane_text.splitlines()[-_PANE_TAIL_LINES:])
    return any(p in tail for p in _RATE_LIMIT_PATTERNS)


# ---------------------------------------------------------------------------
# Round-robin account selection
# ---------------------------------------------------------------------------


def _next_profile(accounts_dir: Path, live_cred: Path) -> Optional[str]:
    """Return the name of the next profile to rotate to, or None.

    Sorts profiles alphabetically, finds the current one, returns
    ``(current_index + 1) % len(profiles)``.  Returns None if fewer
    than 2 profiles exist (no rotation possible).
    """
    from .accounts import CRED_FILENAME

    if not accounts_dir.is_dir():
        return None
    profiles = sorted(
        child.name
        for child in accounts_dir.iterdir()
        if child.is_dir() and (child / CRED_FILENAME).is_file()
    )
    if len(profiles) < 2:
        return None  # nothing to rotate to

    # Identify current.
    current: Optional[str] = None
    if live_cred.is_symlink():
        try:
            target = live_cred.resolve(strict=False)
            rel = target.relative_to(accounts_dir.resolve(strict=False))
            parts = rel.parts
            if len(parts) == 2 and parts[1] == CRED_FILENAME:
                current = parts[0]
        except (ValueError, OSError):
            pass

    if current is None or current not in profiles:
        return profiles[0]  # unknown current → pick first

    idx = profiles.index(current)
    return profiles[(idx + 1) % len(profiles)]


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def _do_rotate(next_name: str, accounts_dir: Path, live_cred: Path) -> bool:
    """Perform the atomic symlink swap.  Returns True on success."""
    from .accounts import CRED_FILENAME, _atomic_symlink

    target = accounts_dir / next_name / CRED_FILENAME
    if not target.is_file():
        return False
    try:
        _atomic_symlink(target, live_cred)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def probe_and_rotate(
    session: str,
    paths,  # metasphere.paths.Paths — typed loosely to avoid circular import
) -> bool:
    """Probe *session* for a rate-limit signal; rotate credentials if found.

    Returns True if a rotation was performed, False otherwise.  Never
    raises — failures are logged and swallowed so the caller (heartbeat)
    stays alive.

    Skipped when:
    - ``sys.platform == "darwin"`` (credentials live in keychain there).
    - Cooldown has not elapsed since the last rotation.
    - Fewer than 2 profiles are configured.
    - No rate-limit pattern found in the pane.
    """
    if sys.platform == "darwin":
        return False
    if not _cooldown_elapsed():
        return False

    try:
        pane_text = _capture_pane(session)
        if not pane_text or not _pane_has_rate_limit(pane_text):
            return False

        from .accounts import ACCOUNTS_DIR, LIVE_CRED

        next_name = _next_profile(ACCOUNTS_DIR, LIVE_CRED)
        if next_name is None:
            return False

        if not _do_rotate(next_name, ACCOUNTS_DIR, LIVE_CRED):
            return False

        _mark_rotated()

        # Log + notify — both are best-effort.
        try:
            from ..events import log_event
            log_event(
                "failsafe.rotate",
                f"rate-limit detected in pane; rotated to {next_name}",
                agent="@orchestrator",
                paths=paths,
            )
        except Exception:
            pass

        try:
            from ..posthook import _resolve_chat_id
            from ..telegram import api as telegram_api
            chat_id = _resolve_chat_id(paths)
            if chat_id:
                telegram_api.send_message(
                    chat_id,
                    f"Rate limit detected — rotated to credentials profile '{next_name}'.",
                )
        except Exception:
            pass

        return True

    except Exception:
        return False
