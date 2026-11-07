# AgentOS agent2 Status Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and physically validate `7.1.9-agent2` with a built-in read-only `/proc/agent_status` interface while preserving the original Omarchy and agent1 boot paths.

**Architecture:** Add one built-in procfs/seq_file source file to the existing Arch `v7.1.9-arch1` tree. Reuse the established build machine and root `agentd`, produce independent agent2 artifacts, and add a separate Limine entry without changing the default.

**Tech Stack:** Linux kernel C, procfs, seq_file, Kbuild, Python standard library for remote file checks, mkinitcpio, Limine, SSH.

---

## File map

- Create locally: `kernel/agentos.c` — procfs implementation and canonical source copy.
- Modify remotely: `/srv/agentos/src/linux/kernel/Makefile` — add `agentos.o` to built-in objects.
- Create remotely: `/srv/agentos/src/linux/kernel/agentos.c` — deploy the canonical source.
- Create remotely: `/srv/agentos/artifacts/agent2/` — patch, configuration, kernel, and initramfs.
- Create remotely: `/usr/lib/modules/7.1.9-agent2/` — agent2 modules.
- Create remotely: `/boot/agentos/agent2/` — independent boot files.
- Modify remotely: `/boot/limine.conf` — append `AgentOS Test 2` only after successful build and initramfs generation.

### Task 1: Preserve the baseline and implement the procfs interface

**Files:**
- Create: `kernel/agentos.c`
- Remote create: `/srv/agentos/artifacts/agent2/kernel-Makefile.before`
- Remote modify: `/srv/agentos/src/linux/kernel/Makefile`
- Remote create: `/srv/agentos/src/linux/kernel/agentos.c`

- [ ] **Step 1: Create the canonical AgentOS source file**

```c
#include <linux/errno.h>
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/proc_fs.h>
#include <linux/seq_file.h>
#include <linux/utsname.h>

#define AGENTOS_VERSION "0.2.0"

static int agent_status_show(struct seq_file *m, void *v)
{
	seq_puts(m, "agentos_version=" AGENTOS_VERSION "\n");
	seq_printf(m, "kernel_release=%s\n", init_utsname()->release);
	seq_puts(m, "status=running\n");
	return 0;
}

static int __init agentos_init(void)
{
	if (!proc_create_single("agent_status", 0444, NULL, agent_status_show)) {
		pr_err("agentos: failed to create /proc/agent_status\n");
		return -ENOMEM;
	}
	return 0;
}
fs_initcall(agentos_init);
```

- [ ] **Step 2: Check the source against kernel formatting expectations**

Run:

```bash
python3 -c 'from pathlib import Path; p=Path("kernel/agentos.c").read_text(); assert "proc_create_single(\"agent_status\", 0444" in p; assert "init_utsname()->release" in p; assert "agentos_version=\" AGENTOS_VERSION" in p'
```

Expected: exit `0`. This checks source content only; the kernel compiler performs the authoritative build validation.

- [ ] **Step 3: Back up the remote Makefile and add exactly one built-in object entry**

Run:

```bash
ssh admin@192.168.3.11 'sudo install -d -m 0755 /srv/agentos/artifacts/agent2 && sudo cp /srv/agentos/src/linux/kernel/Makefile /srv/agentos/artifacts/agent2/kernel-Makefile.before && sudo python -c '"'"'from pathlib import Path; p=Path("/srv/agentos/src/linux/kernel/Makefile"); s=p.read_text(); anchor="\nobj-$(CONFIG_MULTIUSER) += groups.o\n"; assert s.count(anchor) == 1; assert "obj-y += agentos.o" not in s; p.write_text(s.replace(anchor, "\nobj-y += agentos.o\n"+anchor, 1))'"'"''
scp kernel/agentos.c admin@192.168.3.11:/tmp/agentos-agent2.c
ssh admin@192.168.3.11 'sudo install -m 0644 /tmp/agentos-agent2.c /srv/agentos/src/linux/kernel/agentos.c'
```

Expected: remote Makefile contains one `obj-y += agentos.o`; the new C file is installed beside it.

- [ ] **Step 4: Save the exact source changes**

Run:

```bash
ssh admin@192.168.3.11 'cd /srv/agentos/src/linux && { sudo diff -u /srv/agentos/artifacts/agent2/kernel-Makefile.before kernel/Makefile || test $? -eq 1; sudo diff -u /dev/null kernel/agentos.c || test $? -eq 1; } | sudo tee /srv/agentos/artifacts/agent2/agentos.patch >/dev/null && sudo test -s /srv/agentos/artifacts/agent2/agentos.patch'
```

