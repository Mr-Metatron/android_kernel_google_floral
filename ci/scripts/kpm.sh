#!/usr/bin/env bash
# Inject the matched KPM loader after the full LOS kernel has passed its checks.
set -euo pipefail
root=$(cd "$(dirname "$0")/../.." && pwd)
source "$root/ci/scripts/build-jobs.sh"
deps=${FLORAL_DEPS:-"$root/.ci-deps"}
out=${FLORAL_OUT:-"$root/.ci-out/stage1"}
dist=${FLORAL_DIST:-"$root/.ci-dist/stage1"}
kp_source=${FLORAL_KP_SOURCE:-"$deps/kp"}
lock="$root/ci/kpm-sources.json"
patch="$root/ci/patches/kp-legacy-bridge.patch"
evidence="$dist/kpm-evidence"
mkdir -p "$deps" "$evidence"
field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$lock" "$1"; }
source_field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]][sys.argv[3]])' "$root/ci/sources.json" "$1" "$2"; }
kp_sha=$(field kp_commit)
[[ "$kp_sha" == "$(source_field kp commit)" ]] || { echo 'KP source locks disagree' >&2; exit 1; }
export SOURCE_DATE_EPOCH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_date_epoch"])' "$root/ci/sources.json")
export TZ=UTC LC_ALL=C

for file in "$out/vmlinux" "$out/System.map" "$out/.config" \
            "$out/arch/arm64/boot/Image" "$out/arch/arm64/boot/Image.lz4"; do
  [[ -s "$file" ]] || { echo "Missing linked kernel input: $file" >&2; exit 1; }
done
grep -qx 'CONFIG_KPM=y' "$out/.config"
clang_dir=$(source_field clang directory)
llvm_nm="$deps/$clang_dir/bin/llvm-nm"
llvm_objdump="$deps/$clang_dir/bin/llvm-objdump"
"$llvm_nm" -S "$out/vmlinux" > "$evidence/target-elf-symbols.txt"
python3 - "$out" "$evidence" <<'PY'
import pathlib,re,sys
out,evidence=map(pathlib.Path,sys.argv[1:])
table=(out/'System.map').read_text()
elf=(evidence/'target-elf-symbols.txt').read_text()
name='sukisu_kpm_legacy_entry'
if not re.search(r'\b'+name+r'$',table,re.M):
    raise SystemExit('KPM bridge is missing from System.map')
matches=[line for line in elf.splitlines() if re.search(r'\b'+name+r'(?:\.\S+)?$',line)]
sizes=[int(line.split()[1],16) for line in matches if len(line.split())==4]
if not matches or max(sizes,default=0)<16:
    raise SystemExit('KPM bridge has no retained, hookable ELF body')
(evidence/'target-bridge-symbols.txt').write_text('\n'.join(matches)+'\n')
for symbol in ('__cfi_check','__cfi_slowpath','__stack_chk_fail'):
    if not re.search(r'\b'+symbol+r'$',table,re.M):
        raise SystemExit('Missing LOS protection symbol: '+symbol)
# /proc/kallsyms presentation filtering leaves these linked tables intact.
PY
"$llvm_objdump" -d --disassemble-symbols=sukisu_kpm_legacy_entry,sukisu_kpm_legacy_entry.cfi \
  "$out/vmlinux" > "$evidence/target-bridge-disassembly.txt"
rm "$evidence/target-elf-symbols.txt"

if [[ ! -d "$kp_source/.git" ]]; then
  git init -q "$kp_source"
  git -C "$kp_source" remote add origin "$(source_field kp repository)"
  git -C "$kp_source" fetch -q --depth 1 origin "$kp_sha"
  git -C "$kp_source" checkout -q --detach FETCH_HEAD
fi
[[ "$(git -C "$kp_source" rev-parse HEAD)" == "$kp_sha" ]] || { echo 'Wrong KP checkout' >&2; exit 1; }
if git -C "$kp_source" apply --reverse --check "$patch" 2>/dev/null; then
  : # Reuse an already patched checkout without discarding any source changes.
else
  git -C "$kp_source" apply --check "$patch"
  git -C "$kp_source" apply "$patch"
