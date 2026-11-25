#!/usr/bin/env python3
"""Fail builds that lose the selected LOS protections or exceed its stack limit."""
import json
import pathlib
import re
import sys

mode, out_arg, dist_arg, stage = sys.argv[1:]
out, dist = pathlib.Path(out_arg), pathlib.Path(dist_arg)
cfg = (out / '.config').read_text()
required = ['LTO_CLANG', 'CFI_CLANG', 'SHADOW_CALL_STACK', 'REFCOUNT_FULL',
            'AUDIT', 'MODULES', 'MODVERSIONS', 'CC_STACKPROTECTOR_STRONG',
            'SECURITY_SELINUX', 'INIT_STACK_ALL_ZERO']
if stage == 'stage1':
    required += ['KSU', 'KPM', 'KSU_SUSFS', 'KSU_SUSFS_INLINE_HOOK']
errors = [f'CONFIG_{x}=y missing' for x in required if f'CONFIG_{x}=y\n' not in cfg]
for symbol in ['CFI_PERMISSIVE', 'THINLTO', 'KSU_DEBUG']:
    if f'CONFIG_{symbol}=y\n' in cfg:
        errors.append(f'CONFIG_{symbol} must remain off')
if 'CONFIG_FRAME_WARN=3072\n' not in cfg:
    errors.append('FRAME_WARN differs from 3072')
result = {'stage': stage, 'mode': mode, 'required_config': required, 'errors': errors}
if mode == 'linked':
    log = (dist / 'build.log').read_text(errors='replace')
    warnings = [line for line in log.splitlines() if re.search(r'frame size of .*larger than|frame-larger-than|stack frame size', line)]
    result['stack_warnings'] = warnings
    if warnings:
        errors.append('FRAME_WARN stack warning requires review')
    symbols = (out / 'System.map').read_text()
    for symbol in ['__cfi_check', '__cfi_slowpath', '__stack_chk_fail']:
        if not re.search(r'\b' + re.escape(symbol) + r'$', symbols, re.M):
            errors.append(f'missing linked symbol {symbol}')
    if stage == 'stage1':
        command = (out / 'drivers/kernelsu/.ksu.o.cmd').read_text()
        for flag in ['-flto', '-fsanitize=cfi', '-fsanitize=shadow-call-stack',
                     '-fstack-protector-strong', '-Wframe-larger-than=3072',
                     '-ftrivial-auto-var-init=zero']:
            if flag not in command:
                errors.append(f'Nova compiler command lacks {flag}')
        for flag in ['-fno-stack-protector']:
            if flag in command:
                errors.append(f'Nova compiler command disables protection: {flag}')
        (dist / 'nova-compile-command.txt').write_text(command)
        # SUSFS deliberately hides its names from kallsyms. The full ELF table
        # is checked separately before the KPM patch is applied.
result['passed'] = not errors
dist.mkdir(parents=True, exist_ok=True)
(dist / f'checks-{mode}.json').write_text(json.dumps(result, indent=2) + '\n')
if errors:
    raise SystemExit('\n'.join(errors))
print(f'{stage}: {mode} checks passed')
