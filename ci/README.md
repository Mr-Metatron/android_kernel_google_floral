# 构建约定

`sources.json` 固定内核、工具链和集成组件的 Git 提交，`kpm-sources.json` 固定配套 KP 编译器。基线使用当前流水线直接 `git archive bb17b7de4b570f2c0c32ddba0543cac03c58dd84`，无需维护另一套 CI 分支。

工作流支持 `baseline`、`stage1`、`stage2`。默认 `auto` 按实际 checkout 的配置选择阶段；`source_ref` 留空时使用所选工作流 ref，填写分支或 commit 时构建该固定源码。Stage 2 依次合并 `stage1.config`、`stage2.config`，累计保留第一阶段功能。

本地或 Colab 手动构建 Stage 2 时，依次执行 `bash ci/scripts/build.sh stage2`、`bash ci/scripts/kpm.sh stage2`、`bash ci/scripts/package.sh stage2`。三者共用 `FLORAL_DEPS`、`FLORAL_OUT`、`FLORAL_DIST`，默认输出分别位于 `.ci-out/stage2`、`.ci-dist/stage2`。

## 校验边界

SHA256 仅用于外部下载压缩包和最终交付压缩包，每个包只做一层。Clang Release 缓存下载后校验一次，解压即用；已解压的缓存直接复用。禁止增加解压文件树、编译器单文件、源码补丁、目标文件、内核镜像、中间 manifest、单次报告 JSON 的冗余哈希或校验脚本，也不在交付 ZIP 内为各文件再附一份哈希表。

Git 提交固定、编译保护检查、vendor 模块的 `vermagic` 与导出符号 CRC 检查继续保留。内核密码算法、Manager 签名认证和镜像工具自身的格式处理属于功能代码。

## 产物与提交

运行期报告、日志、资源采样和构建清单写入 `.ci-dist/`，仅上传 GitHub Actions Artifacts，不提交到源码树。`reference/vendor-abi.json` 保存共同的完整 `vermagic`、去重符号 CRC 基线和 27 个原厂模块文件名；静态设计说明分别写在 `docs/STAGE1.md`、`docs/STAGE2.md`。

Stage 2 沿用配置、链接、Nova 编译保护、vendor ABI 与 KPM 打包门禁，并增加配置解析后与链接后的容器能力检查。原厂导出符号 CRC 变化时保留原报告，并要求同次构建的完整 27 个模块通过独立兼容检查；其余检查失败或配套模块不完整时停止打包。通过后分别生成 flame、coral 的 AnyKernel3 ZIP，按需附带匹配模块归档，并归档 `Image.lz4`、Stage 2 fragment 和能力报告。需要配套模块的 ZIP 名称带有 `requires-matching-modules`。`BRIDGE_NETFILTER` 默认关闭，本阶段不额外构建可选网桥过滤模块。Artifacts 保存 30 天，实机验收状态独立记录。

内核缺陷修复与 CI 修改分开提交；每个提交围绕可单独审阅和 revert 的改动。Stage 2 容器支持与 Stage 3 优化继续分别维护，按设计方案拆分提交。

## 计算资源

`make_jobs=auto` 使用 `nproc`，也接受正整数覆盖。保留 Full Clang LTO；物理内存小于 48 GiB 的 runner 增加 48 GiB swap。Colab 使用纯 CPU HIGHMEM，临时 runner 的工作目录使用短绝对路径，例如 `/content/b`。

创建 Colab 实例后先检查 CPU 型号；若非 AMD，最多重新分配三次，遇到 AMD 即停止，否则使用最后一次实例。每次替换先销毁前一实例，构建完成并确认 Artifacts 上传后销毁最终实例。
