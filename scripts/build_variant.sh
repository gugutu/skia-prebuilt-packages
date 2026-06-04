#!/usr/bin/env bash
set -euo pipefail

variant="${1:?variant is required}"
skia_tag="${SKIA_TAG:-canvaskit/0.41.0}"
repo_url="${SKIA_REPO:-https://skia.googlesource.com/skia.git}"

case "$variant" in
  libgrapheme)
    unicode_args='skia_use_bidi=true skia_use_libgrapheme=true skia_use_icu4x=false'
    ;;
  icu4x)
    unicode_args='skia_use_bidi=false skia_use_libgrapheme=false skia_use_icu4x=true'
    ;;
  *)
    echo "unknown variant: $variant" >&2
    exit 2
    ;;
esac

root="$(pwd)"
work="$root/work-$variant"
skia="$work/skia"
out_dir="out/$variant"
report_dir="$root/reports/$variant"
raw_dir="$work/raw-libs"
stripped_dir="$work/stripped-libs"

rm -rf "$report_dir" "$raw_dir" "$stripped_dir"
mkdir -p "$work" "$report_dir" "$raw_dir" "$stripped_dir"

echo "::group::checkout skia $skia_tag"
if [[ -d "$skia/.git" ]]; then
  git -C "$skia" fetch --depth=1 origin "refs/tags/$skia_tag:refs/tags/$skia_tag"
  git -C "$skia" checkout --quiet --force "$skia_tag"
  git -C "$skia" reset --hard --quiet "$skia_tag"
else
  rm -rf "$skia"
  git clone --depth=1 --branch "$skia_tag" "$repo_url" "$skia"
fi
echo "::endgroup::"

echo "::group::sync skia deps"
cd "$skia"
GIT_SYNC_DEPS_SKIP_EMSDK=1 GIT_SYNC_DEPS_QUIET=T python3 tools/git-sync-deps
python3 bin/fetch-ninja
echo "::endgroup::"

common_args=$(cat <<'ARGS'
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

skia_use_harfbuzz=true
skia_enable_skshaper=true
skia_enable_skparagraph=true

skia_enable_svg=true
skia_use_expat=true
skia_enable_skottie=true

skia_enable_pdf=false
skia_enable_tools=false
skia_enable_gpu_debug_layers=false
cc_wrapper="sccache"

skia_use_fonthost_mac=true
skia_use_freetype=false
skia_use_fontconfig=false

skia_use_libpng_decode=true
skia_use_libpng_encode=true
skia_use_libjpeg_turbo_decode=true
skia_use_libjpeg_turbo_encode=true
skia_use_libwebp_decode=true
skia_use_libwebp_encode=true
skia_use_wuffs=true
skia_use_zlib=true
ARGS
)

args_file="$report_dir/gn_args.txt"
{
  printf '%s\n' "$common_args"
  printf '%s\n' "$unicode_args"
} > "$args_file"

echo "::group::gn gen $variant"
bin/gn gen "$out_dir" --args="$(tr '\n' ' ' < "$args_file")"
echo "::endgroup::"

echo "::group::ninja $variant"
third_party/ninja/ninja -C "$out_dir" \
  :skia \
  modules/skunicode:skunicode \
  modules/skshaper:skshaper \
  modules/skparagraph:skparagraph \
  modules/svg:svg \
  modules/skottie:skottie
echo "::endgroup::"

echo "::group::collect static archives"
mapfile -t libs < <(find "$out_dir" -name '*.a' -type f | sort)
if [[ "${#libs[@]}" -eq 0 ]]; then
  echo "no static archives found under $out_dir" >&2
  exit 1
fi

for lib in "${libs[@]}"; do
  rel="${lib#$out_dir/}"
  mkdir -p "$raw_dir/$(dirname "$rel")" "$stripped_dir/$(dirname "$rel")"
  cp "$lib" "$raw_dir/$rel"
  cp "$lib" "$stripped_dir/$rel"
  strip -S -x "$stripped_dir/$rel" 2>/dev/null || strip -S "$stripped_dir/$rel" 2>/dev/null || true
done
echo "::endgroup::"

python3 "$root/scripts/size_report.py" \
  --variant "$variant" \
  --skia-tag "$skia_tag" \
  --gn-args "$args_file" \
  --raw-dir "$raw_dir" \
  --stripped-dir "$stripped_dir" \
  --out-json "$report_dir/size.json" \
  --out-md "$report_dir/size.md"

python3 - <<'PY' "$report_dir/size.md"
from pathlib import Path
import sys
print(Path(sys.argv[1]).read_text())
PY
