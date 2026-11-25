# Pixel 4 内核打包

依次运行 `ci/scripts/build.sh stage1`、`ci/scripts/kpm.sh`、`ci/scripts/package.sh stage1`。脚本共用 `FLORAL_DEPS`、`FLORAL_OUT`、`FLORAL_DIST`；`FLORAL_ANYKERNEL` 可指定已准备好的 AnyKernel3 checkout。打包要求配置、链接、Nova linked、单份 `module-abi.json` 检查通过，并确认 `kpm-manifest.json` 中补丁已完成、桥接 ABI 和源码提交一致。

默认打包不访问网络，不下载 stock boot。`$FLORAL_DIST/packages/` 只生成 `AnyKernel3-flame-stage1.zip`、`AnyKernel3-coral-stage1.zip` 和包外的 `SHA256SUMS`。每个最终 ZIP 计算一次 SHA256；包内没有文件校验和或相互套用的 manifest。最终 `Image.lz4`、配置、符号表和检查报告保留在 `$FLORAL_DIST`，随 GitHub Actions Artifacts 上传，运行期报告不提交到源码仓库。`baseline` 打包无需 Nova / KPM / vendor ABI 报告。

## AnyKernel3 与内核格式

AnyKernel3 提交固定在 `ci/sources.json`。安装器检查机型、Android 17、活动 A/B 槽位以及 boot v2 / 4096 字节页 / 64 MiB 布局，调用上游 `split_boot` / `flash_boot` 替换内核并保留当前 ramdisk。它不安装原生模块，不写 vendor、dtbo、vbmeta 分区；保持现有验证配置，关闭额外的 Magisk kernel / DTB 自动改写。

继续交付 `Image.lz4`：固定的 [官方设备配置](https://github.com/LineageOS/android_device_google_coral/blob/54913ec2b6e46e9d8d09ae21a15a3d1f7ca634dc/BoardConfig-common.mk) 使用该文件，并将 DTB 放在 boot v2 的独立段中。此布局不使用附加 DTB 的 `Image.lz4-dtb`。

## 可选 Fastboot boot.img

需要独立镜像时，在手动 workflow 输入中提供当前 ROM、当前机型可直接下载的 HTTPS boot.img URL，并选择 `flame` 或 `coral`。本地运行可设置：

```sh
FLORAL_BOOT_IMAGE_URL=https://example.org/current-rom-boot.img \
FLORAL_BOOT_DEVICE=flame ci/scripts/package.sh stage1
```

此路径额外需要 `curl` 和 `qemu-arm`（Debian / Ubuntu 的 `qemu-user` 包）。`repack-boot.sh` 下载后检查 Android 魔数、boot v2 和 4096 字节页，再通过 QEMU 执行固定 AnyKernel3 自带的静态 ARM32 `magiskboot`：`unpack -n -h` 保留压缩组件，替换 `kernel` 为最终 `Image.lz4`，然后以 `PATCHVBMETAFLAG=false repack -n` 重打包。boot 格式、压缩及 AVB 处理由上游工具负责。

输入 boot 必须匹配当前 ROM 和机型。magiskboot 保留原 AVB 描述符摘要，`PATCHVBMETAFLAG=false` 保持原 flags；替换内核后的镜像沿用可刷自定义内核的解锁与验证配置。此流程没有重新计算 AVB 描述符摘要或重新签名。

额外生成的 `Fastboot-<device>-stage1.zip` 含 `boot.img`、下载的原 `stock-boot.img` 和使用说明。只提供一个机型的 URL 就只生成该机型的 Fastboot 包。刷入前核对机型和活动槽位，保留当前 boot 备份；可先 `fastboot boot boot.img` 试启动，再显式指定已核对的槽位持久刷入。回退时向同一槽位刷回对应原 boot。

## 验收边界

检查报告说明构建、链接与 ABI 的检查结果。本次修正后的产物仍需完成实机启动、Root、SUSFS、KPM 生命周期和日用功能验收。
