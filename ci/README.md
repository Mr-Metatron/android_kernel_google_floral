# 构建约定

`sources.json` 固定内核、工具链和集成组件的 Git 提交，`kpm-sources.json` 固定配套 KP 编译器。基线使用当前流水线直接 `git archive bb17b7de4b570f2c0c32ddba0543cac03c58dd84`，无需维护另一套 CI 分支。

## 校验边界

SHA256 仅用于外部下载压缩包和最终交付压缩包，每个包只做一层。Clang Release 缓存下载后校验一次，解压即用；已解压的缓存直接复用。禁止增加解压文件树、编译器单文件、源码补丁、目标文件、内核镜像、中间 manifest、单次报告 JSON 的冗余哈希或校验脚本，也不在交付 ZIP 内为各文件再附一份哈希表。

Git 提交固定、编译保护检查、vendor 模块的 `vermagic` 与导出符号 CRC 检查继续保留。内核密码算法、Manager 签名认证和镜像工具自身的格式处理属于功能代码。

## 产物与提交

运行期报告、日志、资源采样和构建清单写入 `.ci-dist/`，仅上传 GitHub Actions Artifacts，不提交到源码树。`reference/vendor-abi.json` 只保存共同的完整 `vermagic` 与去重符号 CRC 基线；静态设计说明写在 `docs/STAGE1.md`。

内核缺陷修复与 CI 修改分开提交；每个提交围绕可单独审阅和 revert 的改动。Stage 2 容器支持与 Stage 3 优化继续分别维护，按设计方案拆分提交。

## 计算资源

`make_jobs=auto` 使用 `nproc`，也接受正整数覆盖。保留 Full Clang LTO；物理内存小于 48 GiB 的 runner 增加 48 GiB swap。Colab 使用纯 CPU HIGHMEM，临时 runner 的工作目录使用短绝对路径，例如 `/content/b`。

创建 Colab 实例后先检查 CPU 型号；若非 AMD，最多重新分配三次，遇到 AMD 即停止，否则使用最后一次实例。每次替换先销毁前一实例，构建完成并确认 Artifacts 上传后销毁最终实例。
