# Agent documentation (English)

## A-share downstream policy

This fork restricts the Agent Gateway to GET, HEAD and OPTIONS. All POST, PUT, PATCH and DELETE calls return HTTP 403 `ASHARE_AGENT_READ_ONLY` when the gateway is enabled (503 when disabled), including research job creation, notification changes and token administration. Agents cannot modify or promote running strategies. The original operation descriptions remain in OpenAPI for upstream tracking; they do not grant write capability in this build. Broker access is also hard-disabled and cannot be enabled with environment flags. Human administrators must use the separately controlled human interface for token maintenance.


This folder holds **agent-facing** material for coding assistants (Cursor, Claude Code, Codex, CLI bots, etc.).

| Document | Purpose |
|----------|---------|
| [MCP_SETUP.md](MCP_SETUP.md) | Wire Cursor / Claude Code / Codex / remote agents to a QuantDinger backend via the `quantdinger-mcp` MCP server (local stdio + remote HTTP) |
| [AGENT_QUICKSTART.md](AGENT_QUICKSTART.md) | Operator + integrator walkthrough: issue a token, call the Gateway, run paper trades |
| [cursor-mcp.example.json](cursor-mcp.example.json) | Minimal Cursor MCP configuration with replaceable endpoint and token placeholders |
| [agent-openapi.json](agent-openapi.json) | Machine-readable contract for `/api/agent/v1` (OpenAPI 3.0) |
| [../architecture/API_CONVENTIONS.md](../architecture/API_CONVENTIONS.md) | Shared HTTP conventions (envelopes, auth, Public/Internal tiers) |
| [../api/openapi.yaml](../api/openapi.yaml) | Human Web API spec (flask-smorest; migration in progress) |

**Language policy:** Machine-readable schemas, route names, scopes, environment variables, and tool identifiers remain in English as the canonical contract. Human setup guides are maintained in paired editions: [中文入口](README_CN.md) and this English entry.

Repository-level coding-agent instructions live in [`AGENTS.md`](../../AGENTS.md). Tool-specific entrypoints should load that file instead of duplicating repository rules under this documentation directory.