Expected: the patch contains both the Makefile addition and the complete new source file.

### Task 2: Configure and build agent2

**Files:**
- Modify remotely: existing kernel build workspace `.config`
- Create remotely: `/srv/agentos/artifacts/agent2/config`
- Create remotely: `/srv/agentos/artifacts/agent2/vmlinuz`

- [ ] **Step 1: Change only the local release suffix and regenerate configuration**

Run in the existing build workspace used for agent1:

```bash
ssh admin@192.168.3.11 'sudo /srv/agentos/src/linux/scripts/config --file /srv/agentos/build/agent1/.config --set-str LOCALVERSION -agent2 && sudo make -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= olddefconfig && sudo make -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= prepare && sudo make -s -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= kernelrelease'
```

Expected: final output is exactly `7.1.9-agent2`.

- [ ] **Step 2: Build the modified kernel and modules through agentd**

Run:

```bash
ssh admin@192.168.3.11 'sudo env PYTHONPATH=/usr/local/lib/agentd /usr/bin/python -m agentd exec --timeout 28800 make -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= -j2 bzImage modules'
```

Expected: JSON has `ok:true`, `exit_code:0`, and `timed_out:false`. On failure, retain the run logs and do not install or modify boot configuration.

- [ ] **Step 3: Verify the image and save build configuration**

Run:

```bash
ssh admin@192.168.3.11 'sudo cp /srv/agentos/build/agent1/arch/x86/boot/bzImage /srv/agentos/artifacts/agent2/vmlinuz && sudo cp /srv/agentos/build/agent1/.config /srv/agentos/artifacts/agent2/config && sudo file /srv/agentos/artifacts/agent2/vmlinuz && sudo test -s /srv/agentos/artifacts/agent2/vmlinuz'
```

Expected: `file` reports Linux bzImage version `7.1.9-agent2`.

### Task 3: Install agent2 modules and generate a complete initramfs

**Files:**
- Create remotely: `/usr/lib/modules/7.1.9-agent2/`
- Create remotely: `/srv/agentos/artifacts/agent2/initramfs`

- [ ] **Step 1: Install modules under the agent2 release**

Run:

```bash
ssh admin@192.168.3.11 'sudo make -C /srv/agentos/src/linux O=/srv/agentos/build/agent1 EXTRAVERSION= modules_install && sudo test -d /usr/lib/modules/7.1.9-agent2 && sudo test -d /usr/lib/modules/7.1.9-arch1-2'
```

Expected: agent2 modules install successfully and the original modules remain present.

- [ ] **Step 2: Reuse the verified merged mkinitcpio configuration**

Run:

```bash
ssh admin@192.168.3.11 'sudo cp /srv/agentos/artifacts/agent1/mkinitcpio.conf /srv/agentos/artifacts/agent2/mkinitcpio.conf && sudo mkinitcpio -k 7.1.9-agent2 -c /srv/agentos/artifacts/agent2/mkinitcpio.conf -g /srv/agentos/artifacts/agent2/initramfs'
```

Expected: hook list includes `udev`, `plymouth`, `encrypt`, `btrfs-overlayfs`, and `resume`; image generation succeeds. The warning about not finding a kernel image by version is acceptable only because modules were found and the final image passes content verification.

- [ ] **Step 3: Verify initramfs content and original boot files**

Run:

```bash
ssh admin@192.168.3.11 'sudo lsinitcpio -a /srv/agentos/artifacts/agent2/initramfs && sudo test -s /boot/6e5d19fd7113410e935423dde66fb804/linux/vmlinuz && sudo test -s /boot/agentos/agent1/vmlinuz && sudo test -s /srv/agentos/artifacts/agent2/initramfs'
```

Expected: content listing includes `cryptsetup`, `dm-crypt`, `encrypt`, and `btrfs-overlayfs`; all existence checks pass.

### Task 4: Add the independent AgentOS Test 2 boot entry

**Files:**
- Create remotely: `/boot/agentos/agent2/vmlinuz`
- Create remotely: `/boot/agentos/agent2/initramfs`
- Create remotely: `/srv/agentos/artifacts/agent2/limine.conf.before`
- Modify remotely: `/boot/limine.conf`

- [ ] **Step 1: Install independent boot files and back up Limine**

Run:

```bash
ssh admin@192.168.3.11 'sudo install -d -m 0755 /boot/agentos/agent2 && sudo install -m 0644 /srv/agentos/artifacts/agent2/vmlinuz /boot/agentos/agent2/vmlinuz && sudo install -m 0644 /srv/agentos/artifacts/agent2/initramfs /boot/agentos/agent2/initramfs && sudo cp /boot/limine.conf /srv/agentos/artifacts/agent2/limine.conf.before'
```

