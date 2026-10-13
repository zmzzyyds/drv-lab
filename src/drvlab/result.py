"""Interpreting what the guest reported.

The guest cannot hand back an exit status: QEMU exits 0 whatever happened
inside, because by the time the machine is gone there is nothing left to carry
it. So the guest writes a small ``key=value`` record into the shared directory
and the host turns that into the process exit code. This module is the whole of
that translation, and it is pure: text in, code out.
"""

from __future__ import annotations

from dataclasses import dataclass

from drvlab.errors import (
    EXIT_HARNESS,
    EXIT_NO_BIND,
    EXIT_OK,
    EXIT_TEST_FAILED,
    EXIT_TIMEOUT,
)

#: The stages the guest's /init is allowed to report.
STAGE_BOOTED = "booted"
STAGE_NO_BIND = "no-bind"
STAGE_TEST_DONE = "test-done"
STAGE_HARNESS_ERROR = "harness-error"

#: Key the guest uses to report the userspace test's exit status.
KEY_TEST_EXIT = "test-exit"


@dataclass(frozen=True)
class GuestResult:
    """What the guest said happened."""

    stage: str
    message: str = ""
    test_exit: int | None = None


def parse_sentinel(text: str) -> GuestResult:
    """Parse the record the guest wrote.

    Unknown keys are ignored, so the guest can start reporting more without
    breaking an older host. A missing ``stage`` is an error: without it there is
    no way to tell "the test passed" from "the guest wrote something we do not
    understand".
    """
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            fields[key.strip()] = value.strip()

    stage = fields.get("stage")
    if not stage:
        raise ValueError("sentinel has no stage")

    raw_exit = fields.get(KEY_TEST_EXIT, "")
    test_exit = int(raw_exit) if raw_exit else None
    return GuestResult(stage=stage, message=fields.get("message", ""), test_exit=test_exit)


def exit_code_for(result: GuestResult | None, *, timed_out: bool) -> int:
    """Map what the guest reported to the process exit code.

    ``timed_out`` wins over anything the guest said. A guest that reported
    success and then failed to shut down has not finished, and reporting its
    earlier optimism would be a lie.
    """
    if timed_out:
        return EXIT_TIMEOUT
    if result is None:
        # Nothing was written: the guest never reached the share.
        return EXIT_HARNESS

    if result.stage == STAGE_BOOTED:
        return EXIT_OK
    if result.stage == STAGE_HARNESS_ERROR:
        return EXIT_HARNESS
    if result.stage == STAGE_NO_BIND:
        return EXIT_NO_BIND
    if result.stage == STAGE_TEST_DONE:
        if result.test_exit is None:
            # It claims the test ran but did not say what it returned.
            return EXIT_HARNESS
        return EXIT_OK if result.test_exit == 0 else EXIT_TEST_FAILED

    # An unknown stage is not something to guess about.
    return EXIT_HARNESS
