#!/usr/bin/env python3
import json
from pathlib import Path


def mib(size: int) -> str:
    return f"{size / 1024 / 1024:.2f}"


reports = []
for path in sorted(Path("reports").glob("*/size.json")):
    reports.append(json.loads(path.read_text()))

lines = [
    "# Skia Unicode Backend Size Compare",
    "",
    "| Variant | Skia tag | Raw static archives | Stripped static archives | Strip saved |",
    "|---|---|---:|---:|---:|",
]
for report in reports:
    lines.append(
        "| {variant} | `{skia_tag}` | {raw} MiB | {stripped} MiB | {saved} MiB |".format(
            variant=report["variant"],
            skia_tag=report["skia_tag"],
            raw=mib(report["raw_total_bytes"]),
            stripped=mib(report["stripped_total_bytes"]),
            saved=mib(report["saved_bytes"]),
        )
    )

lines.append("")
for report in reports:
    lines.extend([
        f"## {report['variant']} top raw archives",
        "",
        "| Archive | MiB |",
        "|---|---:|",
    ])
    for row in report["raw_top"][:12]:
        lines.append(f"| `{row['path']}` | {mib(row['bytes'])} |")
    lines.append("")

summary = "\n".join(lines)
Path("reports/summary.md").write_text(summary, encoding="utf-8")
print(summary)
