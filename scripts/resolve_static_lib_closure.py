#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


SYMBOL_TYPE_RE = re.compile(r"^[A-Za-z?]$")


@dataclass(frozen=True)
class ArchiveSymbols:
    path: Path
    defined: frozenset[str]
    undefined: frozenset[str]


@dataclass(frozen=True)
class SymbolTool:
    path: str
    kind: str


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the static archive dependency closure needed by a root archive."
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--extension", required=True, choices=("a", "lib"))
    parser.add_argument("--root-package-name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    symbol_tool = find_symbol_tool()
    root = args.root.resolve()
    archives = discover_archives(args.candidate_root, args.extension, root)
    root_symbols = read_archive_symbols(symbol_tool, root)
    archive_symbols = [read_archive_symbols(symbol_tool, archive) for archive in archives]
    selected = resolve_closure(root_symbols, archive_symbols)
    output_entries = name_entries(selected, args.root_package_name)
    args.output.write_text(
        "".join(f"{entry.path}\t{entry.package_name}\n" for entry in output_entries),
        encoding="utf-8",
    )
    return 0


def find_symbol_tool() -> SymbolTool:
    for candidate in ("LLVM_NM", "NM"):
        configured = resolve_tool_from_env(candidate)
        if configured:
            return SymbolTool(configured, "nm")
    for candidate in ("llvm-nm", "llvm-nm.exe", "nm", "nm.exe"):
        found = shutil.which(candidate)
        if found:
            return SymbolTool(found, "nm")
    dumpbin = shutil.which("dumpbin") or shutil.which("dumpbin.exe")
    if dumpbin:
        return SymbolTool(dumpbin, "dumpbin")
    raise SystemExit("llvm-nm, nm, or dumpbin is required to resolve static library symbol closure")


def resolve_tool_from_env(name: str) -> str | None:
    value = os.environ.get(name)
    if not value:
        return None
    return shutil.which(value) or value


def discover_archives(candidate_root: Path, extension: str, root: Path) -> list[Path]:
    suffix = f".{extension}"
    archives: list[Path] = []
    for path in candidate_root.rglob(f"*{suffix}"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if resolved == root:
            continue
        archives.append(resolved)
    return sorted(dict.fromkeys(archives))


def read_archive_symbols(tool: SymbolTool, path: Path) -> ArchiveSymbols:
    if tool.kind == "dumpbin":
        return read_archive_symbols_with_dumpbin(tool.path, path)
    return read_archive_symbols_with_nm(tool.path, path)


def read_archive_symbols_with_nm(nm: str, path: Path) -> ArchiveSymbols:
    output = subprocess.run(
        [nm, "-g", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="ignore",
        check=False,
    ).stdout
    defined: set[str] = set()
    undefined: set[str] = set()
    for line in output.splitlines():
        parsed = parse_nm_line(line)
        if parsed is None:
            continue
        symbol_type, symbol = parsed
        if symbol_type.upper() == "U":
            undefined.add(symbol)
        elif symbol_type.upper() not in {"N", "I"}:
            defined.add(symbol)
    return ArchiveSymbols(path=path, defined=frozenset(defined), undefined=frozenset(undefined))


def read_archive_symbols_with_dumpbin(dumpbin: str, path: Path) -> ArchiveSymbols:
    output = subprocess.run(
        [dumpbin, "/symbols", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        errors="ignore",
        check=False,
    ).stdout
    defined: set[str] = set()
    undefined: set[str] = set()
    for line in output.splitlines():
        parsed = parse_dumpbin_line(line)
        if parsed is None:
            continue
        is_undefined, symbol = parsed
        if is_undefined:
            undefined.add(symbol)
        else:
            defined.add(symbol)
    return ArchiveSymbols(path=path, defined=frozenset(defined), undefined=frozenset(undefined))


def parse_nm_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.endswith(":"):
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return None
    if SYMBOL_TYPE_RE.match(parts[-2]):
        return parts[-2], parts[-1]
    if SYMBOL_TYPE_RE.match(parts[0]) and len(parts) >= 2:
        return parts[0], parts[-1]
    return None


def parse_dumpbin_line(line: str) -> tuple[bool, str] | None:
    if "|" not in line or "External" not in line:
        return None
    left, symbol = line.rsplit("|", 1)
    symbol = symbol.strip()
    if not symbol:
        return None
    return " UNDEF " in f" {left} ", symbol


def resolve_closure(
    root: ArchiveSymbols,
    candidates: list[ArchiveSymbols],
) -> list[ArchiveSymbols]:
    selected: list[ArchiveSymbols] = [root]
    remaining = candidates[:]
    provided = set(root.defined)
    unresolved = set(root.undefined) - provided

    while True:
        best_index = None
        best_score = 0
        for index, candidate in enumerate(remaining):
            score = len(candidate.defined & unresolved)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is None:
            break

        candidate = remaining.pop(best_index)
        selected.append(candidate)
        provided.update(candidate.defined)
        unresolved.update(candidate.undefined)
        unresolved.difference_update(provided)

    return selected


@dataclass(frozen=True)
class PackageEntry:
    path: Path
    package_name: str


def name_entries(selected: list[ArchiveSymbols], root_package_name: str) -> list[PackageEntry]:
    entries: list[PackageEntry] = []
    used_names = {root_package_name}
    for archive in selected[1:]:
        name = archive.path.name
        if name in used_names:
            stem = archive.path.stem
            suffix = archive.path.suffix
            digest = hashlib.sha256(str(archive.path).encode("utf-8")).hexdigest()[:12]
            name = f"{stem}_{digest}{suffix}"
        used_names.add(name)
        entries.append(PackageEntry(path=archive.path, package_name=name))
    return entries


if __name__ == "__main__":
    raise SystemExit(main())
