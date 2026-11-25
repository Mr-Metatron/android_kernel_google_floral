"""Check the GNU ARM64 target and real global-canary references after Full LTO."""

import re
import subprocess


def run_tool(name, *args):
    result = subprocess.run([name, *map(str, args)], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr.strip()[:2000]}")
    return result.stdout


def compiler_target_check(ir):
    match = re.search(r'^target triple = "([^"]+)"$', ir, re.M)
    target = match.group(1) if match else None
    errors = [] if target == "aarch64-unknown-linux-gnu" else [
        f"LLVM IR target must be aarch64-unknown-linux-gnu; got {target}"]
    return {"ir_target": target, "errors": errors, "passed": not errors}


def object_target_check(out, relative):
    result = compiler_target_check(run_tool("llvm-dis", out / relative, "-o", "-"))
    return dict(result, object=relative)


def disassemble(path, *names, relocations=False):
    flags = ["-dr" if relocations else "-d", "--no-show-raw-insn"]
    return run_tool("llvm-objdump", *flags, f"--disassemble-symbols={','.join(names)}", path)


def global_guard_references(native, linked):
    # vmlinux.o is the native Full LTO output used by the final link. Its code
    # relocations name the referenced guard; a symbol-table definition does not.
    references = re.findall(r'^.*\bR_AARCH64_\w+\s+__stack_chk_guard\b.*$', native, re.M)
    failure = re.findall(r'^.*\b(?:b|bl)\s+.*<__stack_chk_fail(?:\.[^>]*)?>.*$', linked, re.M)
    tls = re.findall(r'^.*\bmrs\s+[^\n]*\btpidr_el0\b.*$', linked, re.M | re.I)
    return {"global_guard_relocations": references, "stack_canary_failure": failure,
            "tpidr_el0_reads": tls, "passed": bool(references and failure and not tls)}


def check_linked_guard(out, function_names, disassembly_path):
    symbols = run_tool("llvm-nm", "--defined-only", "--just-symbol-name", out / "vmlinux").splitlines()
    result = {"functions": {}, "errors": []}
    excerpts = []
    for base in function_names:
        names = [name for name in symbols if name == base or name.startswith(base + ".")]
        if not names:
            result["errors"].append(f"Final ELF lacks selected SSP function: {base}")
            continue
        native = disassemble(out / "vmlinux.o", *names, relocations=True)
        linked = disassemble(out / "vmlinux", *names)
        proof = global_guard_references(native, linked)
        result["functions"][base] = proof
        excerpts.extend((native, linked))
        if not proof["passed"]:
            result["errors"].append(f"{base}: require global guard relocation, final canary branch and no TPIDR_EL0 read")
    disassembly_path.write_text("\n".join(excerpts))
    result["passed"] = not result["errors"]
    return result
