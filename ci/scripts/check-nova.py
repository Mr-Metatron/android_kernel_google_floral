#!/usr/bin/env python3
"""Check Nova hardening in the compiler command, LLVM IR and linked ARM64 ELF.

Usage: check-nova.py OUT DIST [--object-only]
The default mode requires the final vmlinux and build.log. The object-only mode
is an explicit development check and never claims linked-code verification.
"""

import argparse
import hashlib
import json
import pathlib
import re
import shlex
import shutil
import subprocess
import sys


def run_tool(name, *args):
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"Required LLVM tool is unavailable: {name}")
    result = subprocess.run(
        [executable, *map(str, args)], text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr.strip()[:2000]}")
    return result.stdout


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_ir(ir):
    attributes = dict(re.findall(r"^attributes #(\d+) = \{ (.*) \}$", ir, re.M))
    functions = {}
    for match in re.finditer(r"^define ([^\n]+)\{\n(.*?)^\}", ir, re.M | re.S):
        header, body = match.groups()
        name = re.search(r'@"?([A-Za-z_$][\w.$]*)"?\(', header)
        if name is None:
            continue
        attr_id = re.search(r"#(\d+)", header)
        attrs = attributes.get(attr_id.group(1), "") if attr_id else ""
        functions[name.group(1)] = {
            "shadow_call_stack": "shadowcallstack" in attrs.split(),
            "stack_protector_strong": "sspstrong" in attrs.split(),
            "cfi_type_metadata": "!type" in header,
            "frame_warn_3072": '"warn-stack-size"="3072"' in attrs,
            "indirect_cfi_check": "@__cfi_slowpath" in body,
            "attributes": attrs,
        }
    return functions


def instruction_matches(disassembly):
    lines = disassembly.splitlines()
    # LLVM prints LR as x30; accepting lr also supports older objdump versions.
    scs_push = [line.strip() for line in lines if re.search(
        r"\bstr\s+(?:x30|lr),\s*\[x18\],\s*#(?:0x8|8)\b", line)]
    scs_pop = [line.strip() for line in lines if re.search(
        r"\b(?:ldr|ldur)\s+(?:x30|lr),\s*\[x18,\s*#(?:-0x8|-8)\]!", line)]
    canary = [line.strip() for line in lines if re.search(
        r"\b(?:b|bl)\s+.*<__stack_chk_fail(?:\.[^>]*)?>", line)]
    cfi = [line.strip() for line in lines if re.search(
        r"\b(?:b|bl)\s+.*<(?:__cfi_slowpath|__cfi_check|"
        r"__ubsan_handle_cfi_check_fail_abort)(?:\.[^>]*)?>", line)]
    return {"scs_push": scs_push, "scs_pop": scs_pop,
            "stack_canary_failure": canary, "cfi_check": cfi}


