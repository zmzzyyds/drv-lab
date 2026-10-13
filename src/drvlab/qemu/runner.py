"""Driving one guest run and reading back what it said.

This is where the pieces meet: the share is filled, QEMU is started, the console
is captured, and the sentinel the guest wrote becomes an exit code. The
translation itself lives in :mod:`drvlab.result`; what is here is arranging
files and waiting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from drvlab import external, result
from drvlab.qemu.argv import QemuSpec, build_qemu_argv

#: Where the guest looks for its instructions and leaves its answer.
CONTROL_DIR = ".drvlab"

MODULE_FILE = "module"
TEST_FILE = "test"
INJECTOR_NAME = "drvlab-injector.ko"

#: A guest that boots in two seconds and then sits there is not slow, it is
#: stuck, and the difference matters: one is a timeout, the other is a result.
DEFAULT_TIMEOUT = 180.0


@dataclass
class Run:
    """A prepared guest run, and where its output lands."""

    share: Path
    console_log: Path
    module: str | None = None

    @property
    def control(self) -> Path:
        return self.share / CONTROL_DIR

    @property
    def sentinel(self) -> Path:
        return self.control / "result"


def prepare(
    share: Path,
    *,
    module: str | None = None,
    module_image: Path | None = None,
    test_binary: Path | None = None,
    injector: Path | None = None,
) -> Run:
    """Lay out the share the guest will read.

    The guest is told what to do by files rather than by arguments, because
    arguments would have to travel through QEMU. Everything written here is
    visible over 9p and copied nowhere, so changing what a run does never means
    rebuilding the guest.
    """
    control = share / CONTROL_DIR
    control.mkdir(parents=True, exist_ok=True)
    run = Run(share=share, console_log=share.parent / "console.log")

    if module and module_image:
        (share / f"{module}.ko").write_bytes(module_image.read_bytes())
        (control / MODULE_FILE).write_text(module + "\n", encoding="utf-8")
        run.module = module

    if injector is not None:
        (share / INJECTOR_NAME).write_bytes(injector.read_bytes())

    if test_binary is not None:
        target = control / TEST_FILE
        target.write_bytes(test_binary.read_bytes())
        target.chmod(0o755)

    return run


def read_sentinel(run: Run) -> result.GuestResult | None:
    """Read what the guest reported, or ``None`` if it reported nothing usable."""
    if not run.sentinel.exists():
        return None
    text = run.sentinel.read_text(encoding="utf-8", errors="replace")
    try:
        return result.parse_sentinel(text)
    except ValueError:
        # A sentinel we cannot read is a harness failure, not a driver result.
        return None


def execute(
    run: Run,
    spec: QemuSpec,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, result.GuestResult | None]:
    """Run the guest and return ``(exit_code, what_the_guest_reported)``."""
    timed_out = False
    try:
        external.run(build_qemu_argv(spec), timeout=timeout, log_path=run.console_log)
    except external.CommandTimeout:
        timed_out = True

    guest = read_sentinel(run)
    return result.exit_code_for(guest, timed_out=timed_out), guest


def console_text(run: Run) -> str:
    """Everything the guest managed to say, for explaining a failure."""
    if not run.console_log.exists():
        return ""
    return run.console_log.read_text(encoding="utf-8", errors="replace")


def guest_lines(run: Run) -> list[str]:
    """The guest's own narration.

    The /init script writes these, so they are the closest thing to a
    first-person account of where a run got to.
    """
    return [line for line in console_text(run).splitlines() if line.startswith("drvlab:")]
