#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


ARCHIVE_MAGIC = b"!<arch>\n"
THIN_ARCHIVE_MAGIC = b"!<thin>\n"
MACHO_FAT_MAGICS = {b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
SYMBOL_TYPE_RE = re.compile(r"^[A-Za-z?]$")


@dataclass(frozen=True)
class Toolchain:
    ar: str
    ranlib: str | None
    nm: str
    symbol_kind: str
    lib: str | None


@dataclass(frozen=True)
class ObjectSymbols:
    archive: Path
    member_name: str
    object_path: Path
    defined: frozenset[str]
    undefined: frozenset[str]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a compact static archive by selecting the object dependency closure."
    )
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--package-archive", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--extension", required=True, choices=("a", "lib"))
    parser.add_argument("--target-arch", required=True)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()

    start = time.monotonic()
    toolchain = find_toolchain(args.extension)
    work_dir = args.work_dir.resolve()
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    root = normalize_archive(args.root.resolve(), work_dir / "normalized" / "root", args.target_arch)
    root_objects = extract_archive_objects(
        toolchain,
        root,
        work_dir / "objects" / "root",
        args.extension,
    )
    candidate_objects: list[ObjectSymbols] = []
    for archive in discover_archives(args.candidate_root, args.extension, args.root.resolve()):
        normalized = normalize_archive(
            archive,
            work_dir / "normalized" / archive_digest(archive),
            args.target_arch,
        )
        extracted = extract_archive_objects(
            toolchain,
            normalized,
            work_dir / "objects" / "candidates" / archive_digest(archive),
            args.extension,
            original_archive=archive,
        )
        candidate_objects.extend(extracted)

    selected, unresolved = resolve_object_closure(root_objects, candidate_objects)
    candidate_defined = set().union(*(item.defined for item in candidate_objects)) if candidate_objects else set()
    reducer_missed = unresolved & candidate_defined
    write_package_archive(
        toolchain,
        args.package_archive.resolve(),
        [item.object_path for item in root_objects] + [item.object_path for item in selected],
        args.extension,
    )

    elapsed_ms = (time.monotonic() - start) * 1000.0
    args.manifest.write_text(
        json.dumps(
            {
                "root": str(args.root.resolve()),
                "package_archive": str(args.package_archive.resolve()),
                "candidate_root": str(args.candidate_root.resolve()),
                "root_object_count": len(root_objects),
                "candidate_object_count": len(candidate_objects),
                "selected_object_count": len(selected),
                "selected_archives": selected_archive_counts(selected),
                "unresolved_symbol_count": len(unresolved),
                "unresolved_symbols_sample": sorted(unresolved)[:80],
                "reducer_missed_candidate_symbol_count": len(reducer_missed),
                "reducer_missed_candidate_symbols_sample": sorted(reducer_missed)[:80],
                "elapsed_ms": round(elapsed_ms, 3),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    print(
        "object closure: "
        f"root={len(root_objects)} candidates={len(candidate_objects)} "
        f"selected={len(selected)} unresolved={len(unresolved)} "
        f"elapsed_ms={elapsed_ms:.1f}"
    )
    shutil.rmtree(work_dir)
    if reducer_missed:
        raise SystemExit(
            "object closure reducer left symbols unresolved even though candidate archives define them; "
            f"see {args.manifest}"
        )
    return 0


def find_toolchain(extension: str) -> Toolchain:
    ar = find_program_from_env("AR", ("llvm-ar", "ar"))
    ranlib = find_optional_program_from_env("RANLIB", ("llvm-ranlib", "ranlib"))
    nm = find_program_from_env("LLVM_NM", ("llvm-nm", "nm"))
    lib = None
    symbol_kind = "nm"
    if extension == "lib":
        lib = find_msvc_tool(("MSVC_LIB_EXE", "LIB_EXE"), ("lib.exe", "lib"))
        dumpbin = find_optional_msvc_tool(("MSVC_DUMPBIN_EXE", "DUMPBIN_EXE"), ("dumpbin.exe", "dumpbin"))
        if dumpbin:
            nm = dumpbin
            symbol_kind = "dumpbin"
    return Toolchain(ar=ar, ranlib=ranlib, nm=nm, symbol_kind=symbol_kind, lib=lib)


def find_program_from_env(env_name: str, candidates: tuple[str, ...]) -> str:
    configured = os.environ.get(env_name)
    if configured:
        return shutil.which(configured) or configured
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    joined = ", ".join(candidates)
    raise SystemExit(f"{env_name} or one of {joined} is required")


def find_optional_program_from_env(env_name: str, candidates: tuple[str, ...]) -> str | None:
    configured = os.environ.get(env_name)
    if configured:
        return shutil.which(configured) or configured
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def find_msvc_tool(env_names: tuple[str, ...], candidates: tuple[str, ...]) -> str:
    found = find_optional_msvc_tool(env_names, candidates)
    if found:
        return found
    joined_env = ", ".join(env_names)
    joined_candidates = ", ".join(candidates)
    raise SystemExit(f"{joined_env} or one of {joined_candidates} is required")


def find_optional_msvc_tool(env_names: tuple[str, ...], candidates: tuple[str, ...]) -> str | None:
    for env_name in env_names:
        configured = os.environ.get(env_name)
        if configured:
            return shutil.which(configured) or configured
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def normalize_archive(archive: Path, destination_dir: Path, target_arch: str) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as file:
        magic = file.read(8)
    if magic in (ARCHIVE_MAGIC, THIN_ARCHIVE_MAGIC):
        return archive
    if magic[:4] in MACHO_FAT_MAGICS:
        lipo = shutil.which("lipo")
        if not lipo:
            raise SystemExit(f"{archive} is a fat archive, but lipo is not available")
        output = destination_dir / archive.name
        subprocess.run(
            [lipo, str(archive), "-thin", target_arch, "-output", str(output)],
            check=True,
        )
        normalized_magic = output.read_bytes()[:8]
        if normalized_magic not in (ARCHIVE_MAGIC, THIN_ARCHIVE_MAGIC):
            raise SystemExit(
                f"lipo did not produce a static archive for {archive}; "
                f"magic={normalized_magic.hex()}"
            )
        return output
    raise SystemExit(f"not a static archive: {archive}; magic={magic.hex()}")


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


def extract_archive_objects(
    toolchain: Toolchain,
    archive: Path,
    destination: Path,
    extension: str,
    original_archive: Path | None = None,
) -> list[ObjectSymbols]:
    destination.mkdir(parents=True, exist_ok=True)
    if extension == "lib":
        object_paths = extract_lib_objects(toolchain, archive, destination)
    else:
        object_paths = extract_ar_objects(toolchain, archive, destination)
    symbols = read_object_symbols(toolchain, object_paths)
    archive_name = original_archive.resolve() if original_archive else archive.resolve()
    return [
        ObjectSymbols(
            archive=archive_name,
            member_name=path.name,
            object_path=path,
            defined=frozenset(defined),
            undefined=frozenset(undefined),
        )
        for path, defined, undefined in symbols
    ]


def extract_ar_objects(toolchain: Toolchain, archive: Path, destination: Path) -> list[Path]:
    subprocess.run(
        [toolchain.ar, "t", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
        check=True,
    )
    subprocess.run([toolchain.ar, "x", str(archive)], cwd=destination, check=True)
    return sorted(path for path in destination.rglob("*") if path.is_file() and is_object_member(path.name))


def extract_lib_objects(toolchain: Toolchain, archive: Path, destination: Path) -> list[Path]:
    if not toolchain.lib:
        raise SystemExit("lib.exe is required for Windows static libraries")
    output = subprocess.run(
        [toolchain.lib, "/NOLOGO", f"/LIST:{archive}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="ignore",
        check=True,
    ).stdout
    objects: list[Path] = []
    for index, member in enumerate(line.strip() for line in output.splitlines()):
        if not member or not is_object_member(member):
            continue
        destination_path = destination / f"{index:06d}-{sanitize_member_name(Path(member).name)}"
        subprocess.run(
            [
                toolchain.lib,
                "/NOLOGO",
                f"/EXTRACT:{member}",
                f"/OUT:{destination_path}",
                str(archive),
            ],
            check=True,
        )
        if destination_path.is_file():
            objects.append(destination_path)
    return objects


def is_object_member(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".o", ".obj"))


def sanitize_member_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.+-]+", "_", name).strip("._")
    return (sanitized or "object.o")[:120]


def read_object_symbols(toolchain: Toolchain, objects: list[Path]) -> list[tuple[Path, set[str], set[str]]]:
    if toolchain.symbol_kind == "dumpbin":
        return [(path, *read_symbols_with_dumpbin(toolchain.nm, path)) for path in objects]
    return read_object_symbols_with_nm(toolchain.nm, objects)


def read_object_symbols_with_nm(nm: str, objects: list[Path]) -> list[tuple[Path, set[str], set[str]]]:
    result: dict[Path, tuple[set[str], set[str]]] = {path: (set(), set()) for path in objects}
    for batch in chunks(objects, 200):
        output = subprocess.run(
            [nm, "-g", "-A", *map(str, batch)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            errors="ignore",
            check=False,
        ).stdout
        for line in output.splitlines():
            parsed = parse_nm_line(line)
            if parsed is None:
                continue
            path, symbol_type, symbol = parsed
            item = result.get(path)
            if item is None:
                continue
            defined, undefined = item
            if symbol_type.upper() == "U":
                undefined.add(symbol)
            elif symbol_type.upper() not in {"N", "I"}:
                defined.add(symbol)
    return [(path, defined, undefined) for path, (defined, undefined) in result.items()]


def parse_nm_line(line: str) -> tuple[Path, str, str] | None:
    if ":" not in line:
        return None
    path_text, rest = line.split(":", 1)
    parts = rest.strip().split()
    if len(parts) < 2:
        return None
    if SYMBOL_TYPE_RE.match(parts[-2]):
        return Path(path_text), parts[-2], parts[-1]
    if SYMBOL_TYPE_RE.match(parts[0]):
        return Path(path_text), parts[0], parts[-1]
    return None


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
    selected_paths: set[Path] = set()
    defined: set[str] = set()
    unresolved: set[str] = set()

    for item in root_objects:
        defined.update(item.defined)
        unresolved.update(item.undefined)
    unresolved.difference_update(defined)

    providers: dict[str, list[ObjectSymbols]] = {}
    for item in candidate_objects:
        for symbol in item.defined:
            providers.setdefault(symbol, []).append(item)

    queue = list(unresolved)
    while queue:
        symbol = queue.pop()
        if symbol in defined:
            continue
        provider = next((item for item in providers.get(symbol, []) if item.object_path not in selected_paths), None)
        if provider is None:
            continue
        selected.append(provider)
        selected_paths.add(provider.object_path)
        newly_defined = provider.defined - defined
        defined.update(provider.defined)
        for dependency in provider.undefined:
            if dependency not in defined:
                unresolved.add(dependency)
                queue.append(dependency)
        unresolved.difference_update(newly_defined)

    unresolved.difference_update(defined)
    return selected, unresolved


def selected_archive_counts(selected: list[ObjectSymbols]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in selected:
        key = str(item.archive)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def write_package_archive(
    toolchain: Toolchain,
    package_archive: Path,
    objects: list[Path],
    extension: str,
) -> None:
    if not objects:
        raise SystemExit("cannot write an empty static archive")
    package_archive.parent.mkdir(parents=True, exist_ok=True)
    if extension == "lib":
        write_package_archive_with_lib_exe(toolchain, package_archive, objects)
    else:
        write_package_archive_with_ar(toolchain, package_archive, objects)


def write_package_archive_with_ar(toolchain: Toolchain, package_archive: Path, objects: list[Path]) -> None:
    tmp = package_archive.with_suffix(package_archive.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    for index, batch in enumerate(chunks(objects, 200)):
        operation = "crs" if index == 0 else "rs"
        subprocess.run([toolchain.ar, operation, str(tmp), *map(str, batch)], check=True)
    if toolchain.ranlib:
        subprocess.run([toolchain.ranlib, str(tmp)], check=True)
    tmp.replace(package_archive)


def write_package_archive_with_lib_exe(toolchain: Toolchain, package_archive: Path, objects: list[Path]) -> None:
    if not toolchain.lib:
        raise SystemExit("lib.exe is required for Windows static libraries")
    rsp = package_archive.with_suffix(".objects.rsp")
    tmp = package_archive.with_suffix(".tmp.lib")
    rsp.write_text("\n".join(f'"{path}"' for path in objects) + "\n", encoding="utf-8")
    subprocess.run([toolchain.lib, "/NOLOGO", f"/OUT:{tmp}", f"@{rsp}"], check=True)
    tmp.replace(package_archive)
    rsp.unlink(missing_ok=True)


def chunks(items: list, size: int) -> list[list]:
    return [items[index : index + size] for index in range(0, len(items), size)]


if __name__ == "__main__":
    raise SystemExit(main())
