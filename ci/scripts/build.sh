#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
source "$root/ci/scripts/build-jobs.sh"
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
export KBUILD_BUILD_TIMESTAMP='Fri Oct 16 19:43:35 UTC 2026'
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
# Leave the 64-bit GCC prefix empty so this tree selects its native
# aarch64-linux-gnu Clang target. An Android target uses the userspace TLS
# stack canary ABI; the kernel initializes the global __stack_chk_guard.
make_args=(-C "$source_dir" O="$out" ARCH=arm64 LLVM=1 LLVM_IAS=1 CROSS_COMPILE= CROSS_COMPILE_ARM32=arm-linux-androideabi- DTC_EXT="$deps/dtc/dtc" DTC_OVERLAY_TEST_EXT="$deps/dtc/ufdt_apply_overlay")
make "${make_args[@]}" floral_defconfig 2>&1 | tee "$dist/config.log"
if [[ "$stage" == stage1 ]]; then
  "$source_dir/scripts/kconfig/merge_config.sh" -m -O "$out" "$out/.config" "$root/ci/configs/stage1.config" 2>&1 | tee -a "$dist/config.log"
fi
make "${make_args[@]}" olddefconfig 2>&1 | tee -a "$dist/config.log"
python3 "$root/ci/scripts/check-build.py" config "$out" "$dist" "$stage"
# Check real kernel object commands and LLVM triples before paying for the
# full build. These objects are reused by the normal build below.
make "${make_args[@]}" -j"$FLORAL_JOBS" fs/open.o fs/namei.o 2>&1 | tee "$dist/target-probe.log"
python3 "$root/ci/scripts/check-build.py" target "$out" "$dist" "$stage"
if [[ "$stage" == stage1 ]]; then
  make "${make_args[@]}" -j"$FLORAL_JOBS" drivers/kernelsu/ksu.o 2>&1 | tee "$dist/nova-object.log"
  python3 "$root/ci/scripts/check-nova.py" "$out" "$dist" --object-only
fi
set +e
make "${make_args[@]}" -j"$FLORAL_JOBS" Image Image.lz4 dtbs modules 2>&1 | tee "$dist/build.log"
status=${PIPESTATUS[0]}
set -e
cp "$out/.config" "$dist/kernel.config"
cp "$out/.config" "$dist/.config"
"$deps/$clang_dir/bin/clang" --version > "$dist/compiler.txt"
[[ $status == 0 ]] || exit "$status"
cp "$out/Module.symvers" "$out/System.map" "$dist/"
cp "$out/arch/arm64/boot/Image" "$out/arch/arm64/boot/Image.lz4" "$dist/"
python3 "$root/ci/scripts/check-build.py" linked "$out" "$dist" "$stage"
if [[ "$stage" == stage1 ]]; then
  python3 "$root/ci/scripts/check-nova.py" "$out" "$dist"
  python3 "$root/ci/scripts/module_abi.py" check "$out" "$root/ci/reference/vendor-abi.json" "$dist/module-abi.json"
fi
python3 - "$root" "$out" "$dist" "$stage" <<'PY'
import json,pathlib,subprocess,sys
root,out,dist=map(pathlib.Path,sys.argv[1:4]);stage=sys.argv[4]
m=json.loads((root/'ci/sources.json').read_text())
m['clang'].pop('archive_sha256', None)
pipeline_commit=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
source_dirty=bool(subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True).strip())
m.update(stage=stage,source_commit=m['kernel']['commit'] if stage=='baseline' else pipeline_commit,pipeline_commit=pipeline_commit,source_dirty=False if stage=='baseline' else source_dirty,kernel_release=(out/'include/config/kernel.release').read_text().strip(),device_tested=False)
(dist/'build-manifest.json').write_text(json.dumps(m,ensure_ascii=False,indent=2)+'\n')
PY
