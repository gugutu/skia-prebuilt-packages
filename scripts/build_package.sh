#!/usr/bin/env bash
set -euo pipefail

mode="${1:-all}"
skia_ref="${SKIA_REF:-refs/heads/chrome/m150}"
skia_label="${SKIA_LABEL:-chrome/m150}"
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
generated_include_dir="$package_dir/generated-include"
args_file="$package_dir/gn_args.txt"
lib_ext="a"
combined_lib="$lib_dir/lib_skia.a"
package_library_target="ui_tree_skia_prebuilt"

if [[ ! -f "$skia/BUILD.gn" ]]; then
  echo "Skia source tree is missing at $skia" >&2
  echo "Run scripts/prepare_source.sh first, or set SKIA_SOURCE_DIR to a prepared tree." >&2
  exit 1
fi

patch_dawn_cmake_helpers() {
  local cmake_utils="$skia/third_party/dawn/cmake_utils.py"
  local build_dawn="$skia/third_party/dawn/build_dawn.py"

  if [[ -f "$cmake_utils" ]] && ! grep -q 'if os == "ios":' "$cmake_utils"; then
    "$python_cmd" - "$cmake_utils" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
needle = '''  if os == "mac":
    target_cpu_map = {
      "arm64": "arm64",
      "x64": "x86_64",
    }
    return "Darwin", target_cpu_map[cpu]

'''
insert = needle + '''  if os == "ios":
    target_cpu_map = {
      "arm64": "arm64",
      "x64": "x86_64",
    }
    return "iOS", target_cpu_map[cpu]

'''
if needle not in text:
    raise SystemExit("could not find Dawn mac OS mapping")
path.write_text(text.replace(needle, insert))
PY
  fi

  if [[ -f "$cmake_utils" ]] && ! grep -q 'SKIA_DAWN_WINDOWS_HOST_TOOL_CPU' "$cmake_utils"; then
    "$python_cmd" - "$cmake_utils" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
needle = '''  # Explicitly tell CMake where to find the Resource Compiler, Manifest Tool, and Archiver.
  rc_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             args.target_cpu, "rc.exe")
  win_cfgs.append(f"-DCMAKE_RC_COMPILER={rc_exe_path.replace(os.sep, '/')}")
  mt_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             args.target_cpu, "mt.exe")
  win_cfgs.append(f"-DCMAKE_MT={mt_exe_path.replace(os.sep, '/')}")

'''
insert = '''  # Explicitly tell CMake where to find the Resource Compiler, Manifest Tool, and Archiver.
  # rc.exe and mt.exe run on the host during configure/link steps, so ARM64
  # cross-builds from x64 runners must keep using x64 host tools.
  host_tool_cpu = os.environ.get("SKIA_DAWN_WINDOWS_HOST_TOOL_CPU", args.target_cpu)
  rc_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             host_tool_cpu, "rc.exe")
  win_cfgs.append(f"-DCMAKE_RC_COMPILER={rc_exe_path.replace(os.sep, '/')}")
  mt_exe_path = os.path.join(args.win_sdk, "bin", args.win_sdk_version,
                             host_tool_cpu, "mt.exe")
  win_cfgs.append(f"-DCMAKE_MT={mt_exe_path.replace(os.sep, '/')}")

'''
if needle not in text:
    raise SystemExit("could not find Dawn Windows rc/mt block")
path.write_text(text.replace(needle, insert))
PY
  fi

  if [[ -f "$build_dawn" ]] && ! grep -q 'SKIA_DAWN_IOS_SYSROOT' "$build_dawn"; then
    "$python_cmd" - "$build_dawn" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
needle = '''  if target_os == "Darwin" or target_os == "iOS":
    configure_cmd.append(f"-DCMAKE_OSX_ARCHITECTURES={target_cpu}")

'''
insert = '''  if target_os == "Darwin" or target_os == "iOS":
    configure_cmd.append(f"-DCMAKE_OSX_ARCHITECTURES={target_cpu}")

  if target_os == "iOS":
    ios_sysroot = os.environ.get("SKIA_DAWN_IOS_SYSROOT")
    if ios_sysroot:
      configure_cmd.append(f"-DCMAKE_OSX_SYSROOT={ios_sysroot}")
    ios_deployment_target = os.environ.get("SKIA_DAWN_IOS_DEPLOYMENT_TARGET")
    if ios_deployment_target:
      configure_cmd.append(f"-DCMAKE_OSX_DEPLOYMENT_TARGET={ios_deployment_target}")

'''
if needle not in text:
    raise SystemExit("could not find Dawn Apple CMake block")
path.write_text(text.replace(needle, insert))
PY
  fi

  if [[ -f "$build_dawn" ]] && ! grep -q 'TINT_BUILD_CMD_TOOLS=OFF' "$build_dawn"; then
    "$python_cmd" - "$build_dawn" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
needle = '''      "-DTINT_ENABLE_INSTALL=OFF",
'''
insert = '''      "-DTINT_ENABLE_INSTALL=OFF",
      "-DTINT_BUILD_CMD_TOOLS=OFF",
'''
if needle not in text:
    raise SystemExit("could not find Dawn Tint install option")
path.write_text(text.replace(needle, insert))
PY
  fi
}

