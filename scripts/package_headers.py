#!/usr/bin/env python3
"""Complete and verify the public C++ header surface in a Skia package.

The package contract is the enabled Skia public API plus generated WebGPU
headers required by Graphite's Dawn backend. The script does not guess future
entry points one by one: it starts from the public header trees already copied
by build_package.sh, follows their include closure through the Skia checkout,
and then verifies that packaged headers no longer need the checkout.

Dawn/Tint implementation headers are copied only when they are reached from
that public closure. This avoids both failure modes: missing real public
dependencies, and accidentally publishing every Dawn/Tint development header as
part of this SDK.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

PACKAGE_INCLUDE_PREFIXES = (
    "include/",
    "modules/",
    "src/",
    "third_party/",
    "dawn/",
    "webgpu/",
)

TEXT_HEADER_SUFFIXES = {
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".inc",
    ".cc",
    ".cpp",
}

OPTIONAL_MISSING = {
    "SkUserConfig.h",
    "vulkan_sci.h",
}

CONSUMER_SMOKE_SOURCE = """
#include "include/core/SkCanvas.h"
#include "include/core/SkColorSpace.h"
#include "include/core/SkPaint.h"
#include "include/core/SkRRect.h"
#include "include/core/SkSurface.h"
#include "include/gpu/graphite/Context.h"
#include "include/gpu/graphite/Recorder.h"
#include "include/gpu/graphite/Recording.h"
#include "include/gpu/graphite/Surface.h"
#include "include/gpu/graphite/dawn/DawnBackendContext.h"
#include "include/gpu/graphite/dawn/DawnGraphiteTypes.h"
#include "modules/skparagraph/include/Paragraph.h"
#include "modules/skparagraph/include/ParagraphBuilder.h"
#include "modules/skparagraph/include/ParagraphStyle.h"
#include "modules/skshaper/include/SkShaper.h"
#include "modules/skunicode/include/SkUnicode.h"
#include "modules/svg/include/SkSVGDOM.h"
#include "webgpu/webgpu_cpp.h"
"""

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skia", required=True, type=Path)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--dawn-root", required=True, type=Path)
    parser.add_argument("--generated", required=True, type=Path)
    return parser.parse_args()


def is_package_include(include: str) -> bool:
    return include.startswith(PACKAGE_INCLUDE_PREFIXES)


def relative_to_or_none(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


def includes_in_text(text: str) -> list[str]:
    includes = []
    for line in text.splitlines():
        match = INCLUDE_RE.match(line)
        if match:
            includes.append(match.group(1))
    return includes


def includes_in_file(path: Path) -> list[str]:
    try:
        return includes_in_text(path.read_text(errors="ignore"))
    except OSError:
        return []


def source_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in TEXT_HEADER_SUFFIXES
    ]


class HeaderPackage:
    def __init__(self, skia: Path, sdk: Path, dawn_root: Path, generated: Path) -> None:
        self.skia = skia
        self.sdk = sdk
        self.dawn_root = dawn_root
        self.generated = generated
        self.package_roots = [root for root in (self.sdk, self.generated) if root.exists()]
        self.queue: list[Path] = []
        self.seen: set[Path] = set()

    def collect(self) -> None:
        self.copy_runtime_public_headers()

        self.queue = []
        for root in self.package_roots:
            self.queue.extend(source_files(root))
        self.seen = {path.resolve() for path in self.queue}

        smoke_path = self.sdk / "skia_prebuilt_consumer_smoke.cc"
        smoke_path.write_text(CONSUMER_SMOKE_SOURCE, encoding="utf-8")
        self.enqueue(smoke_path)
        for include in includes_in_text(CONSUMER_SMOKE_SOURCE):
            if not self.find_in_package(smoke_path, include):
                self.copy_checkout_header(smoke_path, include)

        self.follow_closure()

    def copy_runtime_public_headers(self) -> None:
        dawn_include = self.dawn_root / "include"
        if not dawn_include.is_dir():
            return
        for source in source_files(dawn_include):
            destination = self.generated / source.relative_to(dawn_include)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def verify(self) -> None:
        missing: list[tuple[Path, str]] = []
        for root in self.package_roots:
            for header in source_files(root):
                for include in includes_in_file(header):
                    if include in OPTIONAL_MISSING:
                        continue
                    if not self.find_in_package(header, include) and (
                        is_package_include(include) or self.find_in_checkout(header, include)
                    ):
                        missing.append((header, include))

        if missing:
            for header, include in missing[:80]:
                print(f"{header}: missing package include {include}")
            if len(missing) > 80:
                print(f"... and {len(missing) - 80} more")
            raise SystemExit(1)

    def follow_closure(self) -> None:
        while self.queue:
            header = self.queue.pop()
            for include in includes_in_file(header):
                if self.find_in_package(header, include):
                    continue
                if not is_package_include(include) and not self.find_in_checkout(header, include):
                    continue
                self.copy_checkout_header(header, include)

    def enqueue(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved in self.seen:
            return
        self.seen.add(resolved)
        if path.suffix.lower() in TEXT_HEADER_SUFFIXES:
            self.queue.append(path)

    def package_owner(self, current: Path) -> tuple[Path | None, Path | None]:
        for root in self.package_roots:
            relative = relative_to_or_none(current, root)
            if relative is not None:
                return root, relative
        return None, None

    def find_in_package(self, current: Path, include: str) -> Path | None:
        candidates: list[Path] = []
        if not include.startswith("/"):
            candidates.append(current.parent / include)
        candidates.extend(root / include for root in self.package_roots)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None

    def checkout_candidates(self, current: Path, include: str) -> list[tuple[Path, Path]]:
        include_path = Path(include)
        if include_path.is_absolute() or ".." in include_path.parts:
            return []

        owner, current_relative = self.package_owner(current)
        candidates: list[tuple[Path, Path]] = []

        if owner is not None and current_relative is not None:
            current_relative_include = current_relative.parent / include
            if owner == self.generated:
                candidates.append(
                    (self.generated / current_relative_include, self.generated / current_relative_include)
                )
                candidates.append(
                    (self.dawn_root / current_relative_include, self.generated / current_relative_include)
                )
                candidates.append(
                    (
                        self.dawn_root / "include" / current_relative_include,
                        self.generated / current_relative_include,
                    )
                )
            else:
                candidates.append(
                    (self.skia / current_relative_include, self.sdk / current_relative_include)
                )

        candidates.extend(
            [
                (self.skia / include, self.sdk / include_path),
                (self.generated / include, self.generated / include_path),
                (self.dawn_root / include, self.generated / include_path),
                (self.dawn_root / "include" / include, self.generated / include_path),
                (self.dawn_root / "src" / include, self.generated / include_path),
            ]
        )
        return candidates

    def find_in_checkout(self, current: Path, include: str) -> Path | None:
        for source, _ in self.checkout_candidates(current, include):
            if source.is_file():
                return source
        return None

    def copy_checkout_header(self, current: Path, include: str) -> bool:
        for source, destination in self.checkout_candidates(current, include):
            if source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                self.enqueue(destination)
                return True
        return False


def main() -> None:
    args = parse_args()
    package = HeaderPackage(
        skia=args.skia,
        sdk=args.sdk,
        dawn_root=args.dawn_root,
        generated=args.generated,
    )
    package.collect()
    package.verify()


if __name__ == "__main__":
    main()
