#!/usr/bin/env python3
"""Package the public C ABI for Skia's bundled HarfBuzz instance.

Skia links a private HarfBuzz build into the complete static archive, but its
normal public-header closure does not install HarfBuzz's C headers. Consumers
must use this exact instance rather than link a second HarfBuzz library, so the
headers are part of the prebuilt package contract.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path


HEADER_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')
GN_HEADER_RE = re.compile(r'"\$_src/([A-Za-z0-9_.+-]+\.(?:h|hh))"')

# Skia compiles the AAT implementation and Orch UI uses its public query API,
# but Skia's GN target does not list hb-aat.h in `public`.
ADDITIONAL_PUBLIC_HEADERS = {"hb-aat.h"}
REQUIRED_ENTRY_HEADERS = {"hb.h", "hb-aat.h", "hb-ot.h"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skia", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    return parser.parse_args()


def skia_public_headers(build_gn: Path) -> set[str]:
    text = build_gn.read_text(encoding="utf-8")
    marker = "public = ["
    start = text.find(marker)
    if start < 0:
        raise SystemExit(f"HarfBuzz public header list is missing from {build_gn}")
    end = text.find("\n    ]", start)
    if end < 0:
        raise SystemExit(f"HarfBuzz public header list is unterminated in {build_gn}")
    headers = set(GN_HEADER_RE.findall(text[start:end]))
    if not REQUIRED_ENTRY_HEADERS - (headers | ADDITIONAL_PUBLIC_HEADERS):
        return headers
    missing = sorted(REQUIRED_ENTRY_HEADERS - (headers | ADDITIONAL_PUBLIC_HEADERS))
    raise SystemExit(f"Skia HarfBuzz target no longer exposes: {', '.join(missing)}")


def local_includes(path: Path) -> set[str]:
    includes: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = HEADER_INCLUDE_RE.match(line)
        if match:
            includes.add(match.group(1))
    return includes


def main() -> int:
    args = parse_args()
    build_gn = args.skia / "third_party/harfbuzz/BUILD.gn"
    source = args.skia / "third_party/externals/harfbuzz/src"
    if not build_gn.is_file() or not source.is_dir():
        raise SystemExit("Skia's bundled HarfBuzz source is incomplete")

    roots = skia_public_headers(build_gn) | ADDITIONAL_PUBLIC_HEADERS
    queue = sorted(roots)
    packaged: set[str] = set()
    args.destination.mkdir(parents=True, exist_ok=True)

    while queue:
        name = queue.pop()
        if name in packaged:
            continue
        if (
            "/" in name
            or "\\" in name
            or (not name.startswith("hb-") and name != "hb.h")
        ):
            continue
        header = source / name
        if not header.is_file():
            raise SystemExit(f"HarfBuzz public header is missing: {header}")
        shutil.copy2(header, args.destination / name)
        packaged.add(name)
        for include in sorted(local_includes(header)):
            if (source / include).is_file() and include not in packaged:
                queue.append(include)

    missing = sorted(REQUIRED_ENTRY_HEADERS - packaged)
    if missing:
        raise SystemExit(f"packaged HarfBuzz ABI is incomplete: {', '.join(missing)}")
    print(f"Packaged {len(packaged)} HarfBuzz public headers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
