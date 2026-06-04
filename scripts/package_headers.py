#!/usr/bin/env python3
"""Collect and verify the C++ header surface exposed by a Skia package.

This package is consumed by a Rust/C++ bridge, not by Skia's own build graph.
The bridge needs Graphite, Dawn native initialization, SkParagraph, SkShaper,
SkUnicode, and SVG entry points. This script treats those entry points as the
package contract, copies the required source headers, follows Skia/Dawn quoted
and angle include closure, and verifies that the resulting package can satisfy
the same contract without reaching back into the checkout.
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
#include "dawn/dawn_proc.h"
#include "dawn/native/DawnNative.h"
#include "webgpu/webgpu_cpp.h"
"""

INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skia", required=True, type=Path)
    parser.add_argument("--sdk", required=True, type=Path)
    parser.add_argument("--dawn-include", required=True, type=Path)
    parser.add_argument("--generated", required=True, type=Path)
    return parser.parse_args()


def is_package_include(include: str) -> bool:
    return include.startswith(PACKAGE_INCLUDE_PREFIXES)


def relative_to_or_none(path: Path, root: Path) -> Path | None:
    try:
        return path.relative_to(root)
    except ValueError:
        return None


class HeaderPackage:
    def __init__(self, skia: Path, sdk: Path, dawn_include: Path, generated: Path) -> None:
        self.skia = skia
        self.sdk = sdk
        self.dawn_include = dawn_include
        self.generated = generated
        self.dawn_sdk = sdk / "third_party/externals/dawn/include"
        self.package_roots = [
            root for root in (self.sdk, self.dawn_sdk, self.generated) if root.exists()
        ]
        self.queue: list[Path] = []
        self.seen: set[Path] = set()

    def collect(self) -> None:
        self.copy_dawn_public_include_tree()
        self.package_roots = [
            root for root in (self.sdk, self.dawn_sdk, self.generated) if root.exists()
        ]
        self.queue = []
        for root in self.package_roots:
            self.queue.extend(root.rglob("*.h"))
        self.seen = {path.resolve() for path in self.queue}

        smoke_path = self.sdk / "skia_prebuilt_consumer_smoke.cc"
        smoke_path.write_text(CONSUMER_SMOKE_SOURCE, encoding="utf-8")
        self.queue.append(smoke_path)
        self.seen.add(smoke_path.resolve())
        for include in includes_in_text(CONSUMER_SMOKE_SOURCE):
            if not self.find_in_package(smoke_path, include):
                self.copy_checkout_header(smoke_path, include)

        self.follow_closure()

    def verify(self) -> None:
        missing: list[tuple[Path, str]] = []
        for root in self.package_roots:
            headers = list(root.rglob("*.h")) + list(root.rglob("*.cc"))
            for header in headers:
                for include in includes_in_file(header):
                    if include in OPTIONAL_MISSING or not is_package_include(include):
                        continue
                    if not self.find_in_package(header, include):
                        missing.append((header, include))

        if missing:
            for header, include in missing[:80]:
                print(f"{header}: missing package include {include}")
            if len(missing) > 80:
                print(f"... and {len(missing) - 80} more")
            raise SystemExit(1)

    def copy_dawn_public_include_tree(self) -> None:
        if not self.dawn_include.is_dir():
            return
        self.dawn_sdk.mkdir(parents=True, exist_ok=True)
        for source in self.dawn_include.rglob("*"):
            if not source.is_file():
                continue
            destination = self.dawn_sdk / source.relative_to(self.dawn_include)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def follow_closure(self) -> None:
        while self.queue:
            header = self.queue.pop()
            for include in includes_in_file(header):
                if self.find_in_package(header, include):
                    continue
                if not is_package_include(include):
                    continue
                self.copy_checkout_header(header, include)

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

    def checkout_header(self, current: Path, include: str) -> tuple[Path, Path] | None:
        include_path = Path(include)
        if include_path.is_absolute() or ".." in include_path.parts:
            return None

        owner, current_relative = self.package_owner(current)
        candidates: list[tuple[Path, Path]] = [(self.skia / include, self.sdk / include_path)]
        if owner is not None and current_relative is not None:
            current_relative_include = current_relative.parent / include
            if owner in (self.dawn_sdk, self.generated) and self.dawn_include.is_dir():
                candidates.append(
                    (
                        self.dawn_include / current_relative_include,
                        self.dawn_sdk / current_relative_include,
                    )
                )
            else:
                candidates.append(
                    (
                        self.skia / current_relative_include,
                        self.sdk / current_relative_include,
                    )
                )

        if self.dawn_include.is_dir():
            candidates.append((self.dawn_include / include, self.dawn_sdk / include_path))

        for source, destination in candidates:
            if source.is_file():
                return source, destination
        return None

    def copy_checkout_header(self, current: Path, include: str) -> bool:
        resolved = self.checkout_header(current, include)
        if resolved is None:
            return False
        source, destination = resolved
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination_resolved = destination.resolve()
        if destination_resolved not in self.seen:
            self.seen.add(destination_resolved)
            self.queue.append(destination)
        return True


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


def main() -> None:
    args = parse_args()
    package = HeaderPackage(
        skia=args.skia,
        sdk=args.sdk,
        dawn_include=args.dawn_include,
        generated=args.generated,
    )
    package.collect()
    package.verify()


if __name__ == "__main__":
    main()
