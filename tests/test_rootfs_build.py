"""Tests for the initramfs build, mostly the parts that can run without cpio."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from drvlab.rootfs import build

# A faithful slice of a generated applet_tables.h: the offset table comes first,
# and the name list uses "\0" separators that must not be mistaken for names.
APPLET_TABLES = r'''/* This is a generated file, don't edit */

#define NUM_APPLETS 4
#define KNOWN_APPNAME_OFFSETS 8

const uint16_t applet_nameofs[] ALIGN2 = {
314,
};

const char applet_names[] ALIGN1 = ""
"[" "\0"
"cat" "\0"
"head" "\0"
"sort" "\0"
;

const char *const applet_main[] = {
};
'''


def test_busybox_tarball_url():
    assert build.busybox_tarball_url("1.36.1") == "https://busybox.net/downloads/busybox-1.36.1.tar.bz2"


def test_busybox_source_dirname():
    assert build.busybox_source_dirname("1.36.1") == "busybox-1.36.1"


def test_build_argv_passes_arch_and_cross_compile_as_make_variables():
    argv = build.busybox_make_argv(["defconfig"], cross_compile="aarch64-linux-gnu-")
    assert argv == ["make", "ARCH=arm", "CROSS_COMPILE=aarch64-linux-gnu-", "defconfig"]


def test_build_argv_omits_jobs_unless_asked():
    assert "-j2" not in build.busybox_make_argv([], cross_compile="c-")
    assert "-j2" in build.busybox_make_argv([], cross_compile="c-", jobs=2)


def test_build_argv_puts_targets_last():
    argv = build.busybox_make_argv(["all", "install"], cross_compile="c-", jobs=4)
    assert argv[-2:] == ["all", "install"]


def test_build_argv_is_all_strings():
    assert all(isinstance(part, str) for part in build.busybox_make_argv([], cross_compile="c-"))


# --- config editing -------------------------------------------------------


def test_config_turns_an_unset_symbol_on():
    assert build.set_config_symbol("# CONFIG_STATIC is not set\n", "CONFIG_STATIC") == (
        "CONFIG_STATIC=y\n"
    )


def test_config_replaces_an_existing_value():
    assert build.set_config_symbol("CONFIG_STATIC=n\n", "CONFIG_STATIC") == "CONFIG_STATIC=y\n"


def test_config_appends_when_the_symbol_is_absent():
    assert build.set_config_symbol("CONFIG_OTHER=y\n", "CONFIG_STATIC") == (
        "CONFIG_OTHER=y\nCONFIG_STATIC=y\n"
    )


def test_config_does_not_match_a_longer_symbol_name():
    # CONFIG_STATIC_FOO must not be touched when asked about CONFIG_STATIC.
    text = "CONFIG_STATIC_FOO=y\n# CONFIG_STATIC is not set\n"
    assert build.set_config_symbol(text, "CONFIG_STATIC") == (
        "CONFIG_STATIC_FOO=y\nCONFIG_STATIC=y\n"
    )


# --- applet extraction ----------------------------------------------------


def test_parses_the_applet_names():
    assert build.parse_applets(APPLET_TABLES) == ["[", "cat", "head", "sort"]


def test_skips_the_nul_separators():
    # "\0" contains a backslash; treating it as a name would try to create a
    # symlink called \0.
    assert "\\0" not in build.parse_applets(APPLET_TABLES)


def test_parses_the_expected_count():
    assert build.parse_num_applets(APPLET_TABLES) == 4


def test_extracted_names_agree_with_the_reported_count():
    # This is the check applets_of() enforces. Measured on busybox 1.36.1:
    # 402 expected, 402 extracted.
    assert len(build.parse_applets(APPLET_TABLES)) == build.parse_num_applets(APPLET_TABLES)


def test_rejects_a_file_that_is_not_generated():
    with pytest.raises(ValueError):
        build.parse_applets("/* nothing useful here */\n")


def test_missing_num_applets_is_an_error():
    with pytest.raises(ValueError):
        build.parse_num_applets("/* no define */\n")


# --- cache key ------------------------------------------------------------


def test_cache_key_is_deterministic_and_sensitive():
    base = build.initramfs_cache_key("bb", "init", "arm64")
    assert base == build.initramfs_cache_key("bb", "init", "arm64")
    assert base != build.initramfs_cache_key("bb2", "init", "arm64")
    assert base != build.initramfs_cache_key("bb", "init2", "arm64")
    assert base != build.initramfs_cache_key("bb", "init", "x86_64")


def test_cache_key_fields_cannot_collide_across_the_boundary():
    assert build.initramfs_cache_key("ab", "c", "a") != build.initramfs_cache_key("a", "bc", "a")


# --- the tree -------------------------------------------------------------


def test_entries_put_the_root_first(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "init").write_text("#!/bin/sh\n")
    entries = build.initramfs_entries(tmp_path)
    assert entries[0] == "."
    assert "bin" in entries
    assert "init" in entries


def test_entries_are_relative_and_sorted(tmp_path: Path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "sh").write_text("")
    (tmp_path / "proc").mkdir()
    entries = build.initramfs_entries(tmp_path)
    assert entries == sorted(entries, key=lambda p: (p != ".", p))
    assert "bin/sh" in entries
    assert all(not entry.startswith("/") for entry in entries)


@pytest.mark.skipif(shutil.which("cpio") is None, reason="cpio is not installed")
def test_pack_produces_a_readable_archive(tmp_path: Path):
    root = tmp_path / "root"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "busybox").write_bytes(b"\x7fELF")
    (root / "bin" / "sh").symlink_to("busybox")
    (root / "init").write_text("#!/bin/sh\n")
    out = build.pack_initramfs(root, tmp_path / "initramfs.cpio.gz")
    assert out.read_bytes()[:2] == b"\x1f\x8b"
