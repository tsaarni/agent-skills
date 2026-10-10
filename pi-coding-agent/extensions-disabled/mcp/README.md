# MCP Extension for Pi Coding Agent

Connects to MCP servers and exposes their tools to pi.

## Setup

Add servers to `~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "mcpuppet": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

## Supported transports

| Config key | Transport |
|---|---|
| `command` + `args` | stdio (subprocess) |
| `url` (http/https) | Streamable HTTP |
| `url` (ws/wss) or `wsUrl` | WebSocket |
| `transport: "sse"` + `url` | SSE |

Override auto-detection with `"transport": "stdio" | "http" | "sse" | "ws"`.

## Tool naming

MCP tools appear as `mcp_<server>_<tool>` in pi. Example: `mcp_filesystem_read_file`.

## Project-local config

By default, only the global `~/.pi/agent/mcp.json` is loaded.
A project can also contribute its own `.pi/mcp.json`, but it is not loaded automatically.
When pi starts in a project whose `.pi/mcp.json` contains MCP servers, it prints a notice to run `/mcp-enable-project` to activate the project-local servers.
This setting is not persisted, so the notice appears each time pi starts.
