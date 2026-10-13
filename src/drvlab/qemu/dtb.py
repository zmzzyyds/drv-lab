"""Device tree handling: dump the machine's tree, merge an overlay into it.

Two facts about the arm64 ``virt`` machine shape everything here, both measured
rather than assumed:

* its root keeps its 48 devices directly — ``psci``, ``memory@40000000``,
  ``pl011@9000000``, a row of ``virtio_mmio@a000xxx`` — and there is **no
  ``/soc``**. An overlay targeting ``&{/soc}`` compiles cleanly and then fails
  at merge time, which is why merging, not compiling, is what gets checked.
* the root bus is ``#address-cells = 2``, ``#size-cells = 1``, so a ``reg``
  entry is three cells, not four.

Everything below is argv construction and one cache key, so all of it is
testable without QEMU.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from drvlab.qemu.argv import MACHINE, QEMU_BINARY

DTC = "dtc"
FDTOVERLAY = "fdtoverlay"
FDTGET = "fdtget"


@dataclass(frozen=True)
class MachineSpec:
    """The boot parameters that get baked into the dumped tree.

    These are not decoration: ``-cpu``, ``-smp`` and ``-m`` decide the ``/cpus``,
    ``/memory`` and GIC nodes. A tree dumped with ``-m 512`` and booted with
    ``-m 1024`` gives a guest that believes it has 512 MB.
    """

    cpu: str = "cortex-a57"
    smp: int = 2
    memory_mb: int = 512


def dumpdtb_argv(spec: MachineSpec, out: Path | str) -> list[str]:
    """Return the argv that dumps the tree QEMU would boot with."""
    return [
        QEMU_BINARY,
        "-M",
        f"{MACHINE},dumpdtb={out}",
        "-cpu",
        spec.cpu,
        "-smp",
        str(spec.smp),
        "-m",
        str(spec.memory_mb),
        "-display",
        "none",
    ]


def dtc_argv(source: Path | str, out: Path | str, *, symbols: bool = True) -> list[str]:
    """Return the argv that compiles an overlay.

    ``-@`` emits a ``__symbols__`` node. It is kept because the documented
    workflow in drvforge's templates uses it, but it is why the node count below
    has to ignore ``__`` names.
    """
    argv = [DTC, "-O", "dtb"]
    if symbols:
        argv.append("-@")
    argv += ["-o", str(out), str(source)]
    return argv


def fdtoverlay_argv(base: Path | str, overlay: Path | str, out: Path | str) -> list[str]:
    """Return the argv that merges an overlay into a tree.

    This is the step that actually catches a bad target: dtc resolves the
    fragment's path only here, so a ``&{/soc}`` that does not exist exits 0
    above and non-zero here.
    """
    return [FDTOVERLAY, "-i", str(base), "-o", str(out), str(overlay)]


def fdtget_root_nodes_argv(dtb: Path | str) -> list[str]:
    """Return the argv that lists the root's direct subnodes."""
    return [FDTGET, "-l", str(dtb), "/"]


def count_root_nodes(fdtget_output: str) -> int:
    """Count real root subnodes, ignoring the ``__*`` nodes dtc emits.

    Counting everything would let a "did the overlay add a node" check pass on
    the strength of ``__symbols__`` alone, with no device ever appearing.
    Measured: a clean tree reports 48; after merging one overlay it reports 49.
    """
    return sum(
        1
        for line in fdtget_output.splitlines()
        if line.strip() and not line.strip().startswith("__")
    )


def dtb_cache_key(qemu_version: str, machine: str, spec: MachineSpec) -> str:
    """Return the cache key for a dumped tree.

    Every parameter that is baked into the tree is in the key, because reusing a
    tree across different boot parameters silently produces a guest with the
    wrong number of CPUs or the wrong amount of memory.
    """
    digest = hashlib.sha256()
    parts = (qemu_version, machine, spec.cpu, str(spec.smp), str(spec.memory_mb))
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
