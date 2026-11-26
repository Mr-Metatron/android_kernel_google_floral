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

Stage 2 起将原本以 LKM 构建的 27 个树内 vendor 驱动静态链接进内核，覆盖 WLAN、MSM 11ad、音频／ADSP、incrementalfs 与 softdog。MEMCG 和 fair 分组调度改变公共结构布局，原厂 `.ko` 与新内核的符号 CRC 不兼容；驱动内建后，更新内核无需替换 `/vendor/lib/modules`。保留 `CONFIG_MODULES=y`、`CONFIG_MODVERSIONS=y`，供按当前内核配置构建的可选 LKM 使用。

四项驱动配置在 `stage2.config` 中设为 `y`；`techpack/audio/config/sm8150auto.conf` 中原有全部 42 项 `=m` 改为 `=y`。WLAN 移除等待用户写 `boot_wlan` 的间接层，由内建初始化入口直接调用 `hdd_driver_load()` 注册 CNSS 驱动，保留原有内建命名与 `-EPROBE_DEFER` 路径。适配参考 Sultan 的[音频内建提交](https://github.com/kerneltoast/android_kernel_google_floral/commit/cd2d304737)和[WLAN 初始化提交](https://github.com/kerneltoast/android_kernel_google_floral/commit/2d87f77f62)。

固定设备树在 `on init` 启动 `insmod_sh`，早于 KernelSU `post-fs-data`。[原脚本](https://github.com/LineageOS/android_device_google_coral/blob/54913ec2b6e46e9d8d09ae21a15a3d1f7ca634dc/init/init.insmod.sh)未启用 `set -e`，模块装载失败后仍继续执行 ADSP／CDSP／SLPI 启动节点和 ready 属性；该 ROM 脚本保持原样。内建驱动的初始化与依赖探测由内核承担，实机需核对 Wi-Fi、音频和 ADSP 启动结果及日志。

Stage 1 保留固定完整 vermagic 与导入 CRC 的原厂模块 ABI 检查。Stage 2 及后续 Stage 3 改为核验静态驱动配置、实际 ELF 入口和构建对象，并要求清理历史残留后的输出目录中没有 `.ko`。Full LTO、CFI、SCS 与栈帧检查失败时停止打包。

flame／coral 使用共用内核，各自生成机型限定的 `AnyKernel3-<device>-stage2.zip`。只需刷入对应机型的 ZIP，安装器保留当前 ramdisk，不修改 vendor、dtbo 或 vbmeta；回退时恢复同一槽位原 boot。最终 KPM 镜像的体积检查与安装器分区检查见 [打包说明](../ci/packaging/README.md)。

两款机型 ZIP、独立 `Image.lz4`、包外 `SHA256SUMS`、构建检查资料分别上传为独立 GitHub Actions Artifacts，保存 30 天。检查资料包含实际 `.config`、编译器版本、源码提交、日志、符号表与能力报告；不携带重复配置、裸模块、手工目录或中间内核镜像。当前 ROM 的 boot 输入由用户按需提供，默认构建不生成独立 `boot.img`。

## 外部环境与实机验收

容器 runtime、rootfs、安装与启停工具沿用现成方案。Android 的 CPU v1／memory v2 布局需要外部环境提供标准 runtime 可用的控制器挂载；内核继续保留 cpuset 的 noprefix 接口。第二阶段不附 cgroup 描述文件、网络脚本或用户态策略。

MEMCG 与 fair 分组调度在未启动容器时也存在。实机检查覆盖未启动容器、运行中、停止后三种状态的前台交互、App／GMS、休眠、人脸、PSI／LMKD 与资源回收。驱动内建后复测 Wi-Fi 启停与休眠恢复、音频播放与录音、通话、ADSP 启动及 incrementalfs 安装路径。CPU、内存、swap、pids、devices 限制需核对真实控制文件和负载效果；网络需核对双栈出网、端口发布、热点与 VPN。依赖 bridge netfilter hook 的网络模式保留待验证状态。

先通过现有工具停止容器，再恢复同一槽位的阶段一 boot／Image；vendor 与外部容器数据保持原样。完整实机验收通过后再创建 accepted 标签。
