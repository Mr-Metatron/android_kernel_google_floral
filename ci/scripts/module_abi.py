#!/usr/bin/env python3
"""Read ELF module version records and check the frozen stock vendor ABI."""
import argparse
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


def vermagic(sec):
    values = sec['.modinfo'].split(b'\0')
    return next(x.split(b'=', 1)[1].decode().strip()
                for x in values if x.startswith(b'vermagic='))


def add_crc(symbols, symbol, crc):
    if symbol in symbols and symbols[symbol] != crc:
        raise ValueError(f'Conflicting CRC for {symbol}: {symbols[symbol]} / {crc}')
    symbols[symbol] = crc


def required_symbols(sec):
    raw = sec['__versions']
    if len(raw) % 64:
        raise ValueError('Unexpected module version record size')
    versions = {}
    for pos in range(0, len(raw), 64):
        crc = struct.unpack_from('<Q', raw, pos)[0]
        symbol = raw[pos+8:pos+64].split(b'\0', 1)[0].decode()
        add_crc(versions, symbol, f'0x{crc:08x}')
    return versions


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest='command', required=True)
p = sub.add_parser('extract')
p.add_argument('directory', type=pathlib.Path)
p.add_argument('output', type=pathlib.Path)
p = sub.add_parser('check')
p.add_argument('out', type=pathlib.Path)
p.add_argument('reference', type=pathlib.Path)
p.add_argument('output', type=pathlib.Path)
args = parser.parse_args()
if args.command == 'extract':
    modules = sorted(args.directory.rglob('*.ko'))
    if not modules:
        raise SystemExit('No stock vendor modules found')
    symbols, magics = {}, set()
    for path in modules:
        sec = sections(path.read_bytes())
        magics.add(vermagic(sec))
        for symbol, crc in required_symbols(sec).items():
            add_crc(symbols, symbol, crc)
    if len(magics) != 1:
        raise SystemExit('Stock modules have different vermagic strings')
    result = {'vermagic': magics.pop(), 'symbols': dict(sorted(symbols.items()))}
else:
    reference = json.loads(args.reference.read_text())
    current = {}
    for line in (args.out / 'Module.symvers').read_text().splitlines():
        fields = line.split()
        if len(fields) >= 3:
            add_crc(current, fields[1], f'0x{int(fields[0], 16):08x}')
    modules = sorted(args.out.rglob('*.ko'))
    if not modules:
        raise SystemExit('No built modules found for full vermagic verification')
    errors = []
    built_magics = set()
    for path in modules:
        actual = vermagic(sections(path.read_bytes()))
        built_magics.add(actual)
        if actual != reference['vermagic']:
            errors.append({'module': str(path.relative_to(args.out)), 'field': 'vermagic',
                           'stock': reference['vermagic'], 'built': actual})
    for symbol, crc in reference['symbols'].items():
        if current.get(symbol) != crc:
            errors.append({'symbol': symbol, 'stock_crc': crc, 'built_crc': current.get(symbol)})
    result = {'vermagic': reference['vermagic'], 'built_vermagic': sorted(built_magics),
              'built_modules_checked': len(modules), 'symbols_checked': len(reference['symbols']),
              'errors': errors, 'passed': not errors,
              'scope': 'Full built-module vermagic and vendor import CRC comparison; device loading remains untested'}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
if args.command == 'check' and errors:
    raise SystemExit(f'{len(errors)} vendor module ABI mismatches: {args.output}')
print(args.output)
