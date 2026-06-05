#!/usr/bin/env python3
from __future__ import annotations

import hashlib
from pathlib import Path


BEGIN = "# SKIA_OUT_CACHE_INPUT_BEGIN"
END = "# SKIA_OUT_CACHE_INPUT_END"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "scripts" / "build_package.sh"
    text = source.read_text(encoding="utf-8")
    blocks: list[str] = []
    offset = 0
    while True:
        begin = text.find(BEGIN, offset)
        if begin < 0:
            break
        content_begin = text.find("\n", begin)
        end = text.find(END, content_begin)
        if content_begin < 0 or end < 0:
            raise SystemExit(f"unclosed {BEGIN} block in {source}")
        blocks.append(text[content_begin + 1 : end])
        offset = end + len(END)
    if not blocks:
        raise SystemExit(f"missing {BEGIN} block in {source}")
    digest = hashlib.sha1("\n---skia-out-cache-block---\n".join(blocks).encode("utf-8")).hexdigest()
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
