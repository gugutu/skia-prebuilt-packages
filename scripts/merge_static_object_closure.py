#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


ARCHIVE_MAGIC = b"!<arch>\n"
SYMBOL_TYPE_RE = re.compile(r"^[A-Za-z?]$")


@dataclass(frozen=True)
class SymbolTool:
    path: str
    kind: str


@dataclass(frozen=True)
class ObjectSymbols:
    archive: Path
    member_name: str
    index: int
    object_path: Path
    defined: frozenset[str]
    undefined: frozenset[str]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Append only the static object members needed to close a root archive's "
            "symbol dependencies."
        )
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--package-archive", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--extension", required=True, choices=("a", "lib"))
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    symbol_tool = find_symbol_tool()
    root = args.root.resolve()
    package_archive = args.package_archive.resolve()
    work_dir = args.work_dir.resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    root_objects = extract_archive_objects(root, work_dir / "root")
    candidate_objects: list[ObjectSymbols] = []
    for archive in discover_archives(args.candidate_root, args.extension, root):
        extracted = extract_archive_objects(archive, work_dir / "candidates" / archive_digest(archive))
        candidate_objects.extend(read_object_symbols(symbol_tool, extracted))

    selected, unresolved = resolve_object_closure(
        read_object_symbols(symbol_tool, root_objects),
        candidate_objects,
    )
    candidate_defined = set().union(*(item.defined for item in candidate_objects)) if candidate_objects else set()
    unresolved_from_candidates = unresolved & candidate_defined
    if selected:
        append_objects_to_archive(package_archive, [item.object_path for item in selected], args.extension)

    args.manifest.write_text(
        json.dumps(
            {
                "root": str(root),
                "package_archive": str(package_archive),
                "candidate_root": str(args.candidate_root.resolve()),
                "selected_object_count": len(selected),
                "selected_archives": selected_archive_counts(selected),
                "unresolved_symbol_count": len(unresolved),
                "unresolved_symbols_sample": sorted(unresolved)[:80],
                "unresolved_from_candidate_archive_count": len(unresolved_from_candidates),
                "unresolved_from_candidate_archive_sample": sorted(unresolved_from_candidates)[:80],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(work_dir)
    if unresolved_from_candidates:
        raise SystemExit(
            "static object closure missed symbols that are defined by candidate archives; "
            f"see {args.manifest}"
        )
    return 0


def find_symbol_tool() -> SymbolTool:
    for name in ("LLVM_NM", "NM"):
        configured = os.environ.get(name)
        if configured:
            return SymbolTool(shutil.which(configured) or configured, "nm")
    for candidate in ("llvm-nm", "llvm-nm.exe", "nm", "nm.exe"):
        found = shutil.which(candidate)
        if found:
            return SymbolTool(found, "nm")
    for candidate in ("dumpbin", "dumpbin.exe"):
        found = shutil.which(candidate)
        if found:
            return SymbolTool(found, "dumpbin")
    raise SystemExit("llvm-nm, nm, or dumpbin is required")


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


def archive_digest(path: Path) -> str:
    import hashlib

    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def extract_archive_objects(archive: Path, destination: Path) -> list[ObjectSymbols]:
    destination.mkdir(parents=True, exist_ok=True)
    objects: list[ObjectSymbols] = []
    string_table = b""
    with archive.open("rb") as file:
        if file.read(len(ARCHIVE_MAGIC)) != ARCHIVE_MAGIC:
            raise SystemExit(f"not a static archive: {archive}")

        index = 0
        while True:
            header = file.read(60)
            if not header:
                break
            if len(header) != 60 or header[58:60] != b"`\n":
                raise SystemExit(f"invalid archive member header in {archive}")
            raw_name = header[:16].decode("utf-8", "replace").strip()
            try:
                size = int(header[48:58].decode("ascii").strip() or "0")
            except ValueError as error:
                raise SystemExit(f"invalid archive member size in {archive}: {error}") from error
            body = file.read(size)
            if size & 1:
                file.read(1)

            if raw_name == "/":
                continue
            if raw_name == "//":
                string_table = body
                continue

            member_name, object_bytes = decode_member(raw_name, body, string_table)
            if not member_name or not object_bytes:
                continue

            object_path = destination / f"{index:06d}-{sanitize_member_name(member_name)}"
            object_path.write_bytes(object_bytes)
            objects.append(
                ObjectSymbols(
                    archive=archive.resolve(),
                    member_name=member_name,
                    index=index,
                    object_path=object_path,
                    defined=frozenset(),
                    undefined=frozenset(),
                )
            )
            index += 1
    return objects


def decode_member(raw_name: str, body: bytes, string_table: bytes) -> tuple[str, bytes]:
    if raw_name.startswith("#1/"):
        name_len = int(raw_name[3:])
        return body[:name_len].decode("utf-8", "replace"), body[name_len:]
    if raw_name.startswith("/") and raw_name[1:].split()[0].isdigit() and string_table:
        offset = int(raw_name[1:].split()[0])
        end = string_table.find(b"\n", offset)
        if end < 0:
            end = len(string_table)
        return string_table[offset:end].decode("utf-8", "replace").rstrip("/\x00"), body
    return raw_name.rstrip("/").strip(), body


def sanitize_member_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.+-]+", "_", name).strip("._")
    if not sanitized:
        sanitized = "object.o"
    return sanitized[:120]


def read_object_symbols(tool: SymbolTool, objects: list[ObjectSymbols]) -> list[ObjectSymbols]:
    return [replace_symbols(item, read_symbols(tool, item.object_path)) for item in objects]


def replace_symbols(item: ObjectSymbols, symbols: tuple[set[str], set[str]]) -> ObjectSymbols:
    defined, undefined = symbols
    return ObjectSymbols(
        archive=item.archive,
        member_name=item.member_name,
        index=item.index,
        object_path=item.object_path,
        defined=frozenset(defined),
        undefined=frozenset(undefined),
    )


def read_symbols(tool: SymbolTool, object_path: Path) -> tuple[set[str], set[str]]:
    if tool.kind == "dumpbin":
        return read_symbols_with_dumpbin(tool.path, object_path)
    return read_symbols_with_nm(tool.path, object_path)


def read_symbols_with_nm(nm: str, object_path: Path) -> tuple[set[str], set[str]]:
    output = subprocess.run(
        [nm, "-g", str(object_path)],
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
    return defined, undefined


def read_symbols_with_dumpbin(dumpbin: str, object_path: Path) -> tuple[set[str], set[str]]:
    output = subprocess.run(
        [dumpbin, "/symbols", str(object_path)],
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
    return defined, undefined


def parse_nm_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.endswith(":"):
        return None
    parts = stripped.split()
    if len(parts) < 2:
        return None
    if SYMBOL_TYPE_RE.match(parts[-2]):
        return parts[-2], parts[-1]
    if SYMBOL_TYPE_RE.match(parts[0]):
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


def resolve_object_closure(
    root_objects: list[ObjectSymbols],
    candidate_objects: list[ObjectSymbols],
) -> tuple[list[ObjectSymbols], set[str]]:
    selected: list[ObjectSymbols] = []
    remaining = candidate_objects[:]
    provided: set[str] = set()
    unresolved: set[str] = set()
    for item in root_objects:
        provided.update(item.defined)
        unresolved.update(item.undefined)
    unresolved.difference_update(provided)

    while True:
        best_index = None
        best_score = 0
        for index, item in enumerate(remaining):
            score = len(item.defined & unresolved)
            if score > best_score:
                best_index = index
                best_score = score
        if best_index is None:
            break
        item = remaining.pop(best_index)
        selected.append(item)
        provided.update(item.defined)
        unresolved.update(item.undefined)
        unresolved.difference_update(provided)
    return selected, unresolved


def selected_archive_counts(selected: list[ObjectSymbols]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in selected:
        key = str(item.archive)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def append_objects_to_archive(package_archive: Path, objects: list[Path], extension: str) -> None:
    if extension == "lib":
        append_objects_with_lib_exe(package_archive, objects)
    else:
        append_objects_with_ar(package_archive, objects)


def append_objects_with_ar(package_archive: Path, objects: list[Path]) -> None:
    ar = find_archiver()
    for chunk in chunks(objects, 80):
        subprocess.run([ar, "q", str(package_archive), *map(str, chunk)], check=True)
    ranlib = find_ranlib()
    if ranlib:
        subprocess.run([ranlib, str(package_archive)], check=True)


def append_objects_with_lib_exe(package_archive: Path, objects: list[Path]) -> None:
    lib = shutil.which("lib") or shutil.which("lib.exe")
    if not lib:
        raise SystemExit("lib.exe is required to update Windows static libraries")
    rsp = package_archive.with_suffix(".objects.rsp")
    tmp = package_archive.with_suffix(".tmp.lib")
    rsp.write_text(
        "\n".join([quote_for_response(package_archive), *map(quote_for_response, objects)]) + "\n",
        encoding="utf-8",
    )
    subprocess.run([lib, "/NOLOGO", f"/OUT:{tmp}", f"@{rsp}"], check=True)
    tmp.replace(package_archive)
    rsp.unlink(missing_ok=True)


def quote_for_response(path: Path) -> str:
    return f'"{path}"'


def find_archiver() -> str:
    configured = os.environ.get("AR")
    if configured:
        return shutil.which(configured) or configured
    for candidate in ("llvm-ar", "ar"):
        found = shutil.which(candidate)
        if found:
            return found
    raise SystemExit("llvm-ar or ar is required")


def find_ranlib() -> str | None:
    configured = os.environ.get("RANLIB")
    if configured:
        return shutil.which(configured) or configured
    for candidate in ("llvm-ranlib", "ranlib"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def chunks(items: list[Path], size: int) -> list[list[Path]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


if __name__ == "__main__":
    raise SystemExit(main())
