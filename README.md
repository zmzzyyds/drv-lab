# drv-lab

**Compile-tested is not tested.**

drv-lab boots a real Linux kernel in QEMU, builds your out-of-tree module
against that kernel, loads it, runs your userspace test against the loaded
driver, and hands back `dmesg` plus a meaningful exit code.

For kernel modules, a virtual machine is not a workaround for missing hardware —
it is a test environment you can reproduce byte for byte on a laptop and in CI.

## What it catches that a build does not

```console
$ drvlab run ./demo_misc
==> building demo_misc.ko
==> booting the guest
    drvlab: share mounted
    drvlab: publishing a platform device named demo_misc
    drvlab: loading demo_misc.ko
    drvlab: /dev/demo_misc appeared
    drvlab: running the userspace test
    drvlab: test exited 1
==> FAIL  the driver's userspace test reported failure
    console: ~/.cache/drvlab/run/current/console.log
```

Everything up to the last two lines looks like success. The module built, it
loaded, `lsmod` shows it, and the driver registered its device. Then the
driver's own test — the one that had been compiled on every CI run and executed
on none of them — wrote its payload and died on `lseek: Illegal seek`.

That is the whole point. A driver that loads is not a driver that works, and the
gap between the two is only visible if something actually runs it.

## Why this exists

Most driver CI stops at compiling:

```make
make -C /lib/modules/$(uname -r)/build M=$PWD modules   # compiles
sudo insmod foo.ko && lsmod | grep foo                  # loads, that's all
```

That proves the module builds and that `init_module()` does not crash on one
particular kernel. It does *not* prove that the driver's `read()` / `write()` /
`ioctl()` path does what it claims, because the userspace test program is
usually compiled and then never executed. Loading a module and driving it are
different claims, and only the first one is being checked.

Two things make that gap easy to leave open, and drv-lab closes both:

- **A module can load without its `probe()` ever running.** If the driver binds
  only through a device tree compatible string and nothing matches it, `insmod`
  succeeds, `lsmod` lists it, and the driver's code has not executed a line.
- **A test binary can be built on every run and executed on none.** The failure
  then waits until a user finds it.

## Install

drv-lab is not published yet. From a checkout:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
drvlab --help
```

It needs Linux (a native host, or WSL2), plus `curl`, `cpio`, `dtc` and
`qemu-system-aarch64`. It fetches and builds its own kernel, so it does not need
the host's kernel headers — which is exactly why the guest subsystem set is
under drv-lab's control rather than whatever the host happens to have.

## Use

```sh
drvlab run ./my-driver
```

The directory needs a `Makefile` that builds one module, and optionally
`tools/test.c` for the userspace test. Everything else — a kernel, an initramfs,
a guest run — drv-lab brings.

| Exit code | Meaning |
|---|---|
| 0 | Everything the driver asserted held. |
| 1 | The userspace test ran and reported failure. A real result, not a harness problem. |
| 2 | The harness failed before the driver could be judged. |
| 3 | The kernel or the module failed to build. |
| 4 | The module loaded but never bound: its device never appeared. |
| 124 | The guest did not finish in time. Matches `timeout(1)`. |

`drvlab kernel build` builds the guest kernel on its own. `drvlab cache dir`
prints where the expensive things live.

## Status

Working end to end on arm64, verified against a real guest:

| | |
|---|---|
| Boot to guest poweroff | 2.9 s |
| Whole run, kernel already built | ~19 s |
| First run, including the kernel build | ~90 min on two cores |

Against the three templates drvforge ships:

| Template | Result |
|---|---|
| `misc` | runs, and reports exit 1 — its own test client seeks on a character device that cannot seek |
| `platform-cdev` | runs, and passes |
| `iio-temp` | will not build: the guest kernel has no IIO, so `devm_iio_device_alloc` is undefined. Testing it needs `CONFIG_IIO` and `i2c-stub` in the fragment |

Not done yet, and it matters for judging the output:

- **The driver's device tree binding is not exercised.** A platform driver needs
  a device node to bind to, and a QEMU arm64 `virt` guest cannot be given one
  (`-dtb` suppresses QEMU's own `/chosen` entries, and `CONFIG_OF_CONFIGFS` is
  not mainline). The guest publishes a platform device named after the driver
  instead, which `platform_match()` accepts. A wrong compatible string is
  therefore not caught, and a driver that reads properties from its node in
  `probe()` will find none. Closing this needs `CONFIG_OF_OVERLAY` and
  `of_overlay_fdt_apply()`.
- Only the platform-driver shape is supported; I2C client drivers are not.
- arm64 only, one kernel version at a time, no machine-readable output, no
  GitHub Action yet.

The reasoning behind each of these is written up in
[`docs/design.md`](docs/design.md), including the experiments that ruled out the
obvious approaches.

It composes with [drvforge](https://github.com/zmzzyyds/drvforge): drvforge
scaffolds the driver, drv-lab proves it runs.

## License

GPL-2.0-only. See [LICENSE](LICENSE).
