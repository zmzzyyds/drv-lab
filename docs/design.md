# drv-lab design

## Goal

One command that takes a directory containing an out-of-tree driver and proves
it works on a real kernel:

```
drvlab run <driver-dir>
```

boils down to: build a kernel with a known configuration → boot it in QEMU →
share the driver directory into the guest → build the module against *that*
kernel → `insmod` → wait for the device to bind → run the driver's userspace
test → report the test's exit code and the guest's `dmesg`.

## Non-goals

- **Not a replacement for hardware testing.** A virtual machine cannot tell you
  whether your I2C bus timing is right. It tells you whether your driver's code
  paths execute and agree with its own userspace contract.
- **Not a kernel test framework.** KUnit and kselftest test the kernel. drv-lab
  tests the driver you wrote, from the outside, using the driver's own
  userspace test program as the oracle.
- **Not a build system.** The driver keeps its own `Makefile`. drv-lab drives it.

## Why the kernel is built rather than borrowed

Using the host's or a CI image's kernel would be much cheaper, and it is
rejected for two reasons.

1. **Configuration control.** The lab needs subsystems that stock images leave
   out — notably the IIO subsystem and `i2c-stub`. Those are exactly the things
   that make the interesting templates untestable today.
2. **Version control.** A matrix of kernel versions is the point. A driver that
   only ever compiles against one kernel is not a driver that is known to work.

The cost is a multi-minute build, which is what the cache exists to hide.

## Decisions

| Decision | Why |
|---|---|
| busybox `initramfs`, not Buildroot | ~1 s boot under TCG versus ~6 s, and no second build system to maintain. Built once and cached. |
| Kernel built from a tarball, cached | Reproducible, and the tarball is checksum-verifiable. |
| `make virtconfig` where available | Upstream fragment (Linux 6.3+) that disables all SoC options, cutting build time ~25%. It *only* subtracts, so drv-lab's own fragment is merged on top of it, then `olddefconfig`. Below 6.3 it falls back to `defconfig` + fragment. |
| `i2c-stub`, not QEMU device emulation | QEMU's `tmp105` model is only confirmed instantiated on Aspeed BMC machines; nothing verifies it exists on `-M virt`. `i2c-stub` is pure software, architecture-independent, and ships with the kernel. |
| `virtio-9p-pci`, not `virtio-mmio` | Passing `-dtb` replaces QEMU's generated device tree, which also removes the virtio-mmio slots QEMU would have created. PCIe is described by the dumped tree itself, so PCI virtio survives. virtiofs is rejected for now because it needs a separate `virtiofsd` on the host. |
| Guest status via a sentinel file on the share | `isa-debug-exit` is x86-only. The guest writes its result into the shared directory and then powers off; the host reads the file. |
| arm64 only in v0.1 | The users are embedded developers, and ARM is where they work. arm64 runs under TCG even on an x86_64 host — KVM can only virtualise the host architecture — so there is no accelerator to tune. |
| The test binary is linked statically | A busybox initramfs has no glibc. A dynamically linked test program would fail with "not found" and look like a driver bug. drv-lab compiles the test itself with `-static`; the project's own `make tools/test` rule is advisory. |
| `CC=` goes on the `make` command line, never in the environment | The kernel Makefile assigns `CC = $(CROSS_COMPILE)gcc`, and a makefile assignment beats an exported variable, so `export CC="ccache aarch64-linux-gnu-gcc"` is silently ignored and the cache stays empty. Measured: zero ccache invocations across a full build before this was noticed. Silent, and it only shows up as CI taking 15 minutes longer per kernel version than it should. |
| Trim the fragment past what `virtconfig` does | `virtconfig` strips only the SoC `ARCH_*` options. Measured on a two-core box: the build still compiles `drivers/gpu/drm/nouveau` and the rest of the module tree, none of which a QEMU guest uses. drv-lab's own fragment disables DRM, SOUND, INFINIBAND and the per-vendor network drivers on top of it. |
| `Module.symvers` instead of the whole module tree — **UNVERIFIED, do not rely on it yet** | `Makefile` makes `vmlinux` depend on `modpost`, and `Makefile.modpost` writes `vmlinux.symvers` when `KBUILD_MODULES` is unset and `Module.symvers` when it is set. So `make Image` alone may leave the tree without the file an external module build reads — and since a driver using only core symbols needs nothing from other modules, copying `vmlinux.symvers` over `Module.symvers` may be sufficient. Check: build `Image` only, copy the file, build an external module against the tree, and confirm it loads in the guest. Until that has been run, drv-lab keeps building `Image modules`. |
| The initramfs applet list is extracted from the busybox build, never written by hand | `include/applet_tables.h` lists exactly the applets a build enabled, and `NUM_APPLETS` in the same file says how many to expect. Measured with busybox 1.36.1: 402 expected, 402 extracted. The hand-written list that came first omitted `sort` and `head`, and a guest diagnostic died with `sort: not found` — a failure that costs a debugging round trip every time it happens. |
| The guest reports through files the host drops in the share | `.drvlab/module` names a module to load, `.drvlab/test` is a test binary to run, and the presence of `drvlab-injector.ko` switches on the platform-device strategy. With none of them, `/init` reports `stage=booted` and stops, which is the boot smoke test. Files were chosen over a command line because the guest has to be able to grow new steps without the host passing new arguments through QEMU. |

