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
  archive="$deps/$clang_dir.tar.gz"
  curl -fL --retry 3 --connect-timeout 30 "$(read_field clang archive_url)" -o "$archive"
  printf '%s  %s\n' "$(read_field clang archive_sha256)" "$archive" | sha256sum --check -
  unpack=$(mktemp -d "$deps/clang-unpack.XXXXXX")
  trap 'rm -rf "$unpack"' EXIT
  tar -xzf "$archive" -C "$unpack"
  mv -T "$unpack" "$deps/$clang_dir"
  trap - EXIT
  rm "$archive"
fi
fetch_repo gcc64
fetch_repo gcc32
fetch_repo anykernel
python3 - "$root/ci/sources.json" "$deps" <<'PY'
import base64,json,pathlib,sys,urllib.request
lock=json.load(open(sys.argv[1]))['dtc'];dest=pathlib.Path(sys.argv[2])/'dtc';dest.mkdir(exist_ok=True)
for relative,name in [('dtc/dtc','dtc'),('libufdt/ufdt_apply_overlay','ufdt_apply_overlay')]:
    path=dest/name
    if not path.exists():
        url=f"{lock['repository']}/+/{lock['commit']}/linux-x86/{relative}?format=TEXT"
        raw=urllib.request.urlopen(url,timeout=60).read()
        path.write_bytes(base64.b64decode(raw));path.chmod(0o755)
PY
"$deps/$clang_dir/bin/clang" --version
