# NOTE: unity build. single unit.

obj-$(CONFIG_KSU) := kernelsu.o
kernelsu-y := ksu.o

CFLAGS_ksu.o += -I$(srctree)/security/selinux -I$(srctree)/security/selinux/include
CFLAGS_ksu.o += -I$(objtree)/security/selinux

# Nova includes SELinux's generated class/permission definitions. Generate a
# local copy so a clean parallel or single-object build has explicit ordering.
$(obj)/ksu.o: $(obj)/flask.h
quiet_cmd_ksu_flask = GEN     $(obj)/flask.h $(obj)/av_permissions.h
      cmd_ksu_flask = $< $(obj)/flask.h $(obj)/av_permissions.h
targets += flask.h av_permissions.h
$(obj)/flask.h: scripts/selinux/genheaders/genheaders FORCE
	$(call if_changed,ksu_flask)

KDIR := $(KDIR)
MDIR := $(realpath $(dir $(abspath $(lastword $(MAKEFILE_LIST)))))

$(info -- KDIR: $(KDIR))
$(info -- MDIR: $(MDIR))

# Frozen identity for the floral stage1 build.  Upstream Makefile computes
# 30000 + main commit count - 4001; main 1439545f12bffe0263690ba862139b70ee5f20cf
# has 4008 commits.  Keep it independent of network, checkout path and date.
# Kernel source: builtin-new 5a3ec4145059ed2c4f22c70a16a28a616e104c33.
REPO_NAME := SukiSU-Nova
KSU_VERSION := 30007
KSU_VERSION_FULL := v3.0.0-5a3ec414@builtin-new-floral
$(info -- $(REPO_NAME) version: $(KSU_VERSION) [$(KSU_VERSION_FULL)])

CFLAGS_ksu.o += -DKSU_VERSION=$(KSU_VERSION)
CFLAGS_ksu.o += -DKSU_VERSION_FULL=\"$(KSU_VERSION_FULL)\"

KERNEL_VERSION := $(VERSION).$(PATCHLEVEL)
KERNEL_TYPE := Non-GKI
# Check for GKI 2.0 (5.10+ or 6.x+)
ifneq ($(shell test \( $(VERSION) -ge 5 -a $(PATCHLEVEL) -ge 10 \) -o $(VERSION) -ge 6; echo $$?),0)
# Check for GKI 1.0 (5.4)
ifeq ($(shell test $(VERSION)-$(PATCHLEVEL) = 5-4; echo $$?),0)
KERNEL_TYPE := GKI 1.0
endif
else
KERNEL_TYPE := GKI 2.0
endif
$(info -- KERNEL_VERSION: $(KERNEL_VERSION))
$(info -- KERNEL_TYPE: $(KERNEL_TYPE))

ifeq ($(CONFIG_KSU_SUSFS), y)
$(info -- $(REPO_NAME): using SUSFS_INLINE_HOOK)
else ifeq ($(CONFIG_KSU_HACK_ARM64_BRANCH_LINK), y)
$(info -- $(REPO_NAME): using ARM64_BRANCH_LINK)
else ifeq ($(CONFIG_KSU_TAMPER_SYSCALL_TABLE), y)
$(info -- $(REPO_NAME): using TAMPER_SYSCALL_TABLE)
else ifeq ($(CONFIG_KSU_KPROBES_KSUD), y)
$(info -- $(REPO_NAME): using KPROBES_KSUD)
else
$(info -- $(REPO_NAME): using Unknown)
endif

# Floral 4.14 built-in KPM uses a dedicated kernel-only entry. The matched
# KernelPatch image is required; other old kernels retain the upstream gate.
ifeq ($(CONFIG_KPM), y)
ifeq ($(KERNEL_TYPE), GKI 2.0)
$(info -- KPM is enabled)
else ifeq ($(KERNEL_VERSION)-$(CONFIG_ARM64)-$(CONFIG_KSU), 4.14-y-y)
$(info -- KPM is enabled: floral 4.14 dedicated bridge)
else
$(info -- KPM is disabled: unsupported kernel/bridge combination)
endif
else
$(info -- KPM is disabled)
endif


# compliant to last upstream kernel change as of 96770df

KSU_PACKAGE_NAME := com.sukisu.nova
KSU_EXPECTED_SIZE := 0x35c
KSU_EXPECTED_HASH := 947ae944f3de4ed4c21a7e4f7953ecf351bfa2b36239da37a34111ad29993eef

