#!/usr/bin/env python3
"""Read ELF module version records and check the frozen stock vendor ABI."""
import argparse
import json
import pathlib
import re
import shutil
import struct
import tempfile


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


def bundle(args):
    """Validate a complete replacement set without relaxing the stock check."""
    args.dist.mkdir(parents=True, exist_ok=True)
    output = args.dist / 'module-bundle.json'
    destination = args.dist / 'vendor-modules'
    errors, records, expected = [], [], []
    reference_magic = None
    kernel_release = None
    temporary = None
    try:
        if not re.fullmatch(r'[0-9a-f]{40}', args.source_commit):
            raise ValueError('source_commit must be a full Git commit SHA')
        manifest = json.loads((args.dist / 'build-manifest.json').read_text())
        if (manifest.get('stage') != 'stage2' or
                manifest.get('source_commit') != args.source_commit or
                manifest.get('source_dirty') is not False):
            raise ValueError('Replacement bundle requires the same clean Stage 2 build manifest')
        stock = json.loads((args.dist / 'module-abi.json').read_text())
        if stock.get('passed') is not False or not stock.get('errors'):
            raise ValueError('Replacement bundle requires the failed stock ABI report')
        for error in stock['errors']:
            if (not isinstance(error, dict) or set(error) != {'symbol', 'stock_crc', 'built_crc'} or
                    not isinstance(error['symbol'], str) or not error['symbol'] or
                    any(not isinstance(error[key], str) or
                        not re.fullmatch(r'0x[0-9a-f]{8}', error[key])
                        for key in ('stock_crc', 'built_crc')) or
                    error['stock_crc'] == error['built_crc']):
                raise ValueError('Only changed CRCs of existing exports permit a replacement bundle')
        reference = json.loads(args.reference.read_text())
        expected = reference['modules']
        if (not expected or len(expected) != len(set(expected)) or
                any(pathlib.Path(name).name != name or not name.endswith('.ko')
                    for name in expected)):
            raise ValueError('Reference module filenames must be unique .ko basenames')
        expected = sorted(expected)
        reference_magic = reference['vermagic']
        kernel_release = (args.out / 'include/config/kernel.release').read_text().strip()
        if reference_magic.split()[0] != kernel_release:
            raise ValueError('Build kernel release differs from reference vermagic')
        current = {}
        for line in (args.out / 'Module.symvers').read_text().splitlines():
            fields = line.split()
            if len(fields) >= 3:
                add_crc(current, fields[1], f'0x{int(fields[0], 16):08x}')
        mismatches = [{'symbol': symbol, 'stock_crc': crc, 'built_crc': current.get(symbol)}
                      for symbol, crc in reference['symbols'].items() if current.get(symbol) != crc]
        if (stock['errors'] != mismatches or stock.get('vermagic') != reference_magic or
                stock.get('built_vermagic') != [reference_magic] or
                stock.get('symbols_checked') != len(reference['symbols'])):
            raise ValueError('Stock ABI report does not match the current reference and Module.symvers')
        paths = {}
        for path in sorted(args.out.rglob('*.ko')):
            if path.name in paths:
                errors.append({'module': path.name, 'error': 'duplicate built filename'})
            else:
                paths[path.name] = path
        for name in sorted(set(expected) - paths.keys()):
            errors.append({'module': name, 'error': 'missing required module'})
        for name in sorted(paths.keys() - set(expected)):
            errors.append({'module': name, 'error': 'unexpected built module'})
        if stock.get('built_modules_checked') != len(paths):
            errors.append({'error': 'Stock ABI report has a different built module count'})
        for name in expected:
            if name not in paths:
                continue
            path = paths[name]
            try:
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Built module must be a regular file')
                data = path.read_bytes()
                if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
                    raise ValueError('Expected little-endian ELF64 version 1')
                if struct.unpack_from('<HHI', data, 16) != (1, 183, 1):
                    raise ValueError('Expected ARM64 relocatable ELF module')
                sec = sections(data)
                actual_magic = vermagic(sec)
                imports = required_symbols(sec)
                if 'module_layout' not in imports:
                    raise ValueError('Module has no module_layout version record')
                records.append({'file': name, 'relative_path': name,
                                'build_path': str(path.relative_to(args.out)),
                                'vermagic': actual_magic, 'imports_checked': len(imports)})
                if actual_magic != reference_magic:
                    errors.append({'module': name, 'field': 'vermagic',
                                   'expected': reference_magic, 'built': actual_magic})
                for symbol, crc in imports.items():
                    if current.get(symbol) != crc:
                        errors.append({'module': name, 'symbol': symbol,
                                       'module_crc': crc, 'build_crc': current.get(symbol)})
            except (OSError, ValueError, KeyError, IndexError, StopIteration, struct.error) as exc:
                errors.append({'module': name, 'error': str(exc) or type(exc).__name__})
        # A failed rerun must not leave an older replacement set in Artifacts.
        if destination.exists():
            shutil.rmtree(destination)
        if not errors:
            temporary = pathlib.Path(tempfile.mkdtemp(prefix='vendor-modules.tmp-', dir=args.dist))
            for name in expected:
                shutil.copy2(paths[name], temporary / name)
            temporary.replace(destination)
            temporary = None
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        errors.append({'error': str(exc) or type(exc).__name__})
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
        if errors and destination.exists():
            shutil.rmtree(destination)
    result = {'source_commit': args.source_commit, 'kernel_release': kernel_release,
              'vermagic': reference_magic, 'required_modules': expected,
              'modules': records, 'modules_checked': len(records),
              'complete_coverage': not errors and len(records) == len(expected),
              'requires_vendor_module_replacement': True,
              'stock_abi_report': 'module-abi.json', 'device_tested': False,
              'errors': errors, 'passed': not errors,
              'scope': 'Complete ARM64 module set, full vermagic and all import CRCs against the same build; device loading remains untested'}
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if errors:
        raise SystemExit(f'{len(errors)} replacement module bundle errors: {output}')
    print(output)


parser = argparse.ArgumentParser()
sub = parser.add_subparsers(dest='command', required=True)
p = sub.add_parser('extract')
p.add_argument('directory', type=pathlib.Path)
p.add_argument('output', type=pathlib.Path)
p = sub.add_parser('check')
p.add_argument('out', type=pathlib.Path)
p.add_argument('reference', type=pathlib.Path)
p.add_argument('output', type=pathlib.Path)
p = sub.add_parser('bundle')
p.add_argument('out', type=pathlib.Path)
p.add_argument('reference', type=pathlib.Path)
p.add_argument('dist', type=pathlib.Path)
p.add_argument('--source-commit', required=True)
args = parser.parse_args()
if args.command == 'bundle':
    bundle(args)
    raise SystemExit(0)
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
