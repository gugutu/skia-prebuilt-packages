#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gn", required=True)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lib-dir", required=True, type=Path)
    parser.add_argument("--extension", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-target", action="append", default=[])
    args = parser.parse_args()
    if not args.output_target:
        raise SystemExit("at least one --output-target is required")

    args.lib_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    manifest: list[dict[str, str]] = []
    seen: set[Path] = set()

    for target in args.output_target:
        before = len(copied)
        for item in gn_desc_property(args.gn, args.out, target, "outputs"):
            path = resolve_gn_output_path(args.source_root, args.out, item)
            if not path.is_file() or path.suffix.lower() != f".{args.extension}":
                continue
            if path in seen:
                continue
            seen.add(path)
            destination = args.lib_dir / path.name
            shutil.copy2(path, destination)
            copied.append(destination.name)
            manifest.append({"name": destination.name, "source": str(path)})
        if len(copied) == before:
            raise SystemExit(f"{target} did not output any .{args.extension} runtime archive")

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({"libraries": manifest}, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    for name in copied:
        print(name)
    return 0


def gn_desc_property(gn: str, out_dir: str, target: str, property_name: str) -> list[str]:
    output = subprocess.run(
        [gn, "desc", out_dir, target, property_name, "--format=json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
        check=True,
    ).stdout
    parsed = json.loads(output)
    values = extract_property(parsed, property_name)
    if values is None:
        raise SystemExit(f"unexpected gn desc {property_name} shape for {target}: {type(parsed).__name__}")
    return values


def extract_property(value: object, property_name: str) -> list[str] | None:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, dict):
        return None

    collected: list[str] = []
    if isinstance(value.get(property_name), list):
        collected.extend(str(item) for item in value[property_name])

    for key, item in value.items():
        if key == property_name:
            continue
        nested = extract_property(item, property_name)
        if nested:
            collected.extend(nested)

    return collected


def resolve_gn_output_path(source_root: Path, out_dir: str, item: str) -> Path:
    source_root = source_root.resolve()
    if item.startswith("//"):
        return (source_root / item[2:]).resolve()

    path = Path(item)
    if path.is_absolute():
        return path.resolve()

    out_path = Path(out_dir)
    out_parts = out_path.parts
    if path.parts[: len(out_parts)] == out_parts:
        return (source_root / path).resolve()

    if not path.is_absolute():
        return (source_root / out_path / path).resolve()

    return path.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
