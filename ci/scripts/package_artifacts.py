#!/usr/bin/env python3
"""Produce separate flame/coral boot images, kernel-only AK3 ZIPs and rollback images."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from boot_image import BootImage, ImageError, require, sha256, write_json


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text())


def verify_inputs(root, dist, stage, sources):
    manifest = load_json(dist / "build-manifest.json")
    require(manifest.get("stage") == stage, "build manifest stage mismatch")
    require(manifest.get("device_tested") is False, "unexpected device validation claim")
    for source in ("kernel", "nova", "susfs", "kp", "anykernel", "stock"):
        require(manifest.get(source) == sources[source], f"build manifest source mismatch: {source}")
    for mode in ("config", "linked"):
        check = load_json(dist / f"checks-{mode}.json")
        require(check.get("passed") is True and not check.get("errors") and check.get("stage") == stage,
                f"{mode} build validation has not passed")
    kernel = (dist / "Image.lz4").read_bytes()
    require(kernel[:4] in (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18"),
            "final Image.lz4 is not an LZ4 frame")
    require((dist / "Image").stat().st_size > 1024 * 1024, "raw Image is missing or truncated")
    kpm = None
    if stage == "stage1":
        nova = load_json(dist / "nova-protection-check.json")
        require(nova.get("passed") is True and nova.get("mode") == "linked",
                "Nova linked protection checks have not passed")
        for device in ("flame", "coral"):
            abi = load_json(dist / f"module-abi-{device}.json")
            require(abi.get("passed") is True and abi.get("rom") == sources["stock"][device]["rom"],
                    f"{device} vendor module ABI checks have not passed")
        config = (dist / "kernel.config").read_text()
        for symbol in ("KSU", "KSU_SUSFS", "KPM"):
            require(f"CONFIG_{symbol}=y\n" in config, f"CONFIG_{symbol}=y is required for this Stage 1 package")
        kpm = load_json(dist / "kpm-manifest.json")
        require(kpm.get("status") == "patched" and kpm.get("bridge_abi") == 2,
                "KPM image patch has not completed")
        require(kpm.get("kp_commit") == sources["kp"]["commit"], "KPM source mismatch")
        require(kpm.get("kernel_source_commit") == manifest["source_commit"], "KPM kernel source mismatch")
        require(kpm.get("extra_root_provider") is False and kpm.get("device_tested") is False,
                "unexpected KPM root provider or device-validation state")
        for filename, field in (("Image", "kernel_sha256_patched"),
                                ("Image.unpatched", "kernel_sha256_unpatched"),
                                ("Image.lz4", "kernel_lz4_sha256")):
            require(file_hash(dist / filename) == kpm.get(field), f"KPM manifest hash mismatch: {filename}")
        require((dist / "Image.lz4.unpatched").is_file(), "original compressed Image backup is missing")
        require(kpm["kernel_sha256_patched"] != kpm["kernel_sha256_unpatched"], "KPM did not change the Image")
    return manifest, kpm, kernel


def fetch_stock(deps, device, identity):
    path = deps / "stock" / f"{device}-boot.img"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        temporary = path.with_suffix(".img.download")
        try:
            with urllib.request.urlopen(identity["boot_url"], timeout=120) as response, temporary.open("wb") as target:
                shutil.copyfileobj(response, target)
            require(file_hash(temporary) == identity["boot_sha256"], f"downloaded {device} boot hash mismatch")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    require(file_hash(path) == identity["boot_sha256"], f"{device} stock boot SHA256 mismatch")
    return path


def ak3_files(path, expected_commit):
    commit = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    require(commit == expected_commit, f"AnyKernel3 checkout mismatch: {commit}")
    subprocess.run(["git", "-C", str(path), "diff", "--quiet", "HEAD", "--", "tools", "META-INF", "LICENSE"], check=True)
    names = subprocess.check_output(["git", "-C", str(path), "ls-files", "-z", "tools", "META-INF", "LICENSE"])
    files = {}
    for raw in names.split(b"\0"):
        if not raw:
            continue
        name = raw.decode()
        source = path / name
        require(source.is_file() and not source.is_symlink(), f"unsupported AnyKernel3 file: {name}")
        files[name] = (source.read_bytes(), 0o755 if name.startswith("tools/") or name.endswith("update-binary") else 0o644)
    for name in ("tools/ak3-core.sh", "tools/busybox", "tools/magiskboot", "META-INF/com/google/android/update-binary"):
        require(name in files, f"missing AnyKernel3 component: {name}")
    return files


def write_zip(path, files, epoch):
    timestamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    date_time = (timestamp.year, timestamp.month, timestamp.day, timestamp.hour, timestamp.minute, timestamp.second)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, (data, mode) in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, "AnyKernel3 archive CRC check failed")
        require(archive.read("Image.lz4") == files["Image.lz4"][0], "AnyKernel3 kernel mismatch")
        forbidden = ("ramdisk/", "modules/", "vendor/", "vendor_boot/", "dtbo")
        require(not any(name.startswith(forbidden) for name in archive.namelist()), "unexpected partition payload in ZIP")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def device_readme(device, stage, sources, manifest):
    identity = sources["stock"][device]
    label = "Pixel 4" if device == "flame" else "Pixel 4 XL"
    return f"""# {label} / {device} / {stage}

