#!/usr/bin/env python3
"""Read ELF module version records and check the frozen stock vendor ABI."""
import argparse
import hashlib
import json
import pathlib
import struct


def sections(data):
    if data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('Expected little-endian ELF64')
    offset = struct.unpack_from('<Q', data, 40)[0]
    stride, count, names_index = struct.unpack_from('<HHH', data, 58)
    headers = [struct.unpack_from('<IIQQQQIIQQ', data, offset + i * stride) for i in range(count)]
    names = headers[names_index]
    strings = data[names[4]:names[4]+names[5]]
    return {strings[h[0]:].split(b'\0', 1)[0].decode(): data[h[4]:h[4]+h[5]] for h in headers}


def module_record(path):
    data = path.read_bytes()
    sec = sections(data)
    values = sec['.modinfo'].split(b'\0')
    vermagic = next(x.split(b'=', 1)[1].decode() for x in values if x.startswith(b'vermagic='))
    raw = sec['__versions']
    if len(raw) % 64:
        raise ValueError('Unexpected module version record size')
    versions = {}
    for pos in range(0, len(raw), 64):
        crc = struct.unpack_from('<Q', raw, pos)[0]
        symbol = raw[pos+8:pos+64].split(b'\0', 1)[0].decode()
        versions[symbol] = f'0x{crc:08x}'
    return {'file': path.name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
            'vermagic': vermagic, 'required_symbols': dict(sorted(versions.items()))}


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest='command', required=True)
p = sub.add_parser('extract')
p.add_argument('directory', type=pathlib.Path)
p.add_argument('output', type=pathlib.Path)
p.add_argument('--device', required=True)
p.add_argument('--rom', required=True)
p.add_argument('--ota-sha256', required=True)
p = sub.add_parser('check')
p.add_argument('out', type=pathlib.Path)
p.add_argument('reference', type=pathlib.Path)
p.add_argument('output', type=pathlib.Path)
args = parser.parse_args()
if args.command == 'extract':
    modules = [module_record(p) for p in sorted(args.directory.glob('*.ko'))]
    if not modules:
        raise SystemExit('No stock vendor modules found')
    result = {'device': args.device, 'rom': args.rom, 'ota_sha256': args.ota_sha256,
              'source': 'Official OTA vendor/lib/modules; payload operation SHA256 verified',
              'modules': modules}
else:
    reference = json.loads(args.reference.read_text())
    current = {}
    for line in (args.out / 'Module.symvers').read_text().splitlines():
        fields = line.split()
        if len(fields) >= 3:
            current[fields[1]] = fields[0].lower()
    release = (args.out / 'include/config/kernel.release').read_text().strip()
    expected_vermagic = release + ' SMP preempt mod_unload modversions aarch64'
    errors = []
    checked = 0
    for module in reference['modules']:
        if module['vermagic'].strip() != expected_vermagic:
            errors.append({'module': module['file'], 'field': 'vermagic', 'stock': module['vermagic'], 'built': expected_vermagic})
        for symbol, crc in module['required_symbols'].items():
            checked += 1
            if current.get(symbol) != crc:
                errors.append({'module': module['file'], 'symbol': symbol, 'stock_crc': crc, 'built_crc': current.get(symbol)})
    result = {'device': reference['device'], 'rom': reference['rom'], 'kernel_release': release,
              'modules_checked': len(reference['modules']), 'import_records_checked': checked,
              'errors': errors, 'passed': not errors,
              'scope': 'Exact release and exported symbol CRC comparison; device loading remains untested'}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
if args.command == 'check' and errors:
    raise SystemExit(f'{len(errors)} vendor module ABI mismatches: {args.output}')
print(args.output)
