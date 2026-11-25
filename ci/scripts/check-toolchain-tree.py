#!/usr/bin/env python3
"""Verify an extracted prebuilt against its immutable Git directory object.

Gitiles archives may differ in archive metadata. Git's tree identity covers
every file, executable bit, directory and symlink independently of that wrapper.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


def object_hash(kind, payload):
    return hashlib.sha1(kind + b' ' + str(len(payload)).encode() + b'\0' + payload).digest()


def tree_hash(directory, counts):
    entries = []
    for path in directory.iterdir():
        info = path.lstat()
        name = os.fsencode(path.name)
        if stat.S_ISLNK(info.st_mode):
            mode = b'120000'
            content = os.fsencode(os.readlink(path))
            digest = object_hash(b'blob', content)
            counts['symlinks'] += 1
            key = name
        elif stat.S_ISDIR(info.st_mode):
            mode = b'40000'
            digest = tree_hash(path, counts)
            counts['directories'] += 1
            key = name + b'/'
        elif stat.S_ISREG(info.st_mode):
            mode = b'100755' if info.st_mode & 0o111 else b'100644'
            h = hashlib.sha1(b'blob ' + str(info.st_size).encode() + b'\0')
            with path.open('rb') as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    h.update(block)
            digest = h.digest()
            counts['files'] += 1
            counts['bytes'] += info.st_size
            key = name
        else:
            raise SystemExit('Unexpected file type in toolchain: ' + str(path))
        entries.append((key, mode + b' ' + name + b'\0' + digest))
    payload = b''.join(entry for _, entry in sorted(entries))
    return object_hash(b'tree', payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('expected_tree')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    counts = {'files': 0, 'symlinks': 0, 'directories': 0, 'bytes': 0}
    actual = tree_hash(args.directory, counts).hex()
    result = dict(expected_tree=args.expected_tree, actual_tree=actual,
                  passed=actual == args.expected_tree, **counts)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    if not result['passed']:
        raise SystemExit('Extracted toolchain differs from the pinned Git tree')


if __name__ == '__main__':
    main()