本包尚未经过实机启动、Root、KPM 生命周期、SUSFS 或日用验收。

内核源码提交：`{manifest['source_commit']}`。LOS 继承点：`{sources['kernel']['commit']}`。
固定官方 ROM 基线：`{identity['rom']}`（Android 17）。
原 boot SHA256：`{identity['boot_sha256']}`。

## 产物用途

`boot.img` 以该机型的固定官方 boot 为输入，仅替换内核并更新 boot SHA1 ID、未签名 AVB 哈希描述符及其派生偏移。压缩 ramdisk、boot v2 内的 DTB、命令行、第二阶段及 recovery_dtbo 段均逐字节保留；镜像保持 64 MiB。它使用上述固定 ROM 的 ramdisk；如需保留当前 ramdisk 的修改，使用 AnyKernel3 ZIP。

`AnyKernel3-{device}-{stage}.zip` 在设备端使用 AnyKernel3 `{sources['anykernel']['commit']}` 的原生 `split_boot` / `flash_boot`。它替换当前活动 A/B 槽位的内核并沿用当前 ramdisk；压缩及 AVB 处理使用固定上游工具的原生逻辑。安装器核对机型、Android 17、活动槽位和 boot v2 / 64 MiB 布局，不要求当前 boot 与官方镜像哈希相同。当前 ROM 仍须满足上述内核基线的模块和接口条件。

ZIP 仅带最终 `Image.lz4`，不会安装原生 `.ko`，不会写 vendor、dtbo、vbmeta 等分区，也不会添加用户态服务。独立 boot 的完整 AVB 哈希校验报告适用于随包生成的 boot.img；设备端 AK3 重包沿用上游 AVB 行为。保持原有解锁状态和现有验证配置。

## 试启动、刷入与回退

先把当前 boot 备份到电脑并确认当前槽位。`fastboot getvar product` 应显示 `{device}`，`fastboot getvar current-slot` 应显示 `a` 或 `b`。可先执行 `fastboot boot boot.img` 临时启动本目录镜像；成功进入系统后再安排功能验收。需要持久刷入时，对核对后的槽位显式执行 `fastboot --slot=a flash boot boot.img` 或对应的 `--slot=b` 命令；随后重启。不要把两个槽位同时覆盖。

AnyKernel3 ZIP 可通过兼容的 recovery / 现有内核刷写工具安装，它只处理当前活动槽位。刷入前保存刷写日志和你的原 boot；AK3 临时工作目录会按上游流程清理。

随包 `rollback/{device}-boot.img` 是上述固定官方 ROM 的原镜像。若需回退，在 bootloader 中对同一槽位显式刷回你自己的 boot 备份；使用固定官方 ROM 时也可刷回该 rollback 镜像。回退内核不负责清理外部 Manager、Root 模块或其他用户态状态。

## 验收状态

