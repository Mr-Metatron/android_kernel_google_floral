#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
stage=${1:-stage1}
case "$stage" in baseline|stage1) ;; *) echo "Unsupported stage: $stage" >&2; exit 2 ;; esac
deps=${FLORAL_DEPS:-"$root/.ci-deps"}
out=${FLORAL_OUT:-"$root/.ci-out/$stage"}
dist=${FLORAL_DIST:-"$root/.ci-dist/$stage"}
mkdir -p "$out" "$dist"
clang_dir=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["clang"]["directory"])' "$root/ci/sources.json")
export PATH="$deps/$clang_dir/bin:$deps/gcc64/bin:$deps/gcc32/bin:$PATH"
export LD_LIBRARY_PATH="$deps/$clang_dir/lib:${LD_LIBRARY_PATH:-}"
export SOURCE_DATE_EPOCH=1792179815
export KBUILD_BUILD_TIMESTAMP='Fri Oct 16 16:23:35 UTC 2026'
export KBUILD_BUILD_USER=floral
export KBUILD_BUILD_HOST=ci
export KBUILD_BUILD_VERSION=1
export TZ=UTC
export LC_ALL=C
source_dir="$root"
if [[ "$stage" == baseline ]]; then
  source_dir="$root/.ci-source/baseline"
  if [[ ! -f "$source_dir/Makefile" ]]; then
    mkdir -p "$source_dir"
    git -C "$root" archive bb17b7de4b570f2c0c32ddba0543cac03c58dd84 | tar -x -C "$source_dir"
  fi
fi
# Match the official module release; source identity remains in build-manifest.
printf '%s\n' '-gbb17b7de4b57' > "$source_dir/.scmversion"
make_args=(-C "$source_dir" O="$out" ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE=aarch64-linux-android- CROSS_COMPILE_ARM32=arm-linux-androideabi-)
make "${make_args[@]}" floral_defconfig 2>&1 | tee "$dist/config.log"
if [[ "$stage" == stage1 ]]; then
  "$source_dir/scripts/kconfig/merge_config.sh" -m -O "$out" "$out/.config" "$root/ci/configs/stage1.config" 2>&1 | tee -a "$dist/config.log"
fi
make "${make_args[@]}" olddefconfig 2>&1 | tee -a "$dist/config.log"
python3 "$root/ci/scripts/check-build.py" config "$out" "$dist" "$stage"
set +e
make "${make_args[@]}" -j"${FLORAL_JOBS:-2}" Image Image.lz4 dtbs modules 2>&1 | tee "$dist/build.log"
status=${PIPESTATUS[0]}
set -e
cp "$out/.config" "$dist/kernel.config"
"$deps/$clang_dir/bin/clang" --version > "$dist/compiler.txt"
sha256sum "$deps/$clang_dir/bin/clang" "$deps/$clang_dir/bin/ld.lld" > "$dist/compiler.sha256"
[[ $status == 0 ]] || exit "$status"
cp "$out/Module.symvers" "$out/System.map" "$dist/"
cp "$out/arch/arm64/boot/Image" "$out/arch/arm64/boot/Image.lz4" "$dist/"
python3 "$root/ci/scripts/check-build.py" linked "$out" "$dist" "$stage"
python3 - "$root" "$out" "$dist" "$stage" <<'PY'
import hashlib,json,pathlib,subprocess,sys
root,out,dist=map(pathlib.Path,sys.argv[1:4]);stage=sys.argv[4]
m=json.loads((root/'ci/sources.json').read_text())
m.update(stage=stage,source_commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),kernel_release=(out/'include/config/kernel.release').read_text().strip(),device_tested=False)
(dist/'build-manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
PY
