/**
 * Registers MCP server tools as Pi tools.
 * Supports stdio, SSE, streamable HTTP, and WebSocket transports.
 *
 * @module mcp-extension
 */

import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { WebSocketClientTransport } from "@modelcontextprotocol/sdk/client/websocket.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import { type TObject, type TSchema, Type } from "typebox";

export interface McpServerConfig {
  /** Streamable HTTP or SSE endpoint URL. */
  url?: string;
  /** WebSocket endpoint URL (takes precedence over `url` for ws:// and wss://). */
  wsUrl?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  /** `"stdio"`, `"http"`, `"sse"`, or `"ws"`. */
  transport?: string;
}

export interface McpConfig {
  mcpServers?: Record<string, McpServerConfig>;
}

const CONFIG_PATH = path.join(os.homedir(), ".pi", "agent", "mcp.json");

async function loadConfig(): Promise<McpConfig | null> {
  try {
    const raw = await fs.readFile(CONFIG_PATH, "utf8");
    return JSON.parse(raw) as McpConfig;
  } catch {
    return null;
  }
}

/**
 * Transport resolution order:
 * 1. Explicit `transport` override
 * 2. WebSocket URL (`wsUrl` or `ws://`/`wss://` scheme)
 * 3. SSE override
 * 4. URL with auto-detection (streamable HTTP with SSE upgrade)
 * 5. Stdio fallback
 */
function createTransport(name: string, server: McpServerConfig): Transport {
  if (server.transport === "stdio") {
    return new StdioClientTransport({
      command: server.command ?? "",
      args: server.args ?? [],
      env: server.env ?? undefined,
    });
  }

  if (server.transport === "ws" || server.wsUrl) {
    const wsUrl = server.wsUrl ?? server.url;
    if (!wsUrl) {
      throw new Error(
        `MCP server "${name}": missing URL for WebSocket transport`,
      );
    }
    return new WebSocketClientTransport(new URL(wsUrl));
  }

  if (server.transport === "sse") {
    if (!server.url) {
      throw new Error(`MCP server "${name}": missing URL for SSE transport`);
    }
    return new SSEClientTransport(new URL(server.url));
  }

  if (server.url) {
    const url = new URL(server.url);
    if (url.protocol === "ws:" || url.protocol === "wss:") {
      return new WebSocketClientTransport(url);
    }
    return new StreamableHTTPClientTransport(url);
  }

  return new StdioClientTransport({
    command: server.command ?? "",
    args: server.args ?? [],
    env: server.env ?? undefined,
  });
}

type JsonSchemaPrimitive =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "object"
  | "array"
  | "null";

interface JsonSchemaProperty {
  type?: JsonSchemaPrimitive | JsonSchemaPrimitive[];
  description?: string;
  enum?: unknown[];
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
}

type TypeConstructor = (options?: { description?: string }) => TSchema;

/**
 * Integer maps to `Type.Number` — TypeBox has no integer type.
 */
const TYPE_MAP = {
  string: Type.String,
  number: Type.Number,
  integer: Type.Number,
  boolean: Type.Boolean,
} as const satisfies Record<string, TypeConstructor>;

/** Converts an MCP JSON Schema property into a TypeBox schema. */
function mapSchemaProperty(prop: JsonSchemaProperty): TSchema {
  if (Array.isArray(prop.type)) {
    const nonNull = prop.type.filter((t) => t !== "null");
    if (nonNull.length === 0) {
      return Type.Any({ description: prop.description });
    }
    if (nonNull.length === 1) {
      const creator = TYPE_MAP[nonNull[0] as keyof typeof TYPE_MAP] ?? Type.Any;
      return creator({ description: prop.description });
    }
    return Type.Any({ description: prop.description });
  }

  const type = prop.type ?? "string";

  if (prop.enum && prop.enum.length > 0) {
    const enumValues = prop.enum.map(String);
    return Type.Union(
      enumValues.map((value) => Type.Literal(value)),
      { description: prop.description },
    );
  }

  if (type === "array" && prop.items) {
    return Type.Array(mapSchemaProperty(prop.items), {
      description: prop.description,
    });
  }

  if (type === "object" && prop.properties) {
    const properties: Record<string, TSchema> = {};
    for (const [key, value] of Object.entries(prop.properties)) {
      properties[key] = mapSchemaProperty(value);
    }
    return Type.Object(properties, { description: prop.description });
  }

  const creator = TYPE_MAP[type as keyof typeof TYPE_MAP] ?? Type.Any;
  return creator({ description: prop.description });
}

