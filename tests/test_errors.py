"""The exit-code contract is public API, so it is pinned here."""

from __future__ import annotations

import pytest

from drvlab import errors

ALL_CODES = [
    errors.EXIT_OK,
    errors.EXIT_TEST_FAILED,
    errors.EXIT_HARNESS,
    errors.EXIT_BUILD,
    errors.EXIT_NO_BIND,
    errors.EXIT_TIMEOUT,
]

ALL_ERRORS = [
    errors.HarnessError,
    errors.BuildError,
    errors.NoBindError,
    errors.GuestTimeoutError,
    errors.TestFailedError,
]


def test_exit_codes_are_distinct():
    assert len(set(ALL_CODES)) == len(ALL_CODES)


def test_success_is_zero():
    assert errors.EXIT_OK == 0


def test_timeout_matches_coreutils():
    # `timeout(1)` uses 124; matching it keeps shell wrappers predictable.
    assert errors.EXIT_TIMEOUT == 124


@pytest.mark.parametrize(
    ("exc_type", "expected"),
    [
        (errors.HarnessError, errors.EXIT_HARNESS),
        (errors.BuildError, errors.EXIT_BUILD),
        (errors.NoBindError, errors.EXIT_NO_BIND),
        (errors.GuestTimeoutError, errors.EXIT_TIMEOUT),
        (errors.TestFailedError, errors.EXIT_TEST_FAILED),
    ],
)
def test_error_maps_to_its_exit_code(exc_type, expected):
    assert exc_type.exit_code == expected


@pytest.mark.parametrize("exc_type", ALL_ERRORS)
def test_every_error_derives_from_drvlab_error(exc_type):
    assert issubclass(exc_type, errors.DrvlabError)


def test_base_error_defaults_to_a_harness_failure():
    # An error we did not foresee must not masquerade as a passing driver.
    assert errors.DrvlabError.exit_code == errors.EXIT_HARNESS


def test_guest_timeout_does_not_shadow_the_builtin():
    assert errors.GuestTimeoutError is not TimeoutError
