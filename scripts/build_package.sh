#!/usr/bin/env bash
set -euo pipefail

mode="${1:-all}"
skia_ref="${SKIA_REF:-refs/heads/chrome/m153}"
skia_label="${SKIA_LABEL:-chrome/m153}"
package_tag="${SKIA_PACKAGE_TAG:-}"
if [[ -z "$package_tag" && "${GITHUB_REF_TYPE:-}" == "tag" ]]; then
  package_tag="${GITHUB_REF_NAME:-}"
fi
package_target="${SKIA_PACKAGE_TARGET:-macos-arm64}"
python_cmd="${PYTHON:-python3}"
if ! command -v "$python_cmd" >/dev/null 2>&1; then
  python_cmd="python"
fi

case "$mode" in
  all | build | package) ;;
  *)
    echo "unknown mode: $mode" >&2
    exit 2
    ;;
esac

root="$(pwd)"
skia="${SKIA_SOURCE_DIR:-$root/work/skia-source}"
out_dir="out/$package_target"
package_dir="$root/work/package/$package_target"
lib_dir="$package_dir/lib"
include_dir="$package_dir/include"
harfbuzz_include_dir="$include_dir/harfbuzz"
license_dir="$package_dir/LICENSES"
abi_manifest="$package_dir/abi.json"
args_file="$package_dir/gn_args.txt"
lib_ext="a"
package_lib="$lib_dir/lib_skia.a"
package_library_target="skia_prebuilt_package"

if [[ ! -f "$skia/BUILD.gn" ]]; then
  echo "Skia source tree is missing at $skia" >&2
  echo "Run scripts/prepare_source.sh first, or set SKIA_SOURCE_DIR to a prepared tree." >&2
  exit 1
fi

mkdir -p "$package_dir"
"$python_cmd" "$root/scripts/prepare_skia_build_tree.py" --skia "$skia"

config_dir="$root/build-config"
target_config="$config_dir/$package_target.gn"
if [[ ! -f "$config_dir/common.gn" || ! -f "$target_config" ]]; then
  echo "missing GN build config for $package_target" >&2
  exit 1
fi

case "$package_target" in
  macos-arm64)
    ;;
  macos-x64)
    ;;
  ios-arm64)
    ;;
  ios-simulator-arm64)
    ;;
  android-arm64)
    android_ndk="${ANDROID_NDK:-${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}}"
    if [[ -z "$android_ndk" || ! -d "$android_ndk" ]]; then
      echo "ANDROID_NDK, ANDROID_NDK_HOME, or ANDROID_NDK_ROOT must point to an Android NDK." >&2
      exit 1
    fi
    android_toolchain=""
    while IFS= read -r toolchain_bin; do
      android_toolchain="$(dirname "$toolchain_bin")"
      break
    done < <(find "$android_ndk/toolchains/llvm/prebuilt" -path "*/bin/llvm-ar" -type f | sort)
    if [[ -z "$android_toolchain" ]]; then
      echo "Could not find llvm-ar under Android NDK: $android_ndk" >&2
      exit 1
    fi
    export AR="$android_toolchain/llvm-ar"
    export RANLIB="$android_toolchain/llvm-ranlib"
    export LLVM_NM="$android_toolchain/llvm-nm"
    ;;
  windows-x64)
    lib_ext="lib"
    package_lib="$lib_dir/skia.lib"
    ;;
  windows-arm64)
    lib_ext="lib"
    package_lib="$lib_dir/skia.lib"
    ;;
  *)
    echo "unknown SKIA_PACKAGE_TARGET: $package_target" >&2
    exit 2
    ;;
esac

{
  cat "$config_dir/common.gn"
  printf '\n'
  cat "$target_config"
  if [[ "$package_target" == "android-arm64" ]]; then
    printf '\nndk="%s"\n' "$android_ndk"
    printf 'ndk_api=%s\n' "${ANDROID_API_LEVEL:-29}"
  fi
} > "$args_file"

cd "$skia"

gn_bin="bin/gn"
if [[ -x "bin/gn.exe" ]]; then
  gn_bin="bin/gn.exe"
fi
ninja_bin="third_party/ninja/ninja"
if [[ -x "third_party/ninja/ninja.exe" ]]; then
  ninja_bin="third_party/ninja/ninja.exe"
fi

if [[ "$mode" != "package" ]]; then
  echo "::group::gn gen"
  "$gn_bin" gen "$out_dir" --args="$(tr '\n' ' ' < "$args_file")"
  echo "::endgroup::"

  echo "::group::ninja"
  export NINJA_STATUS="${NINJA_STATUS:-[%r active %f/%t %es] }"
  "$ninja_bin" -C "$out_dir" ":$package_library_target"
  echo "::endgroup::"
