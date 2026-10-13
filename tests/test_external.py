"""Tests for the process seam."""

from __future__ import annotations

import sys

import pytest

from drvlab import external

PYTHON = sys.executable


def test_run_captures_output():
    process = external.run([PYTHON, "-c", "print('hello')"])
    assert process.returncode == 0
    assert process.stdout.strip() == "hello"


def test_run_returns_the_exit_status_without_raising():
    process = external.run([PYTHON, "-c", "raise SystemExit(3)"])
    assert process.returncode == 3


def test_run_checked_raises_with_the_argv_in_the_message():
    with pytest.raises(external.CommandError) as caught:
        external.run_checked([PYTHON, "-c", "print('boom'); raise SystemExit(2)"])
    assert caught.value.returncode == 2
    assert "boom" in str(caught.value)


def test_run_writes_to_a_log_instead_of_capturing(tmp_path):
    log = tmp_path / "nested" / "out.log"
    process = external.run([PYTHON, "-c", "print('logged')"], log_path=log)
    assert process.returncode == 0
    assert process.stdout is None  # went to the file
    assert log.read_text().strip() == "logged"


def test_failure_output_comes_from_the_log_file(tmp_path):
    log = tmp_path / "out.log"
    with pytest.raises(external.CommandError) as caught:
        external.run_checked(
            [PYTHON, "-c", "import sys; print('real error here'); sys.exit(1)"],
            log_path=log,
        )
    assert "real error here" in str(caught.value)


def test_run_filter_pipes_stdin_to_stdout():
    script = "import sys; sys.stdout.write(sys.stdin.read().upper())"
    assert external.run_filter([PYTHON, "-c", script], "abc") == b"ABC"


def test_run_filter_raises_on_failure():
    with pytest.raises(external.CommandError):
        external.run_filter([PYTHON, "-c", "import sys; sys.exit(4)"], "")


def test_empty_argv_is_rejected():
    with pytest.raises(ValueError):
        external.run([])
    with pytest.raises(ValueError):
        external.run_filter([], "")


def test_cwd_is_honoured(tmp_path):
    process = external.run([PYTHON, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
    assert process.stdout.strip().endswith(tmp_path.name)
