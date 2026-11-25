#!/usr/bin/env python3
"""Exercise the repacker against pinned official images, never as Stage 1 output."""

import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from boot_image import BootImage, ImageError, PAGE_SIZE, require, sha256, write_json


def rejected(data, reason):
    try:
        BootImage(data)
    except ImageError as error:
        require(reason in str(error), f"unexpected rejection: {error}")
        return str(error)
    raise ImageError("corrupted image was accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-dir", type=Path, required=True)
    parser.add_argument("--sources", type=Path, default=Path(__file__).resolve().parents[1] / "sources.json")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--avbtool", type=Path, help="optional independent AOSP avbtool.py verifier")
    args = parser.parse_args()
    sources = json.loads(args.sources.read_text())
    report = {"kind": "packager self-test using official and synthetic inputs",
              "stage1_artifact": False, "device_tested": False, "devices": {}}
    with tempfile.TemporaryDirectory(prefix="floral-boot-selftest-") as directory:
        for device in ("flame", "coral"):
            original = (args.stock_dir / f"{device}-boot.img").read_bytes()
            require(sha256(original) == sources["stock"][device]["boot_sha256"], f"{device} stock identity mismatch")
            stock = BootImage(original)
            roundtrip = stock.repack(stock.sections["kernel"])
            require(roundtrip.data == original, f"{device} roundtrip was not byte-identical")
            results = {"stock_sha256": sha256(original), "roundtrip_byte_identical": True,
                       "resized_layouts": [], "negative_checks": {}}
            # These deliberately synthetic kernels exercise page movement and
            # checksums only. The temporary files are deleted, never delivered.
            for delta in (-PAGE_SIZE, PAGE_SIZE):
                kernel = stock.sections["kernel"]
                kernel = kernel[:delta] if delta < 0 else kernel + bytes(delta)
                rebuilt = stock.repack(kernel)
                require(rebuilt.offsets["ramdisk"] - stock.offsets["ramdisk"] == delta,
                        "resized ramdisk offset did not move by one page")
                result = {"delta": delta, "boot_id_valid": True, "avb_hash_valid": True,
                          "preservation": stock.compare(rebuilt, kernel)}
                if args.avbtool:
                    image = Path(directory) / f"{device}-{delta}" / "boot.img"
                    image.parent.mkdir()
                    image.write_bytes(rebuilt.data)
                    check = subprocess.run(["python3", str(args.avbtool), "verify_image", "--image", str(image)],
                                           text=True, capture_output=True)
                    require(check.returncode == 0, f"independent AVB verification failed: {check.stdout}\n{check.stderr}")
                    result["aosp_avbtool"] = check.stdout.strip()
                results["resized_layouts"].append(result)
            bad_id = bytearray(original)
            bad_id[576] ^= 1
            results["negative_checks"]["boot_id"] = rejected(bad_id, "boot SHA1 ID mismatch")
            bad_hash = bytearray(original)
            bad_hash[stock.vbmeta_offset + stock.hash_descriptor["digest_offset"]] ^= 1
            results["negative_checks"]["avb_digest"] = rejected(bad_hash, "AVB boot hash mismatch")
            signed = bytearray(original)
            struct.pack_into(">I", signed, stock.vbmeta_offset + 28, 1)
            results["negative_checks"]["signed_metadata"] = rejected(signed, "signed AVB metadata")
            report["devices"][device] = results
    report["passed"] = True
    if args.avbtool:
        report["independent_avbtool_sha256"] = sha256(args.avbtool.read_bytes())
    write_json(args.report, report)
    print("PASS: flame/coral exact roundtrips, moved layouts, AVB hashes and corruption rejection")


if __name__ == "__main__":
    main()
