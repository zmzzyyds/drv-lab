"""Kernel acquisition and build.

The pure half of this module — URLs, argv, cache keys, fragment handling — is
what the tests exercise. The half that touches the network and runs make is at
the bottom.

Two rules in here are load-bearing and were both learned by getting them wrong:

    ``CC=`` goes on the ``make`` command line, never in the environment.

The kernel Makefile assigns ``CC = $(CROSS_COMPILE)gcc``, and a makefile
assignment beats an exported variable, so ``export CC="ccache ..."`` is silently
ignored and the cache stays empty. The build still succeeds, just far slower.

    ``CONFIG_DEBUG_INFO`` cannot be cleared on its own.

Since 6.10 it is gated by a choice, so ``olddefconfig`` re-selects it unless the
choice is moved to ``DEBUG_INFO_NONE``. The fragment is checked afterwards for
exactly this reason.
"""

from __future__ import annotations

import hashlib
import os
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from drvlab import external

#: Where the release tarballs live. Measured at ~2 MB/s, and the China mirrors
#: tried did not carry this path at all.
MIRROR = "https://cdn.kernel.org/pub/linux/kernel"

CROSS_COMPILE_PREFIX = "aarch64-linux-gnu-"

#: Linux 6.3 gained ``virtconfig``: defconfig plus a fragment that turns every
#: SoC option off, for guests. It only ever subtracts, so our own fragment is
#: merged on top of it.
VIRTCONFIG_MIN_VERSION = (6, 3)

