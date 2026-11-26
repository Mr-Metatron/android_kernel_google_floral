#!/usr/bin/env python3
"""Check container interfaces and built-in vendor drivers without claiming device tests."""
import json
import pathlib
import re
import subprocess
import sys


mode, out_arg, dist_arg, stage = sys.argv[1:]
if mode not in ('config', 'linked') or stage not in ('stage2', 'stage3'):
    raise SystemExit('Expected: check-containers.py {config|linked} OUT DIST {stage2|stage3}')
root = pathlib.Path(__file__).resolve().parents[2]
out, dist = pathlib.Path(out_arg), pathlib.Path(dist_arg)
dist.mkdir(parents=True, exist_ok=True)


def read_config(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith('CONFIG_') and '=' in line:
            key, value = line.split('=', 1)
            values[key.removeprefix('CONFIG_')] = value
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            values[line[len('# CONFIG_'):-len(' is not set')]] = 'n'
    return values


cfg = read_config(out / '.config')
fragment = read_config(root / 'ci/configs/stage2.config')
groups = {
    'namespaces_and_ipc': ['NAMESPACES', 'UTS_NS', 'NET_NS', 'PID_NS',
                           'IPC_NS', 'USER_NS', 'SYSVIPC', 'POSIX_MQUEUE'],
    'resource_control': ['CGROUPS', 'CGROUP_SCHED', 'CGROUP_CPUACCT',
                         'CPUSETS', 'CGROUP_FREEZER', 'CGROUP_BPF',
                         'CGROUP_PIDS', 'CGROUP_DEVICE', 'FAIR_GROUP_SCHED',
                         'CFS_BANDWIDTH', 'MEMCG', 'MEMCG_SWAP',
                         'MEMCG_SWAP_ENABLED', 'PAGE_COUNTER', 'BLK_CGROUP',
                         'CGROUP_WRITEBACK'],
    'storage': ['OVERLAY_FS', 'EXT4_FS', 'F2FS_FS', 'FUSE_FS', 'BLK_DEV_LOOP',
                'TMPFS', 'TMPFS_XATTR', 'FHANDLE'],
    'network': ['BRIDGE', 'VETH', 'TUN', 'NETFILTER', 'NETFILTER_ADVANCED',
                'NETFILTER_XTABLES', 'NF_CONNTRACK', 'NF_CONNTRACK_IPV4',
                'NF_CONNTRACK_IPV6', 'NF_NAT', 'NF_NAT_IPV4', 'IP_NF_NAT',
                'IP_NF_TARGET_MASQUERADE', 'NETFILTER_XT_MATCH_CONNTRACK',
                'NETFILTER_XT_MATCH_ADDRTYPE', 'NETFILTER_XT_TARGET_CHECKSUM',
                'IPV6', 'IP6_NF_IPTABLES', 'IP6_NF_FILTER', 'NF_NAT_IPV6',
                'IP6_NF_NAT', 'NF_NAT_MASQUERADE_IPV6',
                'IP6_NF_TARGET_MASQUERADE'],
    'builtin_drivers': ['QCA_CLD_WLAN', 'MSM_11AD', 'INCREMENTAL_FS', 'SOFT_WATCHDOG'],
    'host_interfaces': ['SECCOMP', 'SECCOMP_FILTER', 'SECURITY_SELINUX',
                        'AUDIT', 'BPF_SYSCALL', 'PSI', 'SCHED_TUNE',
                        'MODULES', 'MODULE_UNLOAD', 'MODVERSIONS',
                        'STATIC_USERMODEHELPER'],
}
errors = []
# This make fragment supplies audio settings outside the generated .config.
audio = read_config(root / 'techpack/audio/config/sm8150auto.conf')
if not audio or any(value != 'y' for value in audio.values()):
    errors.append('All sm8150 audio make settings must be built in (=y)')
for name, expected in fragment.items():
    if cfg.get(name, 'n') != expected:
        errors.append(f'Fragment CONFIG_{name}: expected {expected}, got {cfg.get(name, "n")}')
for names in groups.values():
    for name in names:
        if cfg.get(name) != 'y':
            errors.append(f'Required built-in CONFIG_{name}=y missing')
for name in ('BRIDGE_NETFILTER', 'BLK_DEV_THROTTLING', 'SCHED_WALT',
             'SECURITY_SELINUX_DISABLE'):
    if cfg.get(name, 'n') != 'n':
        errors.append(f'CONFIG_{name} must remain disabled')
if cfg.get('STATIC_USERMODEHELPER_PATH') != '""':
    errors.append('STATIC_USERMODEHELPER_PATH must remain empty')

result = {
    'stage': stage,
    'mode': mode,
    'source_commit': subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
    'configuration': {group: {name: cfg.get(name, 'n') for name in names}
                      for group, names in groups.items()},
    'fragment': fragment,
    'audio_configuration': audio,
    'errors': errors,
    'device_tested': False,
    'external_conditions': [
        'Use an existing ARM64 Docker/LXC environment and rootfs.',
        'The Android CPU-v1/memory-v2 layout needs external runtime-compatible controller setup.',
        'Preserve the Android cpuset noprefix interface and use a compatible runtime.',
        'BRIDGE_NETFILTER remains disabled; bridge-hook-dependent filtering is untested.',
        'IPv4/IPv6 NAT, port publishing, limits and teardown require on-device validation.',
        'SELinux Enforcing, App/GMS, LMKD, Power HAL and phone functions require on-device validation.',
    ],
}
if mode == 'linked':
    # Read the actual ELF, since LTO may suffix local symbols and SUSFS filters
    # its own names from the runtime kallsyms view.
    symbols = subprocess.check_output(
        ['llvm-nm', '--defined-only', str(out / 'vmlinux')], text=True)
    names = {line.split()[-1] for line in symbols.splitlines() if line.split()}
    required_symbols = [
        'cpu_cgrp_subsys', 'memory_cgrp_subsys', 'pids_cgrp_subsys',
        'devices_cgrp_subsys', 'cpuset_cgrp_subsys', 'cgroupns_operations',
        'pidns_operations', 'userns_operations', 'ipcns_operations',
        'nf_nat_ipv6_fn', 'nf_nat_masquerade_ipv6',
        'sys_pidfd_open', 'sys_pidfd_send_signal', 'sys_clone3',
        'sys_name_to_handle_at', 'sys_open_by_handle_at',
    ]
    linked = {}
    for symbol in required_symbols:
        matches = sorted(name for name in names
                         if name == symbol or name.startswith(symbol + '.'))
        linked[symbol] = matches
        if not matches:
            errors.append(f'Missing linked container interface: {symbol}')
    result['linked_interfaces'] = linked
    # Initcall references survive LTO even when their function bodies are folded.
    driver_entries = ['hdd_module_init', 'adsp_loader_init', 'init_incfs_module',
                      'softdog_init', 'msm_11ad_driver_init']
    result['builtin_driver_entries'] = {}
    for symbol in driver_entries:
        initcall = r'__initcall_(?:.*_)?' + re.escape(symbol) + r'\d+s?(?:\..*)?'
        matches = sorted(name for name in names
                         if name == symbol or name.startswith(symbol + '.')
                         or re.fullmatch(initcall, name))
        result['builtin_driver_entries'][symbol] = matches
        if not matches:
            errors.append(f'Missing linked built-in driver entry: {symbol}')
    modules = sorted(str(path.relative_to(out)) for path in out.rglob('*.ko'))
    result['loadable_modules'] = modules
    if modules:
        errors.append('Unexpected loadable driver outputs: ' + ', '.join(modules))
    # These objects prove the selected table/match/target providers were
    # built; their static entry points can be folded into LTO initcall data.
    objects = [
        'net/netfilter/xt_addrtype.o', 'net/netfilter/xt_CHECKSUM.o',
        'net/ipv6/netfilter/nf_nat_l3proto_ipv6.o',
        'net/ipv6/netfilter/nf_nat_masquerade_ipv6.o',
        'net/ipv6/netfilter/ip6table_nat.o',
        'net/ipv6/netfilter/ip6t_MASQUERADE.o',
        'mm/memcontrol.o', 'kernel/cgroup/pids.o', 'security/device_cgroup.o',
        'drivers/staging/qcacld-3.0/core/hdd/src/wlan_hdd_main.o',
        'techpack/audio/dsp/adsp-loader.o', 'fs/incfs/main.o',
        'drivers/watchdog/softdog.o', 'drivers/platform/msm/msm_11ad/msm_11ad.o',
    ]
    result['built_objects'] = {path: (out / path).is_file() for path in objects}
    for path, present in result['built_objects'].items():
        if not present:
            errors.append(f'Missing container object: {path}')

result['passed'] = not errors
(dist / f'{stage}-container-capabilities.json').write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + '\n')
if errors:
    raise SystemExit('\n'.join(errors))
print(f'{stage}: container {mode} checks passed')