prepare_unified_static_package_target() {
  local build_config="$skia/gn/BUILDCONFIG.gn"
  local package_gn_dir="$skia/ui_tree_package"

  "$python_cmd" - "$build_config" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text()
needle = '''set_defaults("component") {
  configs = default_configs
  if (!is_component_build) {
    complete_static_lib = true
  }
}
'''
replacement = '''set_defaults("component") {
  configs = default_configs
}
'''
if needle in text:
    path.write_text(text.replace(needle, replacement))
elif replacement not in text:
    raise SystemExit("could not update component static-library defaults")
PY

  mkdir -p "$package_gn_dir"
  cat > "$package_gn_dir/BUILD.gn" <<'GN'
import("../gn/skia.gni")

static_library("ui_tree_skia_prebuilt") {
  complete_static_lib = true
  sources = [ "empty.cpp" ]
  public_deps = [
    "//:skia",
    "//modules/skparagraph:skparagraph",
    "//modules/skresources:skresources",
    "//modules/skshaper:skshaper",
    "//modules/skunicode",
    "//modules/svg:svg",
  ]
}
GN
  cat > "$package_gn_dir/empty.cpp" <<'CPP'
namespace ui_tree_skia_prebuilt {
void anchor() {}
}
CPP
}

mkdir -p "$package_dir"
patch_dawn_cmake_helpers
prepare_unified_static_package_target

target_args="$(mktemp)"
case "$package_target" in
  macos-arm64)
    cat > "$target_args" <<'ARGS'
target_os="mac"
target_cpu="arm64"
skia_use_fonthost_mac=true
skia_use_freetype=false
skia_use_fontconfig=false
dawn_enable_metal=true
dawn_enable_vulkan=false
dawn_enable_d3d12=false
ARGS
    ;;
  macos-x64)
    cat > "$target_args" <<'ARGS'
target_os="mac"
target_cpu="x64"
skia_use_fonthost_mac=true
skia_use_freetype=false
skia_use_fontconfig=false
dawn_enable_metal=true
dawn_enable_vulkan=false
dawn_enable_d3d12=false
ARGS
    ;;
  ios-arm64)
    SKIA_DAWN_IOS_SYSROOT="$(xcrun --sdk iphoneos --show-sdk-path)"
    export SKIA_DAWN_IOS_SYSROOT
    export SKIA_DAWN_IOS_DEPLOYMENT_TARGET="15.0"
    cat > "$target_args" <<'ARGS'
target_os="ios"
target_cpu="arm64"
ios_min_target="15.0"
skia_use_fonthost_mac=true
skia_use_freetype=false
skia_use_fontconfig=false
dawn_enable_metal=true
dawn_enable_vulkan=false
dawn_enable_d3d12=false
ARGS
    ;;
  ios-simulator-arm64)
    SKIA_DAWN_IOS_SYSROOT="$(xcrun --sdk iphonesimulator --show-sdk-path)"
    export SKIA_DAWN_IOS_SYSROOT
    export SKIA_DAWN_IOS_DEPLOYMENT_TARGET="15.0"
    cat > "$target_args" <<'ARGS'