Expected: all commands exit `0`; no original file is overwritten.

- [ ] **Step 2: Append exactly one new entry through a verified temporary file**

Create local `limine/agentos-agent2.conf` with this content:

```text
/+AgentOS Test 2
  //linux
  comment: AgentOS kernel 7.1.9-agent2 with proc status
  protocol: linux
  path: boot():/agentos/agent2/vmlinuz
  module_path: boot():/agentos/agent2/initramfs
  cmdline: cryptdevice=PARTUUID=e553df7c-02:root root=/dev/mapper/root zswap.enabled=0 rootflags=subvol=@ rw rootfstype=btrfs loglevel=7 systemd.show_status=true
```

Then run:

```bash
scp limine/agentos-agent2.conf admin@192.168.3.11:/tmp/agentos-agent2.conf
ssh admin@192.168.3.11 'sudo python -c '"'"'from pathlib import Path; target=Path("/boot/limine.conf"); before=Path("/srv/agentos/artifacts/agent2/limine.conf.before").read_text(); fragment=Path("/tmp/agentos-agent2.conf").read_text(); assert target.read_text() == before; assert "/+AgentOS Test 2" not in before; temporary=Path("/boot/limine.conf.agentos2.tmp"); temporary.write_text(before+"\n"+fragment); assert temporary.read_text().startswith(before)'"'"' && sudo install -m 0600 /boot/limine.conf.agentos2.tmp /boot/limine.conf'
```

This preserves the complete previous configuration and appends the new block once.

- [ ] **Step 3: Verify all boot choices and unchanged default**

Run a root Python check that asserts:

```python
from pathlib import Path

current = Path("/boot/limine.conf").read_text()
before = Path("/srv/agentos/artifacts/agent2/limine.conf.before").read_text()
assert current.startswith(before)
assert current.count("/+AgentOS Test 2") == 1
assert current.count("/+AgentOS Test\n") == 1
assert "default_entry: 2" in current
assert Path("/boot/agentos/agent2/vmlinuz").stat().st_size > 0
assert Path("/boot/agentos/agent2/initramfs").stat().st_size > 0
assert Path("/boot/agentos/agent1/vmlinuz").stat().st_size > 0
assert Path("/boot/6e5d19fd7113410e935423dde66fb804/linux/vmlinuz").stat().st_size > 0
```

Expected: exit `0`. Restore the backup immediately if this check fails.

### Task 5: Validate agent2 on physical hardware

**Files:**
- Remote create after boot: `/srv/agentos/artifacts/agent2/boot-verification.txt`

- [ ] **Step 1: Reboot only after the operator confirms physical access**

The operator selects `AgentOS Test 2` in Limine and unlocks LUKS using the existing procedure. Do not change the default entry. If boot fails, the operator restarts into original Omarchy or agent1 and records the visible error.

- [ ] **Step 2: Verify the new interface and system health over SSH**

Run:

```bash
ssh admin@192.168.3.11 'uname -r; stat -c "%a %U %G" /proc/agent_status; cat /proc/agent_status; findmnt -no SOURCE,FSTYPE,OPTIONS /; systemctl is-system-running; systemctl is-active agentd sshd; lspci -nnk'
```

Expected:

- release is `7.1.9-agent2`;
- proc mode is `444` and owner/group are root;
- output contains `agentos_version=0.2.0`, the matching release, and `status=running`;
- root is the expected writable Btrfs subvolume;
- systemd is `running`, agentd and sshd are `active`;
- storage, display, and network drivers are loaded.

Save the same output to `/srv/agentos/artifacts/agent2/boot-verification.txt` using sudo after the checks succeed.

### Task 6: Verify the return path and kernel-specific behavior

**Files:**
- No new implementation files.

- [ ] **Step 1: Return to the original Omarchy entry**

After the operator confirms physical access, reboot remotely. The operator selects original `Omarchy` and unlocks LUKS normally.

- [ ] **Step 2: Verify the original kernel and absence of the new proc entry**

Run:

```bash
ssh admin@192.168.3.11 'uname -r; systemctl is-system-running; systemctl is-active agentd sshd; test ! -e /proc/agent_status'
```

Expected: `7.1.9-arch1-2`, `running`, two `active` results, and the final absence check exits `0`. This proves the proc interface belongs to agent2 rather than a user-space file in the shared root filesystem.