$(info -- KernelSU Manager package name: $(KSU_PACKAGE_NAME))
$(info -- KernelSU Manager signature size: $(KSU_EXPECTED_SIZE))
$(info -- KernelSU Manager signature hash: $(KSU_EXPECTED_HASH))

CFLAGS_ksu.o += -DKSU_PACKAGE_NAME=\"$(KSU_PACKAGE_NAME)\"
CFLAGS_ksu.o += -DEXPECTED_SIZE=$(KSU_EXPECTED_SIZE)
CFLAGS_ksu.o += -DEXPECTED_HASH=\"$(KSU_EXPECTED_HASH)\"

ifeq ($(shell grep -q " current_sid(void)" $(srctree)/security/selinux/include/objsec.h; echo $$?),0)
CFLAGS_ksu.o += -DKSU_COMPAT_HAS_CURRENT_SID
endif

ifeq ($(shell grep -q "struct selinux_state " $(srctree)/security/selinux/include/security.h; echo $$?),0)
CFLAGS_ksu.o += -DKSU_COMPAT_HAS_SELINUX_STATE
endif

# UL, look for iterate_dir on ‎fs/readdir.c
ifeq ($(shell grep -q "^int iterate_dir" $(srctree)/fs/readdir.c 2>/dev/null; echo $$?),0)
$(info -- KernelSU/compat: iterate_dir found!)
CFLAGS_ksu.o += -DKSU_HAS_ITERATE_DIR
endif

ifeq ($(shell grep -q "struct type_datum \*\*type_val_to_struct;" $(srctree)/security/selinux/ss/policydb.h; echo $$?),0)
CFLAGS_ksu.o += -DKSU_TYPE_VAL_TO_STRUCT
endif

# half-assed-backport from 5.1
ifeq ($(shell grep -q "struct type_datum \*\*type_val_to_struct_array;" $(srctree)/security/selinux/ss/policydb.h; echo $$?),0)
CFLAGS_ksu.o += -DKSU_TYPE_VAL_TO_STRUCT_ARRAY
endif

# branch link hook vfs_statx, this newer version uses struct filename
ifeq ($(shell grep "int vfs_statx" $(srctree)/fs/stat.c 2>/dev/null | grep -q "struct filename" 2>/dev/null; echo $$?),0)
# $(info -- KernelSU/compat: vfs_statx has struct filename argument!)
CFLAGS_ksu.o += -DKSU_HAS_VFS_STATX2
endif

# branch link hook, do_faccessat
ifeq ($(shell grep "long do_faccessat" $(srctree)/fs/open.c 2>/dev/null | grep -q "flags" 2>/dev/null; echo $$?),0)
# $(info -- KernelSU/compat: do_faccessat has flags argument!)
CFLAGS_ksu.o += -DKSU_HAS_FACCESSAT2
endif

# UL, look for read_iter on f_op struct
ifeq ($(shell grep -q "read_iter" $(srctree)/include/linux/fs.h 2>/dev/null; echo $$?),0)
$(info -- KernelSU/compat: f_op->read_iter found!)
CFLAGS_ksu.o += -DKSU_HAS_FOP_READ_ITER
endif

# KPM
# Vendor backports may change these fields independently of LINUX_VERSION_CODE.
# Inspect the actual target source header, not the host kernel or KMI label.
ksu_netlink_cfg := $(shell sed -n '/struct netlink_kernel_cfg {/,/^};/p' $(srctree)/include/linux/netlink.h)
ifneq ($(findstring cb_mutex,$(ksu_netlink_cfg)),)
ccflags-y += -DKSU_NETLINK_CFG_HAS_CB_MUTEX=1
endif
ifneq ($(findstring compare,$(ksu_netlink_cfg)),)
ccflags-y += -DKSU_NETLINK_CFG_HAS_COMPARE=1
endif

