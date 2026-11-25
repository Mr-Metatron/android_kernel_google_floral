#!/usr/bin/env bash
# Arguments: pinned magiskboot, Image.lz4, current-ROM boot URL, output directory.
set -euo pipefail
[[ $# == 4 ]] || { echo "Usage: $0 MAGISKBOOT IMAGE_LZ4 BOOT_URL OUTPUT_DIR" >&2; exit 2; }
magiskboot=$(realpath "$1")
kernel=$(realpath "$2")
url=$3
mkdir -p "$4"
output=$(cd "$4" && pwd)
command -v qemu-arm >/dev/null
[[ -s "$magiskboot" && -s "$kernel" ]]
work=$(mktemp -d "$output/.repack-XXXXXX")
trap 'rm -rf "$work"' EXIT
curl --fail --location --retry 3 --proto '=https' --proto-redir '=https' \
  --output "$work/stock-boot.img" "$url"
(
  cd "$work"
  qemu-arm "$magiskboot" unpack -n -h stock-boot.img
  cp "$kernel" kernel
  PATCHVBMETAFLAG=false qemu-arm "$magiskboot" repack -n stock-boot.img boot.img
  [[ -s boot.img ]]
)
mv "$work/boot.img" "$work/stock-boot.img" "$output/"
