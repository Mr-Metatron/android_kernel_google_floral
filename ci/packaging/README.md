# Pixel 4 boot 与 AnyKernel3 打包

构建先完成 `ci/scripts/build.sh stage1`，随后执行 KPM 的 `ci/scripts/kpm.sh`，最后执行 `ci/scripts/package.sh stage1`。这些脚本共用 `FLORAL_DEPS`、`FLORAL_OUT`、`FLORAL_DIST`；AnyKernel3 路径还可用 `FLORAL_ANYKERNEL` 指定。打包读取最终 `Image.lz4`，核对 KPM manifest 的源提交和哈希，并要求原生 `Image.unpatched`、`Image.lz4.unpatched` 已保留。`baseline` 模式读取基线构建输出，无 KPM patch 要求。

`ci/sources.json` 固定两机型的官方 ROM、boot SHA256 和 AnyKernel3 提交。缺少官方 boot 时下载到依赖缓存，下载及已有缓存都必须通过同一 SHA256 检查。AnyKernel3 的受用文件必须与固定 checkout 一致。

## 独立 boot.img

`boot_image.py` 直接解析并重打包 Pixel 4 的 4096 页、v2 header、64 MiB boot 布局，无 ramdisk 解包或 DTB 重编译。仅更新内核、内核长度、boot SHA1 ID、可选 recovery_dtbo 派生偏移，以及未签名 AVB 的 boot 哈希描述符和 footer 偏移。现有签名 metadata 会被拒绝，避免留下已失效的签名。所有其他 header 字节、压缩 ramdisk、DTB、second / recovery_dtbo、对应 padding、AVB 属性及 flags 均保留并读回校验。

结构与校验算法依据 AOSP [bootimg.h](https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/include/bootimg/bootimg.h)、[mkbootimg.py](https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/main/mkbootimg.py)、[avb_hash_descriptor.h](https://android.googlesource.com/platform/external/avb/+/refs/heads/main/libavb/avb_hash_descriptor.h) 与 [avbtool.py](https://android.googlesource.com/platform/external/avb/+/refs/heads/main/avbtool.py)。代码只接受本项目实际使用的布局；没有通用刷机、解锁或修改验证配置功能。

## AnyKernel3 ZIP

每个 ZIP 只放一个机型标识与最终 `Image.lz4`，使用固定 AnyKernel3 的 `split_boot` / `flash_boot`，保留当前 ramdisk 的 CPIO 内容，不进行 CPIO 提取、编辑或重建。组件压缩及 AVB 处理沿用固定上游 magiskboot，具体行为见其 [工具说明](https://github.com/topjohnwu/Magisk/blob/master/docs/tools.md)。因此设备端结果不承诺与独立 boot.img 字节相同；独立 boot 的 AVB 校验报告也不覆盖设备端生成的镜像。

安装器校验机型、Android 17、活动 `_a` / `_b` 槽位、64 MiB 和 v2 boot 布局。`do.modules=0`，不带模块安装包、ramdisk 变更、dtbo 或 vendor 产物。关闭上游 Magisk 自动 kernel / DTB 改写，明确保持 AVB flags。只调用 `flash_boot`，不调用附带其他分区处理的 `write_boot`。

## 产物与验证

输出在 `$FLORAL_DIST/packages/`：`flame/`、`coral/` 各有 boot.img、AnyKernel3 ZIP、README 和 boot-validation.json；`rollback/` 保留对应固定官方 boot；根目录提供 build / package / KPM manifest 与 SHA256SUMS。最终及未 patch 的原始 Image、配置、日志、Module.symvers 等仍由上级构建输出归档。

执行真实官方镜像 roundtrip 与布局校验：

```sh
python3 ci/scripts/test-boot-image.py \
  --stock-dir "$FLORAL_DEPS/stock" \
  --report "$FLORAL_DIST/packager-selftest.json"
```

传入 `--avbtool /path/to/aosp/avbtool.py` 可额外使用独立 AOSP 工具验证偏移变化后的 AVB 哈希。测试要求 flame / coral 的原内核重包与原始 64 MiB 镜像逐字节一致，模拟内核缩小及增大一页，核对所有非内核段，并确认损坏 SHA1、AVB digest 和签名 metadata 会被拒绝。模拟内核只用于临时布局测试，不生成可刷入阶段产物。

打包 manifest 和说明始终保留 `device_tested=false`。设备启动、Root、KPM load / list / info / control / event / unload、SUSFS、vendor 模块 ABI 及日用功能由阶段验收记录另行确认。
