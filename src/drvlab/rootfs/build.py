"""Building the guest initramfs: one busybox, our /init, and nothing else.

Three things in here were learned the hard way:

* **The applet list comes from the build.** ``include/applet_tables.h`` lists
  exactly the applets a build enabled and ``NUM_APPLETS`` says how many to
  expect. The hand-written list that was tried first omitted ``sort`` and
  ``head``, and a guest diagnostic died with ``sort: not found``.
* **``/init`` must be mode 0755.** The kernel refuses to exec it otherwise, and
  because the command line says ``rdinit=/init``, that failure looks like a
  missing initramfs rather than a permission problem.
* **busybox is built with ``ARCH=arm`` for aarch64.** busybox spells both 32-
  and 64-bit ARM that way; the toolchain decides which you get. Verified:
  building with ``aarch64-linux-gnu-gcc`` produces a 64-bit statically linked
  binary.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import tarfile
from pathlib import Path

from drvlab import external

BUSYBOX_VERSION = "1.36.1"
BUSYBOX_MIRROR = "https://busybox.net/downloads"

#: busybox's own name for the ARM architecture, 32- and 64-bit alike.
BUSYBOX_ARCH = "arm"

#: The kernel will not exec /init unless it is executable.
INIT_MODE = 0o755

APPLET_NAMES_MARKER = "const char applet_names[]"


# --------------------------------------------------------------------------
# pure
# --------------------------------------------------------------------------


def busybox_tarball_url(version: str = BUSYBOX_VERSION) -> str:
    return f"{BUSYBOX_MIRROR}/busybox-{version}.tar.bz2"


def busybox_source_dirname(version: str = BUSYBOX_VERSION) -> str:
    return f"busybox-{version}"


def busybox_make_argv(
    targets: list[str],
    *,
    cross_compile: str,
    jobs: int | None = None,
) -> list[str]:
    """Return a busybox build argv.

    ``ARCH`` and ``CROSS_COMPILE`` are passed as make variables rather than
    exported, for the same reason ``CC`` is: makefile assignments would win over
    the environment.
    """
    argv = ["make", f"ARCH={BUSYBOX_ARCH}", f"CROSS_COMPILE={cross_compile}"]
    if jobs is not None:
        argv.append(f"-j{jobs}")
    return argv + targets


def set_config_symbol(text: str, symbol: str, value: str = "y") -> str:
    """Set one symbol in a busybox ``.config``.

    busybox ships nothing like the kernel's ``scripts/config``, so this is a
    plain text edit covering the two shapes Kconfig writes.
    """
    unset = f"# {symbol} is not set"
    if unset in text:
        return text.replace(unset, f"{symbol}={value}")
    if re.search(rf"^{re.escape(symbol)}=", text, flags=re.MULTILINE):
        return re.sub(
            rf"^{re.escape(symbol)}=.*$", f"{symbol}={value}", text, flags=re.MULTILINE
        )
    return text + f"{symbol}={value}\n"


def parse_applets(applet_tables: str) -> list[str]:
    """Return the applets a busybox build enabled, sorted.

    Reads the generated ``include/applet_tables.h``. This is the authority on
    both the spelling and the set — the file is produced by the build, so it
    cannot disagree with the binary the way a hand-written list can.
    """
    start = applet_tables.find(APPLET_NAMES_MARKER)
    if start == -1:
        raise ValueError("applet_names not found; is this a generated applet_tables.h?")
    end = applet_tables.find(";", start)
    body = applet_tables[start : end if end != -1 else len(applet_tables)]
    # Quoted runs of name-like characters. Excluding whitespace as well as the
    # backslash matters: the strings are written as "cat" "\0" "head", and a
    # pattern that allowed whitespace would let the closing quote of one string
    # open a "match" that swallows the "\0" and the newline after it.
    return sorted(set(re.findall(r'"([^"\\\s]+)"', body)))


def parse_num_applets(applet_tables: str) -> int:
    """Return the applet count the build reports."""
    match = re.search(r"#define\s+NUM_APPLETS\s+(\d+)", applet_tables)
    if not match:
        raise ValueError("NUM_APPLETS not found")
    return int(match.group(1))


def initramfs_cache_key(busybox_sha256: str, init_sha256: str, arch: str) -> str:
    """Key the packed initramfs on the things that decide its bytes."""
    digest = hashlib.sha256()
    for part in (busybox_sha256, init_sha256, arch):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def initramfs_entries(root: Path) -> list[str]:
    """Return the paths to hand to cpio, with ``.`` first.

    Separators are POSIX on purpose: they go into an archive that a Linux kernel
    unpacks, so a backslash from a Windows host would become a literal character
    in a filename. ``as_posix`` is what makes that host-independent.

    cpio writes entries in the order it is given them, so the root has to come
    first or the archive holds files before the directory that contains them.
    """
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        relative = Path(dirpath).relative_to(root)
        found.extend((relative / name).as_posix() for name in dirnames)
        found.extend((relative / name).as_posix() for name in sorted(filenames))
    return ["."] + sorted(found)


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------


def init_script_path() -> Path:
    """The /init that ships with drv-lab."""
    return Path(__file__).resolve().parent / "assets" / "init"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_busybox(cache_dir: Path, *, version: str = BUSYBOX_VERSION) -> Path:
    """Download the busybox tarball once and return its path."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    tarball = cache_dir / f"busybox-{version}.tar.bz2"
    if tarball.exists():
        return tarball
    external.run_checked(
        ["curl", "-sSL", "--retry", "3", "-o", str(tarball), busybox_tarball_url(version)]
    )
    return tarball