target_os="ios"
target_cpu="arm64"
ios_use_simulator=true
ios_min_target="15.0"
skia_use_fonthost_mac=true
skia_use_freetype=false
skia_use_fontconfig=false
dawn_enable_metal=true
dawn_enable_vulkan=false
dawn_enable_d3d12=false
ARGS
    ;;
  android-arm64)
    android_ndk="${ANDROID_NDK:-${ANDROID_NDK_HOME:-${ANDROID_NDK_ROOT:-}}}"
    if [[ -z "$android_ndk" || ! -d "$android_ndk" ]]; then
      echo "ANDROID_NDK, ANDROID_NDK_HOME, or ANDROID_NDK_ROOT must point to an Android NDK." >&2
      exit 1
    fi
    cat > "$target_args" <<ARGS
target_os="android"
target_cpu="arm64"
ndk="$android_ndk"
ndk_api=${ANDROID_API_LEVEL:-29}
skia_use_fonthost_mac=false
skia_use_freetype=true
skia_use_system_freetype2=false
skia_use_freetype_woff2=false
skia_use_freetype_svg=true
skia_use_freetype_zlib=true
skia_use_freetype_zlib_bundled=true
skia_use_fontconfig=false
skia_enable_fontmgr_android=true
skia_enable_fontmgr_android_ndk=false
dawn_enable_metal=false
dawn_enable_vulkan=true
dawn_enable_d3d12=false
extra_cflags=["-mno-outline-atomics"]
ARGS
    ;;
  windows-x64)
    export SKIA_DAWN_WINDOWS_HOST_TOOL_CPU="x64"
    lib_ext="lib"
    combined_lib="$lib_dir/skia.lib"
    cat > "$target_args" <<'ARGS'
target_os="win"
target_cpu="x64"
is_trivial_abi=false
skia_use_fonthost_mac=false
skia_use_freetype=false
skia_use_fontconfig=false
skia_enable_fontmgr_win=true
skia_enable_fontmgr_win_gdi=true
dawn_enable_metal=false
dawn_enable_vulkan=false
dawn_enable_d3d12=true
ARGS
    ;;
  windows-arm64)
    export SKIA_DAWN_WINDOWS_HOST_TOOL_CPU="x64"
    lib_ext="lib"
    combined_lib="$lib_dir/skia.lib"
    cat > "$target_args" <<'ARGS'
target_os="win"
target_cpu="arm64"
is_trivial_abi=false
skia_use_fonthost_mac=false
skia_use_freetype=false
skia_use_fontconfig=false
skia_enable_fontmgr_win=true
skia_enable_fontmgr_win_gdi=true
dawn_enable_metal=false
dawn_enable_vulkan=false
dawn_enable_d3d12=true
ARGS
    ;;
  *)
    echo "unknown SKIA_PACKAGE_TARGET: $package_target" >&2
    exit 2
    ;;
esac

cat > "$args_file" <<'ARGS'
is_debug=false
is_official_build=true
is_component_build=false

skia_enable_graphite=true
skia_use_dawn=true
skia_enable_ganesh=false
skia_use_gl=false
skia_use_metal=false
skia_use_vulkan=false
skia_use_direct3d=false
skia_use_angle=false

skia_use_icu=false
skia_use_system_icu=false
skia_use_runtime_icu=false
skia_use_client_icu=false
skia_use_bidi=true
skia_use_libgrapheme=true
skia_use_icu4x=false

skia_use_harfbuzz=true
skia_use_system_harfbuzz=false
skia_enable_skshaper=true
skia_enable_skparagraph=true

skia_enable_svg=true
skia_use_expat=true
skia_use_system_expat=false
skia_enable_skottie=false

skia_enable_pdf=false
skia_enable_tools=false
skia_enable_gpu_debug_layers=false

skia_use_libpng_decode=true
skia_use_libpng_encode=true
skia_use_system_libpng=false
skia_use_libjpeg_turbo_decode=true
skia_use_libjpeg_turbo_encode=true
skia_use_system_libjpeg_turbo=false
skia_use_libwebp_decode=true
skia_use_libwebp_encode=true
skia_use_system_libwebp=false
skia_use_wuffs=true
skia_use_zlib=true
skia_use_system_zlib=false
ARGS
cat "$target_args" >> "$args_file"
rm -f "$target_args"

"$python_cmd" - "$args_file" "$package_target" <<'PY'
from pathlib import Path
import sys

args_file = Path(sys.argv[1])
target = sys.argv[2]
args = {}
for line in args_file.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    args[key.strip()] = value.strip()