def linked_check(out, dist, ir_functions, result, errors):
    vmlinux = out / "vmlinux"
    nm_output = run_tool("llvm-nm", "--defined-only", "--format=posix", vmlinux)
    symbols = {}
    for line in nm_output.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            symbols[fields[0]] = fields[1:]
    for symbol in ("__cfi_check", "__cfi_slowpath", "__stack_chk_fail"):
        if symbol not in symbols:
            errors.append(f"Final ELF lacks required symbol: {symbol}")

    nova_names = {name for name in ir_functions
                  if name.startswith(("ksu_", "kernelsu_", "sukisu_"))}
    candidates = []
    for name, fields in symbols.items():
        base = re.split(r"\.(?:cfi|llvm|constprop|isra|part|cold)(?:\.|$)", name)[0]
        if fields[0] in {"t", "T", "w", "W"} and base in nova_names:
            candidates.append(name)

    preferred = ("ksu_setup_preinit_file", "ksu_preinit_handle_post_execveat",
                 "ksu_wrapper_read", "ksu_wrapper_write", "ksu_extension_get",
                 "ksu_extension_set", "kernelsu_init", "ksu_install_su_fd")
    candidates.sort(key=lambda name: (
        next((index for index, item in enumerate(preferred)
              if name == item or name.startswith(item + ".")), len(preferred)), name))

    proofs = {"shadow_call_stack": None, "stack_canary": None, "cfi": None}
    checked = []
    excerpts = []
    # Only inspect functions whose names are also defined in Nova's own IR.
    # This avoids accidentally accepting instrumentation from unrelated code.
    for name in candidates:
        assembly = run_tool("llvm-objdump", "--no-show-raw-insn",
                            f"--disassemble-symbols={name}", vmlinux)
        matches = instruction_matches(assembly)
        checked.append(name)
        accepted = False
        if proofs["shadow_call_stack"] is None and matches["scs_push"] and matches["scs_pop"]:
            proofs["shadow_call_stack"] = {"function": name,
                "instructions": matches["scs_push"] + matches["scs_pop"]}
            accepted = True
        if proofs["stack_canary"] is None and matches["stack_canary_failure"]:
            proofs["stack_canary"] = {"function": name,
                "instructions": matches["stack_canary_failure"]}
            accepted = True
        if proofs["cfi"] is None and matches["cfi_check"]:
            proofs["cfi"] = {"function": name, "instructions": matches["cfi_check"]}
            accepted = True
        if accepted:
            excerpts.append(assembly)
        if all(proofs.values()):
            break

    for protection, proof in proofs.items():
        if proof is None:
            errors.append(f"No emitted Nova function supplied {protection} instruction evidence")
    log = (dist / "build.log").read_text(errors="replace")
    warnings = [line for line in log.splitlines() if re.search(
        r"warning:.*(?:stack frame size|frame size of|stack frame.*exceeds)", line, re.I)]
    if warnings:
        errors.append("The linked build reports stack frames exceeding FRAME_WARN")
    result["linked"] = {"vmlinux_sha256": file_sha256(vmlinux),
                        "nova_functions_inspected": checked,
                        "instruction_evidence": proofs, "stack_warnings": warnings}
    (dist / "nova-protection-disassembly.txt").write_text("\n".join(excerpts))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=pathlib.Path)
    parser.add_argument("dist", type=pathlib.Path)
    parser.add_argument("--object-only", action="store_true")
    args = parser.parse_args()
    out, dist = args.out.resolve(), args.dist.resolve()
    dist.mkdir(parents=True, exist_ok=True)
    errors = []
    result = {"mode": "object-only" if args.object_only else "linked",
              "errors": errors, "device_tested": False}
    try:
        config = (out / ".config").read_text()
        for symbol in ("KSU", "LTO_CLANG", "CFI_CLANG", "SHADOW_CALL_STACK",
                       "CC_STACKPROTECTOR_STRONG", "INIT_STACK_ALL_ZERO"):
            if f"CONFIG_{symbol}=y\n" not in config:
                errors.append(f"Required config is missing: CONFIG_{symbol}=y")
        for symbol in ("CFI_PERMISSIVE", "THINLTO", "KSU_DEBUG"):
            if f"CONFIG_{symbol}=y\n" in config:
                errors.append(f"CONFIG_{symbol} must stay disabled")
        if "CONFIG_FRAME_WARN=3072\n" not in config:
            errors.append("CONFIG_FRAME_WARN must stay at 3072")

        command = (out / "drivers/kernelsu/.ksu.o.cmd").read_text().splitlines()[0]
        tokens = shlex.split(command.split(":=", 1)[1])
        required = ("-flto", "-fsanitize=cfi", "-fsanitize=shadow-call-stack",
                    "-fstack-protector-strong", "-Wframe-larger-than=3072",
                    "-ftrivial-auto-var-init=zero")
        result["compile_flags"] = {flag: flag in tokens for flag in required}
        for flag in required:
            if flag not in tokens:
                errors.append(f"Nova compiler command lacks {flag}")
        # LOS itself uses -fno-stack-check; that is not stack protector removal.
        for flag in ("-fno-stack-protector", "-fno-lto",
                     "-fno-sanitize=cfi", "-fno-sanitize=shadow-call-stack"):
            if flag in tokens:
                errors.append(f"Nova compiler command disables required protection: {flag}")
        (dist / "nova-compile-command.txt").write_text(command + "\n")

        obj = out / "drivers/kernelsu/ksu.o"
        ir = run_tool("llvm-dis", obj, "-o", "-")
        functions = read_ir(ir)
        result["object_sha256"] = file_sha256(obj)
        result["llvm_dis_version"] = run_tool("llvm-dis", "--version").strip()
        selected = ["ksu_setup_preinit_file", "ksu_preinit_handle_post_execveat",
                    "ksu_selinux_hide_is_active"]
        if "CONFIG_KPM=y\n" in config:
            selected.append("sukisu_kpm_legacy_entry")
        result["ir_functions"] = {}
        for name in selected:
            evidence = functions.get(name)
            result["ir_functions"][name] = evidence
            if evidence is None:
                errors.append(f"Nova object lacks expected function: {name}")
                continue
            for protection in ("shadow_call_stack", "stack_protector_strong",
                               "cfi_type_metadata", "frame_warn_3072"):
                if not evidence[protection]:
                    errors.append(f"{name} lacks {protection} in LLVM IR")
        if not args.object_only:
            linked_check(out, dist, functions, result, errors)
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(str(exc))
    result["passed"] = not errors
    (dist / "nova-protection-check.json").write_text(json.dumps(result, indent=2) + "\n")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"Nova {result['mode']} protection checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