fi
git -C "$kp_source" diff --exit-code -- . ':(exclude)kernel/patch/sukisu/sukisu.c' > "$evidence/unexpected-source-diff.txt"
python3 - "$lock" "$kp_source" "$evidence" <<'PY'
import json,pathlib,re,sys
lock=json.loads(pathlib.Path(sys.argv[1]).read_text());src=pathlib.Path(sys.argv[2]);e=pathlib.Path(sys.argv[3])
def strip_comments(text):
    pattern=r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/'
    return re.sub(pattern,lambda m:m.group() if m.group()[0] in "\"'" else ' ',text,flags=re.S)
boot=strip_comments((src/'kernel/patch/patch.c').read_text())
start=boot.index('static void before_rest_init(')
end=boot.index('static int extra_event_load_kpm(',start)
active=boot[start:end]
inactive=['supercall_install','su_compat_init','android_user_init','bypass_selinux']
for function in inactive:
    if re.search(r'\b'+function+r'\s*\(',active):
        raise SystemExit('Unexpected extra root/Android entry: '+function)
if 'sukisu_kpm_supercall_install();' not in active:
    raise SystemExit('Nova KPM entry is not installed')
# Only a declaration and the definition reference android_user_init in active C.
references=[]
for path in sorted((src/'kernel').rglob('*.c')):
    clean=strip_comments(path.read_text(errors='replace'))
    for line in clean.splitlines():
        if re.search(r'\bandroid_user_init\s*\(',line):
            references.append({'file':str(path.relative_to(src)),'code':line.strip()})
if len(references)!=2 or any(not re.match(r'int\s+android_user_init\s*\(\s*(?:void)?\s*\)\s*;?$',x['code']) for x in references):
    raise SystemExit('Review android_user_init call graph before proceeding')
review={
 'extra_root_provider':False,
 'active_control_entry':'Nova manager_or_root ioctl -> ABI 2 -> sukisu_kpm_legacy_entry -> KP loader',
 'inactive_boot_calls':inactive,
 'android_user_init_active_references':references,
 'before_openat_registration':'Only android_user_init registers before_openat; that initializer is not called or KP-exported',
 'known_stack_warning':{'file':'kernel/patch/android/userd.c','function':'before_openat.part.0','bytes':4384,'limit':3072,'status':'reviewed inactive APatch init.rc hook; original source retained'},
 'key_mode':'No key supplied to kptools; zero root verifier; no APatch root entry installed',
 'default_embedded_kpm_modules':[],
}
(e/'source-review.json').write_text(json.dumps(review,indent=2)+'\n')
PY

gnu_dir=$(field gnu_directory)
toolchain=${FLORAL_KP_TOOLCHAIN:-"$deps/$gnu_dir"}
cross="$toolchain/bin/aarch64-none-elf-"
if [[ ! -x "${cross}gcc" ]]; then
  archive="$deps/$gnu_dir.tar.xz"
  curl -fL --retry 3 --connect-timeout 30 "$(field gnu_url)" -o "$archive"
  printf '%s  %s\n' "$(field gnu_archive_sha256)" "$archive" | sha256sum -c -
  unpack=$(mktemp -d "$deps/gnu-unpack.XXXXXX")
  trap 'rm -rf "$unpack"' EXIT
  tar -xJf "$archive" -C "$unpack" --strip-components=1
  mv -T "$unpack" "$toolchain"
  trap - EXIT
  rm "$archive"
fi
"${cross}gcc" --version > "$evidence/compiler.txt"
cc --version > "$evidence/host-compiler.txt"
make -C "$kp_source/kernel" clean TARGET_COMPILE="$cross" > "$evidence/clean.log" 2>&1
CFLAGS='-Wframe-larger-than=3072' make -C "$kp_source/kernel" -j"$FLORAL_JOBS" \
  TARGET_COMPILE="$cross" > "$evidence/build.log" 2>&1
