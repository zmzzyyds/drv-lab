"""Tests for the command line's shape, not for what it does.

Everything the commands actually do touches a kernel, a compiler or a virtual
machine. What can be checked without any of those is that the arguments mean
what they say.
"""

from __future__ import annotations

import pytest

from drvlab import cli
from drvlab.qemu import runner


def _parse(argv):
    return cli.build_parser().parse_args(argv)


def test_run_takes_a_driver_directory():
    assert _parse(["run", "./my-driver"]).driver.name == "my-driver"


def test_run_defaults_to_the_lts_kernel():
    assert _parse(["run", "./d"]).kernel == cli.DEFAULT_KERNEL


def test_run_defaults_to_parallel_builds():
    # A single-job kernel build on a multicore machine is pure waiting.
    assert _parse(["run", "./d"]).jobs >= 1


def test_run_timeout_defaults_to_the_shared_constant():
    assert _parse(["run", "./d"]).timeout == runner.DEFAULT_TIMEOUT


def test_run_can_turn_ccache_off():
    assert _parse(["run", "./d"]).no_ccache is False
    assert _parse(["run", "./d", "--no-ccache"]).no_ccache is True


def test_run_kernel_version_can_be_chosen():
    assert _parse(["run", "./d", "--kernel", "6.6"]).kernel == "6.6"


def test_a_driver_is_required():
    with pytest.raises(SystemExit):
        _parse(["run"])


def test_no_subcommand_is_an_error():
    with pytest.raises(SystemExit):
        _parse([])


def test_kernel_build_is_its_own_command():
    args = _parse(["kernel", "build", "--kernel", "6.6"])
    assert args.kernel_command == "build"
    assert args.kernel == "6.6"


def test_cache_dir_needs_no_further_arguments():
    assert _parse(["cache", "dir"]).cache_command == "dir"


def test_explanations_cover_every_documented_exit_code():
    from drvlab import errors

    for code in (
        errors.EXIT_TEST_FAILED,
        errors.EXIT_HARNESS,
        errors.EXIT_NO_BIND,
        errors.EXIT_TIMEOUT,
    ):
        assert cli._explain(code) != f"exit {code}"


def test_an_unknown_exit_code_is_still_reported():
    assert "7" in cli._explain(7)


def test_a_directory_without_a_makefile_fails_before_anything_is_built(tmp_path):
    # The check has to come first: otherwise the user waits for a kernel build
    # before being told the path was wrong.
    code = cli.main(["run", str(tmp_path)])
    from drvlab import errors

    assert code == errors.EXIT_HARNESS
