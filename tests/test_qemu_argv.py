"""Structural tests for the QEMU command line.

These assert the properties the design depends on rather than a frozen golden
string: individual flags will change as the lab grows, but the reasons behind
virtio-9p-pci, -dtb and the file-backed console will not.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from drvlab.qemu.argv import SHARE_TAG, QemuSpec, build_kernel_cmdline, build_qemu_argv


@pytest.fixture
def spec(tmp_path: Path) -> QemuSpec:
    return QemuSpec(
        kernel_image=tmp_path / "Image",
        initramfs=tmp_path / "initramfs.cpio.gz",
        share_dir=tmp_path / "share",
        console_log=tmp_path / "console.log",
        dtb=tmp_path / "merged.dtb",
    )


def _value_of(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_starts_with_the_arm64_emulator(spec):
    assert build_qemu_argv(spec)[0] == "qemu-system-aarch64"


def test_uses_the_virt_machine(spec):
    assert _value_of(build_qemu_argv(spec), "-M") == "virt"


def test_share_is_virtio_9p_over_pci(spec):
    # Not virtio-9p-device: -dtb removes QEMU's generated virtio-mmio nodes.
    device = _value_of(build_qemu_argv(spec), "-device")
    assert device.startswith("virtio-9p-pci,")
    assert f"mount_tag={SHARE_TAG}" in device


def test_share_directory_is_exported(spec):
    assert f"path={spec.share_dir}" in _value_of(build_qemu_argv(spec), "-fsdev")


def test_console_is_captured_to_a_file(spec):
    assert _value_of(build_qemu_argv(spec), "-serial") == f"file:{spec.console_log}"
    assert _value_of(build_qemu_argv(spec), "-display") == "none"
    assert _value_of(build_qemu_argv(spec), "-monitor") == "none"


def test_dtb_is_passed_when_present(spec):
    assert _value_of(build_qemu_argv(spec), "-dtb") == str(spec.dtb)


def test_dtb_is_omitted_when_absent(spec):
    without = dataclasses.replace(spec, dtb=None)
    assert "-dtb" not in build_qemu_argv(without)


def test_guest_cannot_restart_itself(spec):
    # -no-reboot makes a guest reboot terminate QEMU instead of looping.
    assert "-no-reboot" in build_qemu_argv(spec)


def test_cmdline_hands_control_to_our_init(spec):
    assert "rdinit=/init" in _value_of(build_qemu_argv(spec), "-append")


def test_cmdline_turns_a_panic_into_a_reboot():
    # panic=-1 reboots immediately; with -no-reboot that exits QEMU, so a
    # panicking kernel cannot hang the run until the timeout.
    assert "panic=-1" in build_kernel_cmdline()


def test_no_isa_debug_exit(spec):
    # isa-debug-exit is x86-only. On arm64 the guest reports its status through
    # the shared directory, so the argv must not pretend otherwise.
    assert "isa-debug-exit" not in " ".join(build_qemu_argv(spec))


def test_argv_is_all_strings(spec):
    assert all(isinstance(part, str) for part in build_qemu_argv(spec))