def unpack_busybox(tarball: Path, cache_dir: Path, *, version: str = BUSYBOX_VERSION) -> Path:
    """Extract the tarball if it has not been extracted already."""
    source = cache_dir / busybox_source_dirname(version)
    if source.is_dir():
        return source
    with tarfile.open(tarball, "r:bz2") as archive:
        archive.extractall(cache_dir)
    return source


def build_busybox(
    source: Path,
    *,
    cross_compile: str,
    jobs: int | None = None,
    log_path: Path | None = None,
) -> Path:
    """Configure and build busybox, returning the binary.

    Static, because the guest has no libc to link against.
    """
    config = source / ".config"
    if not config.exists():
        external.run_checked(
            busybox_make_argv(["defconfig"], cross_compile=cross_compile), cwd=source
        )
        config.write_text(
            set_config_symbol(config.read_text(encoding="utf-8"), "CONFIG_STATIC", "y"),
            encoding="utf-8",
        )
        external.run_checked(
            busybox_make_argv(["oldconfig"], cross_compile=cross_compile),
            cwd=source,
            log_path=log_path,
        )

    external.run_checked(
        busybox_make_argv([], cross_compile=cross_compile, jobs=jobs),
        cwd=source,
        log_path=log_path,
    )
    binary = source / "busybox"
    if not binary.exists():
        raise RuntimeError(f"busybox did not produce {binary}")
    return binary


def applets_of(source: Path) -> list[str]:
    """Return the applets this busybox build enabled, cross-checked."""
    tables = (source / "include" / "applet_tables.h").read_text(encoding="utf-8")
    names = parse_applets(tables)
    expected = parse_num_applets(tables)
    if len(names) != expected:
        raise RuntimeError(
            f"extracted {len(names)} applets but the build reports {expected}; "
            "the extraction is wrong, not the build"
        )
    return names


def assemble_initramfs(root: Path, *, busybox: Path, init_script: Path, applets: list[str]) -> None:
    """Lay out the initramfs tree: busybox, one symlink per applet, and /init."""
    for name in ("bin", "proc", "sys", "dev", "tmp", "mnt/host"):
        (root / name).mkdir(parents=True, exist_ok=True)

    target = root / "bin" / "busybox"
    target.write_bytes(busybox.read_bytes())
    target.chmod(0o755)

    for applet in applets:
        link = root / "bin" / applet
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to("busybox")

    init = root / "init"
    init.write_bytes(init_script.read_bytes())
    # Without this the kernel refuses to exec it, and the failure reads like a
    # missing initramfs rather than a permission problem.
    init.chmod(INIT_MODE)


def pack_initramfs(root: Path, out: Path) -> Path:
    """Pack a laid-out tree into a gzipped newc cpio archive.

    The file list is handed to cpio on stdin so no shell is involved; gzip is
    done in Python for the same reason.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = "\n".join(initramfs_entries(root)) + "\n"
    archive = external.run_filter(["cpio", "-o", "-H", "newc", "--quiet"], listing, cwd=root)
    with gzip.open(out, "wb", compresslevel=9) as handle:
        handle.write(archive)
    return out
