"""Pure construction of the QEMU command line.

There is deliberately no I/O and no subprocess call in this module: the whole
argv is a pure function of :class:`QemuSpec`, which is what makes it cheap to
test. Everything that actually needs a running QEMU lives in ``runner.py``.

v0.1 targets ``qemu-system-aarch64 -M virt`` only. Note that arm64 guests run
under TCG even on an x86_64 host — KVM can only virtualise the host
architecture — so there is no accelerator flag to set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

QEMU_BINARY = "qemu-system-aarch64"
MACHINE = "virt"

#: The 9p share tag the guest mounts. Must match the tag in ``rootfs/assets/init``.
SHARE_TAG = "hostshare"

#: QEMU's arm virt machine wires the PL011 UART to ttyAMA0.
CONSOLE = "ttyAMA0"

#: Kernel image and initramfs paths are resolved inside the guest, so the
#: cmdline hands control straight to our init without an initramfs shell detour.
INIT = "/init"


@dataclass(frozen=True)
class QemuSpec:
    """Everything the QEMU command line depends on."""

    kernel_image: Path
    initramfs: Path
    share_dir: Path
    console_log: Path
    dtb: Path | None = None
    cpu: str = "cortex-a57"
    smp: int = 2
    memory_mb: int = 512


def build_qemu_argv(spec: QemuSpec) -> list[str]:
    """Return the argv for one guest run.

    The console is the only output channel, and it goes to a file the host
    reads back once the guest has exited. ``-no-reboot`` turns a guest reboot
    into process exit, and ``panic=-1`` in the cmdline turns a kernel panic
    into a reboot, so neither can leave the run hanging.
    """
    argv = [
        QEMU_BINARY,
        "-M",
        MACHINE,
        "-cpu",
        spec.cpu,
        "-smp",
        str(spec.smp),
        "-m",
        str(spec.memory_mb),
        "-display",
        "none",
        "-monitor",
        "none",
        "-serial",
        f"file:{spec.console_log}",
        "-no-reboot",
        "-kernel",
        str(spec.kernel_image),
        "-initrd",
        str(spec.initramfs),
        "-append",
        build_kernel_cmdline(),
        "-fsdev",
        f"local,id=fs0,path={spec.share_dir},security_model=none",
        # virtio-9p-pci rather than virtio-9p-device on purpose: passing -dtb
        # replaces QEMU's generated device tree, which also removes the
        # virtio-mmio slots QEMU would have created. PCIe is described by the
        # dumped tree itself, so a PCI virtio device survives -dtb.
        "-device",
        f"virtio-9p-pci,fsdev=fs0,mount_tag={SHARE_TAG}",
    ]
    if spec.dtb is not None:
        argv += ["-dtb", str(spec.dtb)]
    return argv


def build_kernel_cmdline() -> str:
    """Return the guest kernel command line."""
    return f"console={CONSOLE} rdinit={INIT} loglevel=7 panic=-1"
