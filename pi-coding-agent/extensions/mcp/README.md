# pi-extension-mcp

MCP (Model Context Protocol) extension for pi coding agent. Connects to MCP servers and exposes their tools to pi.

## Setup

Add servers to `~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    },
    "fetch": {
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

