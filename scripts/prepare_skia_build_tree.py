#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path


PACKAGE_TARGET = "skia_prebuilt_package"
PACKAGE_MARKER_BEGIN = "# SKIA_PREBUILT_PACKAGE_BEGIN"
PACKAGE_MARKER_END = "# SKIA_PREBUILT_PACKAGE_END"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skia", required=True, type=Path)
    args = parser.parse_args()

    skia = args.skia.resolve()
    if not (skia / "BUILD.gn").is_file():
        raise SystemExit(f"Skia source tree is missing: {skia}")

    add_unified_static_package_target(skia)
    return 0


def add_unified_static_package_target(skia: Path) -> None:
    build_config = skia / "gn/BUILDCONFIG.gn"
    root_build = skia / "BUILD.gn"
    package_gn_dir = skia / "skia_prebuilt_package_gen"

    replace_once(
        build_config,
        marker='set_defaults("component") {\n  configs = default_configs\n}\n',
        needle='''set_defaults("component") {
  configs = default_configs
  if (!is_component_build) {
    complete_static_lib = true
  }
}
''',
        replacement='''set_defaults("component") {
  configs = default_configs
}
''',
        description="component static-library defaults",
    )

    text = root_build.read_text(encoding="utf-8")
    if PACKAGE_MARKER_BEGIN not in text:
        block = f'''
{PACKAGE_MARKER_BEGIN}
static_library("{PACKAGE_TARGET}") {{
  complete_static_lib = true
  sources = [ "skia_prebuilt_package_gen/empty.cpp" ]
  public_deps = [
    "//:skia",
    "//modules/skparagraph:skparagraph",
    "//modules/skresources:skresources",
    "//modules/skshaper:skshaper",
    "//modules/skunicode",
    "//modules/svg:svg",
  ]
}}
{PACKAGE_MARKER_END}
'''
        root_build.write_text(text.rstrip() + "\n" + block, encoding="utf-8")

    package_gn_dir.mkdir(parents=True, exist_ok=True)
    (package_gn_dir / "empty.cpp").write_text(
        "namespace skia_prebuilt_package {\nvoid anchor() {}\n}\n",
        encoding="utf-8",
    )


def replace_once(path: Path, *, marker: str, needle: str, replacement: str, description: str) -> None:
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    if needle not in text:
        raise SystemExit(f"could not find {description} in {path}")
    path.write_text(text.replace(needle, replacement), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
