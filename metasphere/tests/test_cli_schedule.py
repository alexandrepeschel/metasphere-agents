"""CLI-layer tests for ``metasphere schedule``.

Library-level coverage lives in ``test_schedule.py``; this file
exercises the argv-parsing surface — specifically the flag-shaped
job-ref rejection added after df6812e / 206c14b. Without these
guards, ``schedule enable --help`` fell through to
``set_enabled('--help', ...)`` and printed ``job not found: --help``
instead of usage.
"""

from __future__ import annotations

from unittest.mock import patch

from metasphere.cli import schedule as cli


def test_enable_help_prints_usage_no_lookup(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["enable", "--help"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "schedule" in out and "enable" in out


def test_disable_help_prints_usage_no_lookup(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["disable", "-h"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "disable" in out


def test_enable_flag_shaped_ref_rejected(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["enable", "--force"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "--force" in err
    assert "flag" in err.lower()


def test_disable_flag_shaped_ref_rejected(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["disable", "-x"])
    assert rc == 2
    m.assert_not_called()


def test_enable_missing_arg_prints_usage(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["enable"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "enable" in err


def test_enable_real_job_id_still_dispatches():
    with patch("metasphere.schedule.set_enabled", return_value=True) as m:
        rc = cli.main(["enable", "metasphere-auto-update"])
    assert rc == 0
    m.assert_called_once()
    args, kwargs = m.call_args
    assert args[0] == "metasphere-auto-update"
    assert args[1] is True


def test_top_level_help_unchanged(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "schedule" in out
