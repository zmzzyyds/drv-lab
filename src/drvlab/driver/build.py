"""Building the driver under test, its test client, and the injector.

The module is cross-built on the host against the kernel drv-lab just built, so
it is compiled by one compiler against one configuration — the one the guest
will actually be running. That is the whole reason for building a kernel rather
than borrowing the host's.

The userspace test is linked **statically**, and drv-lab compiles it itself
rather than leaning on the project's own ``make tools/test`` rule. A busybox
initramfs has no glibc, so a dynamically linked test dies with "not found" — a
failure that reads like a broken driver and is not one.
"""

from __future__ import annotations

import re
from pathlib import Path

from drvlab import external

DEFAULT_CROSS_COMPILE = "aarch64-linux-gnu-"

INJECTOR_BASENAME = "drvlab-injector"

_MODULE_NAME = re.compile(r"^\s*MODULE_NAME\s*[:?]?=\s*([A-Za-z0-9_.\-]+)\s*$", re.MULTILINE)
_OBJ_M = re.compile(r"^\s*obj-m\s*[:?]?=\s*([A-Za-z0-9_.\-]+)\.o\s*$", re.MULTILINE)


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------


def module_build_argv(
    kernel_build_dir: Path | str,
    driver_dir: Path | str,
    *,
    arch: str = "arm64",
    cross_compile: str = DEFAULT_CROSS_COMPILE,
) -> list[str]:
    """Return the argv that builds an out-of-tree module.

    ``kernel_build_dir`` is the tree drv-lab built, not ``/lib/modules/...``:
    the point is to compile against the kernel the guest will boot, and the
    host's own kernel is a different thing entirely.
    """
    return [
        "make",
        "-C",
        str(kernel_build_dir),
        f"M={driver_dir}",
        f"ARCH={arch}",
        f"CROSS_COMPILE={cross_compile}",
        "modules",
    ]


def static_test_argv(
    source: Path | str,
    out: Path | str,
    *,
    cross_compile: str = DEFAULT_CROSS_COMPILE,
) -> list[str]:
    """Return the argv that builds the driver's userspace test client."""
    return [
        f"{cross_compile}gcc",
        "-static",
        "-Wall",
        "-Wextra",
        "-O2",
        "-o",
        str(out),
        str(source),
    ]


def module_name_of(driver_dir: Path | str) -> str:
    """Work out what the module in a driver directory is called.

    drv-lab needs this because the platform device it publishes has to carry the
    *driver's* name: ``platform_match()`` falls back to comparing the device's
    name with the driver's, and nothing else will bind a driver whose only
    binding path is a device tree compatible string.

    Reads MODULE_NAME first, then the ``obj-m`` line, which is what actually
    decides the file kbuild produces.
    """
    driver_dir = Path(driver_dir)
    makefile = driver_dir / "Makefile"
    if not makefile.exists():
        raise ValueError(f"{driver_dir} has no Makefile")
    text = makefile.read_text(encoding="utf-8")

    for pattern in (_MODULE_NAME, _OBJ_M):
        match = pattern.search(text)
        if match:
            return match.group(1)
    raise ValueError(f"cannot tell what module {driver_dir} builds")


def module_path(driver_dir: Path | str, name: str | None = None) -> Path:
    """The ``.ko`` a module build produces."""
    driver_dir = Path(driver_dir)
    return driver_dir / f"{name or module_name_of(driver_dir)}.ko"


def test_client_source(driver_dir: Path | str) -> Path:
    """The userspace test that ships with the driver."""
    return Path(driver_dir) / "tools" / "test.c"


def injector_source_path() -> Path:
    """The module drv-lab compiles to publish a platform device."""
    return Path(__file__).resolve().parent / "assets" / f"{INJECTOR_BASENAME}.c"


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------


def build_module(
    kernel_build_dir: Path,
    driver_dir: Path,
    *,
    arch: str = "arm64",
    cross_compile: str = DEFAULT_CROSS_COMPILE,
    log_path: Path | None = None,
) -> Path:
    """Build the driver and return the ``.ko``."""
    external.run_checked(
        module_build_argv(kernel_build_dir, driver_dir, arch=arch, cross_compile=cross_compile),
        log_path=log_path,
    )
    built = module_path(driver_dir)
    if not built.exists():
        raise RuntimeError(f"the build produced no {built}")
    return built


def build_static_test(
    source: Path,
    out: Path,
    *,
    cross_compile: str = DEFAULT_CROSS_COMPILE,
    log_path: Path | None = None,
) -> Path:
    """Build the userspace test, statically, and return it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    external.run_checked(
        static_test_argv(source, out, cross_compile=cross_compile), log_path=log_path
    )
    return out


def build_injector(
    kernel_build_dir: Path,
    work_dir: Path,
    *,
    arch: str = "arm64",
    cross_compile: str = DEFAULT_CROSS_COMPILE,
    log_path: Path | None = None,
) -> Path:
    """Build the platform device injector and return the ``.ko``.

    The source and a one-line Makefile are copied into ``work_dir`` because
    kbuild only builds out-of-tree modules from a directory that has both.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / f"{INJECTOR_BASENAME}.c").write_bytes(injector_source_path().read_bytes())
    (work_dir / "Makefile").write_text(f"obj-m := {INJECTOR_BASENAME}.o\n", encoding="utf-8")

    external.run_checked(
        module_build_argv(kernel_build_dir, work_dir, arch=arch, cross_compile=cross_compile),
        log_path=log_path,
    )
    built = work_dir / f"{INJECTOR_BASENAME}.ko"
    if not built.exists():
        raise RuntimeError(f"the injector build produced no {built}")
    return built
