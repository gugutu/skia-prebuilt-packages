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
    parser.add_argument("--out", required=True)
    parser.add_argument("--lib-dir", required=True, type=Path)
    parser.add_argument("--extension", required=True)
    parser.add_argument("--target", action="append", default=[])
    args = parser.parse_args()

    args.lib_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    seen: set[Path] = set()

    for target in args.target:
        before = len(copied)
        for item in gn_desc_libs(args.gn, args.out, target):
            path = resolve_gn_lib_path(args.out, item)
            if not path.is_file() or path.suffix.lower() != f".{args.extension}":
                continue
            if path in seen:
                continue
            seen.add(path)
            destination = args.lib_dir / path.name
            shutil.copy2(path, destination)
            copied.append(destination.name)
        if len(copied) == before:
            raise SystemExit(f"{target} did not expose any .{args.extension} runtime archive")

    for name in copied:
        print(name)
    return 0


def gn_desc_libs(gn: str, out_dir: str, target: str) -> list[str]:
    output = subprocess.run(
        [gn, "desc", out_dir, target, "libs", "--format=json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
        check=True,
    ).stdout
    parsed = json.loads(output)
    libs = extract_libs(parsed)
    if libs is None:
        raise SystemExit(f"unexpected gn desc libs shape for {target}: {type(parsed).__name__}")
    return libs


def extract_libs(value: object) -> list[str] | None:
    if isinstance(value, list):
        return [str(item) for item in value]
    if not isinstance(value, dict):
        return None

    collected: list[str] = []
    if isinstance(value.get("libs"), list):
        collected.extend(str(item) for item in value["libs"])

    for key, item in value.items():
        if key == "libs":
            continue
        nested = extract_libs(item)
        if nested:
            collected.extend(nested)

    return collected


def resolve_gn_lib_path(out_dir: str, item: str) -> Path:
    path = Path(item)
    if not path.is_absolute():
        path = Path(out_dir) / path
    return path.resolve()


if __name__ == "__main__":
    raise SystemExit(main())
