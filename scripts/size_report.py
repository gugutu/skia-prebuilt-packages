#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


def collect(root: Path):
    rows = []
    total = 0
    for path in sorted(root.rglob("*.a")):
        size = path.stat().st_size
        total += size
        rows.append({
            "path": str(path.relative_to(root)),
            "bytes": size,
        })
    rows.sort(key=lambda row: row["bytes"], reverse=True)
    return total, rows


def mib(size: int) -> str:
    return f"{size / 1024 / 1024:.2f}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", required=True)
    parser.add_argument("--skia-tag", required=True)
    parser.add_argument("--gn-args", required=True)
    parser.add_argument("--raw-dir", required=True)
    parser.add_argument("--stripped-dir", required=True)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", required=True)
    args = parser.parse_args()

    raw_total, raw_rows = collect(Path(args.raw_dir))
    stripped_total, stripped_rows = collect(Path(args.stripped_dir))

    data = {
        "variant": args.variant,
        "skia_tag": args.skia_tag,
        "raw_total_bytes": raw_total,
        "stripped_total_bytes": stripped_total,
        "saved_bytes": raw_total - stripped_total,
        "raw_top": raw_rows[:30],
        "stripped_top": stripped_rows[:30],
        "gn_args": Path(args.gn_args).read_text().splitlines(),
    }

    Path(args.out_json).write_text(json.dumps(data, indent=2), encoding="utf-8")

    lines = [
        f"## {args.variant}",
        "",
        f"- Skia tag: `{args.skia_tag}`",
        f"- Raw static archives: **{mib(raw_total)} MiB**",
        f"- Stripped static archives: **{mib(stripped_total)} MiB**",
        f"- Strip saved: **{mib(raw_total - stripped_total)} MiB**",
        "",
        "### Top raw archives",
        "",
        "| Archive | MiB |",
        "|---|---:|",
    ]
    for row in raw_rows[:20]:
        lines.append(f"| `{row['path']}` | {mib(row['bytes'])} |")
    lines += [
        "",
        "### GN args",
        "",
        "```gn",
        *data["gn_args"],
        "```",
        "",
    ]
    Path(args.out_md).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