bundled_third_party = {
    "skia_use_system_icu": "false",
    "skia_use_system_harfbuzz": "false",
    "skia_use_system_expat": "false",
    "skia_use_system_libpng": "false",
    "skia_use_system_libjpeg_turbo": "false",
    "skia_use_system_libwebp": "false",
    "skia_use_system_zlib": "false",
}
missing_bundled = [
    f"{key}={expected}"
    for key, expected in bundled_third_party.items()
    if args.get(key) != expected
]
if missing_bundled:
    joined = ", ".join(missing_bundled)
    raise SystemExit(f"{target}: bundled third-party config is incomplete: {joined}")

graphite_only_backend = {
    "skia_enable_graphite": "true",
    "skia_use_dawn": "true",
    "skia_enable_ganesh": "false",
    "skia_use_gl": "false",
    "skia_use_metal": "false",
    "skia_use_vulkan": "false",
    "skia_use_direct3d": "false",
}
missing_backend = [
    f"{key}={expected}"
    for key, expected in graphite_only_backend.items()
    if args.get(key) != expected
]
if missing_backend:
    joined = ", ".join(missing_backend)
    raise SystemExit(f"{target}: expected Graphite+Dawn-only backend config: {joined}")

if args.get("skia_use_freetype") == "true":
    required = {
        "skia_use_system_freetype2": "false",
        "skia_use_freetype_woff2": "false",
        "skia_use_freetype_svg": "true",
        "skia_use_freetype_zlib": "true",
        "skia_use_freetype_zlib_bundled": "true",
    }
    missing = [
        f"{key}={expected}"
        for key, expected in required.items()
        if args.get(key) != expected
    ]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"{target}: bundled FreeType config is incomplete: {joined}")
PY

cd "$skia"

if [[ "$mode" != "package" ]]; then
  echo "::group::gn gen"
  gn_bin="bin/gn"
  if [[ -x "bin/gn.exe" ]]; then
    gn_bin="bin/gn.exe"
  fi
  "$gn_bin" gen "$out_dir" --args="$(tr '\n' ' ' < "$args_file")"
  echo "::endgroup::"

  echo "::group::ninja"
  ninja_bin="third_party/ninja/ninja"
  if [[ -x "third_party/ninja/ninja.exe" ]]; then
    ninja_bin="third_party/ninja/ninja.exe"
  fi
  "$ninja_bin" -C "$out_dir" "ui_tree_package:$package_library_target"
  echo "::endgroup::"
fi

if [[ "$mode" == "build" ]]; then
  exit 0
fi

rm -rf "$lib_dir" "$include_dir" "$generated_include_dir"
mkdir -p "$lib_dir" "$include_dir" "$generated_include_dir"

echo "::group::copy package static library"
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

cp "${package_lib_candidates[0]}" "$combined_lib"
if [[ "$package_target" != windows-* ]]; then
  ranlib "$combined_lib"
fi
echo "::endgroup::"

echo "::group::collect public headers"
mkdir -p "$include_dir/skia/include"
cp -R "$skia/include/." "$include_dir/skia/include/"
mkdir -p "$include_dir/skia/modules" "$include_dir/skia/third_party/externals/dawn/include"
for module in skparagraph skresources skshaper skunicode svg; do
  module_include="$skia/modules/$module/include"
  [[ -d "$module_include" ]] || continue
  mkdir -p "$include_dir/skia/modules/$module/include"
  cp -R "$module_include/." "$include_dir/skia/modules/$module/include/"
done

echo "::endgroup::"

echo "::group::generate dawn headers"
if [[ -f "$skia/third_party/externals/dawn/generator/dawn_json_generator.py" ]]; then
  (
    cd "$skia/third_party/externals/dawn"
    PYTHONPATH="$skia/third_party/externals" "$python_cmd" generator/dawn_json_generator.py \
      --dawn-json src/dawn/dawn.json \
      --targets headers,cpp_headers \
      --template-dir generator/templates \
      --output-dir "$generated_include_dir"
  )
fi
echo "::endgroup::"

echo "::group::complete header closure"
"$python_cmd" - \
  "$skia" \
  "$include_dir/skia" \
  "$skia/third_party/externals/dawn/include" \
  "$generated_include_dir/include" <<'PY'
from pathlib import Path
import re
import shutil
import sys

