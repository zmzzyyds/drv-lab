"""drvlab's command line.

The only job here is to sequence the other modules and turn what they report
into an exit code. Anything that could be a pure function lives elsewhere.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from drvlab import errors, external
from drvlab.driver import build as driver_build
from drvlab.kernel import build as kernel_build
from drvlab.qemu import argv as qemu_argv
from drvlab.qemu import runner
from drvlab.rootfs import build as rootfs_build

DEFAULT_KERNEL = "6.12"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="drvlab",
        description="Boot a real kernel in QEMU and actually run an out-of-tree driver.",
    )
    parser.add_argument("--version", action="version", version="drvlab 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="build a driver, boot it, run its test")
    run.add_argument("driver", type=Path, help="directory holding the driver's Makefile")
    run.add_argument(
        "--kernel", default=DEFAULT_KERNEL, help=f"kernel version (default {DEFAULT_KERNEL})"
    )
    run.add_argument("--jobs", type=int, default=os.cpu_count() or 2, help="parallel build jobs")
    run.add_argument(
        "--timeout",
        type=float,
        default=runner.DEFAULT_TIMEOUT,
        help="guest timeout in seconds",
    )
    run.add_argument("--no-ccache", action="store_true", help="build the kernel without ccache")

    kernel = subparsers.add_parser("kernel", help="kernel tree commands")
    kernel_sub = kernel.add_subparsers(dest="kernel_command", required=True)
    kernel_build_cmd = kernel_sub.add_parser(
        "build", help="fetch, configure and build the guest kernel"
    )
    kernel_build_cmd.add_argument("--kernel", default=DEFAULT_KERNEL)
    kernel_build_cmd.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    kernel_build_cmd.add_argument("--no-ccache", action="store_true")

    cache = subparsers.add_parser("cache", help="cache commands")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_sub.add_parser("dir", help="print where the cache lives")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "cache":
            print(kernel_build.Cache.default().root)
            return errors.EXIT_OK
        if args.command == "kernel":
            _kernel(args, None)
            return errors.EXIT_OK
        return _run(args)
    except external.CommandError as failure:
        print(f"drvlab: a command failed\n{failure}", file=sys.stderr)
        return errors.EXIT_BUILD
    except (ValueError, RuntimeError) as failure:
        print(f"drvlab: {failure}", file=sys.stderr)
        return errors.EXIT_HARNESS


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------


def _kernel(args: argparse.Namespace, run_dir: Path | None) -> tuple[kernel_build.KernelSpec, Path]:
    """Make sure a built kernel tree is in the cache, and return it."""
    spec = kernel_build.KernelSpec(version=args.kernel)
    cache = kernel_build.Cache.default()
    cache.sources.mkdir(parents=True, exist_ok=True)

    tarball = kernel_build.fetch_tarball(cache, spec.version)
    source = kernel_build.unpack_source(cache, tarball, spec.version)

    logs = run_dir or cache.root
    kernel_build.configure(source, spec, log_path=logs / "kernel-configure.log")

    image = kernel_build.kernel_image(source, spec.arch)
    have_tree = image.exists() and kernel_build.module_symvers(source).exists()
    if not have_tree:
        print(f"==> building kernel {spec.version} {spec.arch} (this is the slow part)")
        kernel_build.build(
            source,
            spec,
            jobs=args.jobs,
            ccache=not args.no_ccache,
            cache=cache,
            log_path=logs / "kernel-build.log",
        )
    return spec, source


def _initramfs(cache: kernel_build.Cache, run_dir: Path, args: argparse.Namespace) -> Path:
    tarball = rootfs_build.fetch_busybox(cache.busybox)
    source = rootfs_build.unpack_busybox(tarball, cache.busybox)
    binary = rootfs_build.build_busybox(
        source, cross_compile=kernel_build.CROSS_COMPILE_PREFIX, log_path=run_dir / "busybox.log"
    )
    applets = rootfs_build.applets_of(source)
    root = run_dir / "initramfs"
    if root.exists():
        shutil.rmtree(root)
    rootfs_build.assemble_initramfs(
        root,
        busybox=binary,
        init_script=rootfs_build.init_script_path(),
        applets=applets,
    )
    return rootfs_build.pack_initramfs(root, run_dir / "initramfs.cpio.gz")


def _run(args: argparse.Namespace) -> int:
    driver_dir = args.driver.resolve()
    if not (driver_dir / "Makefile").exists():
        print(f"drvlab: {driver_dir} has no Makefile", file=sys.stderr)
        return errors.EXIT_HARNESS

    cache = kernel_build.Cache.default()
    run_dir = cache.run_dir("current")
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)

    _spec, source = _kernel(args, run_dir)
    initramfs = _initramfs(cache, run_dir, args)

    module_name = driver_build.module_name_of(driver_dir)
    print(f"==> building {module_name}.ko")
    module = driver_build.build_module(source, driver_dir, log_path=run_dir / "module-build.log")
    test = driver_build.build_static_test(
        driver_build.test_client_source(driver_dir),
        run_dir / "test-client",
        log_path=run_dir / "test-build.log",
    )
    injector = driver_build.build_injector(
        source, run_dir / "injector", log_path=run_dir / "injector-build.log"
    )

    share = run_dir / "share"
    run = runner.prepare(
        share, module=module_name, module_image=module, test_binary=test, injector=injector
    )

    print("==> booting the guest")
    qemu_spec = qemu_argv.QemuSpec(
        kernel_image=kernel_build.kernel_image(source, _spec.arch),
        initramfs=initramfs,
        share_dir=share,
        console_log=run.console_log,
    )
    code, guest = runner.execute(run, qemu_spec, timeout=args.timeout)
    _report(run, guest, code)
    return code


def _report(run: runner.Run, guest: object, code: int) -> None:
    """Print what happened, in the order that makes a failure readable."""
    for line in runner.guest_lines(run):
        print(f"    {line}")

    if code == errors.EXIT_OK:
        print("==> PASS")
    else:
        print(f"==> FAIL  {_explain(code)}")

    print(f"    console: {run.console_log}")
    if guest is None and code != errors.EXIT_OK:
        print("    the guest reported nothing; the console log is the only account")


def _explain(code: int) -> str:
    explanations = {
        errors.EXIT_TEST_FAILED: "the driver's userspace test reported failure",
        errors.EXIT_HARNESS: "the harness failed before the driver could be judged",
        errors.EXIT_NO_BIND: "the module loaded but its device never appeared",
        errors.EXIT_TIMEOUT: "the guest did not finish in time",
    }
    return explanations.get(code, f"exit {code}")


if __name__ == "__main__":
    raise SystemExit(main())
