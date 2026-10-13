"""Error types and the process exit-code contract.

The exit codes are part of drv-lab's public interface: CI job definitions and
shell scripts branch on them, so they are named constants here and must never
be renumbered. ``drvlab run`` returning the right code is the whole product,
which is why this table has its own test file.
"""

from __future__ import annotations

#: Everything the driver asserted held.
EXIT_OK = 0

#: The driver's userspace test ran and reported failure. This is a real result,
#: not a harness problem: the lab worked, the driver did not.
EXIT_TEST_FAILED = 1

#: The harness itself failed — QEMU could not be started, the guest never
#: reached the shared directory, or the result sentinel never appeared.
EXIT_HARNESS = 2

#: The kernel or the module failed to build.
EXIT_BUILD = 3

#: The module loaded but never bound: the expected device node or sysfs
#: attribute never appeared. Usually a missing or mismatched device tree node.
EXIT_NO_BIND = 4

#: The guest did not finish within the timeout. Matches ``timeout(1)``.
EXIT_TIMEOUT = 124


class DrvlabError(Exception):
    """Base class for every error drv-lab reports to the user."""

    exit_code = EXIT_HARNESS


class HarnessError(DrvlabError):
    """The lab could not be brought up or driven."""

    exit_code = EXIT_HARNESS


class BuildError(DrvlabError):
    """The kernel or the driver module failed to build."""

    exit_code = EXIT_BUILD


class NoBindError(DrvlabError):
    """The module loaded but no device appeared for it to drive."""

    exit_code = EXIT_NO_BIND


class GuestTimeoutError(DrvlabError):
    """The guest did not finish in time.

    Named with the ``Guest`` prefix to avoid shadowing the builtin
    ``TimeoutError``, which means something entirely different.
    """

    exit_code = EXIT_TIMEOUT


class TestFailedError(DrvlabError):
    """The driver's userspace test reported failure."""

    exit_code = EXIT_TEST_FAILED
