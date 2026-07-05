// Defines LSP types, client requests, and JSON-RPC stream decoding.
import { EventEmitter } from "node:events";
import { type Readable, Transform } from "node:stream";
import { JSONRPCClient } from "json-rpc-2.0";

export class JSONRPCTransform extends Transform {
  private buffer = Buffer.alloc(0);

  constructor() {
    super({ objectMode: true });
  }

  override _transform(
    chunk: unknown,
    encoding: BufferEncoding | "buffer",
    callback: (error?: Error | null) => void,
  ): void {
    const chunkBuffer = Buffer.isBuffer(chunk)
      ? chunk
      : Buffer.from(chunk as string, encoding === "buffer" ? "utf8" : encoding);
    this.buffer = Buffer.concat([this.buffer, chunkBuffer]);

    while (true) {
      const headerEndIndex = this.buffer.indexOf("\r\n\r\n");
      if (headerEndIndex === -1) {
        break;
      }

      const headersStr = this.buffer.slice(0, headerEndIndex).toString("utf8");
      const contentLengthMatch = /Content-Length:\s*(\d+)/i.exec(headersStr);
      if (!contentLengthMatch) {
        callback(new Error("Missing Content-Length header"));
        return;
      }

      const contentLength = Number.parseInt(contentLengthMatch[1], 10);
      const messageStartIndex = headerEndIndex + 4;
      const messageEndIndex = messageStartIndex + contentLength;

      if (this.buffer.length < messageEndIndex) {
        break; // Wait for more data
      }

      const messageJson = this.buffer.slice(messageStartIndex, messageEndIndex).toString("utf8");
      this.buffer = this.buffer.slice(messageEndIndex);

      try {
        this.push(messageJson);
      } catch (err) {
        callback(err as Error);
        return;
      }
    }

    callback();
  }

  static createStream(readStream: NodeJS.ReadableStream): JSONRPCTransform {
    const transform = new JSONRPCTransform();
    readStream.pipe(transform);
    return transform;
  }
}

export interface LSPPosition {
  line: number;
  character: number;
}

export interface LSPRange {
  start: LSPPosition;
  end: LSPPosition;
}

export interface LSPTextEdit {
  range: LSPRange;
  newText: string;
}

export interface LSPVersionedTextDocumentIdentifier {
  uri: string;
  version?: number | null;
}

export interface LSPTextDocumentEdit {
  textDocument: LSPVersionedTextDocumentIdentifier;
  edits: LSPTextEdit[];
}

export interface LSPWorkspaceEdit {
  changes?: Record<string, LSPTextEdit[]>;
  documentChanges?: LSPTextDocumentEdit[];
}

export interface LSPSymbol {
  name: string;
  kind: number;
  range?: LSPRange;
  selectionRange?: LSPRange;
  detail?: string;
  containerName?: string;
  location?: {
    uri: string;
    range: LSPRange;
  };
  children?: LSPSymbol[];
}

export interface LSPLocation {
  uri: string;
  range: LSPRange;
}

export interface LSPLocationLink {
  originSelectionRange?: LSPRange;
  targetUri: string;
  targetRange: LSPRange;
  targetSelectionRange: LSPRange;
}

export type LSPDefinition = LSPLocation | LSPLocation[] | LSPLocationLink[] | null;

export interface LSPMarkupContent {
  kind: string;
  value: string;
}

export interface LSPMarkedString {
  language: string;
  value: string;
}

export interface LSPHover {
  contents: string | LSPMarkedString | (string | LSPMarkedString)[] | LSPMarkupContent;
  range?: LSPRange;
}

export class LspJSONRPCEndpoint extends EventEmitter {
  private writable: NodeJS.WritableStream;
  private readable: NodeJS.ReadableStream;
  private readableByline: JSONRPCTransform;
  private client: JSONRPCClient;
  private nextId = 0;

  constructor(writable: NodeJS.WritableStream, readable: NodeJS.ReadableStream) {
    super();
    this.writable = writable;
    this.readable = readable;
    this.readableByline = JSONRPCTransform.createStream(this.readable as Readable);

    const createId = () => this.nextId++;
    this.client = new JSONRPCClient(async (jsonRPCRequest) => {
      const jsonRPCRequestStr = JSON.stringify(jsonRPCRequest);
      const contentLength = Buffer.from(jsonRPCRequestStr, "utf-8").byteLength;
      this.writable.write(`Content-Length: ${contentLength}\r\n\r\n${jsonRPCRequestStr}`);
    }, createId);

    this.readableByline.on("data", (jsonRPCResponseOrRequest: string) => {
      try {
        const jsonrpc = JSON.parse(jsonRPCResponseOrRequest);

        if (
          Object.hasOwn(jsonrpc, "id") &&
          (Object.hasOwn(jsonrpc, "result") || Object.hasOwn(jsonrpc, "error"))
        ) {
          this.client.receive(jsonrpc);
        } else if (Object.hasOwn(jsonrpc, "method")) {
          if (Object.hasOwn(jsonrpc, "id")) {
            this.emit(jsonrpc.method, jsonrpc.params, jsonrpc.id);
            if (this.listenerCount(jsonrpc.method) === 0) {
              this.respondToRequest(jsonrpc.id, {});
            }
          } else {
            this.emit(jsonrpc.method, jsonrpc.params);
          }
        } else {
          this.emit(
            "error",
            new Error(`[transform] Received invalid JSON-RPC message: ${jsonRPCResponseOrRequest}`),
          );
        }
      } catch (err) {
        this.emit("error", err);
      }
    });
  }

  send<T = unknown>(method: string, message?: unknown): Promise<T> {
    return Promise.resolve(this.client.request(method, message));
  }

  notify(method: string, message?: unknown): void {
    this.client.notify(method, message);
  }

  respondToRequest(id: unknown, result: unknown): void {
    const response = {
      jsonrpc: "2.0",
      id: id,
      result: result,
    };
    const responseStr = JSON.stringify(response);
    const contentLength = Buffer.from(responseStr, "utf-8").byteLength;
    this.writable.write(`Content-Length: ${contentLength}\r\n\r\n${responseStr}`);
  }
}

export class LspClient {
  constructor(private endpoint: LspJSONRPCEndpoint) {}

  initialize(params: unknown): Promise<unknown> {
    return this.endpoint.send<unknown>("initialize", params);
  }

  initialized(): void {
    this.endpoint.notify("initialized");
  }

  shutdown(): Promise<unknown> {
    return this.endpoint.send<unknown>("shutdown");
  }

  exit(): void {
    this.endpoint.notify("exit");
  }

  didOpen(params: unknown): void {
    this.endpoint.notify("textDocument/didOpen", params);
  }

  didClose(params: unknown): void {
    this.endpoint.notify("textDocument/didClose", params);
  }

  hover(params: unknown): Promise<LSPHover | null> {
    return this.endpoint.send<LSPHover | null>("textDocument/hover", params);
  }

  definition(params: unknown): Promise<LSPDefinition> {
    return this.endpoint.send<LSPDefinition>("textDocument/definition", params);
  }

  references(params: unknown): Promise<LSPLocation[] | null> {
    return this.endpoint.send<LSPLocation[] | null>("textDocument/references", params);
  }

  documentSymbol(params: unknown): Promise<LSPSymbol[] | null> {
    return this.endpoint.send<LSPSymbol[] | null>("textDocument/documentSymbol", params);
  }
}
