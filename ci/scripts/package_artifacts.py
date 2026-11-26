#!/usr/bin/env python3
"""Package checked kernels as AnyKernel3 ZIPs, with optional magiskboot repacking."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


CONTAINER_STAGES = ("stage2", "stage3")


def load_json(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_inputs(dist, stage, sources):
    manifest = load_json(dist / "build-manifest.json")
    require(manifest.get("stage") == stage, "build stage mismatch")
    for source in ("kernel", "nova", "susfs", "kp", "anykernel"):
        require(manifest.get(source, {}).get("commit") == sources[source]["commit"], f"build source mismatch: {source}")
    for mode in ("config", "linked"):
        check = load_json(dist / f"checks-{mode}.json")
        require(check.get("passed") is True and not check.get("errors") and
                check.get("stage") == stage and check.get("mode") == mode,
                f"{mode} build checks have not passed")
    if stage != "baseline":
        nova = load_json(dist / "nova-protection-check.json")
        require(nova.get("passed") is True and nova.get("mode") == "linked", "Nova linked checks have not passed")
        if stage == "stage1":
            abi = load_json(dist / "module-abi.json")
            require(abi.get("passed") is True and not abi.get("errors"),
                    "vendor module ABI checks have not passed")
        kpm = load_json(dist / "kpm-manifest.json")
        require(kpm.get("status") == "patched" and kpm.get("bridge_abi") == 2,
                "KPM image patch has not completed")
        require(kpm.get("kp_commit") == sources["kp"]["commit"] and
                kpm.get("kernel_source_commit") == manifest["source_commit"], "KPM source mismatch")
        require(kpm.get("extra_root_provider") is False, "unexpected additional root provider")
    if stage in CONTAINER_STAGES:
        containers = load_json(dist / f"{stage}-container-capabilities.json")
        require(containers.get("passed") is True and not containers.get("errors") and
                containers.get("mode") == "linked" and containers.get("stage") == stage,
                "Container and built-in driver capability checks have not passed")
        require(containers.get("source_commit") == manifest["source_commit"],
                "Container and built-in driver capability source mismatch")
    kernel = (dist / "Image.lz4").read_bytes()
    require(kernel[:4] in (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18"), "Image.lz4 is not an LZ4 frame")
    if stage in CONTAINER_STAGES:
        partition_bytes = 64 * 1024 * 1024
        reserved_bytes = 16 * 1024 * 1024
        size = {"source_commit": manifest["source_commit"], "stage": stage,
                "image": "Image.lz4", "image_bytes": len(kernel),
                "boot_partition_bytes": partition_bytes,
                "reserved_non_kernel_bytes": reserved_bytes,
                "kernel_budget_bytes": partition_bytes - reserved_bytes,
                "remaining_boot_bytes": partition_bytes - len(kernel),
                "passed": len(kernel) <= partition_bytes - reserved_bytes}
        (dist / "image-size.json").write_text(json.dumps(size, indent=2) + "\n")
        require(size["passed"], "Image.lz4 exceeds the 48 MiB kernel budget")
    return manifest, kernel


def ak3_files(path, commit):
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    require(actual == commit, "AnyKernel3 checkout does not match the pinned commit")
    subprocess.run(["git", "-C", str(path), "diff", "--quiet", "HEAD", "--", "tools", "META-INF", "LICENSE"], check=True)
    names = subprocess.check_output(["git", "-C", str(path), "ls-files", "-z", "tools", "META-INF", "LICENSE"])
    files = {}
    for raw in filter(None, names.split(b"\0")):
        name = raw.decode()
        source = path / name
        require(source.is_file() and not source.is_symlink(), f"unsupported AnyKernel3 file: {name}")
        files[name] = (source.read_bytes(), 0o755 if name.startswith("tools/") or name.endswith("update-binary") else 0o644)
    for name in ("tools/ak3-core.sh", "tools/busybox", "tools/magiskboot", "META-INF/com/google/android/update-binary"):
        require(name in files, f"missing AnyKernel3 component: {name}")
    return files


def write_zip(path, files, epoch):
    timestamp = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, (data, mode) in sorted(files.items()):
            info = zipfile.ZipInfo(name, timestamp.timetuple()[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100000 | mode) << 16
            archive.writestr(info, data)


def readme(device, stage, manifest, fastboot=False):
    header = f"# {device} / {stage}\n\n内核源码提交：`{manifest['source_commit']}`。\n\n"
    if fastboot:
        body = ("`boot.img` 使用本次提供的当前 ROM boot 重打包；`stock-boot.img` 是原输入的副本。"
                "magiskboot 保留原 ramdisk、独立 DTB 和其他 boot 组件，只替换内核。\n\n"
                f"先确认 `fastboot getvar product` 为 `{device}`，并用 `fastboot getvar current-slot` 核对槽位。"
                "可用 `fastboot boot boot.img` 试启动；持久刷入时显式指定已核对的槽位，"
                "例如槽位 a 使用 `fastboot --slot=a flash boot boot.img`。回退时对同一槽位刷回"
                "`stock-boot.img`。请保留自己的当前 boot 备份。\n\n")
    else:
        body = ("通过兼容的 recovery / 内核刷写工具安装本 ZIP。安装器校验机型、Android 17、活动 A/B 槽位"
                "及 boot v2 / 64 MiB 布局，再用固定版 AnyKernel3 替换当前 boot 的内核、保留当前 ramdisk。"
                "刷入前备份当前 boot，回退时对同一槽位刷回该备份。\n\n"
                "ZIP 使用 `Image.lz4`；本机型的 DTB 在 boot v2 中独立存放。安装器不安装原生模块，"
                "也不写 vendor、dtbo、vbmeta 分区。\n\n")
    checks = "配置与链接" if stage == "baseline" else "配置、链接、Nova、KPM"
    if stage == "stage1":
        checks += "、原厂模块 ABI"
    if stage in CONTAINER_STAGES:
        checks += "、容器与内建驱动、镜像体积"
        body += ("原有 27 个树内 vendor 驱动已静态内建进内核，无需替换 /vendor/lib/modules。"
                 "驱动随内核一起更新；恢复同一槽位原 boot 即可回退。"
                 "CONFIG_MODULES 与 CONFIG_MODVERSIONS 保留，后续可选 LKM 需按本内核配置构建。\n\n")
        body += ("本阶段累计包含 Nova、KPM、SUSFS 与容器内核配置，IPv6 NAT 静态内建，"
                 "BRIDGE_NETFILTER 保持关闭。Docker／LXC 的运行环境、cgroup 挂载与网络配置"
                 "由现有外部方案提供；容器功能和资源限制等待实机验收。\n\n")
    return (header + body + f"{checks}检查结果随 Actions Artifacts 提供；"
            "本次构建的设备功能验收需在刷入后完成。\n").encode()


def package(args):
    root, dist, anykernel = (Path(value).resolve() for value in (args.root, args.dist, args.anykernel))
    sources = load_json(root / "ci/sources.json")
    manifest, kernel = verify_inputs(dist, args.stage, sources)
    upstream = ak3_files(anykernel, sources["anykernel"]["commit"])
    require(not args.boot_url or args.boot_device in ("flame", "coral"), "select flame or coral for the optional boot image")
    template = (root / "ci/packaging/anykernel.sh.in").read_text()
    temporary = Path(tempfile.mkdtemp(prefix="packages.tmp-", dir=dist))
    try:
        for device in ("flame", "coral"):
            installer = template.replace("@DEVICE@", device).replace("@STAGE@", args.stage).replace(
                "@SOURCE_SHORT@", manifest["source_commit"][:12])
            files = dict(upstream)
            files.update({"anykernel.sh": (installer.encode(), 0o755), "Image.lz4": (kernel, 0o644),
                          "README.md": (readme(device, args.stage, manifest), 0o644)})
            write_zip(temporary / f"AnyKernel3-{device}-{args.stage}.zip", files, sources["source_date_epoch"])
        if args.boot_url:
            boot_dir = temporary / "boot"
            subprocess.run(["bash", str(root / "ci/scripts/repack-boot.sh"), str(anykernel / "tools/magiskboot"),
                            str(dist / "Image.lz4"), args.boot_url, str(boot_dir)], check=True)
            files = {name: ((boot_dir / name).read_bytes(), 0o644) for name in ("boot.img", "stock-boot.img")}
            files["README.md"] = (readme(args.boot_device, args.stage, manifest, fastboot=True), 0o644)
            write_zip(temporary / f"Fastboot-{args.boot_device}-{args.stage}.zip", files, sources["source_date_epoch"])
            shutil.rmtree(boot_dir)
        # Only final delivery archives are hashed, once each and outside the ZIPs.
        sums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                       for path in sorted(temporary.glob("*.zip")))
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
    for argument in ("root", "dist", "anykernel"):
        parser.add_argument("--" + argument, required=True)
    parser.add_argument("--stage", choices=("baseline", "stage1", "stage2", "stage3"), default="stage1")
    parser.add_argument("--boot-url", default=os.environ.get("FLORAL_BOOT_IMAGE_URL", ""))
    parser.add_argument("--boot-device", default=os.environ.get("FLORAL_BOOT_DEVICE", ""))
    try:
        package(parser.parse_args())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"package: {error}\n")


if __name__ == "__main__":
    main()
