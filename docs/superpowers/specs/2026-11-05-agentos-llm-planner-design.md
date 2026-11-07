# AgentOS LLM Planner and MCP Design

## Goal

Add a real Agent execution layer to AgentOS. A user can submit a natural-language goal, and the system uses an LLM to choose tools, executes them, returns results to the model, and reports completion.

## Architecture

- `a do <goal>` on the Mac sends the goal to the ThinkPad gateway.
- The planner uses an OpenAI-compatible `/chat/completions` provider.
- Local tools are exposed through the existing `agentd.dispatch` method.
- MCP servers are queried through JSON-RPC `initialize`, `tools/list`, and `tools/call`.
- The agent loop is limited to eight iterations. Every tool result is recorded and returned to the model.

## Tool model

Initial local tools:

- `native.start`, `native.stop`, `native.restart`, `native.status`, `native.logs`, `native.list`
- `system.status`, `system.health`, `network.status`
- `file.read`, `file.write`, `file.restore`
- `package.info`
- `exec`

MCP tools use the namespace `mcp__<server>__<tool>`. Their JSON schemas come from `tools/list`.

## Provider configuration

`/etc/agentd/llm.json` supports:

- `base_url`, `api_key`, `model`
- `mock`, used only for deterministic validation.

Real inference requires user-provided credentials. The mock emits a heartbeat restart/status sequence and a final report; it is not presented as an LLM.

## MCP configuration

`/etc/agentd/mcp.json` contains named servers with `url` and optional HTTP `headers`. The client uses HTTP JSON-RPC with `Accept: application/json, text/event-stream`.

## Error handling

- Missing provider configuration returns an explicit error.
- Invalid tool calls return errors to the model when possible, otherwise fail the goal with details.
- Iteration limit, timeout, invalid JSON, MCP errors, and provider failures are reported rather than silently ignored.

## Validation

- Mock a heartbeat restart and status check.
- Use a local mock MCP server to verify discovery and invocation.
- Run `a do` end-to-end and confirm the final report and service state.
- Do not claim a real LLM is configured until real credentials are supplied.
