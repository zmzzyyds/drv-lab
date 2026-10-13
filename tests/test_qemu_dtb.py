"""Tests for device tree command construction and the node-count check."""

from __future__ import annotations

from pathlib import Path

import pytest

from drvlab.qemu.dtb import (
    MachineSpec,
    count_root_nodes,
    dtb_cache_key,
    dtc_argv,
    dumpdtb_argv,
    fdtget_root_nodes_argv,
    fdtoverlay_argv,
)

SPEC = MachineSpec()


def _value_of(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def test_dumpdtb_asks_for_a_machine_dump():
    argv = dumpdtb_argv(SPEC, "/tmp/virt.dtb")
    assert _value_of(argv, "-M") == "virt,dumpdtb=/tmp/virt.dtb"


def test_dumpdtb_passes_the_parameters_that_get_baked_in():
    argv = dumpdtb_argv(MachineSpec(cpu="cortex-a53", smp=4, memory_mb=1024), "/tmp/v.dtb")
    assert _value_of(argv, "-cpu") == "cortex-a53"
    assert _value_of(argv, "-smp") == "4"
    assert _value_of(argv, "-m") == "1024"


def test_dumpdtb_takes_no_display():
    assert "none" == _value_of(dumpdtb_argv(SPEC, "/tmp/v.dtb"), "-display")


def test_dtc_emits_symbols_by_default():
    assert "-@" in dtc_argv("in.dts", "out.dtbo")


def test_dtc_symbols_can_be_turned_off():
    assert "-@" not in dtc_argv("in.dts", "out.dtbo", symbols=False)


def test_dtc_compiles_to_a_dtb():
    argv = dtc_argv("in.dts", "out.dtbo")
    assert argv[:3] == ["dtc", "-O", "dtb"]
    assert _value_of(argv, "-o") == "out.dtbo"
    assert argv[-1] == "in.dts"


def test_fdtoverlay_merges_base_and_overlay():
    argv = fdtoverlay_argv("virt.dtb", "ov.dtbo", "merged.dtb")
    assert argv[0] == "fdtoverlay"
    assert _value_of(argv, "-i") == "virt.dtb"
    assert _value_of(argv, "-o") == "merged.dtb"
    assert argv[-1] == "ov.dtbo"


def test_fdtget_lists_root_subnodes():
    assert fdtget_root_nodes_argv("merged.dtb") == ["fdtget", "-l", "merged.dtb", "/"]


def test_argv_is_path_valued():
    # Paths are passed through as strings even when given as Path objects.
    argv = fdtoverlay_argv(Path("/a/virt.dtb"), Path("/a/ov.dtbo"), Path("/a/out.dtb"))
    assert all(isinstance(part, str) for part in argv)
    # Compared via str(Path(...)) rather than a hardcoded POSIX string: on
    # Windows the separators differ, and asserting the literal would make this
    # a test of the host's path style rather than of the argv.
    assert str(Path("/a/virt.dtb")) in argv


# --- the node count -------------------------------------------------------


def test_counts_real_root_nodes():
    assert count_root_nodes("psci\nmemory@40000000\npl011@9000000\n") == 3


def test_ignores_the_symbols_node_dtc_adds():
    # dtc -@ emits __symbols__. Counting it would let the check pass with no
    # device node ever landing.
    assert count_root_nodes("psci\nmemory@40000000\n__symbols__\n") == 2


def test_ignores_blank_lines():
    assert count_root_nodes("psci\n\n\nmemory@40000000\n") == 2


def test_measured_virt_tree_counts():
    # Measured on QEMU 6.2: 48 root subnodes, and merging one overlay leaves 49.
    base = "\n".join(f"node{i}" for i in range(48)) + "\n"
    merged = base + "demo-misc@9040000\n__symbols__\n"
    assert count_root_nodes(base) == 48
    assert count_root_nodes(merged) == 49


def test_merged_must_be_greater_than_base():
    base = count_root_nodes("a\nb\n")
    merged = count_root_nodes("a\nb\nc\n__symbols__\n")
    assert merged > base


# --- the tree cache key ---------------------------------------------------


def _key(**overrides):
    kwargs = {
        "qemu_version": "QEMU emulator version 6.2.0",
        "machine": "virt",
        "spec": SPEC,
    }
    kwargs.update(overrides)
    return dtb_cache_key(**kwargs)


def test_tree_cache_key_is_deterministic():
    assert _key() == _key()


def test_tree_cache_key_changes_with_the_boot_parameters():
    # A tree dumped for one set of parameters is wrong for another.
    assert _key(spec=MachineSpec(smp=4)) != _key()
    assert _key(spec=MachineSpec(memory_mb=1024)) != _key()
    assert _key(spec=MachineSpec(cpu="cortex-a72")) != _key()


def test_tree_cache_key_changes_with_qemu_or_machine():
    assert _key(qemu_version="QEMU emulator version 8.2.0") != _key()
    assert _key(machine="q35") != _key()


def test_tree_cache_key_is_a_hex_digest():
    key = _key()
    assert len(key) == 64
    int(key, 16)


@pytest.mark.parametrize("smp", [2, 4, 8])
def test_cpu_count_is_not_stringified_loosely(smp):
    # 2 vs 4 must differ; guard against comparing "2" and "4" as equal.
    assert _key(spec=MachineSpec(smp=smp)) != _key(spec=MachineSpec(smp=1))
