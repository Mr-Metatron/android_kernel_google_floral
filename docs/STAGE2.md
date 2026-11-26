# 第二阶段：原生容器内核能力

本阶段在已交付的 Nova／KPM／SUSFS 内核上补齐 Docker 和 LXC 使用的内核能力。基于 Stage 1 成功构建提交 `c53203f06977f3662beb515bbc58152e6fb031f0`，继承其后的 `b597f31c903d03a089246428e62e1e6bf24e7536` 缓存安装修复。阶段分支为 `feature/stage2-native-containers`。

执行范围依据[统一设计方案](https://docs.google.com/document/d/1372tIPpVH5t92IrW639lcuIUpHLKBSNazwvumTV0O8s/edit)的容器能力、构建交付与验收章节。性能优化属于第三阶段。

## 配置与接口

构建依次合并 [stage1.config](../ci/configs/stage1.config) 和 [stage2.config](../ci/configs/stage2.config)，再执行 `olddefconfig`。第二阶段新增的配置如下。

| 能力 | 新增配置 |
| --- | --- |
| namespace 与 IPC | `PID_NS`、`USER_NS`、`SYSVIPC`、`POSIX_MQUEUE`、`IPC_NS` |
| CPU、内存与进程限制 | `FAIR_GROUP_SCHED`、`CFS_BANDWIDTH`、`MEMCG`、`MEMCG_SWAP`、`MEMCG_SWAP_ENABLED`、`CGROUP_PIDS`、`CGROUP_DEVICE` |
| 文件句柄接口 | `FHANDLE` |
| 网络规则扩展 | `NETFILTER_XT_MATCH_ADDRTYPE`、`NETFILTER_XT_TARGET_CHECKSUM` |
| IPv6 NAT | `NF_NAT_IPV6`、`IP6_NF_NAT`、`NF_NAT_MASQUERADE_IPV6`、`IP6_NF_TARGET_MASQUERADE` |

这些配置全部静态内建。已有 OverlayFS、ext4、F2FS、FUSE、loop、tmpfs、bridge、veth、TUN、IPv4 NAT、seccomp、SELinux、Capabilities 与 Android cgroup 接口保持。`PAGE_COUNTER`、`CGROUP_WRITEBACK` 等由 Kconfig 依赖选中。

`BRIDGE_NETFILTER=n`、`BLK_DEV_THROTTLING=n`；默认包无新增必需网络 LKM。`STATIC_USERMODEHELPER=y` 和空 helper 路径保持。设备 boot 参数、Android Task Profiles、Power HAL、LMKD、zram 与宿主网络配置沿用当前 ROM。

## CPU 带宽引用释放

本树创建有限 CPU quota 时增加带宽 static key 引用，直接删除该 cgroup 时原路径没有对应减少。`cpu_cgroup_css_free()` 先保存是否存在有限 quota，沿原路径释放组并停止带宽计时器，随后在 CPU hotplug 读锁内减少引用。

修复采用[上游 v2 补丁](https://lkml.iu.edu/2407.2/06639.html)的释放阶段，适配本树 `cfs_bandwidth_usage_dec()` 所需的 CPU hotplug 锁。该函数由 cgroup 释放工作队列调用，避免在 RCU 回调内执行可睡眠的 static key 更新。配置关闭时没有新增执行路径；回退只需撤销对应独立提交。

## 构建与检查

运行 **Floral kernel** 工作流，选择 `stage2` 与本阶段源码提交；`auto` 根据实际 checkout 中的配置选择阶段。脚本顺序为 `build.sh stage2`、`kpm.sh stage2`、`package.sh stage2`。

Full Clang LTO、非 permissive CFI、SCS、强栈保护、栈初始化、`REFCOUNT_FULL`、`AUDIT`、`FRAME_WARN=3072` 和 Stage 1 的 Nova／KPM 检查继续执行。容器检查核对解析后的配置、实际 ELF 中的 controller／namespace／syscall／IPv6 NAT 接口与对应构建对象。

vendor ABI 检查使用固定的完整 vermagic 与符号 CRC 基线。MEMCG 和 fair 组调度改变公共结构布局，需要为第二阶段配套当前源码构建的完整 27 个 vendor 模块；文件名清单与原厂 CRC 保存在同一份 [vendor-abi.json](../ci/reference/vendor-abi.json)。

原厂 ABI 对照继续保留真实差异。只有错误全部属于已存在导出符号的 CRC 变化时，才进入独立的配套模块检查：核对完整文件集合、ARM64 ELF、完整 vermagic、`module_layout` 和所有导入符号 CRC，包括模块间导出。缺失导出、配置、链接、Nova 或配套模块检查失败都会停止打包。

需要匹配模块的 AnyKernel3 ZIP 文件名带有 `requires-matching-modules`，并同时交付 `VendorModules-floral-stage2.zip`。模块归档包含 27 个 `.ko` 与使用说明；刷入前需为当前 ROM 准备同步替换。AnyKernel3 继续只更新 boot 内核，模块部署不自动写入 vendor。回退时内核和模块一起恢复。

每次运行的配置、编译器版本、源码提交、日志、符号表、容器能力报告与 ABI 结果保存在 GitHub Actions Artifacts。flame／coral 使用共用内核，各自生成机型限定的 AnyKernel3 ZIP；`Image.lz4` 单独归档。当前 ROM 的 boot 输入由用户按需提供，默认构建不生成独立 `boot.img`。

## 外部环境与实机验收

容器 runtime、rootfs、安装与启停工具沿用现成方案。Android 的 CPU v1／memory v2 布局需要外部环境提供标准 runtime 可用的控制器挂载；内核继续保留 cpuset 的 noprefix 接口。第二阶段不附 cgroup 描述文件、网络脚本或用户态策略。

MEMCG 与 fair 分组调度在未启动容器时也存在。实机检查覆盖未启动容器、运行中、停止后三种状态的前台交互、App／GMS、休眠、人脸、PSI／LMKD 与资源回收。CPU、内存、swap、pids、devices 限制需核对真实控制文件和负载效果；网络需核对双栈出网、端口发布、热点与 VPN。依赖 bridge netfilter hook 的网络模式保留待验证状态。

回退使用阶段一匹配的 boot／Image 和模块，保留外部容器数据。先通过现有工具停止容器，再按原恢复路径回退内核；完整实机验收通过后再创建 accepted 标签。