_FRAGMENT_SET = re.compile(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$")
_FRAGMENT_UNSET = re.compile(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$")

FRAGMENT_PATH = Path(__file__).resolve().parent / "fragments" / "drvlab.config"

#: Marks a tree as already configured from a particular fragment. Without it a
#: second run would reconfigure the tree, and a changed .config throws away
#: every compiled object.
CONFIG_STAMP = ".drvlab-config"


@dataclass(frozen=True)
class KernelSpec:
    """The kernel to build. One architecture for now."""

    version: str
    arch: str = "arm64"


@dataclass(frozen=True)
class Cache:
    """Where the expensive, rebuildable things live.

    Defaults under ``$XDG_CACHE_HOME/drvlab``. Nothing in here is precious: every
    entry can be produced again from the definitions above, which is why it is
    outside the repository.
    """

    root: Path

    @classmethod
    def default(cls) -> Cache:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        return cls(Path(base) / "drvlab")

    @property
    def tarballs(self) -> Path:
        return self.root / "tarballs"

    @property
    def sources(self) -> Path:
        return self.root / "src"

    @property
    def ccache(self) -> Path:
        return self.root / "ccache"

    @property
    def busybox(self) -> Path:
        return self.root / "busybox"

    def run_dir(self, name: str) -> Path:
        return self.root / "run" / name


# --------------------------------------------------------------------------
# pure: names, URLs, argv
# --------------------------------------------------------------------------


def _as_version_tuple(version: str) -> tuple[int, ...]:
    """Turn "6.12", "6.12.1" or "6.12-rc1" into a comparable tuple."""
    parts: list[int] = []
    for piece in version.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def kernel_tarball_url(version: str) -> str:
    """Return the download URL for a release tarball."""
    major = version.split(".")[0]
    return f"{MIRROR}/v{major}.x/linux-{version}.tar.xz"


def kernel_source_dirname(version: str) -> str:
    """Return the directory the tarball unpacks to."""
    return f"linux-{version}"


def supports_virtconfig(version: str) -> bool:
    """Whether this kernel has the upstream guest fragment."""
    return _as_version_tuple(version) >= VIRTCONFIG_MIN_VERSION


def compiler_wrapper(prefix: str = CROSS_COMPILE_PREFIX, *, ccache: bool = True) -> str:
    """Return the compiler command, optionally wrapped in ccache."""
    compiler = f"{prefix}gcc"
    return f"ccache {compiler}" if ccache else compiler


def build_kernel_argv(
    spec: KernelSpec,
    targets: list[str],
    *,
    jobs: int,
    ccache: bool = True,
    cross_compile: str = CROSS_COMPILE_PREFIX,
) -> list[str]:
    """Return the argv for one kernel build.

    ``targets`` come last so the fixed part of the command line reads the same
    in every invocation.
    """
    argv = [
        "make",
        f"-j{jobs}",
        f"ARCH={spec.arch}",
        f"CROSS_COMPILE={cross_compile}",
    ]
    if ccache:
        # As a make variable, on the command line. See the module docstring for
        # what happens if this is an exported environment variable instead.
        argv.append(f"CC={compiler_wrapper(cross_compile, ccache=True)}")
    argv.extend(targets)
    return argv


def ccache_env(cache: Cache) -> dict[str, str]:
    """The ccache settings a kernel build needs, as environment variables.

    ``CCACHE_NOHASHDIR`` is the interesting one. Without it ccache folds the
    build directory into its hash, so moving a tree between checkouts — which is
    what putting it in a cache means — turns every lookup into a miss.
    """
    return {
        "CCACHE_DIR": str(cache.ccache),
        "CCACHE_MAXSIZE": "2G",
        "CCACHE_COMPRESS": "true",
        "CCACHE_NOHASHDIR": "1",
    }


def kernel_image(source: Path, arch: str = "arm64") -> Path:
    """The bootable image a build produces."""
    return source / "arch" / arch / "boot" / "Image"


def module_symvers(source: Path) -> Path:
    """The symbol version dump an out-of-tree module build reads."""
    return source / "Module.symvers"


# --------------------------------------------------------------------------
# pure: the config fragment
# --------------------------------------------------------------------------


def fragment_directives(fragment: str) -> list[tuple[str, str | None]]:
    """Return ``(symbol, wanted)`` for each directive in a fragment.

    ``wanted`` is the value string, or ``None`` for ``# CONFIG_X is not set``.
    Comment lines are ignored, so a fragment can explain itself.
    """
    directives: list[tuple[str, str | None]] = []
    for raw in fragment.splitlines():
        line = raw.strip()
        if not line:
            continue
        unset = _FRAGMENT_UNSET.match(line)
        if unset:
            directives.append((unset.group(1), None))
            continue
        if line.startswith("#"):
            continue
        set_directive = _FRAGMENT_SET.match(line)
        if set_directive:
            directives.append((set_directive.group(1), set_directive.group(2)))
    return directives


def config_value(config: str, symbol: str) -> str | None:
    """Return a symbol's value in a ``.config``, or ``None`` if it is not set."""
    set_pattern = re.compile(rf"^{re.escape(symbol)}=(.*)$", flags=re.MULTILINE)
    match = set_pattern.search(config)
    return match.group(1) if match else None


def apply_fragment(config: str, fragment: str) -> str:
    """Return ``config`` with every directive in ``fragment`` applied.

    Only the two shapes Kconfig writes are handled, which is all a fragment may
    contain. Symbols that are absent are appended; the rest are replaced in
    place.
    """
    for symbol, wanted in fragment_directives(fragment):
        replacement = f"{symbol}={wanted}" if wanted is not None else f"# {symbol} is not set"
        set_pattern = re.compile(rf"^{re.escape(symbol)}=.*$", flags=re.MULTILINE)
        unset_pattern = re.compile(rf"^# {re.escape(symbol)} is not set$", flags=re.MULTILINE)
        if set_pattern.search(config):
            config = set_pattern.sub(replacement, config, count=1)
        elif unset_pattern.search(config):
            config = unset_pattern.sub(replacement, config, count=1)
        else:
            config = config + replacement + "\n"
    return config


def verify_fragment(config: str, fragment: str) -> list[str]:
    """Return the directives whose value did not survive configuration.

    A symbol that will not take is either a typo or a dependency that is not
    met. Both deserve to stop the build: the alternative is a kernel that
    compiles the module and then refuses to load it, which is a much worse thing
    to debug than a failed configure step.
    """
    mismatches: list[str] = []
    for symbol, wanted in fragment_directives(fragment):
        actual = config_value(config, symbol)
        if wanted is None and actual is None:
            continue
        if wanted is not None and actual == wanted:
            continue
        shown = actual if actual is not None else "not set"
        wanted_text = "not set" if wanted is None else wanted
        mismatches.append(f"{symbol}: wanted {wanted_text}, got {shown}")
    return mismatches


def kernel_cache_key(
    version: str,
    arch: str,
    fragment: str,
    compiler_version: str,
    ccache_version: str,
) -> str:
    """Return the cache key for a built kernel tree.

    Every input that changes the bytes of the result is in the key. A rebuilt
    tree that reused a stale key would silently test the wrong kernel, so the
    fragment and the toolchain versions are as load-bearing as the version.
    """
    digest = hashlib.sha256()
    for part in (version, arch, fragment, compiler_version, ccache_version):
        # Separated, so ("ab", "c") cannot collide with ("a", "bc").
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------


def fetch_tarball(cache: Cache, version: str) -> Path:
    """Download the release tarball once and return its path."""
    cache.tarballs.mkdir(parents=True, exist_ok=True)
    tarball = cache.tarballs / f"linux-{version}.tar.xz"
    if not tarball.exists():
        external.run_checked(
            ["curl", "-sSL", "--retry", "3", "-o", str(tarball), kernel_tarball_url(version)]
        )
    return tarball


def unpack_source(cache: Cache, tarball: Path, version: str) -> Path:
    """Extract the tarball if it has not been extracted already."""
    source = cache.sources / kernel_source_dirname(version)
    if source.is_dir():
        return source
    cache.sources.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:xz") as archive:
        archive.extractall(cache.sources)
    return source


def _fragment_stamp(fragment: str) -> str:
    return hashlib.sha256(fragment.encode("utf-8")).hexdigest()


def config_is_current(source: Path, fragment: str) -> bool:
    """Whether this tree already holds the configuration named by ``fragment``.

    Gestamped rather than inferred: a ``.config`` that happens to satisfy the
    fragment may still have come from a different base, and rebuilding on every
    run would throw away the whole object cache for nothing.
    """
    stamp = source / CONFIG_STAMP
    config_path = source / ".config"
    if not stamp.exists() or not config_path.exists():
        return False
    if stamp.read_text(encoding="utf-8").strip() != _fragment_stamp(fragment):
        return False
    return not verify_fragment(config_path.read_text(encoding="utf-8"), fragment)


def configure(
    source: Path,
    spec: KernelSpec,
    *,
    fragment_path: Path = FRAGMENT_PATH,
    cross_compile: str = CROSS_COMPILE_PREFIX,
    log_path: Path | None = None,
) -> None:
    """Produce the ``.config`` for a guest kernel.

    ``virtconfig`` where the kernel has it, then our fragment, then
    ``olddefconfig`` to settle dependencies — and then a check that the fragment
    actually took, because the failure mode otherwise is silent.
    """
    fragment = fragment_path.read_text(encoding="utf-8")
    if config_is_current(source, fragment):
        return

    base = ["make", f"ARCH={spec.arch}", f"CROSS_COMPILE={cross_compile}"]
    if supports_virtconfig(spec.version):
        external.run_checked(base + ["virtconfig"], cwd=source, log_path=log_path)
    else:
        external.run_checked(base + ["defconfig"], cwd=source, log_path=log_path)

    config_path = source / ".config"
    config_path.write_text(
        apply_fragment(config_path.read_text(encoding="utf-8"), fragment), encoding="utf-8"
    )
    external.run_checked(base + ["olddefconfig"], cwd=source, log_path=log_path)

    mismatches = verify_fragment(config_path.read_text(encoding="utf-8"), fragment)
    if mismatches:
        raise RuntimeError(
            "the config fragment did not survive olddefconfig:\n  " + "\n  ".join(mismatches)
        )
    (source / CONFIG_STAMP).write_text(_fragment_stamp(fragment) + "\n", encoding="utf-8")


def build(
    source: Path,
    spec: KernelSpec,
    *,
    jobs: int,
    ccache: bool = True,
    cross_compile: str = CROSS_COMPILE_PREFIX,
    cache: Cache | None = None,
    log_path: Path | None = None,
) -> Path:
    """Build the image and the symbol dump, and return the tree to build against.

    ``modules`` is not optional: ``Module.symvers`` is written only when
    KBUILD_MODULES is set, and an out-of-tree module build reads it.
    """
    argv = build_kernel_argv(
        spec,
        ["Image", "modules"],
        jobs=jobs,
        ccache=ccache,
        cross_compile=cross_compile,
    )
    external.run_checked(
        argv,
        cwd=source,
        env=ccache_env(cache) if cache is not None else None,
        log_path=log_path,
    )
    image = kernel_image(source, spec.arch)
    if not image.exists():
        raise RuntimeError(f"the build produced no {image}")
    return source
