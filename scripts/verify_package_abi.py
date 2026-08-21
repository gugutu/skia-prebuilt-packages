#!/usr/bin/env python3
"""Verify public headers and symbols promised by a packaged native ABI."""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
from pathlib import Path


HARFBUZZ_REQUIRED_SYMBOLS = {
    "hb_aat_layout_has_substitution",
    "hb_blob_create",
    "hb_buffer_add",
    "hb_buffer_add_utf16",
    "hb_buffer_create",
    "hb_buffer_destroy",
    "hb_buffer_get_glyph_infos",
    "hb_buffer_get_glyph_positions",
    "hb_buffer_guess_segment_properties",
    "hb_buffer_set_direction",
    "hb_buffer_set_language",
    "hb_buffer_set_script",
    "hb_face_create_for_tables",
    "hb_face_destroy",
    "hb_face_get_glyph_count",
    "hb_font_create",
    "hb_font_destroy",
    "hb_font_get_face",
    "hb_font_get_glyph_extents",
    "hb_font_get_h_extents",
    "hb_font_get_nominal_glyph",
    "hb_font_set_scale",
    "hb_font_set_variations",
    "hb_glyph_info_get_glyph_flags",
    "hb_language_from_string",
    "hb_ot_font_set_funcs",
    "hb_ot_layout_has_substitution",
    "hb_ot_metrics_get_position",
    "hb_script_from_iso15924_tag",
    "hb_shape",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True, type=Path)
    parser.add_argument("--harfbuzz-include", required=True, type=Path)
    parser.add_argument("--nm", default="nm")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def defined_symbols(nm: str, library: Path) -> set[str]:
    options = ["-gU"] if platform.system() == "Darwin" else ["-g", "--defined-only"]
    result = subprocess.run(
        [nm, *options, str(library)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    symbols: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if not fields:
            continue
        symbol = fields[-1]
        if symbol.startswith("_"):
            symbol = symbol[1:]
        symbols.add(symbol)
    return symbols


def main() -> int:
    args = parse_args()
    if not args.library.is_file():
        raise SystemExit(f"package library is missing: {args.library}")
    headers = sorted(args.harfbuzz_include.glob("*.h"))
    if not headers:
        raise SystemExit(f"HarfBuzz headers are missing: {args.harfbuzz_include}")
    header_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore") for path in headers
    )
    missing_declarations = sorted(
        symbol for symbol in HARFBUZZ_REQUIRED_SYMBOLS if symbol not in header_text
    )
    symbols = defined_symbols(args.nm, args.library)
    missing_definitions = sorted(HARFBUZZ_REQUIRED_SYMBOLS - symbols)
    if missing_declarations or missing_definitions:
        if missing_declarations:
            print("Missing HarfBuzz declarations:")
            print("\n".join(f"  {symbol}" for symbol in missing_declarations))
        if missing_definitions:
            print("Missing HarfBuzz definitions:")
            print("\n".join(f"  {symbol}" for symbol in missing_definitions))
        return 1

    manifest = {
        "schema_version": 1,
        "native_abis": {
            "harfbuzz": {
                "include_dir": "include/harfbuzz",
                "headers": [path.name for path in headers],
                "verified_symbols": sorted(HARFBUZZ_REQUIRED_SYMBOLS),
            }
        },
    }
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"Verified {len(headers)} HarfBuzz headers and "
        f"{len(HARFBUZZ_REQUIRED_SYMBOLS)} exported symbols"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