fi

if [[ "$mode" == "build" ]]; then
  exit 0
fi

rm -rf "$lib_dir" "$include_dir" "$license_dir" "$abi_manifest"
mkdir -p "$lib_dir" "$include_dir" "$license_dir"

echo "::group::package static library"

package_lib_candidates=()
while IFS= read -r lib; do
  package_lib_candidates+=("$lib")
done < <(find "$out_dir" -maxdepth 4 -type f \( \
  -name "lib${package_library_target}.$lib_ext" -o \
  -name "${package_library_target}.$lib_ext" \
\) | sort)

if [[ "${#package_lib_candidates[@]}" -ne 1 ]]; then
  printf '%s\n' "${package_lib_candidates[@]}"
  echo "expected exactly one package static library for $package_library_target" >&2
  exit 1
fi

cp "${package_lib_candidates[0]}" "$package_lib"

if [[ ! -f "$package_lib" ]]; then
  echo "package library was not generated: $package_lib" >&2
  exit 1
fi

echo "::endgroup::"

echo "::group::collect public headers"
mkdir -p "$include_dir/skia/include"
cp -R "$skia/include/." "$include_dir/skia/include/"
mkdir -p "$include_dir/skia/modules"
for module in skparagraph skresources skshaper skunicode svg; do
  module_include="$skia/modules/$module/include"
  [[ -d "$module_include" ]] || continue
  mkdir -p "$include_dir/skia/modules/$module/include"
  cp -R "$module_include/." "$include_dir/skia/modules/$module/include/"
done

echo "::endgroup::"

echo "::group::complete and verify package headers"
"$python_cmd" "$root/scripts/package_headers.py" \
  --skia "$skia" \
  --sdk "$include_dir/skia"
echo "::endgroup::"

echo "::group::package bundled HarfBuzz C ABI"
"$python_cmd" "$root/scripts/package_harfbuzz_headers.py" \
  --skia "$skia" \
  --destination "$harfbuzz_include_dir"
nm_cmd="${LLVM_NM:-nm}"
"$python_cmd" "$root/scripts/verify_package_abi.py" \
  --library "$package_lib" \
  --harfbuzz-include "$harfbuzz_include_dir" \
  --nm "$nm_cmd" \
  --output "$abi_manifest"
echo "::endgroup::"

{
  echo "skia_ref=$skia_ref"
  echo "skia_label=$skia_label"
  echo "skia_commit=$(git -C "$skia" rev-parse HEAD)"
  echo "target=$package_target"
  echo "library=lib/$(basename "$package_lib")"
  echo "include_path=include/skia"
  echo "include_paths=include/skia,include/harfbuzz"
  echo "abi_manifest=abi.json"
} > "$package_dir/manifest.txt"

echo "::group::collect licenses"
"$python_cmd" - "$skia" "$license_dir" <<'PY'
from pathlib import Path
import json
import shutil
import sys

skia = Path(sys.argv[1])
license_dir = Path(sys.argv[2])
license_dir.mkdir(parents=True, exist_ok=True)

roots = [
    ("skia", skia),
    ("dng_sdk", skia / "third_party/externals/dng_sdk"),
    ("expat", skia / "third_party/externals/expat"),
    ("freetype2", skia / "third_party/externals/freetype2"),
    ("harfbuzz", skia / "third_party/externals/harfbuzz"),
    ("libgrapheme", skia / "third_party/externals/libgrapheme"),
    ("libjpeg-turbo", skia / "third_party/externals/libjpeg-turbo"),
    ("libpng", skia / "third_party/externals/libpng"),
    ("libwebp", skia / "third_party/externals/libwebp"),
    ("piex", skia / "third_party/externals/piex"),
    ("wuffs", skia / "third_party/externals/wuffs"),
    ("zlib", skia / "third_party/externals/zlib"),
]

license_names = {
    "license",
    "license.md",
    "license.txt",
    "copying",
    "copying.md",
    "copying.txt",
    "copyright",
    "copyright.md",
    "copyright.txt",
    "notice",
    "notice.md",
    "notice.txt",
    "patents",
}

copied = []
for label, root in roots:
    if not root.exists():
        continue
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name not in license_names and not name.startswith(("license.", "copying.", "copyright.", "notice.")):
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = Path(path.name)
        destination = license_dir / label / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied.append(
            {
                "component": label,
                "source": str(path.relative_to(skia)),
                "package_path": str(destination.relative_to(license_dir)),
            }
        )