## Architecture

```
cli.py                     sequence the steps, turn a result into an exit code
errors.py                  the exit-code contract, as named constants
external.py                the only module that starts a process
kernel/build.py            fetch, configure, build, cache; hands back the tree
rootfs/build.py            busybox + /init -> initramfs.cpio.gz
driver/build.py            the module, the static test client, the injector
qemu/argv.py               pure: QemuSpec -> argv
qemu/dtb.py                dumpdtb -> dtc -> fdtoverlay -> fdtget, for dt-overlay
qemu/runner.py             fill the share, run the guest, read the sentinel
result.py                  the sentinel -> exit code translation
```

The rule that makes the suite testable: **no module calls `subprocess` except
`external.py`.** Everything that would otherwise be an unverifiable shell
command is instead a pure function returning an argv list, which is asserted in
unit tests.

## Getting the module to bind

A module that loads but never binds is the most common failure, and it is
invisible to `lsmod`. Each driver shape needs a different strategy.

### `platform-injector` — platform drivers (what v0.1 uses)

drvforge's `misc` and `platform-cdev` drivers bind only through
`.of_match_table`, so a matching device tree node has to exist before probe can
run. A QEMU arm64 `virt` guest cannot be given one. That took three experiments
to establish, and the first of them is the one that disposed of the obvious
approach:

* **`-dtb` does not work.** It suppresses the `/chosen` entries QEMU would
  otherwise add, so the guest boots with no command line and no initramfs.
  Measured: the console log is 0 bytes and QEMU runs to the timeout — both for a
  freshly dumped tree and for one with `bootargs` injected by hand with
  `fdtput`. The raw dumped tree behaves identically to a merged one, so the
  overlay was never the problem.
* `CONFIG_OF_CONFIGFS` is not mainline, so
  `/sys/kernel/config/device-tree/overlays/` does not exist in the guest.
* `of_overlay_fdt_apply()` *is* exported, so applying an overlay to the live
  tree would work — but it needs `CONFIG_OF_OVERLAY`, which a stock guest config
  leaves off.

What is always available is `platform_match()`'s last resort:

```c
	/* fall-back to driver name match */
	return (strcmp(pdev->name, drv->name) == 0);
```

So drv-lab ships a small module (`driver/assets/drvlab-injector.c`) that
publishes a platform device named after the driver. The driver binds to it with
no device tree at all. The guest loads the injector first, then the driver.

**What this does not cover, and it matters:** the driver's `of_match_table` is
never exercised, so a wrong compatible string is not caught, and a driver that
reads properties out of its device node in `probe()` will find none. Closing
that needs a real node. Which brings us to:

### `dt-overlay` — the same drivers, with a real node (not built yet)

Since `-dtb` is out, a real node has to be applied to the live tree at runtime:
`CONFIG_OF_OVERLAY=y` plus a second small module calling
`of_overlay_fdt_apply()`. Enabling it rewrites `include/generated/autoconf.h`,
which invalidates the entire ccache and costs a full rebuild — so it is being
batched with the other config additions (I2C stub, IIO) rather than paid for on
its own.

