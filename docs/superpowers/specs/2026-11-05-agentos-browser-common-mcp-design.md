# AgentOS Headless Browser and Common MCP Design

## Goal

Let AgentOS complete tasks that require JavaScript-rendered web pages and persistent agent knowledge, while exposing ordinary workspace files through standard MCP tools.

## Architecture

- Add a local `browser_fetch` planner tool backed by Playwright and the Chromium already installed on the ThinkPad. It waits for scripted page rendering, retries page-declared module imports when an SPA leaves an empty body, and converts the resulting DOM to title, plain text, and links.
- Extend `agentd/mcp_client.py` with MCP stdio transport while retaining the existing HTTP transport. A stdio client starts a configured process, performs `initialize`, sends `notifications/initialized`, and supports `tools/list` and `tools/call`.
- Configure the official filesystem and memory MCP servers. Filesystem access is restricted to `/var/lib/agentos/workspace`; memory is stored under `/var/lib/agentos`.
- Keep web and browser results bounded, validate HTTP(S) URLs, and apply navigation and subprocess timeouts. Browser errors and MCP process errors are returned to the planner instead of disconnecting the gateway.

## Components

- `agentd/web.py`: share HTML extraction between static and rendered fetches.
- `agentd/browser.py`: Playwright browser lifecycle, SPA rendering fallback, and rendered-page extraction.
- `agentd/planner.py`: expose `browser_fetch` and guide the model to use it when static extraction has no body text or JavaScript is required.
- `agentd/mcp_client.py`: support `command`, `args`, and `env` server definitions in addition to `url`.
- `/etc/agentd/mcp.json`: filesystem and memory server definitions; contains no credentials.

## Validation

- Use a local page that adds body text with JavaScript and verify rendered text differs from static HTML.
- Verify the original `a do` request for `https://xinference.cn` returns rendered page text and finishes cleanly.
- Exercise MCP discovery and real calls through the planner for workspace file writing/reading and memory create/read.
- Confirm static HTTP MCP behavior and existing local tools still work.

## Boundaries

This iteration does not add arbitrary browser scripting, screenshots, authenticated browsing, GitHub, email, or other credential-dependent services. No LLM or gateway secrets are added to MCP configuration.
