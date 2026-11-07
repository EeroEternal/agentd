# AgentOS Native Usability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the native local console interactive and stop native services in reverse priority order.

**Architecture:** Bind the console process to `/dev/tty1`, make its process group foreground, set explicit termios modes, and use existing native definitions for deployment. No kernel or bootloader behavior changes.

**Tech Stack:** Python termios, TTY ioctls, native supervisor.

---

### Task 1: Fix the native console

**Files:**
- Modify: `agentd/native-console.py`

- [ ] **Step 1: Set foreground process group and complete terminal modes**

Use this implementation:

```python
#!/usr/bin/python
import fcntl
import os
import termios

fd = os.open("/dev/tty1", os.O_RDWR)
fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
os.tcsetpgrp(fd, os.getpid())
os.dup2(fd, 0)
os.dup2(fd, 1)
os.dup2(fd, 2)
attributes = termios.tcgetattr(fd)
attributes[0] |= termios.ICRNL
attributes[1] |= termios.ONLCR
attributes[2] |= termios.CREAD
attributes[3] |= termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ICANON | termios.ISIG
attributes[6][termios.VMIN] = 1
attributes[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, attributes)
os.execv("/usr/bin/bash", ["/usr/bin/bash", "--login"])
```

- [ ] **Step 2: Check syntax**

Run `python3 -m py_compile agentd/native-console.py`. Expected: exit `0`.

### Task 2: Fix native shutdown ordering

**Files:**
- Modify: `agentd/native.py`

- [ ] **Step 1: Stop definitions in reverse priority order**

```python
    def stop_all(self):
        definitions = [json.loads(path.read_text(encoding="utf-8")) for path in DEFINITIONS.glob("*.json")]
        definitions.sort(key=lambda definition: definition["priority"], reverse=True)
        for definition in definitions:
            try:
                self.stop(definition["name"])
            except Exception:
                pass
```

- [ ] **Step 2: Check syntax**

Run `python3 -m py_compile agentd/native.py`. Expected: exit `0`.

### Task 3: Deploy and verify on the native system

**Files:**
- Remote modify: `/usr/local/lib/agentd/`
- Remote modify: `/etc/agentd/services/agentos-console.json`

- [ ] **Step 1: Deploy source and enable console**

Copy both Python files, install them as executable, then use `native.install` to set `agentos-console` enabled=true with argv `["/usr/bin/python", "/usr/local/lib/agentd/native-console.py"]`, priority 60 and restart on failure.

- [ ] **Step 2: Reboot with the operator present**

The operator selects `AgentOS Native`. Existing boot entries remain unchanged.

- [ ] **Step 3: Verify console and network**

At the local console, type `echo native-console-ok`. Expected: characters are visible while typing and the command prints `native-console-ok`. Then verify remote SSH, `native.list`, and that heartbeat remains running. If console input still fails, disable it remotely without changing the native network system.