The supporting machinery is already worked out and measured. Dumping the tree is
still worth doing, for validating the overlay *before* the guest boots — does the
fragment's target path exist, does its `reg` collide — and the merged blob then
goes to the module rather than to `-dtb`:

```
qemu-system-aarch64 -M virt,dumpdtb=virt.dtb
dtc -O dtb -@ -o overlay.dtbo <driver>-overlay.dts
fdtoverlay -i virt.dtb -o merged.dtb overlay.dtbo
```

What the virt machine's tree actually looks like, measured on QEMU 6.2: the
root carries 48 top-level nodes directly — `psci`, `memory@40000000`,
`platform@c000000`, `pl011@9000000`, a row of `virtio_mmio@a000xxx` — and there
is **no `/soc`**. `/soc` is a real-SoC convention, not a QEMU one. An overlay
that targets `&{/soc}` compiles without complaint and only fails at merge time,
which is exactly the bug that shipped in drvforge's templates until `6bf4921`.
The root bus is `#address-cells = 2`, `#size-cells = 1`, so a `reg` entry is
three cells; an overlay fragment should declare those counts, or dtc can only
assume defaults and say so.

The dumped tree is cached, keyed on **every boot parameter that is baked into
it** — QEMU version, cpu model, `smp`, memory — because those determine the
`/cpus`, `/memory` and GIC nodes. A tree dumped with `-m 512` and booted with
`-m 1024` gives a guest that reports 512 MB. The cache key is what enforces
that; silently booting a mismatched tree would produce a nasty class of bug.

Preflight checks run before QEMU is started, because they fail in a way that is
hard to read from the guest's silence:

- the overlay's target path must exist in the dumped tree
- the overlay's `reg` address must not collide with an existing node
- merging the overlay must actually add a node, counted **excluding `__*`
  names** — `dtc -@` emits a `__symbols__` node of its own, and counting that
  would let the check pass without a device ever appearing

Runtime configfs overlay (`/sys/kernel/config/device-tree/overlays/`) is
rejected outright rather than deferred: `OF_CONFIGFS` is not mainline, so it
cannot be relied on to be present.

### `i2c-stub` — I2C client drivers

drvforge's `iio-temp` driver declares both `of_device_id` and `i2c_device_id`,
and its `i2c_device_id` name is the module's driver name. So no device tree is
needed — the client is created directly:

```sh
modprobe i2c-stub chip_addr=0x48
echo <driver-name> 0x48 > /sys/bus/i2c/devices/i2c-0/new_device
insmod <module>.ko
```

`i2c_match_id` compares the client's type string against `i2c_device_id`, and
the type is whatever the `new_device` write supplies, so an in-tree driver such
as `lm75` cannot steal the client — it is never offered one with a name its own
id table matches. (`CONFIG_SENSORS_LM75` is still disabled in the fragment as
belt and braces.)

A driver that only declares `of_match_table` **cannot** be bound this way,
because a sysfs-created client has no `of_node`. That is reported as a bind
failure with the reason spelled out, not as a mystery hang.

For an IIO driver, "running the test" means asserting the IIO device appeared
and reading its sysfs attributes — IIO devices are not character devices.

### `none` — drivers that need neither

Reserved for drivers that register a misc device or a class unconditionally.

## Exit-code contract

Part of the public interface: CI job definitions branch on these, so they are
never renumbered.

| Code | Meaning |
|---|---|
| 0 | Everything the driver asserted held. |
| 1 | The userspace test ran and reported failure. A real result, not a harness problem. |
| 2 | The harness failed — QEMU would not start, the guest never reached the share, or the sentinel never appeared. |
| 3 | The kernel or the module failed to build. |
| 4 | The module loaded but never bound: the expected device node or sysfs attribute never appeared. |
| 124 | Timeout. Matches `timeout(1)`. |

## Cache

Root is `~/.cache/drvlab`, always on the ext4 side of the filesystem — never
under `/mnt/c`, where drvfs gives wrong permission semantics and no usable
`chmod`. `drvlab doctor` refuses to run from `/mnt/*` and says why.