make -C "$kp_source/tools" -j"$FLORAL_JOBS" > "$evidence/kptools-build.log" 2>&1
"${cross}nm" -u "$kp_source/kernel/kpimg.elf" > "$evidence/undefined-symbols.txt"
[[ ! -s "$evidence/undefined-symbols.txt" ]] || { echo 'KP has unresolved symbols' >&2; exit 1; }
"${cross}readelf" -h "$kp_source/kernel/kpimg.elf" > "$evidence/elf-header.txt"
"${cross}nm" -n "$kp_source/kernel/kpimg.elf" > "$evidence/System.map"
python3 - "$evidence" <<'PY'
import json,pathlib,re,sys
e=pathlib.Path(sys.argv[1]);log=(e/'build.log').read_text(errors='replace')
warnings=[];context=''
for line in log.splitlines():
    if 'In function ' in line: context=line
    if re.search(r'warning:.*(?:frame size|stack frame)',line):
        known=('before_openat.part.0' in context and 'patch/android/userd.c:' in line and '4384' in line and '3072' in line)
        warnings.append({'diagnostic':line,'context':context,'reviewed_inactive_path':known})
report={'frame_warn':3072,'stack_warnings':warnings,'undefined_symbols':[], 'passed':all(x['reviewed_inactive_path'] for x in warnings)}
(e/'static-checks.json').write_text(json.dumps(report,indent=2)+'\n')
if not report['passed']:
    raise SystemExit('New KP stack warning requires source review; inspect kpm-evidence/static-checks.json')
PY
cp "$kp_source/kernel/kpimg" "$kp_source/kernel/kpimg.elf" "$evidence/"
kptools="$kp_source/tools/kptools"
"$kptools" -l -k "$evidence/kpimg" > "$evidence/version.txt"
"$kptools" -d -i "$out/arch/arm64/boot/Image" > "$evidence/kallsyms.tmp" 2>&1
python3 - "$evidence" <<'PY'
import pathlib,re,sys
e=pathlib.Path(sys.argv[1]);dump=(e/'kallsyms.tmp').read_text(errors='replace')
lines=[line for line in dump.splitlines() if re.search(r'\bsukisu_kpm_legacy_entry(?:\.\S+)?$',line)]
if not lines: raise SystemExit('Bridge is absent from the raw Image kallsyms table')
(e/'image-bridge-symbols.txt').write_text('\n'.join(lines)+'\n')
(e/'kallsyms.tmp').unlink()
PY
cp "$out/arch/arm64/boot/Image" "$dist/Image.unpatched"
cp "$out/arch/arm64/boot/Image.lz4" "$dist/Image.lz4.unpatched"
# The fixed patcher supports an absent key and zeroes the root verifier.
# Nova's bridge uses its existing ioctl authorization and never consumes a key.
"$kptools" -p -i "$dist/Image.unpatched" -k "$evidence/kpimg" \
  -o "$dist/Image.kpm-tmp" > "$evidence/patch.log" 2>&1
"$kptools" -l -i "$dist/Image.kpm-tmp" > "$evidence/patched-info.txt" 2>&1
lz4c -9 < "$dist/Image.kpm-tmp" > "$dist/Image.lz4.kpm-tmp"
mv "$dist/Image.kpm-tmp" "$dist/Image"
mv "$dist/Image.lz4.kpm-tmp" "$dist/Image.lz4"
python3 - "$root" "$dist" "$lock" "$kp_source" <<'PY'
import json,pathlib,subprocess,sys
root,dist,lock,kp=map(pathlib.Path,sys.argv[1:]);e=dist/'kpm-evidence'
sources=json.loads((root/'ci/sources.json').read_text());kp_lock=json.loads(lock.read_text())
manifest={
 'status':'patched','kp_commit':sources['kp']['commit'],
 'kernel_source_commit':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
 'bridge_abi':2,'bridge_entry':'sukisu_kpm_legacy_entry',
 'source_date_epoch':sources['source_date_epoch'],
 'toolchain':kp_lock,
 'device_tested':False,'extra_root_provider':False,
 'source_review':json.loads((e/'source-review.json').read_text()),
 'static_checks':json.loads((e/'static-checks.json').read_text()),
 'protection_scope':'LOS CFI/SCS retained; existing KP-only CFI exceptions and upstream KP SCS coverage retained',
}
(dist/'kpm-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
PY
echo "KPM loader injected; evidence and manifest: $dist"
