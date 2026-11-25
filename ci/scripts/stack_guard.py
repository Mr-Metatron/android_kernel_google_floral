"""Shared ARM64 global stack-canary checks for this frozen 4.14 kernel.

The kernel initializes __stack_chk_guard. Clang's Android target instead uses
the userspace TLS slot at TPIDR_EL0 + 40, even with -fstack-protector-strong.
Check the real compiler command/IR and resolve the final selected functions'
guard loads against the ELF symbol address; __stack_chk_fail alone is not proof.
"""

import hashlib
import re
import shlex
import shutil
import subprocess


EXPECTED_COMMAND_TARGET = "aarch64-linux-gnu"
EXPECTED_IR_TARGET = "aarch64-unknown-linux-gnu"


def run_tool(name, *args):
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"Required LLVM tool is unavailable: {name}")
    result = subprocess.run([executable, *map(str, args)], text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{name} failed: {result.stderr.strip()[:2000]}")
    return result.stdout


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compiler_target_check(command, ir):
    """Fail closed on target changes or per-command/per-IR guard overrides."""
    tokens = shlex.split(command.split(":=", 1)[-1])
    targets = []
    for index, token in enumerate(tokens):
        if token.startswith(("--target=", "-target=")):
            targets.append(token.split("=", 1)[1])
        elif token in ("--target", "-target") and index + 1 < len(tokens):
            targets.append(tokens[index + 1])
    match = re.search(r'^target triple = "([^"]+)"$', ir, re.M)
    ir_target = match.group(1) if match else None
    overrides = [token for token in tokens if "stack-protector-guard" in token]
    ir_overrides = [line.strip() for line in ir.splitlines()
                    if '"stack-protector-guard' in line]
    errors = []
    if targets != [EXPECTED_COMMAND_TARGET]:
        errors.append(f"Compiler target must be {EXPECTED_COMMAND_TARGET}; got {targets}")
    if ir_target != EXPECTED_IR_TARGET:
        errors.append(f"LLVM IR target must be {EXPECTED_IR_TARGET}; got {ir_target}")
    if overrides or ir_overrides:
        errors.append("Unexpected stack-protector guard override changes the frozen ABI")
    if "-fstack-protector-strong" not in tokens or "-fno-stack-protector" in tokens:
        errors.append("Compiler command must retain -fstack-protector-strong")
    return {"expected_command_target": EXPECTED_COMMAND_TARGET,
            "command_targets": targets, "expected_ir_target": EXPECTED_IR_TARGET,
            "ir_target": ir_target, "guard_overrides": overrides,
            "ir_guard_overrides": ir_overrides, "errors": errors,
            "passed": not errors}


def object_target_check(out, relative):
    obj = out / relative
    command_path = obj.parent / ("." + obj.name + ".cmd")
    command = command_path.read_text().splitlines()[0]
    ir = run_tool("llvm-dis", obj, "-o", "-")
    result = compiler_target_check(command, ir)
    result.update(object=relative, object_sha256=file_sha256(obj),
                  compile_command=command)
    return result


def elf_symbols(vmlinux):
    output = run_tool("llvm-nm", "--defined-only", "--format=posix", vmlinux)
    symbols = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            symbols[fields[0]] = {"type": fields[1], "address": int(fields[2], 16)}
    return symbols


def _number(value):
    value = value.strip().lstrip("#")
    return int(value, 16 if "0x" in value.lower() else 10)


def _register(value):
    value = value.strip().lower()
    if re.fullmatch(r"[xw]\d+", value):
        return "x" + value[1:]
    return {"fp": "x29", "lr": "x30", "wsp": "sp"}.get(value, value)


def _instructions(disassembly):
    for line in disassembly.splitlines():
        match = re.match(r"^\s*([0-9a-f]+):\s+([a-z][a-z0-9.]*)\s*(.*?)\s*$",
                         line, re.I)
        if match:
            yield int(match[1], 16), match[2].lower(), match[3].split("//")[0].strip(), line.strip()


def guard_instructions(disassembly, guard_address, failure_address):
    """Trace the small address/load/store patterns emitted by ARM64 Clang.

    This is a bounded instruction-evidence check, not a general ARM emulator.
    Only the selected SSP functions are inspected. Normal TPIDR_EL0 reads in
    other kernel functions are permitted; the Android guard slot is rejected
    when it is loaded in a selected SSP function. Unknown register writes clear
    provenance, and calls clear caller-saved registers.
    """
    registers = {}
    stack_slots = {}
    proof = {"guard_symbol": "__stack_chk_guard", "guard_address": hex(guard_address),
             "global_guard_loads": [], "guard_stack_stores": [],
             "guard_comparisons": [], "stack_canary_failure": [],
             "tls_guard_loads": []}
    for address, op, operands, line in _instructions(disassembly):
        parts = [part.strip() for part in operands.split(",")]
        dest = _register(parts[0]) if parts else ""
        if op in ("b", "bl"):
            target = re.match(r"(?:0x)?([0-9a-f]+)\b", operands, re.I)
            if target and int(target[1], 16) == failure_address:
                proof["stack_canary_failure"].append(line)
        if op in ("bl", "blr"):
            for index in (*range(18), 30):
                registers.pop(f"x{index}", None)
            continue
        if op in ("adr", "adrp") and len(parts) >= 2:
            target = re.match(r"(?:0x)?([0-9a-f]+)\b", parts[1], re.I)
            if target:
                registers[dest] = ("address", int(target[1], 16))
            else:
                registers.pop(dest, None)
            continue
        if op == "mrs":
            registers.pop(dest, None)
            if len(parts) == 2 and parts[1].lower() == "tpidr_el0":
                registers[dest] = ("tls", 0)
            continue
        if op == "mov" and len(parts) == 2:
            source = registers.get(_register(parts[1]))
            registers.pop(dest, None)
            if source is not None and parts[0].startswith("x"):
                registers[dest] = source
            continue
        if op in ("add", "sub") and len(parts) >= 3:
            source = registers.get(_register(parts[1]))
            registers.pop(dest, None)
            if source and source[0] in ("address", "tls") and parts[2].startswith("#"):
                offset = _number(parts[2])
                if len(parts) == 4 and parts[3].startswith("lsl #"):
                    offset <<= _number(parts[3][4:])
                registers[dest] = (source[0], source[1] + (offset if op == "add" else -offset))
            continue
        if op in ("ldr", "ldur", "str", "stur"):
            memory = re.fullmatch(r"([xw]\d+|[xw]zr|lr|fp),\s*\[(\w+)(?:,\s*(#[^\]]+))?\]!?",
                                  operands, re.I)
            if memory:
                reg, base = _register(memory[1]), _register(memory[2])
                offset = _number(memory[3]) if memory[3] else 0
                location = (base, offset)
                base_value = registers.get(base)
                if op in ("ldr", "ldur"):
                    registers.pop(reg, None)
                    if memory[1].startswith("x") and base_value:
                        kind, value = base_value
                        if kind == "address" and value + offset == guard_address:
                            registers[reg] = ("global_guard", 0)
                            proof["global_guard_loads"].append(line)
                        elif kind == "tls" and value + offset == 40:
                            registers[reg] = ("tls_guard", 0)
                            proof["tls_guard_loads"].append(line)
                    if base in ("sp", "x29") and location in stack_slots:
                        registers[reg] = ("saved_" + stack_slots[location], 0)
                elif base in ("sp", "x29"):
                    stack_slots.pop(location, None)
                    source = registers.get(reg)
                    if source and source[0] in ("global_guard", "tls_guard"):
                        stack_slots[location] = source[0]
                        if source[0] == "global_guard":
                            proof["guard_stack_stores"].append(line)
                continue
            if op in ("ldr", "ldur"):
                registers.pop(dest, None)
            continue
        if op in ("cmp", "subs", "eor", "eors"):
            compared = parts[:2] if op == "cmp" else parts[1:3]
            kinds = {registers.get(_register(value), (None,))[0] for value in compared}
            if {"global_guard", "saved_global_guard"} <= kinds:
                proof["guard_comparisons"].append(line)
        if op in ("ldp", "ldnp"):
            # Canary loads are single-register loads in the frozen compiler's
            # selected functions. A changed pattern must be reviewed explicitly.
            for value in parts[:2]:
                registers.pop(_register(value), None)
        elif op not in ("str", "stur", "stp", "stnp", "cmp", "cmn", "tst", "prfm",
                         "ret", "b", "br", "cbz", "cbnz", "tbz", "tbnz") and not op.startswith("b."):
            registers.pop(dest, None)
    proof["passed"] = bool(proof["global_guard_loads"] and proof["guard_stack_stores"]
                            and proof["guard_comparisons"] and proof["stack_canary_failure"]
                            and not proof["tls_guard_loads"])
    return proof


def check_linked_guard(vmlinux, function_names, disassembly_path):
    symbols = elf_symbols(vmlinux)
    errors = []
    result = {"functions": {}, "errors": errors}
    for required in ("__stack_chk_guard", "__stack_chk_fail"):
        if required not in symbols:
            errors.append(f"Final ELF lacks required symbol: {required}")
    excerpts = []
    if not errors:
        for base in function_names:
            names = [name for name, entry in symbols.items()
                     if entry["type"] in ("t", "T", "w", "W") and
                     (name == base or name.startswith(base + "."))]
            if not names:
                errors.append(f"Final ELF lacks selected SSP function: {base}")
                continue
            candidates = []
            for name in sorted(names):
                assembly = run_tool("llvm-objdump", "--no-show-raw-insn",
                                    f"--disassemble-symbols={name}", vmlinux)
                proof = guard_instructions(assembly, symbols["__stack_chk_guard"]["address"],
                                           symbols["__stack_chk_fail"]["address"])
                proof["function"] = name
                candidates.append(proof)
                excerpts.append(assembly)
            result["functions"][base] = candidates
            if any(proof["tls_guard_loads"] and proof["stack_canary_failure"] for proof in candidates):
                errors.append(f"{base} uses the Android userspace TLS canary slot")
            if not any(proof["passed"] for proof in candidates):
                errors.append(f"{base} lacks complete global __stack_chk_guard instruction evidence")
        result["guard_address"] = hex(symbols["__stack_chk_guard"]["address"])
        result["failure_address"] = hex(symbols["__stack_chk_fail"]["address"])
    disassembly_path.write_text("\n".join(excerpts))
    result["passed"] = not errors
    return result