interface ToolInputSchema {
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
}

function buildToolParameters(schema: ToolInputSchema): TObject {
  const properties: Record<string, TSchema> = {};
  const required = schema.required ?? [];

  if (schema.properties) {
    for (const [key, prop] of Object.entries(schema.properties)) {
      properties[key] = mapSchemaProperty(prop);
    }
  }

  return Type.Object(properties, { required });
}

interface ToolCallContent {
  type: string;
  text?: string;
  data?: string;
  mimeType?: string;
  resource?: unknown;
}

interface ToolResult {
  content: Array<{ type: "text"; text: string }>;
  details: { server: string; tool: string };
}

/** Images, audio, and resources become text placeholders. */
function formatToolResult(
  result: { content: ToolCallContent[] },
  serverName: string,
  toolName: string,
): ToolResult {
  const content = result.content.map(
    (block): { type: "text"; text: string } => {
      if (block.type === "text" && block.text !== undefined) {
        return { type: "text", text: block.text };
      }
      if (block.type === "image") {
        return {
          type: "text",
          text: `[Image: ${block.mimeType ?? "unknown"}, data length: ${block.data?.length ?? 0}]`,
        };
      }
      if (block.type === "audio") {
        return {
          type: "text",
          text: `[Audio: ${block.mimeType ?? "unknown"}, data length: ${block.data?.length ?? 0}]`,
        };
      }
      if (block.type === "resource") {
        return {
          type: "text",
          text: `[Resource: ${JSON.stringify(block.resource)}]`,
        };
      }
      return { type: "text", text: JSON.stringify(block) };
    },
  );

  return {
    content,
    details: { server: serverName, tool: toolName },
  };
}

interface ServerConnection {
  name: string;
  client: Client;
  cleanup: () => Promise<void>;
}

/** Tools are re-registered when the server sends a list-changed notification. */
async function connectServer(
  pi: ExtensionAPI,
  name: string,
  server: McpServerConfig,
): Promise<ServerConnection | null> {
  const transport = createTransport(name, server);
  const client = new Client(
    { name: "pi-mcp", version: "0.1.0" },
    {
      capabilities: {},
      listChanged: {
        tools: {
          onChanged: () => {
            registerTools(pi, name, client).catch((error: unknown) => {
              console.error(`MCP [${name}]: Failed to refresh tools:`, error);
            });
          },
        },
      },
    },
  );

  await client.connect(transport);
  await registerTools(pi, name, client);

  const cleanup = async (): Promise<void> => {
    try {
      await client.close();
    } catch {
      // Ignore close errors during shutdown
    }
  };

  return { name, client, cleanup };
}

async function registerTools(
  pi: ExtensionAPI,
  serverName: string,
  client: Client,
): Promise<void> {
  const { tools } = await client.listTools();

  for (const tool of tools) {
    const toolName = `mcp_${serverName}_${tool.name}`;
    const parameters = buildToolParameters(
      (tool.inputSchema as ToolInputSchema) ?? {},
    );

    pi.registerTool({
      name: toolName,
      label: `MCP: ${serverName}/${tool.name}`,
      description: tool.description ?? tool.name,
      parameters,
      async execute(_ctx, params) {
        const result = await client.callTool({
          name: tool.name,
          arguments: params as Record<string, unknown>,
        });
        return formatToolResult(result, serverName, tool.name);
      },
    });
  }
}

/**
 * Reads `mcp.json`, connects to all servers, registers tools.
 * Connections are closed on session shutdown.
 */
export default async function mcpExtension(pi: ExtensionAPI): Promise<void> {
  const config = await loadConfig();
  if (!config?.mcpServers) {
    return;
  }

  const connections: ServerConnection[] = [];

  for (const [name, server] of Object.entries(config.mcpServers)) {
    try {
      const conn = await connectServer(pi, name, server);
      if (conn) {
        connections.push(conn);
      }
    } catch (error: unknown) {
      console.error(`MCP: Failed to connect to server "${name}":`, error);
    }
  }

  pi.on("session_shutdown", async () => {
    await Promise.all(connections.map((c) => c.cleanup()));
    connections.length = 0;
  });
}
