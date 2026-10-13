"""Tests for the driver and test-client build commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from drvlab.driver import build as driver_build
from drvlab.driver.build import module_build_argv, static_test_argv


def test_module_is_built_against_the_given_kernel_tree():
    argv = module_build_argv("/cache/build/linux-6.12-arm64", "/work/demo_misc")
    assert argv[0] == "make"
    assert argv[1] == "-C"
    assert argv[2] == "/cache/build/linux-6.12-arm64"
    assert argv[-1] == "modules"


def test_module_build_points_kbuild_at_the_driver_directory():
    argv = module_build_argv("/kdir", "/work/demo_misc")
    assert "M=/work/demo_misc" in argv


def test_module_build_is_cross_compiled():
    argv = module_build_argv("/kdir", "/drv")
    assert "ARCH=arm64" in argv
    assert "CROSS_COMPILE=aarch64-linux-gnu-" in argv


def test_module_build_does_not_override_the_compiler():
    # kbuild derives CC from CROSS_COMPILE. Passing one here would be a second
    # place for the toolchain to drift out of sync with the kernel build.
    argv = module_build_argv("/kdir", "/drv")
    assert not any(part.startswith("CC=") for part in argv)


def test_module_build_accepts_paths_and_strings():
    argv = module_build_argv(Path("/kdir"), Path("/drv"))
    assert all(isinstance(part, str) for part in argv)


def test_test_client_is_linked_statically():
    # A busybox initramfs has no glibc; a dynamic binary fails with "not found"
    # and reads like a driver bug.
    argv = static_test_argv("tools/test.c", "tools/test")
    assert "-static" in argv


def test_test_client_uses_the_cross_compiler_and_keeps_warnings_on():
    argv = static_test_argv("tools/test.c", "tools/test")
    assert argv[0] == "aarch64-linux-gnu-gcc"
    assert "-Wall" in argv
    assert "-Wextra" in argv


def test_test_client_output_and_input_are_positioned_correctly():
    argv = static_test_argv("tools/test.c", "tools/test")
    assert argv[argv.index("-o") + 1] == "tools/test"
    assert argv[-1] == "tools/test.c"


# --- finding out what the module is called ---------------------------------
#
# drv-lab has to know this because the platform device it publishes must carry
# the driver's own name: platform_match() falls back to comparing the device's
# name with the driver's, and nothing else will bind a driver whose only binding
# path is a device tree compatible string.

DRVFORGE_STYLE_MAKEFILE = """\
MODULE_NAME := demo_misc
obj-m := $(MODULE_NAME).o
"""

PLAIN_MAKEFILE = """\
obj-m := my_sensor.o
"""


def _driver(tmp_path: Path, makefile: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "Makefile").write_text(makefile)
    return tmp_path


def test_module_name_comes_from_the_module_name_variable(tmp_path):
    assert driver_build.module_name_of(_driver(tmp_path, DRVFORGE_STYLE_MAKEFILE)) == "demo_misc"


def test_module_name_falls_back_to_the_obj_m_line(tmp_path):
    assert driver_build.module_name_of(_driver(tmp_path, PLAIN_MAKEFILE)) == "my_sensor"


def test_module_name_accepts_other_make_assignments(tmp_path):
    assert driver_build.module_name_of(_driver(tmp_path, "MODULE_NAME ?= thing\n")) == "thing"


def test_a_driver_without_a_makefile_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        driver_build.module_name_of(tmp_path)


def test_a_makefile_that_says_nothing_useful_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        driver_build.module_name_of(_driver(tmp_path, "all:\n\techo hi\n"))


def test_module_path_follows_the_name(tmp_path):
    assert driver_build.module_path(tmp_path, "demo_misc") == tmp_path / "demo_misc.ko"


def test_test_client_source_is_where_drvforge_puts_it(tmp_path):
    assert driver_build.test_client_source(tmp_path) == tmp_path / "tools" / "test.c"


def test_the_injector_source_ships_with_the_package():
    source = driver_build.injector_source_path()
    assert source.exists(), f"{source} is missing from the package"
    text = source.read_text(encoding="utf-8")
    # It only works with the GPL-only symbol it calls.
    assert 'MODULE_LICENSE("GPL")' in text


# --- shared UAPI headers ---------------------------------------------------
#
# drvforge's cdev template ships a header under include/ that both the driver and
# its test client include. Without the path the test does not compile at all,
# which took a real run to notice.


def test_include_dirs_is_empty_without_an_include_directory(tmp_path):
    assert driver_build.test_include_dirs(tmp_path) == []


def test_include_dirs_finds_a_shared_header_directory(tmp_path):
    (tmp_path / "include").mkdir()
    assert driver_build.test_include_dirs(tmp_path) == [tmp_path / "include"]


def test_test_argv_adds_the_include_path():
    argv = static_test_argv("tools/test.c", "out", include_dirs=["/proj/include"])
    assert argv[argv.index("-I") + 1] == "/proj/include"


def test_test_argv_adds_one_include_path_per_directory():
    argv = static_test_argv("t.c", "o", include_dirs=["/a", "/b"])
    assert argv.count("-I") == 2


def test_test_argv_is_unchanged_without_include_paths():
    assert "-I" not in static_test_argv("t.c", "o")


def test_include_paths_do_not_disturb_compiler_flags():
    argv = static_test_argv("t.c", "o", include_dirs=["/a"])
    assert argv[:2] == ["aarch64-linux-gnu-gcc", "-static"]
    assert "-Wall" in argv
    assert argv[-1] == "t.c"
    assert argv[argv.index("-o") + 1] == "o"