readme = license_dir / "README.md"
readme.write_text(
    "# License files\n\n"
    "This package bundles Skia and selected third-party dependencies into a "
    "static library. License, notice, copying, copyright, and patent files "
    "found in the bundled source components are copied here. The exact Skia "
    "revision and build configuration are recorded in `../metadata.json` and "
    "`../gn_args.txt`.\n",
    encoding="utf-8",
)
(license_dir / "manifest.json").write_text(
    json.dumps({"files": copied}, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
echo "::endgroup::"

echo "::group::write package metadata"
"$python_cmd" - \
  "$package_dir/metadata.json" \
  "$args_file" \
  "$package_target" \
  "$skia_ref" \
  "$skia_label" \
  "$package_tag" \
  "$(git -C "$skia" rev-parse HEAD)" \
  "$(basename "$package_lib")" <<'PY'
from pathlib import Path
import json
import sys

metadata_path = Path(sys.argv[1])
args_file = Path(sys.argv[2])
target = sys.argv[3]
skia_ref = sys.argv[4]
skia_label = sys.argv[5]
package_tag = sys.argv[6]
skia_commit = sys.argv[7]
libraries = [sys.argv[8]]

gn_args = {}
for line in args_file.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    gn_args[key.strip()] = value.strip()

target_link = {
    "macos-arm64": {
        "rust_target": "aarch64-apple-darwin",
        "frameworks": ["AppKit", "CoreFoundation", "CoreGraphics", "CoreText", "Foundation", "IOSurface", "IOKit", "Metal", "QuartzCore"],
        "system_libs": ["c++"],
        "min_os": "macos11",
    },
    "macos-x64": {
        "rust_target": "x86_64-apple-darwin",
        "frameworks": ["AppKit", "CoreFoundation", "CoreGraphics", "CoreText", "Foundation", "IOSurface", "IOKit", "Metal", "QuartzCore"],
        "system_libs": ["c++"],
        "min_os": "macos11",
    },
    "ios-arm64": {
        "rust_target": "aarch64-apple-ios",
        "frameworks": ["CoreFoundation", "CoreGraphics", "CoreText", "Foundation", "IOSurface", "IOKit", "Metal", "QuartzCore", "UIKit"],
        "system_libs": ["c++"],
        "min_os": "ios15",
    },
    "ios-simulator-arm64": {
        "rust_target": "aarch64-apple-ios-sim",
        "frameworks": ["CoreFoundation", "CoreGraphics", "CoreText", "Foundation", "IOSurface", "IOKit", "Metal", "QuartzCore", "UIKit"],
        "system_libs": ["c++"],
        "min_os": "ios15",
    },
    "android-arm64": {
        "rust_target": "aarch64-linux-android",
        "frameworks": [],
        "system_libs": ["android", "c++_static", "log", "vulkan"],
        "min_os": "android29",
    },
    "windows-x64": {
        "rust_target": "x86_64-pc-windows-msvc",
        "frameworks": [],
        "system_libs": ["dwrite", "gdi32", "ole32", "user32", "vulkan-1", "windowscodecs"],
        "min_os": "windows10",
    },
    "windows-arm64": {
        "rust_target": "aarch64-pc-windows-msvc",
        "frameworks": [],
        "system_libs": ["dwrite", "gdi32", "ole32", "user32", "vulkan-1", "windowscodecs"],
        "min_os": "windows10",
    },
}

if target not in target_link:
    raise SystemExit(f"missing metadata target mapping: {target}")

metadata = {
    "schema_version": 2,
    "target": target,
    "skia_ref": skia_ref,
    "skia_label": skia_label,
    "package_tag": package_tag or None,
    "skia_commit": skia_commit,
    "library_kind": "static",
    "libraries": [f"lib/{library}" for library in libraries],
    "include_dirs": [
        "include/skia",
        "include/harfbuzz",
    ],
    "cxx_standard": "c++20",
    "backend": {
        "graphite": True,
        "dawn": False,
        "ganesh": False,
    },
    "features": {
        "skparagraph": True,
        "skshaper": True,
        "harfbuzz": {
            "bundled": True,
            "public_c_headers": True,
            "abi_manifest": "abi.json",
        },
        "skunicode": "libgrapheme",
        "svg": True,
        "codec_png": True,
        "codec_jpeg": True,
        "codec_webp": True,
        "codec_raw": gn_args.get("skia_use_dng_sdk") == "true",
        "pdf": False,
        "skottie": False,
        "icu": False,
        "icu4x": False,
    },
    "link": target_link[target],
    "gn_args_file": "gn_args.txt",
    "license_dir": "LICENSES",
    "gn_args": gn_args,
}

metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
echo "::endgroup::"

ls -lh "$lib_dir"
