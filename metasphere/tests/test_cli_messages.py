"""Tests for ``metasphere msg send`` CLI shim (cli/messages.py).

Focused on the flag-shaped-positional reject guard added 2026-05-05
after @rage-changelog (msg-1777966286-551715) and @explorer
(msg-1777975636) both shipped malformed messages by confabulating
``--to`` / ``--body`` flags. The shim is purely positional —
``send <target> <label> <body...>`` — so a literal ``--to`` was
silently accepted as the target, the real target became the label,
and the body landed with the leading ``--body`` token still in it.
The guard hard-fails on either positional starting with ``--`` so
the corruption can't ship.
"""

from __future__ import annotations

from unittest import mock

import pytest

from metasphere.cli import messages as cli_msgs


def test_send_rejects_flag_shaped_target(capsys, tmp_paths):
    """First positional starting with ``--`` is rejected. Reproduces
    the @rage-changelog / @explorer shape exactly: agent invokes
    ``send --to @orchestrator --body "..."`` thinking those are flags;
    without the guard, the unpack writes ``to: --to`` to disk."""
    rc = cli_msgs._cmd_send(["--to", "@orchestrator", "--body", "some text"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert "--to" in err
    assert "looks like a flag" in err
    assert "msg send @target !label" in err.lower() or (
        "messages send @target !label" in err
    )


def test_send_rejects_flag_shaped_label(capsys, tmp_paths):
    """Second positional starting with ``--`` (target was real but the
    LABEL got a flag-shape) is also rejected. Defensive symmetry: the
    same confabulation could land in either slot depending on how the
    agent shuffled args."""
    rc = cli_msgs._cmd_send(["@orchestrator", "--body", "some text"])
    assert rc == 1
    _, err = capsys.readouterr()
    assert "--body" in err
    assert "looks like a flag" in err


def test_send_correct_positional_shape_succeeds(capsys, tmp_paths):
    """Sanity: the canonical positional shape still works — target and
    label are real, body is non-empty. Guard must not regress the
    legitimate path."""
    sent_calls: list[tuple] = []

    fake_msg = mock.MagicMock()
    fake_msg.id = "msg-test-12345"
    fake_msg.scope = "/"

    def fake_send_message(target, label, body, agent, paths=None):
        sent_calls.append((target, label, body, agent))
        return fake_msg

    with mock.patch.object(cli_msgs._msgs, "send_message", side_effect=fake_send_message):
        rc = cli_msgs._cmd_send(["@orchestrator", "!info", "all good", "more", "words"])

    assert rc == 0
    assert len(sent_calls) == 1
    target, label, body, _agent = sent_calls[0]
    assert target == "@orchestrator"
    assert label == "!info"
    assert body == "all good more words"


def test_send_body_containing_double_dash_mid_text_succeeds(capsys, tmp_paths):
    """A body argument with ``--`` mid-text (legitimate prose / em-dash
    rendering / cli-example quoting) must not be rejected. The guard
    only inspects target and label; body tokens are body content."""
    sent_calls: list[tuple] = []
    fake_msg = mock.MagicMock()
    fake_msg.id = "msg-test-67890"
    fake_msg.scope = "/"

    def fake_send_message(target, label, body, agent, paths=None):
        sent_calls.append((target, label, body, agent))
        return fake_msg

    with mock.patch.object(cli_msgs._msgs, "send_message", side_effect=fake_send_message):
        rc = cli_msgs._cmd_send([
            "@orchestrator", "!info",
            "ran", "git", "log", "--oneline", "--since=yesterday",
            "—", "5", "commits",
        ])

    assert rc == 0
    assert sent_calls[0][2] == "ran git log --oneline --since=yesterday — 5 commits"
