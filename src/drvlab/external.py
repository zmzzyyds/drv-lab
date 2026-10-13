"""The only module in drv-lab that starts a process.

Everything else builds an argv and hands it here. Confining the
subprocess-shaped part of the tool to one file is what lets the rest of it be
tested as pure functions — which, given that the interesting behaviour only
appears inside a virtual machine, is most of what makes the suite worth having.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

#: How much of a failed command's output to carry in the exception. Enough to
#: see the first real error, short enough to stay readable in a terminal.
TAIL_CHARS = 2000


class CommandError(RuntimeError):
    """A command drv-lab ran exited non-zero."""

    def __init__(self, argv: Sequence[str], returncode: int, output: str = ""):
        self.argv = list(argv)
        self.returncode = returncode
        self.output = output
        message = f"{' '.join(self.argv)} exited {returncode}"
        if output:
            message = f"{message}\n{output}"
        super().__init__(message)


class CommandTimeout(RuntimeError):
    """A command was still running when its time ran out.

    Translated from ``subprocess``'s own timeout exception so that nothing
    outside this module has to know which library is underneath. A guest that
    hits this did not finish, which is a different thing from a guest that
    finished and failed.
    """

    def __init__(self, argv: Sequence[str], timeout: float):
        self.argv = list(argv)
        self.timeout = timeout
        super().__init__(f"{' '.join(self.argv)} exceeded {timeout:g}s")


def run(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    log_path: Path | str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return the finished process.

    With ``log_path`` the output is written to that file instead of being
    captured. That is how the noisy steps — a kernel build produces tens of
    thousands of lines — stay out of the user's terminal while still being there
    to read when something goes wrong.

    ``env`` is overlaid on the current environment. ccache's settings can only
    arrive this way; its cache directory and size are environment variables and
    nothing else.
    """
    argv = list(argv)
    if not argv:
        raise ValueError("empty argv")

    child_env = _merged_env(env)

    try:
        if log_path is None:
            return subprocess.run(
                argv, cwd=cwd, env=child_env, capture_output=True, text=True, timeout=timeout
            )

        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            return subprocess.run(
                argv,
                cwd=cwd,
                env=child_env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as expired:
        raise CommandTimeout(argv, timeout if timeout is not None else 0.0) from expired


def run_checked(
    argv: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    log_path: Path | str | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and raise :class:`CommandError` if it fails."""
    process = run(argv, cwd=cwd, env=env, log_path=log_path, timeout=timeout)
    if process.returncode != 0:
        raise CommandError(list(argv), process.returncode, _failure_output(process, log_path))
    return process


def run_filter(
    argv: Sequence[str],
    data: str,
    *,
    cwd: Path | str | None = None,
) -> bytes:
    """Run a command with ``data`` on stdin and return its stdout as bytes.

    Used for the one pipeline drv-lab needs — handing a file list to ``cpio`` —
    without going through a shell, which would mean quoting paths.
    """
    argv = list(argv)
    if not argv:
        raise ValueError("empty argv")

    process = subprocess.run(
        argv, cwd=cwd, input=data.encode("utf-8"), capture_output=True, check=False
    )
    if process.returncode != 0:
        raise CommandError(argv, process.returncode, process.stderr.decode("utf-8", "replace"))
    return process.stdout


def _merged_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    if env is None:
        return None
    merged = dict(os.environ)
    merged.update(env)
    return merged


def _failure_output(process: subprocess.CompletedProcess[str], log_path: Path | str | None) -> str:
    if log_path is not None:
        path = Path(log_path)
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            return text[-TAIL_CHARS:]
    combined = (process.stdout or "") + (process.stderr or "")
    return combined[-TAIL_CHARS:]
