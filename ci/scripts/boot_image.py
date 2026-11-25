#!/usr/bin/env python3
"""Lossless Pixel 4 boot-v2 kernel replacement, including its unsigned AVB hash.

Only the pinned 64 MiB, 4096-byte-page v2 layout is accepted. The original
compressed ramdisk, DTB, second/recovery sections, metadata and header bytes are
retained. This does not sign an image or alter the device's AVB configuration.
Structure references: AOSP bootimg.h, avb_footer.h and avb_hash_descriptor.h.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys

PARTITION_SIZE = 64 * 1024 * 1024
PAGE_SIZE = 4096
HEADER_SIZE = 1660
FOOTER_SIZE = 64
SECTION_NAMES = ("kernel", "ramdisk", "second", "recovery_dtbo", "dtb")


class ImageError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ImageError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def align(size, page=PAGE_SIZE):
    return (size + page - 1) // page * page


def u32(data, offset, endian="<"):
    return struct.unpack_from(endian + "I", data, offset)[0]


def u64(data, offset, endian="<"):
    return struct.unpack_from(endian + "Q", data, offset)[0]


def cstring(data):
    return data.split(b"\0", 1)[0].decode("utf-8", errors="strict")


class BootImage:
    def __init__(self, data):
        self.data = bytes(data)
        require(len(data) == PARTITION_SIZE, "boot partition must be exactly 64 MiB")
        require(data[:8] == b"ANDROID!", "missing Android boot magic")
        require(u32(data, 40) == 2, "only boot header v2 is supported")
        require(u32(data, 36) == PAGE_SIZE, "boot page size must be 4096")
        require(u32(data, 1644) == HEADER_SIZE, "incorrect boot-v2 header size")
        self.header = data[:PAGE_SIZE]
        sizes = (u32(data, 8), u32(data, 16), u32(data, 24),
                 u32(data, 1632), u32(data, 1648))
        require(sizes[0] > 0 and sizes[1] > 0 and sizes[4] > 0,
                "kernel, ramdisk and header DTB must all be present")
        self.sections = {}
        self.pads = {}
        self.offsets = {}
        offset = PAGE_SIZE
        for name, size in zip(SECTION_NAMES, sizes):
            require(offset + align(size) <= PARTITION_SIZE - FOOTER_SIZE,
                    f"{name} is outside the boot partition")
            if name == "recovery_dtbo":
                require(u64(data, 1636) == (offset if size else 0),
                        "noncanonical recovery_dtbo offset")
            self.offsets[name] = offset
            self.sections[name] = data[offset:offset + size]
            self.pads[name] = data[offset + size:offset + align(size)]
            offset += align(size)
        self.payload_end = offset
        require(self.sections["dtb"][:4] == b"\xd0\x0d\xfe\xed", "invalid header DTB magic")
        require(data[576:608] == self.boot_id() + bytes(12), "boot SHA1 ID mismatch")
        self._parse_avb()

    @classmethod
    def read(cls, path):
        return cls(Path(path).read_bytes())

    def boot_id(self):
        digest = hashlib.sha1()
        for name in SECTION_NAMES:
            section = self.sections[name]
            digest.update(section)
            digest.update(struct.pack("<I", len(section)))
        return digest.digest()

    def _parse_avb(self):
        self.footer = self.data[-FOOTER_SIZE:]
        magic, major, minor, original, offset, size, reserved = struct.unpack(
            ">4sIIQQQ28s", self.footer)
        require(magic == b"AVBf" and (major, minor) == (1, 0), "unsupported AVB footer")
        require(original == self.payload_end and offset == self.payload_end,
                "AVB footer does not follow the aligned boot payload")
        require(offset + size <= len(self.data) - PAGE_SIZE, "AVB metadata overlaps footer page")
        require(size >= 256, "truncated AVB metadata")
        require(not any(self.data[offset + size:-FOOTER_SIZE]), "unknown data after AVB metadata")
        self.vbmeta_offset = offset
        self.vbmeta = self.data[offset:offset + size]
        require(self.vbmeta[:4] == b"AVB0", "missing AVB metadata header")
        auth_size, aux_size = u64(self.vbmeta, 12, ">"), u64(self.vbmeta, 20, ">")
        require(u32(self.vbmeta, 28, ">") == 0 and auth_size == 0,
                "signed AVB metadata needs the signing key; refusing to invalidate it")
        require(256 + auth_size + aux_size == size, "AVB metadata size mismatch")
        require(u64(self.vbmeta, 40, ">") == 0 and u64(self.vbmeta, 56, ">") == 0,
                "unexpected authentication fields in unsigned AVB metadata")
        require(u64(self.vbmeta, 72, ">") == 0 and u64(self.vbmeta, 88, ">") == 0,
                "unexpected public key fields in unsigned AVB metadata")
        desc_offset = u64(self.vbmeta, 96, ">")
        desc_size = u64(self.vbmeta, 104, ">")
        require(desc_offset + desc_size <= aux_size, "AVB descriptor region out of bounds")
        position = 256 + auth_size + desc_offset
        end = position + desc_size
        self.descriptors = []
        self.hash_descriptor = None
        while position < end:
            require(position + 16 <= end, "truncated AVB descriptor")
            tag, body_size = struct.unpack_from(">QQ", self.vbmeta, position)
            desc_end = position + 16 + body_size
            require(body_size % 8 == 0 and desc_end <= end, "invalid AVB descriptor size")
            desc = self.vbmeta[position:desc_end]
            self.descriptors.append((tag, desc))
            if tag == 2:
                require(self.hash_descriptor is None, "multiple AVB hash descriptors")
                require(len(desc) >= 132, "truncated AVB hash descriptor")
                name_len, salt_len, digest_len = struct.unpack_from(">III", desc, 56)
                require(132 + name_len + salt_len + digest_len <= len(desc),
                        "AVB hash descriptor payload out of bounds")
                name = desc[132:132 + name_len]
                require(name == b"boot", "AVB hash descriptor is not for boot")
                algorithm = cstring(desc[24:56])
                require(algorithm == "sha256" and digest_len == 32,
                        "only a SHA256 boot hash descriptor is supported")
                image_size = u64(desc, 16, ">")
                require(image_size == self.payload_end, "AVB hashed image size mismatch")
                salt = desc[132 + name_len:132 + name_len + salt_len]
                digest_offset = 132 + name_len + salt_len
                digest = desc[digest_offset:digest_offset + digest_len]
                calculated = hashlib.sha256(salt + self.data[:image_size]).digest()
                require(digest == calculated, "AVB boot hash mismatch")
                self.hash_descriptor = {
                    "offset": position, "image_size": image_size,
                    "digest_offset": position + digest_offset,
                    "salt": salt, "digest": digest,
                }
            position = desc_end
        require(position == end and self.hash_descriptor is not None,
                "missing AVB boot hash descriptor")

    def repack(self, kernel):
        require(0 < len(kernel) < 2 ** 32, "invalid kernel length")
        header = bytearray(self.header)
        struct.pack_into("<I", header, 8, len(kernel))
        payload = bytearray(header)
        digest = hashlib.sha1()
        for name in SECTION_NAMES:
            section = kernel if name == "kernel" else self.sections[name]
            if name == "recovery_dtbo" and section:
                struct.pack_into("<Q", payload, 1636, len(payload))
            digest.update(section)
            digest.update(struct.pack("<I", len(section)))
            payload.extend(section)
            pad_size = align(len(section)) - len(section)
            old_pad = self.pads[name]
            if len(old_pad) == pad_size:
                payload.extend(old_pad)
            else:
                require(not any(old_pad), f"cannot resize nonzero {name} alignment padding")
                payload.extend(bytes(pad_size))
        payload[576:608] = digest.digest() + bytes(12)
        image_size = len(payload)
        vbmeta = bytearray(self.vbmeta)
        desc = self.hash_descriptor
        struct.pack_into(">Q", vbmeta, desc["offset"] + 16, image_size)
        digest = hashlib.sha256(desc["salt"] + payload).digest()
        vbmeta[desc["digest_offset"]:desc["digest_offset"] + len(digest)] = digest
        require(image_size + align(len(vbmeta)) + PAGE_SIZE <= PARTITION_SIZE,
                "new kernel does not fit the boot partition with AVB footer space")
        footer = bytearray(self.footer)
        struct.pack_into(">QQ", footer, 12, image_size, image_size)
        output = payload + vbmeta
        output.extend(bytes(PARTITION_SIZE - FOOTER_SIZE - len(output)))
        output.extend(footer)
        result = BootImage(output)
        self.compare(result, kernel)
        return result

    def compare(self, other, kernel=None):
        require(len(self.data) == len(other.data), "partition size changed")
        old_header, new_header = bytearray(self.header), bytearray(other.header)
        # The kernel length, boot ID, and an optional recovery offset are derived.
        for start, end in ((8, 12), (576, 608), (1636, 1644)):
            old_header[start:end] = new_header[start:end]
        require(old_header == new_header, "unrelated boot header bytes changed")
        for name in SECTION_NAMES[1:]:
            require(self.sections[name] == other.sections[name], f"{name} changed")
            require(self.pads[name] == other.pads[name], f"{name} padding changed")
        require(kernel is None or other.sections["kernel"] == kernel, "replacement kernel mismatch")
        old_vbmeta, new_vbmeta = bytearray(self.vbmeta), bytearray(other.vbmeta)
        require(len(old_vbmeta) == len(new_vbmeta), "AVB metadata size changed")
        desc = self.hash_descriptor
        for start, size in ((desc["offset"] + 16, 8), (desc["digest_offset"], 32)):
            old_vbmeta[start:start + size] = new_vbmeta[start:start + size]
        require(old_vbmeta == new_vbmeta, "unrelated AVB metadata changed")
        old_footer = bytearray(self.footer)
        old_footer[12:28] = other.footer[12:28]
        require(old_footer == other.footer, "unrelated AVB footer fields changed")
        return {"ramdisk_identical": True, "dtb_identical": True,
                "second_identical": True, "recovery_dtbo_identical": True,
                "cmdline_identical": True, "other_header_fields_identical": True,
                "other_avb_metadata_identical": True, "partition_size_identical": True}

    def report(self):
        return {
            "sha256": sha256(self.data), "partition_size": len(self.data),
            "header_version": 2, "header_size": HEADER_SIZE, "page_size": PAGE_SIZE,
            "boot_id_sha1": self.data[576:596].hex(), "boot_id_valid": True,
            "cmdline": cstring(self.data[64:576] + self.data[608:1632]),
            "cmdline_fields_sha256": sha256(self.data[64:576] + self.data[608:1632]),
            "os_version_encoded": u32(self.data, 44),
            "addresses": {"kernel": u32(self.data, 12), "ramdisk": u32(self.data, 20),
                          "second": u32(self.data, 28), "tags": u32(self.data, 32),
                          "dtb": u64(self.data, 1652)},
            "sections": {name: {"offset": self.offsets[name], "size": len(section),
                                "sha256": sha256(section)}
                         for name, section in self.sections.items()},
            "avb": {"algorithm": "NONE", "flags": u32(self.vbmeta, 120, ">"),
                    "rollback_index": u64(self.vbmeta, 112, ">"),
                    "vbmeta_offset": self.vbmeta_offset, "vbmeta_size": len(self.vbmeta),
                    "hashed_image_size": self.hash_descriptor["image_size"],
                    "salt": self.hash_descriptor["salt"].hex(),
                    "digest": self.hash_descriptor["digest"].hex(), "hash_valid": True},
        }


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, report):
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if path:
        atomic_write(path, text.encode())
    else:
        print(text, end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="validate and describe a boot image")
    inspect.add_argument("image", type=Path)
    inspect.add_argument("--report", type=Path)
    repack = sub.add_parser("repack", help="replace only the kernel and derived checksums")
    repack.add_argument("--stock", type=Path, required=True)
    repack.add_argument("--kernel", type=Path, required=True)
    repack.add_argument("--output", type=Path, required=True)
    repack.add_argument("--expected-stock-sha256", required=True)
    repack.add_argument("--report", type=Path)
    verify = sub.add_parser("verify", help="check that unrelated sections are unchanged")
    verify.add_argument("stock", type=Path)
    verify.add_argument("image", type=Path)
    verify.add_argument("--kernel", type=Path)
    verify.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            report = BootImage.read(args.image).report()
        elif args.command == "repack":
            stock = BootImage.read(args.stock)
            require(sha256(stock.data) == args.expected_stock_sha256, "stock boot SHA256 mismatch")
            require(args.output.resolve() != args.stock.resolve(), "refusing to overwrite stock boot")
            kernel = args.kernel.read_bytes()
            result = stock.repack(kernel)
            atomic_write(args.output, result.data)
            report = {"stock": stock.report(), "output": result.report(),
                      "preservation": stock.compare(result, kernel), "device_tested": False}
        else:
            stock, result = BootImage.read(args.stock), BootImage.read(args.image)
            kernel = args.kernel.read_bytes() if args.kernel else None
            report = {"output": result.report(), "preservation": stock.compare(result, kernel),
                      "device_tested": False}
        write_json(args.report, report)
    except (ImageError, OSError, UnicodeError, struct.error) as error:
        parser.exit(1, f"boot_image: {error}\n")


if __name__ == "__main__":
    main()
