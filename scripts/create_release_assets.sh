#!/usr/bin/env bash
set -euo pipefail

artifact_root="${1:-work/release-artifacts}"
asset_dir="${2:-work/release-assets}"
tag="${SKIA_PACKAGE_TAG:-${GITHUB_REF_NAME:-local}}"
python_cmd="${PYTHON:-python3}"
if ! command -v "$python_cmd" >/dev/null 2>&1; then
  python_cmd="python"
fi
expected_targets="${SKIA_RELEASE_TARGETS:-macos-arm64 macos-x64 ios-arm64 ios-simulator-arm64 android-arm64 windows-x64 windows-arm64}"

if [[ ! -d "$artifact_root" ]]; then
  echo "artifact directory is missing: $artifact_root" >&2
  exit 1
fi

rm -rf "$asset_dir"
mkdir -p "$asset_dir"

"$python_cmd" - "$artifact_root" "$asset_dir" "$tag" "$expected_targets" <<'PY'
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile

artifact_root = Path(sys.argv[1])
asset_dir = Path(sys.argv[2])
tag = sys.argv[3]
expected_targets = set(sys.argv[4].split())

packages = []
seen_targets = set()
for metadata_path in sorted(artifact_root.rglob("metadata.json")):
    package_dir = metadata_path.parent
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    target = metadata["target"]
    if target in seen_targets:
        raise SystemExit(f"duplicate package target found: {target}")
    seen_targets.add(target)
    asset_base = f"{tag}-{target}"
    archive = asset_dir / f"{asset_base}.tar.zst"
    if shutil.which("zstd"):
        subprocess.run(
            f'tar -cf - -C "{package_dir}" . | zstd -T0 -q -o "{archive}"',
            shell=True,
            check=True,
        )
    else:
        fallback = asset_dir / f"{asset_base}.tar.gz"
        with tarfile.open(fallback, "w:gz") as tf:
            for path in sorted(package_dir.rglob("*")):
                tf.add(path, path.relative_to(package_dir))
        archive = fallback

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    packages.append(
        {
            "target": target,
            "asset": archive.name,
            "sha256": digest,
            "size": archive.stat().st_size,
            "metadata": metadata,
        }
    )

if not packages:
    raise SystemExit(f"no package metadata found under {artifact_root}")

missing_targets = sorted(expected_targets - seen_targets)
unexpected_targets = sorted(seen_targets - expected_targets)
if missing_targets or unexpected_targets:
    details = []
    if missing_targets:
        details.append(f"missing targets: {', '.join(missing_targets)}")
    if unexpected_targets:
        details.append(f"unexpected targets: {', '.join(unexpected_targets)}")
    raise SystemExit("; ".join(details))

manifest = {
    "schema_version": 1,
    "tag": tag,
    "skia_ref": packages[0]["metadata"]["skia_ref"],
    "skia_label": packages[0]["metadata"]["skia_label"],
    "skia_commit": packages[0]["metadata"]["skia_commit"],
    "packages": packages,
}
(asset_dir / "manifest.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

with (asset_dir / "checksums.txt").open("w", encoding="utf-8") as out:
    for package in packages:
        out.write(f"{package['sha256']}  {package['asset']}\n")
    for extra in ("manifest.json",):
        path = asset_dir / extra
        out.write(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {extra}\n")
PY

cat > "$asset_dir/release-notes.md" <<EOF
Skia prebuilt packages for $tag.

Assets:
- Per-target package archives contain \`include/\`, \`generated-include/\`, \`lib/\`, \`LICENSES/\`, \`metadata.json\`, \`manifest.txt\`, and \`gn_args.txt\`.
- \`manifest.json\` maps Rust targets to package assets and records link metadata.
- \`checksums.txt\` contains SHA-256 checksums for release assets.

Skia ref: ${SKIA_LABEL:-unknown}
EOF

ls -lh "$asset_dir"