| Artifact | Cached | Key |
|---|---|---|
| kernel tarball | yes | `sha256(version)` |
| extracted source | yes, one | `version` |
| built tree (the KDIR) | yes, one locally | `sha256(version ∥ arch ∥ fragment ∥ gcc ∥ ccache)` |
| ccache dir | yes | same tuple |
| busybox + initramfs | yes | `sha256(busybox ∥ applet-config ∥ host gcc)` |
| dumped base DTB | yes | `sha256(qemu ∥ machine ∥ cpu ∥ smp ∥ mem)` |
| merged DTB, `.ko`, test binary | no | depends on the driver; rebuilt each run |

One ready-to-use kernel version is roughly **3.5–4.2 GB** (tarball 150 MB,
source 1.4 GB, built tree 1.8–2.5 GB with `DEBUG_INFO` off, ccache capped at
1 GB, plus ~100 MB of busybox and images). Disabling `DEBUG_INFO` is the single
biggest saving. On a disk-constrained machine, `drvlab cache evict --keep
<version>` is the mechanism that enforces "at most one kernel tree" rather than
leaving it as a rule people break.

### Local and CI cache differ deliberately

Locally, disk is scarce and rebuilds are expensive, so the built tree is cached
and an iteration costs seconds. In CI the cache budget is scarce (10 GB per
repository) so only ccache is cached and a warm rebuild costs 2–4 minutes. The
asymmetry is the point.

## CI

Two workflows, split by cost.

- **`ci.yml`** — every pull request, target under 60 s, no kernel involved:
  ruff, `pytest -m "not qemu and not slow"`.
- **`integration.yml`** — push to `main`, nightly, and manual. Deliberately not
  on every pull request; a four-way kernel matrix per push gets disabled by
  users within a week.

Matrix: **6.1** (the only entry below 6.3, so it exercises the `defconfig`
fallback — without it that path is dead and will rot), **6.6** and **6.12**
(current LTS), **6.17** (latest, catches API removals as they land).

Two things the matrix cannot do on the stock `ubuntu-24.04` image:

- **Disk.** The image has ~14 GB free; a kernel tree plus ccache does not fit.
  A free-disk-space step is mandatory, not optional.
- **Old kernels.** The image ships GCC 13, and kernels before ~6.1 break under
  it. That is why 5.19 and 5.15 are absent: a permanently red build everyone
  learns to ignore is worse than not testing them. Supporting them means a
  separate nightly job that installs `gcc-12-aarch64-linux-gnu`.

One job exists purely to protect the product thesis: run the harness against a
**deliberately buggy fixture** committed in-repo and assert that drv-lab exits
**1** — not 0, not 2. If that ever drifts, the tool has stopped doing the one
thing it exists for.

## Testing strategy

One rule: anything that is a pure function of its inputs is unit-tested; only
the kernel's behaviour justifies a QEMU run.