skia = Path(sys.argv[1])
sdk = Path(sys.argv[2])
dawn_include = Path(sys.argv[3])
generated = Path(sys.argv[4])
dawn_sdk = sdk / "third_party/externals/dawn/include"
include_re = re.compile(r'^\s*#\s*include\s+"([^"]+)"')
package_roots = [root for root in (sdk, dawn_sdk, generated) if root.exists()]
queue = []
for root in package_roots:
    queue.extend(root.rglob("*.h"))
seen = {path.resolve() for path in queue}

def relative_to_or_none(path, root):
    try:
        return path.relative_to(root)
    except ValueError:
        return None

def package_owner(current):
    for root in package_roots:
        relative = relative_to_or_none(current, root)
        if relative is not None:
            return root, relative
    return None, None

def find_in_package(current, include):
    candidates = []
    if not include.startswith("/"):
        candidates.append(current.parent / include)
    candidates.extend(root / include for root in package_roots)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None

def checkout_header(current, include):
    path = Path(include)
    if path.is_absolute() or ".." in path.parts:
        return None

    owner, current_relative = package_owner(current)
    candidates = [
        (skia / include, sdk / path),
    ]
    if owner is not None:
        current_relative_include = current_relative.parent / include
        if owner == dawn_sdk and dawn_include.is_dir():
            candidates.append((
                dawn_include / current_relative_include,
                dawn_sdk / current_relative_include,
            ))
        elif owner == generated and dawn_include.is_dir():
            candidates.append((
                dawn_include / current_relative_include,
                dawn_sdk / current_relative_include,
            ))
        else:
            candidates.append((
                skia / current_relative_include,
                sdk / current_relative_include,
            ))

    if dawn_include.is_dir():
        candidates.append((dawn_include / include, dawn_sdk / path))

    for source, destination in candidates:
        if source.is_file():
            return source, destination
    return None

while queue:
    header = queue.pop()
    try:
        lines = header.read_text(errors="ignore").splitlines()
    except OSError:
        continue
    for line in lines:
        match = include_re.match(line)
        if not match:
            continue
        include = match.group(1)
        if find_in_package(header, include) is not None:
            continue

        # Follow the quoted-include closure from the public package entries,
        # including generated Dawn headers. This keeps the package
        # self-contained without copying unrelated entry points such as Tint.
        resolved = checkout_header(header, include)
        if resolved is not None:
            source, destination = resolved
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            destination_resolved = destination.resolve()
            if destination_resolved not in seen:
                seen.add(destination_resolved)
                queue.append(destination)
PY
echo "::endgroup::"

echo "::group::verify header closure"
"$python_cmd" - \
  "$include_dir/skia" \
  "$include_dir/skia/third_party/externals/dawn/include" \
  "$generated_include_dir/include" <<'PY'
from pathlib import Path
import re
import sys

roots = [Path(arg) for arg in sys.argv[1:] if Path(arg).exists()]
include_re = re.compile(r'^\s*#\s*include\s+"([^"]+)"')
optional_missing = {
    "SkUserConfig.h",
    "vulkan_sci.h",
}
missing = []

def find_header(current: Path, include: str) -> bool:
    candidates = []
    if not include.startswith("/"):
        candidates.append(current.parent / include)
    candidates.extend(root / include for root in roots)
    return any(candidate.exists() for candidate in candidates)

for root in roots:
    for header in root.rglob("*.h"):
        try:
            lines = header.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            match = include_re.match(line)
            if not match:
                continue
            include = match.group(1)
            if include in optional_missing:
                continue
            if not find_header(header, include):
                missing.append((header, include))

if missing:
    for header, include in missing[:80]:
        print(f"{header}: missing quoted include {include}", file=sys.stderr)
    if len(missing) > 80:
        print(f"... and {len(missing) - 80} more", file=sys.stderr)
    raise SystemExit(1)
PY
echo "::endgroup::"

cat > "$package_dir/manifest.txt" <<EOF
skia_ref=$skia_ref
skia_label=$skia_label
skia_commit=$(git -C "$skia" rev-parse HEAD)
target=$package_target
library=lib/$(basename "$combined_lib")
include_path=include/skia
include_path=include/skia/third_party/externals/dawn/include
include_path=generated-include/include
EOF

ls -lh "$combined_lib"
