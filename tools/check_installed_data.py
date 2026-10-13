#!/usr/bin/env python3
"""Check that an installed drv-lab ships the files it reads at runtime.

drvlab reads three data files while it runs: the guest's ``/init``, the platform
device injector's source, and the kernel config fragment. A checkout always
finds them, because they are simply there on disk whatever the packaging says —
so only an install can answer whether they travel with the distribution. A wheel
that omits them produces a tool that fails on the user's first run, with every
unit test still green.

Run it from outside the checkout so that ``import drvlab`` resolves to the
installed copy:

    cd "$(mktemp -d)" && python /path/to/tools/check_installed_data.py
"""

from __future__ import annotations

import pathlib
import sys

import drvlab

WANTED = (
    "rootfs/assets/init",
    "driver/assets/drvlab-injector.c",
    "kernel/fragments/drvlab.config",
)


def main() -> int:
    package = pathlib.Path(drvlab.__file__).parent
    missing = [name for name in WANTED if not (package / name).exists()]
    if missing:
        print(f"missing from the installed package at {package}:", file=sys.stderr)
        for name in missing:
            print(f"  {name}", file=sys.stderr)
        return 1
    print(f"all {len(WANTED)} runtime files are present in {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