构建与打包结果见 `build-manifest.json`、`boot-validation.json` 和 SHA256SUMS；上级目录中的 `module-abi-{device}.json` 记录配套 vendor 模块的 CRC / vermagic 检查，`nova-protection-check.json` 记录实际编译及最终机器码检查。KPM 内核原始备份保留在 `Image.unpatched` 与 `Image.lz4.unpatched`，最终刷入版本由 `kpm-manifest.json` 追溯。
"""


def package(args):
    root, deps, dist, anykernel = [Path(value).resolve() for value in
                                   (args.root, args.deps, args.dist, args.anykernel)]
    sources = load_json(root / "ci/sources.json")
    manifest, kpm, kernel = verify_inputs(root, dist, args.stage, sources)
    upstream_files = ak3_files(anykernel, sources["anykernel"]["commit"])
    stock_paths = {device: fetch_stock(deps, device, sources["stock"][device]) for device in ("flame", "coral")}
    template = (root / "ci/packaging/anykernel.sh.in").read_text()
    dist.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="packages.tmp-", dir=dist))
    try:
        (temporary / "rollback").mkdir()
        package_manifest = {
            "stage": args.stage, "source_commit": manifest["source_commit"],
            "device_tested": False, "kernel_lz4_sha256": sha256(kernel),
            "anykernel_commit": sources["anykernel"]["commit"],
            "anykernel_policy": {"slot": "active", "modules_installed": False,
                                 "ramdisk": "preserve current CPIO contents via upstream split_boot/flash_boot",
                                 "avb": "upstream AnyKernel3/magiskboot behavior"},
            "devices": {},
        }
        for device, stock_path in stock_paths.items():
            target = temporary / device
            target.mkdir()
            stock = BootImage.read(stock_path)
            if args.stage == "stage1":
                require(kernel != stock.sections["kernel"], "refusing to label a stock kernel as Stage 1")
            result = stock.repack(kernel)
            (target / "boot.img").write_bytes(result.data)
            # Read the completed file back before producing the ZIP and manifest.
            written = BootImage.read(target / "boot.img")
            validation = {"stock": stock.report(), "output": written.report(),
                          "preservation": stock.compare(written, kernel), "device_tested": False}
            write_json(target / "boot-validation.json", validation)
            readme = device_readme(device, args.stage, sources, manifest)
            (target / "README.md").write_text(readme)
            shutil.copyfile(stock_path, temporary / "rollback" / f"{device}-boot.img")
            require(file_hash(temporary / "rollback" / f"{device}-boot.img") == sources["stock"][device]["boot_sha256"],
                    "rollback copy hash mismatch")
            installer = template.replace("@DEVICE@", device).replace("@STAGE@", args.stage).replace(
                "@SOURCE_SHORT@", manifest["source_commit"][:12])
            files = dict(upstream_files)
            files.update({"anykernel.sh": (installer.encode(), 0o755), "Image.lz4": (kernel, 0o644),
                          "README.md": (readme.encode(), 0o644),
                          "build-manifest.json": (json_bytes(manifest), 0o644),
                          "kernel.config": ((dist / "kernel.config").read_bytes(), 0o644)})
            if kpm:
                files["kpm-manifest.json"] = (json_bytes(kpm), 0o644)
            sums = "".join(f"{sha256(data)}  {name}\n" for name, (data, _) in sorted(files.items()))
            files["SHA256SUMS"] = (sums.encode(), 0o644)
            zip_name = f"AnyKernel3-{device}-{args.stage}.zip"
            write_zip(target / zip_name, files, sources["source_date_epoch"])
            package_manifest["devices"][device] = {
                "rom": sources["stock"][device]["rom"], "boot_sha256": sha256(written.data),
                "anykernel_sha256": file_hash(target / zip_name),
                "rollback_sha256": sources["stock"][device]["boot_sha256"],
                "ramdisk_sha256": sha256(stock.sections["ramdisk"]),
                "dtb_sha256": sha256(stock.sections["dtb"]),
            }
        shutil.copyfile(dist / "build-manifest.json", temporary / "build-manifest.json")
        if kpm:
            shutil.copyfile(dist / "kpm-manifest.json", temporary / "kpm-manifest.json")
        write_json(temporary / "package-manifest.json", package_manifest)
        (temporary / "README.md").write_text(
            "# Pixel 4 / Pixel 4 XL 刷机产物\n\n"
            "`flame/` 对应 Pixel 4，`coral/` 对应 Pixel 4 XL。每个目录含独立 boot.img、AnyKernel3 ZIP、"
            "打包校验与刷入说明；`rollback/` 保留各自固定官方 boot。请先阅读对应机型的 README.md。\n\n"
            "本次为构建及静态校验产物，尚未实机验收。最终 Image、未进行 KPM patch 的原生备份、"
            "编译配置、日志与符号表保留在本目录的上级构建输出中。\n")
        sums = "".join(f"{file_hash(path)}  {path.relative_to(temporary).as_posix()}\n"
                       for path in sorted(temporary.rglob("*")) if path.is_file())
        (temporary / "SHA256SUMS").write_text(sums)
        destination = dist / "packages"
        if destination.exists():
            shutil.rmtree(destination)
        temporary.replace(destination)
        print(f"Packaged {args.stage}: {destination}")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ("root", "deps", "dist", "anykernel"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--stage", choices=("baseline", "stage1"), required=True)
    args = parser.parse_args()
    try:
        package(args)
    except (ImageError, OSError, KeyError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"package: {error}\n")


if __name__ == "__main__":
    main()
