"""Tests for reading a guest's console log back.

Only the reporting parts: running QEMU needs a kernel, a guest and time. The
log fragments below are trimmed from a real run.
"""

from __future__ import annotations

from pathlib import Path

from drvlab.qemu import runner

REAL_ISH_LOG = """\
[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x411fd070]
[    1.428794] drvlab-injector: published platform device 'demo_misc'
[    1.482334] demo_misc demo_misc: registered /dev/demo_misc (minor 125)
drvlab: share mounted
drvlab: publishing a platform device named demo_misc
drvlab: loading demo_misc.ko
drvlab: /dev/demo_misc appeared
drvlab: running the userspace test
[ok] wrote 14 bytes ('drvforge-hello')
lseek: Illegal seek
drvlab: test exited 1
[    1.978820] Freeing unused kernel memory: 3008K
"""


def _run(tmp_path: Path, text: str) -> runner.Run:
    log = tmp_path / "console.log"
    log.write_text(text, encoding="utf-8")
    return runner.Run(share=tmp_path / "share", console_log=log)


def test_guest_lines_are_the_narration(tmp_path):
    lines = runner.guest_lines(_run(tmp_path, REAL_ISH_LOG))
    assert lines[0] == "drvlab: share mounted"
    assert lines[-1] == "drvlab: test exited 1"
    assert len(lines) == 6


def test_test_output_is_what_the_test_printed(tmp_path):
    # Identified by elimination: not narrated by /init, and not stamped by the
    # kernel.
    printed = runner.test_output(_run(tmp_path, REAL_ISH_LOG))
    assert printed == ["[ok] wrote 14 bytes ('drvforge-hello')", "lseek: Illegal seek"]


def test_test_output_keeps_a_failure_that_looks_like_a_kernel_line(tmp_path):
    # The test's own "[ok] ..." line starts with a bracket. Only a real kernel
    # timestamp makes something a kernel line.
    printed = runner.test_output(_run(tmp_path, "[ok] wrote 14 bytes\n"))
    assert printed == ["[ok] wrote 14 bytes"]


def test_relevant_kernel_lines_find_the_module(tmp_path):
    lines = runner.relevant_kernel_lines(_run(tmp_path, REAL_ISH_LOG), "demo_misc")
    assert any("registered /dev/demo_misc" in line for line in lines)


def test_relevant_kernel_lines_do_not_dump_the_whole_boot_log(tmp_path):
    lines = runner.relevant_kernel_lines(_run(tmp_path, REAL_ISH_LOG), "demo_misc")
    assert not any("Freeing unused kernel memory" in line for line in lines)
    assert not any("Booting Linux" in line for line in lines)


def test_relevant_kernel_lines_notice_trouble_without_the_module_name(tmp_path):
    text = "[    2.000000] demo_misc: module verification failed: signature missing\n"
    lines = runner.relevant_kernel_lines(_run(tmp_path, text), "something_else")
    assert len(lines) == 1
    assert "verification failed" in lines[0]


def test_relevant_kernel_lines_are_deduplicated(tmp_path):
    text = "[    1.0] demo_misc demo_misc: registered\n" * 3
    assert len(runner.relevant_kernel_lines(_run(tmp_path, text), "demo_misc")) == 1


def test_relevant_kernel_lines_respect_the_limit(tmp_path):
    text = "".join(
        f"[    {i}.0] demo_misc: a message about demo_misc number {i}\n" for i in range(40)
    )
    assert len(runner.relevant_kernel_lines(_run(tmp_path, text), "demo_misc")) == 15


def test_an_empty_console_is_not_an_error(tmp_path):
    run = _run(tmp_path, "")
    assert runner.test_output(run) == []
    assert runner.relevant_kernel_lines(run, "demo_misc") == []
    assert runner.guest_lines(run) == []


def test_a_missing_console_file_is_not_an_error(tmp_path):
    run = runner.Run(share=tmp_path / "share", console_log=tmp_path / "nothing.log")
    assert runner.console_text(run) == ""
    assert runner.test_output(run) == []
