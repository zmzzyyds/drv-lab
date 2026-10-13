"""Tests for the guest-report to exit-code translation."""

from __future__ import annotations

import pytest

from drvlab import errors
from drvlab.result import (
    STAGE_BOOTED,
    STAGE_HARNESS_ERROR,
    STAGE_NO_BIND,
    STAGE_TEST_DONE,
    GuestResult,
    exit_code_for,
    parse_sentinel,
)


def test_parses_a_minimal_sentinel():
    result = parse_sentinel("stage=booted\nmessage=\n")
    assert result == GuestResult(stage=STAGE_BOOTED, message="")


def test_parses_the_test_exit_status():
    result = parse_sentinel("stage=test-done\ntest-exit=3\n")
    assert result.test_exit == 3


def test_parses_a_message_with_equals_signs_in_it():
    result = parse_sentinel("stage=harness-error\nmessage=could not mount /dev/vda\n")
    assert result.message == "could not mount /dev/vda"


def test_ignores_unknown_keys():
    # So the guest can start reporting more without breaking an older host.
    result = parse_sentinel("stage=booted\nsomething=new\n")
    assert result.stage == STAGE_BOOTED


def test_ignores_lines_without_a_separator():
    result = parse_sentinel("noise\nstage=booted\n")
    assert result.stage == STAGE_BOOTED


def test_a_sentinel_without_a_stage_is_an_error():
    with pytest.raises(ValueError):
        parse_sentinel("message=hello\n")


def test_a_garbled_exit_status_is_an_error():
    with pytest.raises(ValueError):
        parse_sentinel("stage=test-done\ntest-exit=not-a-number\n")


@pytest.mark.parametrize(
    ("sentinel", "expected"),
    [
        (None, errors.EXIT_HARNESS),
        (f"stage={STAGE_BOOTED}\n", errors.EXIT_OK),
        (f"stage={STAGE_HARNESS_ERROR}\n", errors.EXIT_HARNESS),
        (f"stage={STAGE_NO_BIND}\n", errors.EXIT_NO_BIND),
        (f"stage={STAGE_TEST_DONE}\ntest-exit=0\n", errors.EXIT_OK),
        (f"stage={STAGE_TEST_DONE}\ntest-exit=1\n", errors.EXIT_TEST_FAILED),
        (f"stage={STAGE_TEST_DONE}\ntest-exit=139\n", errors.EXIT_TEST_FAILED),
        (f"stage={STAGE_TEST_DONE}\n", errors.EXIT_HARNESS),
        ("stage=something-new\n", errors.EXIT_HARNESS),
    ],
)
def test_stage_maps_to_an_exit_code(sentinel, expected):
    result = parse_sentinel(sentinel) if sentinel is not None else None
    assert exit_code_for(result, timed_out=False) == expected


def test_timeout_beats_a_reported_success():
    # A guest that said "fine" and then failed to shut down has not finished.
    result = parse_sentinel("stage=test-done\ntest-exit=0\n")
    assert exit_code_for(result, timed_out=True) == errors.EXIT_TIMEOUT


def test_timeout_beats_a_missing_sentinel():
    assert exit_code_for(None, timed_out=True) == errors.EXIT_TIMEOUT


def test_a_stage_we_do_not_recognise_is_not_success():
    # Guard against a stage name silently defaulting to success.
    assert exit_code_for(GuestResult(stage=""), timed_out=False) == errors.EXIT_HARNESS
