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

if [[ ! -f "$skia/BUILD.gn" ]]; then
  echo "Skia source tree is missing at $skia" >&2
  echo "Run scripts/prepare_source.sh first, or set SKIA_SOURCE_DIR to a prepared tree." >&2
  exit 1
fi

mkdir -p "$package_dir"

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
    cat > "$target_args" <<'ARGS'
target_os="ios"
target_cpu="arm64"
ios_min_target="13.0"
skia_use_fonthost_mac=true
skia_use_freetype=false
skia_use_fontconfig=false
dawn_enable_metal=true
dawn_enable_vulkan=false
dawn_enable_d3d12=false
ARGS
    ;;
  ios-simulator-arm64)
    cat > "$target_args" <<'ARGS'
target_os="ios"
target_cpu="arm64"
ios_use_simulator=true
ios_min_target="13.0"
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
  "$ninja_bin" -C "$out_dir" \
    :skia \
    modules/skunicode:skunicode \
    modules/skshaper:skshaper \
    modules/skparagraph:skparagraph \
    modules/svg:svg
  echo "::endgroup::"
fi

if [[ "$mode" == "build" ]]; then
  exit 0
fi

rm -rf "$lib_dir" "$include_dir" "$generated_include_dir"
mkdir -p "$lib_dir" "$include_dir" "$generated_include_dir"

echo "::group::combine static library"
libs_file="$package_dir/static-archives.txt"
find "$out_dir" -maxdepth 1 -name "*.$lib_ext" -type f | sort > "$libs_file"
if [[ ! -s "$libs_file" ]]; then
  echo "no top-level static archives found under $out_dir" >&2
  exit 1
fi

rm -f "$combined_lib"
if [[ "$package_target" == windows-* ]]; then
  lib_rsp="$package_dir/lib.exe.rsp"
  : > "$lib_rsp"
  while IFS= read -r lib; do
    if command -v cygpath >/dev/null 2>&1; then
      cygpath -w "$lib" >> "$lib_rsp"
    else
      echo "$lib" >> "$lib_rsp"
    fi
  done < "$libs_file"

  out_arg="$combined_lib"
  rsp_arg="$lib_rsp"
  if command -v cygpath >/dev/null 2>&1; then
    out_arg="$(cygpath -w "$combined_lib")"
    rsp_arg="$(cygpath -w "$lib_rsp")"
  fi
  lib.exe /nologo "/OUT:$out_arg" "@$rsp_arg"
else
  if [[ "$(uname -s)" == Darwin ]]; then
    libtool -static -o "$combined_lib" $(cat "$libs_file")
  else
    ar_bin="${AR:-llvm-ar}"
    if ! command -v "$ar_bin" >/dev/null 2>&1; then
      ar_bin="ar"
    fi
    mri_file="$package_dir/ar.mri"
    {
      echo "CREATE $combined_lib"
      while IFS= read -r lib; do
        echo "ADDLIB $lib"
      done < "$libs_file"
      echo "SAVE"
      echo "END"
    } > "$mri_file"
    "$ar_bin" -M < "$mri_file"
  fi
  ranlib "$combined_lib"
fi
echo "::endgroup::"

echo "::group::collect public headers"
mkdir -p "$include_dir/skia/include"
cp -R "$skia/include/." "$include_dir/skia/include/"
mkdir -p "$include_dir/skia/modules" "$include_dir/skia/third_party/externals/dawn/include"
for module in skparagraph skresources skshaper skunicode svg; do
  if [[ -d "$skia/modules/$module/include" ]]; then
    mkdir -p "$include_dir/skia/modules/$module/include"
    cp -R "$skia/modules/$module/include/." "$include_dir/skia/modules/$module/include/"
  fi
done
if [[ -d "$skia/third_party/externals/dawn/include" ]]; then
  cp -R "$skia/third_party/externals/dawn/include/." "$include_dir/skia/third_party/externals/dawn/include/"
fi
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