CFLAGS_ksu.o += $(call cc-option, -Wno-declaration-after-statement)
CFLAGS_ksu.o += $(call cc-option, -Wno-implicit-function-declaration)
CFLAGS_ksu.o += $(call cc-option, -Wno-missing-prototypes)
CFLAGS_ksu.o += $(call cc-option, -Wno-strict-prototypes)
CFLAGS_ksu.o += $(call cc-option, -Wno-int-conversion)
CFLAGS_ksu.o += $(call cc-option, -Wno-int-to-pointer-cast)
CFLAGS_ksu.o += $(call cc-option, -Wno-pointer-to-int-cast)
CFLAGS_ksu.o += $(call cc-option, -Wno-unused-variable)
CFLAGS_ksu.o += $(call cc-option, -Wno-unused-function)
CFLAGS_ksu.o += $(call cc-option, -Wno-unused-label)
CFLAGS_ksu.o += $(call cc-option, -Wno-format)
CFLAGS_ksu.o += $(call cc-option, -Wno-macro-redefined)
CFLAGS_ksu.o += $(call cc-option, -Wno-incompatible-pointer-types-discards-qualifiers)
CFLAGS_ksu.o += $(call cc-option, -Wno-discarded-qualifiers)
CFLAGS_ksu.o += $(call cc-option, -Wno-deprecated-octal-literals)

# dont be too strict
CFLAGS_REMOVE_ksu.o += -Werror

# Inherit the target kernel FRAME_WARN threshold and hardening flags.

# to make sure we can use builtins
CFLAGS_REMOVE_ksu.o += -fno-builtin

# Os to O2, we dont care for anything else. O3 vs O2 codegen on hotpath is same.
ifeq ($(CONFIG_CC_OPTIMIZE_FOR_SIZE),y)
CFLAGS_REMOVE_ksu.o += -Oz
CFLAGS_REMOVE_ksu.o += -Os
CFLAGS_ksu.o += -O2
endif

# attempt to enable gnu11 up to gnu2y
# we skip gnu2x here due to its inconsistency
CFLAGS_ksu.o += $(call cc-option, -std=gnu11)
CFLAGS_ksu.o += $(call cc-option, -std=gnu17)
CFLAGS_ksu.o += $(call cc-option, -std=gnu23)
CFLAGS_ksu.o += $(call cc-option, -std=gnu2y)

#ifeq ($(shell $(CC) -v 2>&1 | grep -q 'clang'; echo $$?),0)
# $(info -- KernelSU/CC: clang!)
#endif

# always inline atomics
# clang and gcc CAN try to drop a runtime C11 atomics detector
# https://github.com/llvm/llvm-project/blob/main/llvm/test/CodeGen/AArch64/atomic-ops.ll
# however this is problematic to us as those cannot be linked to the kernel (e.g. __aarch64_cas4_acq, __aarch64_cas4_rel)
# so for LKM they just get plain ARMv8.0 atomics, not LSE / LSE2, even when the device is capable of LSE.
ifeq ($(CONFIG_ARM64),y)
CFLAGS_ksu.o += $(call cc-option, -mno-outline-atomics)
endif

# Optional: for LKM users, I recommend users to build it themselves with optimizations.
# you can get optimized codegen especially around atomics.
# hint: decode your own /proc/cpuinfo and check it here https://github.com/llvm/llvm-project/blob/main/llvm/lib/TargetParser/Host.cpp
# comment it out and put your cpu / arch here
# CFLAGS_ksu.o += -mcpu=cortex-a76 -mtune=cortex-a76

# Preserve the LOS stack protector, Shadow Call Stack, stack initialization,
# frame pointer, unwind information and tracing options in release builds too.
# The parent kernel owns these flags; do not override them per object.

## For susfs stuff ##
ifeq ($(shell test -e $(srctree)/fs/susfs.c; echo $$?),0)
$(eval SUSFS_VERSION=$(shell cat $(srctree)/include/linux/susfs.h | grep -E '^#define SUSFS_VERSION' | cut -d' ' -f3 | sed 's/"//g'))
$(info )
$(info -- SUSFS_VERSION: $(SUSFS_VERSION))
else
$(info -- You have not integrated susfs in your kernel yet.)
$(info -- Read: https://gitlab.com/simonpunk/susfs4ksu)
ifeq ($(CONFIG_KSU_SUSFS),y)
$(warning -- CONFIG_KSU_SUSFS is enabled but $(srctree)/fs/susfs.c does not exist, build will fail)
endif
endif

# Native fallback targets matching the traditional out-of-tree build method
all:
	$(MAKE) -C $(KDIR) M=$(MDIR) modules
clean:
	$(MAKE) -C $(KDIR) M=$(MDIR) clean

# Keep a new line here!! Because someone may append config
