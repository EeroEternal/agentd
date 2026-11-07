# AgentOS Headless Browser and Common MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add JavaScript-rendered page extraction and official filesystem/memory MCP tools to AgentOS.

**Architecture:** Reuse the installed Chromium and existing HTML parser for rendered pages. Add stdio JSON-RPC beside the existing HTTP MCP transport, then configure official local MCP servers with restricted storage paths.

**Tech Stack:** Python standard library, Chromium, MCP JSON-RPC, Node.js/npm official MCP servers.

---

## File map

- Create `agentd/browser.py`: bounded, timed Chromium DOM rendering.
- Modify `agentd/web.py`: shared HTML extraction.
- Modify `agentd/planner.py`: `browser_fetch` schema, dispatch, and prompt.
- Modify `agentd/mcp_client.py`: stdio lifecycle and RPC.
- Deploy `/etc/agentd/mcp.json`: official filesystem and memory definitions.

### Task 1: Render pages with Chromium

**Files:**
- Create: `agentd/browser.py`
- Modify: `agentd/web.py`
- Modify: `agentd/planner.py`

- [ ] **Step 1: Write and run a failing inline test**

Use a fake Playwright page whose initial body is empty and whose module fallback sets its content to `<p>Rendered</p>`. Assert `fetch("https://example.test")` returns `Rendered`; confirm failure while browser rendering is absent or returns the empty SPA body.

- [ ] **Step 2: Extract shared HTML conversion**

Add to `agentd/web.py` and call it from static `fetch`:

```python
def extract(url, data):
    parser = TextExtractor()
    parser.feed(data.decode("utf-8", errors="replace"))
    text = re.sub(r"\s+", " ", " ".join(parser.text)).strip()
    return {"url": url, "title": parser.title, "text": text, "links": parser.links}
```

- [ ] **Step 3: Implement bounded Chromium rendering**

Install `python-playwright` from pacman. Launch the existing `/usr/bin/chromium` headlessly, navigate with `wait_until="commit"`, and wait ten seconds. If body text is empty, dynamically import the page's declared module-script URLs and wait another five seconds before calling `page.content()`. Validate HTTP(S), cap output at 2 MiB, and close Chromium in `finally`.

- [ ] **Step 4: Expose and verify the planner tool**

Add `browser_fetch` with required string `url`; dispatch it to `agentd.browser.fetch`. Tell the planner to use it when JavaScript rendering is needed. Run the inline extraction test, an `execute_tool` dispatch test, and `python3 -m py_compile agentd/web.py agentd/browser.py agentd/planner.py`; expect PASS and exit 0.

### Task 2: Support stdio MCP servers

**Files:**
- Modify: `agentd/mcp_client.py`

- [ ] **Step 1: Write and run a failing inline test**

Define a server with `command` and `args`, instantiate `MCPClient`, and assert `initialize`, `list_tools`, and `call_tool` work against a newline-delimited JSON-RPC fixture. Confirm the current constructor fails with an unexpected `command` argument.

- [ ] **Step 2: Implement transport selection and stdio handshake**

Keep `MCPClient(name, url, headers=None)` compatible and add optional `command=None, args=None, env=None`. For stdio, start `subprocess.Popen` with text line-oriented pipes, merge only configured environment entries into `os.environ`, send one JSON object per line, and read responses until the request id matches. After `initialize`, write `{"jsonrpc":"2.0","method":"notifications/initialized"}` without an id. Raise the server stderr tail on startup/protocol failure and close pipes/process in `close()`.

- [ ] **Step 3: Parse stdio server definitions**

Extend `clients()` so entries containing `command` construct stdio clients with `args` and `env`, while entries containing `url` retain HTTP behavior. Verify both the stdio fixture and the existing HTTP mock discover and invoke tools.

### Task 3: Install and validate real capabilities

**Files:**
- Deploy: `agentd/browser.py`, `agentd/web.py`, `agentd/planner.py`, `agentd/mcp_client.py`
- Configure: `/etc/agentd/mcp.json`

- [ ] **Step 1: Install runtime and official servers**

Install Node.js/npm through pacman, then install `@modelcontextprotocol/server-filesystem` and `@modelcontextprotocol/server-memory` globally with npm. Verify each executable starts and responds to MCP `initialize` and `tools/list`.

- [ ] **Step 2: Configure restricted storage**

Create `/var/lib/agentos/workspace` and `/var/lib/agentos`. Configure filesystem with its allowed directory argument and memory with `MEMORY_FILE_PATH=/var/lib/agentos/memory.json`. Do not put API keys in this file.

- [ ] **Step 3: Deploy and run end-to-end tasks**

Restart `agentos-gateway`; run `a health`, the original `xinference.cn` extraction, and natural-language workspace/memory tasks. Inspect actual tool results as well as the final summary. Confirm rendered body text is nonempty, MCP files contain the requested content, memory can be read back, and every goal exits cleanly.