**Unit, no QEMU, no network** — CLI parsing; argv construction for QEMU, the
kernel build and the driver build; cache-key determinism *and sensitivity*
(flipping one byte of a fragment must change the key); the initramfs manifest
(the extracted applet count must equal the build's `NUM_APPLETS`); sentinel parsing against
malformed, truncated and absent input; `MODULE_NAME` extraction from real
drvforge Makefiles; overlay classification; version logic; exit-code mapping.

**Integration, `@pytest.mark.qemu`** — ordered cheapest-first so a failure
localises: boot smoke → 9p round-trip → devtmpfs node appears → DTB merge →
i2c-stub bind → negative cases (a driver that fails its test must yield 1; a
wrong `compatible` must yield 4 rather than hanging; a C syntax error must
yield 3 before QEMU is ever launched).

**Slow, nightly** — kernel fetch, build, cache transitions, eviction.

**Cross-repo** — drvforge is pinned to a specific commit and all three of its
templates are generated and driven through the lab. Pinned, because tracking its
`main` would make this repository's CI hostage to an unrelated one.

## Milestones

**M1a — it boots. DONE.** Kernel plus busybox initramfs boots under QEMU, the 9p
share mounts, the guest writes its sentinel and powers off. Measured end to end:
**2.9 s** — and `poweroff -f` really does terminate QEMU through PSCI rather than
leaving the run to hit its timeout, which was the one thing no amount of reading
could establish.

**M1b — the concept, end to end. DONE.** The `misc` template on arm64: the module
is cross-built against the kernel drv-lab produced, the guest loads the injector
and then the driver, the driver's `probe()` runs, `/dev/<name>` appears, the
driver's own userspace test executes, and its exit code travels back through the
sentinel. Measured: **2.0 s** for the whole boot-and-test cycle.

It also settled the question the project was started over. drvforge's `misc`
`tools/test` **cannot pass**: it writes its payload successfully, then calls
`lseek()`, which returns `-ESPIPE` because the driver leaves `f_op->llseek` unset
on kernels ≥ 6.12 and the VFS falls back to `no_llseek()`. Changing that one call
to a reopen turns exit 1 into exit 0 with all three checks passing, so the failure
is the `lseek` and nothing else about the environment. That is observed, not
argued.

M1 does **not** include: `platform-cdev`, `iio-temp`, the built-tree cache, the
version matrix, any GitHub Action, x86_64, machine-readable reports, or a driver
bound through a real device tree node.

The exit criterion for M1 was: a stranger clones the repository, runs one command,
and gets a truthful exit code together with a `dmesg` that explains it. That is
met. `drvlab run <dir>` does it, verified against a real guest: two drivers
differing in one function call produce exit 1 and exit 0, the console log says
which, and the whole cycle takes about 19 s once the kernel is cached.

Attribution: this project would not have been worth building if the answer to
"does it work" were already assumed. The `-dtb` dead end, the missing device
tree node, and the `lseek` failure were all found by running things, and every
one of them contradicted a plausible-looking plan.

**M2 — it is a tool (1–2 weeks).** The other two templates and the `i2c-stub`
strategy, ccache, the built-tree cache with eviction, `drvlab doctor`,
`drvlab cache`, per-template assertions, machine-readable reports.

**M3 — it is infrastructure.** `drvlab matrix`, the CI matrix, x86_64 (where
`isa-debug-exit` does work, which is a real payoff for keeping the arch
abstraction), virtiofs as an opt-in, and `drvlab bisect` to find the kernel
version at which a driver stopped working.

## Risks

Ranked by blast radius over cost to falsify. Each has a check under 30 minutes.

| Risk | Cheapest check | Fallback |
|---|---|---|
| ~~The dumped arm64 `virt` tree has no `/soc`~~ — **confirmed and fixed.** Measured: the virt root has 48 nodes directly off `/` and no `/soc`; drvforge's overlays targeted `&{/soc}`, so `fdtoverlay` exited 1 | resolved | fixed in drvforge `6bf4921`; drv-lab still preflights the target path against the dumped tree |
| ~~`0x09040000` collides with the machine's secure UART~~ — **checked, no collision.** On a plain `-M virt` only `0x9000000`, `0x9010000`, `0x9020000` and `0x9030000` are occupied | resolved | the address is only taken with `-machine virt,secure=on`, which drv-lab never passes; keep the check as a guard |
| `fdtoverlay` rejects an overlay that needs symbols absent from QEMU's dump | run `fdtoverlay` on one real overlay | drvforge's overlays are path-based and need no symbols; translate `FDT_ERR_NOTFOUND` into an actionable message |
| `security_model=none` misbehaves for a root guest | boot and write to the share | `security_model=mapped-xattr` |
| IIO config symbol names drift between 6.1 and 6.17 | grep `config IIO_` in `drivers/iio/Kconfig` per version | `olddefconfig` drops unknown symbols silently, so assert the built `.config` at build time |
| `poweroff -f` does not terminate QEMU on arm64 virt | part of M1a: boot and time it | `reboot -f` fallback in `/init`; the timeout catches the rest |
| A user's driver is OF-only and cannot bind to a sysfs-created client | grep it for `i2c_device_id` | report exit 4 with the exact remedy |
| Cold ccache makes the CI matrix too slow to keep | one run with cache-miss timings | if over 25 minutes, cache the built artifacts |
| Running from `/mnt/c` silently breaks builds | run the lab from `/mnt/c` and read the error | `drvlab doctor` refuses with an actionable message |
