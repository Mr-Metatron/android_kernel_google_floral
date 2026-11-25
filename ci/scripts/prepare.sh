#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
deps=${FLORAL_DEPS:-"$root/.ci-deps"}
mkdir -p "$deps"
read_field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d[sys.argv[2]][sys.argv[3]])' "$root/ci/sources.json" "$1" "$2"; }
fetch_repo() {
  local name=$1 repo sha actual
  repo=$(read_field "$name" repository)
  sha=$(read_field "$name" commit)
  if [[ -d "$deps/$name/.git" ]]; then
    actual=$(git -C "$deps/$name" rev-parse HEAD)
    [[ "$actual" == "$sha" ]] || { echo "Wrong $name checkout: $actual" >&2; exit 1; }
  else
    git init -q "$deps/$name"
    git -C "$deps/$name" remote add origin "$repo"
    git -C "$deps/$name" fetch -q --depth 1 origin "$sha"
    git -C "$deps/$name" checkout -q --detach FETCH_HEAD
  fi
}
clang_dir=$(read_field clang directory)
if [[ ! -x "$deps/$clang_dir/bin/clang" ]]; then
  repo=$(read_field clang repository)
  sha=$(read_field clang commit)
  archive="$deps/$clang_dir.tar.gz"
  curl -fL --retry 3 --connect-timeout 30 "$repo/+archive/$sha/$clang_dir.tar.gz" -o "$archive"
  mkdir -p "$deps/$clang_dir"
  tar -xzf "$archive" -C "$deps/$clang_dir"
  sha256sum "$archive" > "$deps/clang-archive.sha256"
  rm "$archive"
fi
fetch_repo gcc64
fetch_repo gcc32
fetch_repo anykernel
"$deps/$clang_dir/bin/clang" --version
