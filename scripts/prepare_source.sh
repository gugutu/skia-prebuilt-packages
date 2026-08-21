#!/usr/bin/env bash
set -euo pipefail

root="$(pwd)"
skia_ref="${SKIA_REF:-refs/heads/chrome/m153}"
skia_label="${SKIA_LABEL:-chrome/m153}"
repo_url="${SKIA_REPO:-https://skia.googlesource.com/skia.git}"
skia="${SKIA_SOURCE_DIR:-$root/work/skia-source}"
python_cmd="${PYTHON:-python3}"
if ! command -v "$python_cmd" >/dev/null 2>&1; then
  python_cmd="python"
fi

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    git config --global core.longpaths true
    ;;
esac

echo "::group::checkout skia $skia_label"
rm -rf "$skia"
mkdir -p "$(dirname "$skia")"
git init "$skia"
git -C "$skia" remote add origin "$repo_url"
git -C "$skia" fetch --depth=1 origin "$skia_ref"
git -C "$skia" checkout --quiet --force FETCH_HEAD
git -C "$skia" reset --hard --quiet FETCH_HEAD
echo "::endgroup::"

echo "::group::sync skia deps"
cd "$skia"
GIT_SYNC_DEPS_SKIP_EMSDK=1 GIT_SYNC_DEPS_QUIET=T "$python_cmd" tools/git-sync-deps
"$python_cmd" bin/fetch-ninja
echo "::endgroup::"

cat > "$root/work/skia-source-manifest.txt" <<EOF
skia_ref=$skia_ref
skia_label=$skia_label
skia_commit=$(git -C "$skia" rev-parse HEAD)
EOF
