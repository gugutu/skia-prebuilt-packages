#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SKIA_PACKAGE_TARGET", "")
    if not target:
        raise SystemExit("SKIA_PACKAGE_TARGET or an explicit target argument is required")

    config_dir = root / "build-config"
    common = config_dir / "common.gn"
    target_config = config_dir / f"{target}.gn"
    if not common.is_file() or not target_config.is_file():
        raise SystemExit(f"missing build config for target {target}")

    args = parse_gn_args(common)
    args.update(parse_gn_args(target_config))
    if target == "android-arm64":
        args["ndk_api"] = os.environ.get("ANDROID_API_LEVEL", "29")

    payload = {
        "target": target,
        "gn_args": args,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    print(hashlib.sha256(encoded).hexdigest())
    return 0


def parse_gn_args(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = normalize_gn_line(raw_line)
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        result[key] = normalize_gn_value(value)
    return result


def normalize_gn_line(line: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    for char in line:
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            output.append(char)
            escaped = True
            continue
        if char == '"':
            output.append(char)
            in_string = not in_string
            continue
        if char == "#" and not in_string:
            break
        output.append(char)
    return "".join(output).strip()


def normalize_gn_value(value: str) -> str:
    output: list[str] = []
    in_string = False
    escaped = False
    for char in value.strip():
        if escaped:
            output.append(char)
            escaped = False
            continue
        if char == "\\" and in_string:
            output.append(char)
            escaped = True
            continue
        if char == '"':
            output.append(char)
            in_string = not in_string
            continue
        if char.isspace() and not in_string:
            continue
        output.append(char)
    return "".join(output)


if __name__ == "__main__":
    raise SystemExit(main())
