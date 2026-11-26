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


def load_json(path):
    return json.loads(path.read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify_inputs(dist, stage, sources):
    manifest = load_json(dist / "build-manifest.json")
    module_bundle = None
    require(manifest.get("stage") == stage, "build stage mismatch")
    for source in ("kernel", "nova", "susfs", "kp", "anykernel"):
        require(manifest.get(source, {}).get("commit") == sources[source]["commit"], f"build source mismatch: {source}")
    for mode in ("config", "linked"):
        check = load_json(dist / f"checks-{mode}.json")
        require(check.get("passed") is True and not check.get("errors") and
                check.get("stage") == stage and check.get("mode") == mode,
                f"{mode} build checks have not passed")
    if stage in ("stage1", "stage2"):
        nova = load_json(dist / "nova-protection-check.json")
        require(nova.get("passed") is True and nova.get("mode") == "linked", "Nova linked checks have not passed")
        abi = load_json(dist / "module-abi.json")
        stock_compatible = abi.get("passed") is True and not abi.get("errors")
        if not stock_compatible:
            require(stage == "stage2", "vendor module ABI checks have not passed")
            require(abi.get("passed") is False and abi.get("errors"),
                    "missing failed stock vendor ABI comparison")
            module_bundle = load_json(dist / "module-bundle.json")
            require(module_bundle.get("passed") is True and not module_bundle.get("errors") and
                    module_bundle.get("complete_coverage") is True and
                    module_bundle.get("requires_vendor_module_replacement") is True,
                    "complete matching vendor modules have not passed their checks")
            require(module_bundle.get("source_commit") == manifest["source_commit"] and
                    module_bundle.get("kernel_release") == manifest["kernel_release"] and
                    module_bundle.get("vermagic") == abi.get("vermagic"),
                    "matching vendor modules belong to a different kernel build")
            required = module_bundle.get("required_modules", [])
            modules = module_bundle.get("modules", [])
            require(module_bundle.get("modules_checked") == len(modules) == len(required) == 27 and
                    len(set(required)) == 27 and
                    sorted(item.get("relative_path", "") for item in modules) == sorted(required),
                    "matching vendor module coverage is incomplete")
            for relative in required:
                path = dist / "vendor-modules" / relative
                require(Path(relative).name == relative and relative.endswith(".ko") and
                        path.is_file() and not path.is_symlink(),
                        f"missing matching vendor module: {relative}")
        kpm = load_json(dist / "kpm-manifest.json")
        require(kpm.get("status") == "patched" and kpm.get("bridge_abi") == 2,
                "KPM image patch has not completed")
        require(kpm.get("kp_commit") == sources["kp"]["commit"] and
                kpm.get("kernel_source_commit") == manifest["source_commit"], "KPM source mismatch")
        require(kpm.get("extra_root_provider") is False, "unexpected additional root provider")
    if stage == "stage2":
        containers = load_json(dist / "stage2-container-capabilities.json")
        require(containers.get("passed") is True and not containers.get("errors") and
                containers.get("mode") == "linked" and containers.get("stage") == stage,
                "Stage 2 container capability checks have not passed")
        require(containers.get("source_commit") == manifest["source_commit"],
                "Stage 2 container capability source mismatch")
    kernel = (dist / "Image.lz4").read_bytes()
    require(kernel[:4] in (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18"), "Image.lz4 is not an LZ4 frame")
    return manifest, kernel, module_bundle


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


def readme(device, stage, manifest, fastboot=False, module_bundle=None):
    header = f"# {device} / {stage}\n\n内核源码提交：`{manifest['source_commit']}`。\n\n"
    if module_bundle:
        header += ("**本内核要求同步配套替换 27 个 vendor 模块。** 请搭配同次构建的 "
                   "`VendorModules-floral-stage2.zip` 使用。原厂模块的符号 CRC 与本内核存在差异，"
                   "刷入前需为当前 ROM 准备这套模块的同步替换；当前 AnyKernel3 仅替换 boot 中的内核。"
                   "请保留原内核和原 vendor 模块，以便一起回退。\n\n")
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
    checks = "配置、链接、Nova、KPM 与模块 ABI" if stage in ("stage1", "stage2") else "配置与链接"
    if stage == "stage2":
        checks += "、容器内核能力"
        if module_bundle:
            body += ("原厂 vendor ABI 对照报告保留为失败；独立的配套模块报告核验了完整模块覆盖、"
                     "ARM64 ELF、完整 vermagic 及全部导入符号 CRC。设备加载与日用功能仍需验收。\n\n")
        body += ("本阶段累计包含 Nova、KPM、SUSFS 与容器内核配置，IPv6 NAT 静态内建，"
                 "BRIDGE_NETFILTER 保持关闭。Docker／LXC 的运行环境、cgroup 挂载与网络配置"
                 "由现有外部方案提供；容器功能和资源限制等待实机验收。\n\n")
    return (header + body + f"{checks}检查结果随 Actions Artifacts 提供；"
            "本次构建的设备功能验收需在刷入后完成。\n").encode()


def package(args):
    root, dist, anykernel = (Path(value).resolve() for value in (args.root, args.dist, args.anykernel))
    sources = load_json(root / "ci/sources.json")
    manifest, kernel, module_bundle = verify_inputs(dist, args.stage, sources)
    suffix = "-requires-matching-modules" if module_bundle else ""
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
                          "README.md": (readme(device, args.stage, manifest, module_bundle=module_bundle), 0o644)})
            write_zip(temporary / f"AnyKernel3-{device}-{args.stage}{suffix}.zip", files, sources["source_date_epoch"])
        if module_bundle:
            files = {"vendor-modules/" + item["relative_path"]:
                     ((dist / "vendor-modules" / item["relative_path"]).read_bytes(), 0o644)
                     for item in module_bundle["modules"]}
            module_readme = ("# Stage 2 配套 vendor 模块\n\n"
                             f"内核源码提交：`{manifest['source_commit']}`。\n\n"
                             "本包提供本次内核构建的完整 27 个 ARM64 原生模块。请将全部模块与同次 Stage 2 "
                             "内核同步配套使用；模块清单及逐模块导入 CRC 检查结果见 Actions Artifact 中的 "
                             "`module-bundle.json`。\n\n"
                             "`vendor-modules/` 中的文件对应原厂 vendor 模块文件名。此压缩包只提供模块文件，"
                             "需要按当前 ROM 的模块加载路径完成部署；AnyKernel3 安装器只更新 boot。"
                             "原厂模块与本内核的 ABI 不兼容，请保留原 boot 和原模块并在回退时一起恢复。\n")
            files["README.md"] = (module_readme.encode(), 0o644)
            write_zip(temporary / f"VendorModules-floral-{args.stage}.zip", files, sources["source_date_epoch"])
        if args.boot_url:
            boot_dir = temporary / "boot"
            subprocess.run(["bash", str(root / "ci/scripts/repack-boot.sh"), str(anykernel / "tools/magiskboot"),
                            str(dist / "Image.lz4"), args.boot_url, str(boot_dir)], check=True)
            files = {name: ((boot_dir / name).read_bytes(), 0o644) for name in ("boot.img", "stock-boot.img")}
            files["README.md"] = (readme(args.boot_device, args.stage, manifest, fastboot=True, module_bundle=module_bundle), 0o644)
            write_zip(temporary / f"Fastboot-{args.boot_device}-{args.stage}{suffix}.zip", files, sources["source_date_epoch"])
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
    parser.add_argument("--stage", choices=("baseline", "stage1", "stage2"), default="stage1")
    parser.add_argument("--boot-url", default=os.environ.get("FLORAL_BOOT_IMAGE_URL", ""))
    parser.add_argument("--boot-device", default=os.environ.get("FLORAL_BOOT_DEVICE", ""))
    try:
        package(parser.parse_args())
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"package: {error}\n")


if __name__ == "__main__":
    main()
