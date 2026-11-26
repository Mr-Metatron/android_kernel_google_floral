#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
stage=${1:-stage1}
case "$stage" in baseline|stage1|stage2|stage3) ;; *) echo "Unsupported stage: $stage" >&2; exit 2 ;; esac
deps=${FLORAL_DEPS:-"$root/.ci-deps"}
dist=${FLORAL_DIST:-"$root/.ci-dist/$stage"}
anykernel=${FLORAL_ANYKERNEL:-"$deps/anykernel"}
if [[ ! -d "$anykernel" && -d "$deps/AnyKernel3" ]]; then
  anykernel="$deps/AnyKernel3"
fi
python3 "$root/ci/scripts/package_artifacts.py" \
  --root "$root" --dist "$dist" --anykernel "$anykernel" --stage "$stage"
