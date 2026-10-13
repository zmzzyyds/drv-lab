"""Tests for kernel build argv and cache-key construction.

The ccache rule is asserted explicitly, because getting it wrong is silent: the
build still succeeds, only the cache stays empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from drvlab.kernel import build
from drvlab.kernel.build import (
    KernelSpec,
    build_kernel_argv,
    compiler_wrapper,
    kernel_cache_key,
    kernel_source_dirname,
    kernel_tarball_url,
    supports_virtconfig,
)

SPEC = KernelSpec("6.12")


def test_tarball_url_points_at_the_release_mirror():
    assert (
        kernel_tarball_url("6.12")
        == "https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.tar.xz"
    )


def test_tarball_url_uses_the_major_series_directory():
    assert "/v6.x/" in kernel_tarball_url("6.12.1")
    assert "/v5.x/" in kernel_tarball_url("5.15")


def test_source_dirname():
    assert kernel_source_dirname("6.12") == "linux-6.12"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("5.15", False),
        ("6.1", False),
        ("6.2", False),
        ("6.3", True),
        ("6.6", True),
        ("6.12", True),
        ("6.17", True),
    ],
)
def test_virtconfig_availability(version, expected):
    assert supports_virtconfig(version) is expected


@pytest.mark.parametrize(("version", "expected"), [("6.2.14", False), ("6.12.1", True)])
def test_virtconfig_handles_point_releases(version, expected):
    assert supports_virtconfig(version) is expected


def test_compiler_wrapper():
    assert compiler_wrapper(ccache=True) == "ccache aarch64-linux-gnu-gcc"
    assert compiler_wrapper(ccache=False) == "aarch64-linux-gnu-gcc"


def test_ccache_is_a_make_variable_and_not_split_on_the_space():
    # The kernel Makefile assigns CC = $(CROSS_COMPILE)gcc, and a makefile
    # assignment beats an exported variable, so this has to be argv.
    argv = build_kernel_argv(SPEC, ["Image"], jobs=2)
    assert "CC=ccache aarch64-linux-gnu-gcc" in argv
    assert "CC=ccache" not in argv


def test_ccache_can_be_turned_off():
    argv = build_kernel_argv(SPEC, ["Image"], jobs=2, ccache=False)
    assert not any(arg.startswith("CC=") for arg in argv)


def test_fixed_arguments_come_first_in_a_stable_order():
    argv = build_kernel_argv(SPEC, ["Image"], jobs=2)
    assert argv[:4] == ["make", "-j2", "ARCH=arm64", "CROSS_COMPILE=aarch64-linux-gnu-"]


def test_jobs_is_passed_through():
    assert "-j8" in build_kernel_argv(SPEC, ["Image"], jobs=8)


def test_targets_come_last():
    argv = build_kernel_argv(SPEC, ["Image", "modules"], jobs=2)
    assert argv[-2:] == ["Image", "modules"]


def test_argv_is_all_strings():
    assert all(isinstance(arg, str) for arg in build_kernel_argv(SPEC, ["Image"], jobs=2))


def _key(**overrides: str) -> str:
    base = {
        "version": "6.12",
        "arch": "arm64",
        "fragment": "CONFIG_NET_9P=y\n",
        "compiler_version": "aarch64-linux-gnu-gcc 11.4.0",
        "ccache_version": "4.5.1",
    }
    base.update(overrides)
    return kernel_cache_key(**base)


def test_cache_key_is_deterministic():
    assert _key() == _key()


def test_cache_key_is_a_hex_digest():
    key = _key()
    assert len(key) == 64
    int(key, 16)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "6.13"),
        ("arch", "x86_64"),
        ("fragment", "CONFIG_NET_9P=y\nCONFIG_IIO=y\n"),
        ("compiler_version", "aarch64-linux-gnu-gcc 12.3.0"),
        ("ccache_version", "4.6"),
    ],
)
def test_every_input_changes_the_key(field, value):
    # A stale key would silently test the wrong kernel.
    assert _key(**{field: value}) != _key()


def test_flipping_one_byte_of_the_fragment_changes_the_key():
    assert _key(fragment="CONFIG_NET_9P=y\n") != _key(fragment="CONFIG_NET_9P=n\n")


def test_fields_cannot_collide_across_the_boundary():
    # Without a separator between fields, ("ab", "c") and ("a", "bc") collide.
    assert _key(version="ab", arch="c") != _key(version="a", arch="bc")


# --- config fragment ------------------------------------------------------


def test_fragment_directives_reads_both_shapes():
    fragment = "CONFIG_A=y\n# CONFIG_B is not set\n"
    assert build.fragment_directives(fragment) == [("CONFIG_A", "y"), ("CONFIG_B", None)]


def test_fragment_directives_ignores_comments_and_blanks():
    fragment = "# a comment\n\nCONFIG_A=y\n"
    assert build.fragment_directives(fragment) == [("CONFIG_A", "y")]


def test_config_value_reads_a_set_symbol():
    assert build.config_value("CONFIG_A=y\nCONFIG_B=n\n", "CONFIG_B") == "n"


def test_config_value_is_none_for_an_unset_symbol():
    assert build.config_value("# CONFIG_A is not set\n", "CONFIG_A") is None
    assert build.config_value("CONFIG_OTHER=y\n", "CONFIG_A") is None


def test_apply_fragment_replaces_an_existing_value():
    assert build.apply_fragment("CONFIG_A=n\n", "CONFIG_A=y\n") == "CONFIG_A=y\n"


def test_apply_fragment_replaces_an_unset_line():
    assert build.apply_fragment("# CONFIG_A is not set\n", "CONFIG_A=y\n") == "CONFIG_A=y\n"


def test_apply_fragment_appends_a_missing_symbol():
    assert build.apply_fragment("CONFIG_OTHER=y\n", "CONFIG_A=y\n") == (
        "CONFIG_OTHER=y\nCONFIG_A=y\n"
    )


def test_apply_fragment_can_unset():
    assert build.apply_fragment("CONFIG_A=y\n", "# CONFIG_A is not set\n") == (
        "# CONFIG_A is not set\n"
    )


def test_apply_fragment_does_not_match_a_longer_name():
    text = "CONFIG_AB=y\nCONFIG_A=n\n"
    assert build.apply_fragment(text, "CONFIG_A=y\n") == "CONFIG_AB=y\nCONFIG_A=y\n"


def test_verify_fragment_is_clean_when_everything_took():
    assert build.verify_fragment("CONFIG_A=y\n", "CONFIG_A=y\n") == []


def test_verify_fragment_reports_a_value_that_did_not_take():
    # This is the DEBUG_INFO case: olddefconfig re-selects it from the choice.
    problems = build.verify_fragment("CONFIG_A=y\n", "# CONFIG_A is not set\n")
    assert len(problems) == 1
    assert "CONFIG_A" in problems[0]


def test_verify_fragment_reports_a_symbol_that_is_absent():
    problems = build.verify_fragment("CONFIG_OTHER=y\n", "CONFIG_A=y\n")
    assert len(problems) == 1


def test_round_trip_apply_then_verify_is_clean():
    fragment = build.FRAGMENT_PATH.read_text(encoding="utf-8")
    assert build.verify_fragment(build.apply_fragment("", fragment), fragment) == []


def test_shipped_fragment_reproduces_the_config_that_was_verified_by_hand():
    # These are the values measured in the tree the M1 runs used. If the shipped
    # fragment stops reproducing them, the cache key changes and every object in
    # the cache has to be compiled again -- worth being told about.
    hand_built = (
        "CONFIG_MODULES=y\n"
        "CONFIG_MODULE_UNLOAD=y\n"
        "CONFIG_DEVTMPFS=y\n"
        "CONFIG_DEVTMPFS_MOUNT=y\n"
        "CONFIG_PCI=y\n"
        "CONFIG_VIRTIO=y\n"
        "CONFIG_VIRTIO_PCI=y\n"
        "CONFIG_NET_9P=y\n"
        "CONFIG_9P_FS=y\n"
        "CONFIG_NET_9P_VIRTIO=y\n"
        "CONFIG_DEBUG_INFO_NONE=y\n"
        "# CONFIG_DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT is not set\n"
    )
    fragment = build.FRAGMENT_PATH.read_text(encoding="utf-8")
    assert build.verify_fragment(hand_built, fragment) == []


# --- cache layout and environment -----------------------------------------


def test_cache_defaults_under_xdg_cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    cache = build.Cache.default()
    assert cache.root == tmp_path / "drvlab"


def test_cache_paths_are_distinct():
    root = Path("/cache")
    cache = build.Cache(root=root)
    paths = [cache.tarballs, cache.sources, cache.ccache, cache.busybox]
    assert len(set(paths)) == len(paths)
    assert all(path.is_relative_to(root) for path in paths)


def test_run_dir_is_per_run():
    cache = build.Cache(root=Path("/cache"))
    assert cache.run_dir("a") != cache.run_dir("b")


def test_ccache_env_points_at_the_cache_and_ignores_the_build_directory():
    root = Path("/cache")
    env = build.ccache_env(build.Cache(root=root))
    assert env["CCACHE_DIR"] == str(root / "ccache")
    # Without this, moving the tree turns every lookup into a miss.
    assert env["CCACHE_NOHASHDIR"] == "1"


def test_artefact_paths():
    source = Path("/src")
    assert build.kernel_image(source) == source / "arch" / "arm64" / "boot" / "Image"
    assert build.module_symvers(source) == source / "Module.symvers"
