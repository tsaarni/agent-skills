import { type ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { JSONRPCEndpoint, LspClient } from "ts-lsp-client";
import { IGNORE_DIRS, type ServersConfig } from "./heuristics.js";

// Convert a file path to a file:// URL string
export function pathToUri(workspaceDir: string, filePath: string): string {
  const absolutePath = path.isAbsolute(filePath) ? filePath : path.resolve(workspaceDir, filePath);
  return pathToFileURL(absolutePath).href;
}

// Convert a file:// URL string back to an absolute file path
export function uriToPath(uri: string): string {
  return fileURLToPath(uri);
}

const DEFAULT_LANGUAGE_IDS: Record<string, string> = {
  ".ts": "typescript",
  ".tsx": "typescript",
  ".cts": "typescript",
  ".mts": "typescript",
  ".js": "javascript",
  ".jsx": "javascript",
  ".cjs": "javascript",
  ".mjs": "javascript",
  ".py": "python",
  ".go": "go",
  ".rs": "rust",
};

export function getLanguageId(filePath: string, config?: ServersConfig | null): string {
  const ext = path.extname(filePath).toLowerCase();
  if (config?.languages) {
    for (const [langId, langConfig] of Object.entries(config.languages)) {
      if (langConfig.fileExtensions.includes(ext)) {
        return langId;
      }
    }
  }
  return DEFAULT_LANGUAGE_IDS[ext] || "plaintext";
}

const SYMBOL_KINDS = [
  "Unknown",
  "File",
  "Module",
  "Namespace",
  "Package",
  "Class",
  "Method",
  "Property",
  "Field",
  "Constructor",
  "Enum",
  "Interface",
  "Function",
  "Variable",
  "Constant",
  "String",
  "Number",
  "Boolean",
  "Array",
  "Object",
  "Key",
  "Null",
  "EnumMember",
  "Struct",
  "Event",
  "Operator",
  "TypeParameter",
];

export function getSymbolKindName(kind: number): string {
  return SYMBOL_KINDS[kind] || `Kind ${kind}`;
}

export interface DiagnosticInfo {
  severity?: number;
  message: string;
  range: {
    start: { line: number; character: number };
    end: { line: number; character: number };
  };
}

export interface LocationInfo {
  filePath: string;
  line: number;
  character: number;
}

export interface SymbolInfo {
  name: string;
  kind: string;
  line: number;
  detail?: string;
  containerName?: string;
  filePath?: string;
}

export interface FormattedDiagnostic {
  severity: string;
  line: number;
  character: number;
  message: string;
}

export class LspClientManager {
  private process: ChildProcess | null = null;
  private endpoint: JSONRPCEndpoint | null = null;
  private client: LspClient | null = null;
  private diagnostics: Map<string, DiagnosticInfo[]> = new Map();
  private openedFiles: Set<string> = new Set();
  private activeLanguage: string | null = null;
  private workspaceDir: string;
  private isConnected = false;
  private config: ServersConfig | null = null;
  private diagnosticResolvers: Map<string, (() => void)[]> = new Map();

  constructor(workspaceDir: string, config?: ServersConfig | null) {
    this.workspaceDir = workspaceDir;
    if (config) {
      this.config = config;
    }
  }

  getRunningLanguage(): string | null {
    return this.activeLanguage;
  }

  isServerRunning(): boolean {
    return this.isConnected && this.process !== null && !this.process.killed;
  }

  getPid(): number | null {
    return this.process?.pid || null;
  }

  getSyncedFilesCount(): number {
    return this.openedFiles.size;
  }

  async waitForDiagnostics(filePath: string, timeoutMs = 2000): Promise<void> {
    const uri = pathToUri(this.workspaceDir, filePath);

    return new Promise<void>((resolve) => {
      let resolved = false;
      const done = () => {
        if (resolved) return;
        resolved = true;
        resolve();
      };

      const list = this.diagnosticResolvers.get(uri) || [];
      list.push(done);
      this.diagnosticResolvers.set(uri, list);

      setTimeout(done, timeoutMs);
    });
  }

  // Spawn the LSP server process and initialize the RPC connection
  async start(language: string, command: string, args: string[]): Promise<void> {
    if (this.isServerRunning()) {
      await this.stop();
    }

    this.activeLanguage = language;
    this.diagnostics.clear();
    this.openedFiles.clear();

    try {
      this.process = spawn(command, args, {
        cwd: this.workspaceDir,
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"],
      });

      this.process.on("error", (err) => {
        console.error(`[LSP Client] Process spawn error:`, err);
      });

      // Avoid blocking process exits
      this.process.unref();

      if (!this.process.stdin || !this.process.stdout) {
        throw new Error("LSP process spawned without stdin or stdout pipes.");
      }

      this.endpoint = new JSONRPCEndpoint(this.process.stdin, this.process.stdout);

      // Handle general JSON-RPC endpoint errors to prevent crashing the extension process
      this.endpoint.on("error", (err) => {
        console.error(`[LSP Client] JSONRPC Endpoint error:`, err);
      });

      // Patch the JSONRPCEndpoint to allow asynchronous, out-of-order responses.
      // The ts-lsp-client library incorrectly checks that the incoming response ID matches
      // the single most recently sent request (nextId - 1), which breaks concurrent requests.
      // biome-ignore lint/suspicious/noExplicitAny: patching internal properties of JSONRPCEndpoint
      const rawEndpoint = this.endpoint as any;
      if (rawEndpoint.readableByline && rawEndpoint.client) {
        rawEndpoint.readableByline.removeAllListeners("data");
        rawEndpoint.readableByline.on("data", (jsonRPCResponseOrRequest: string) => {
          try {
            const jsonrpc = JSON.parse(jsonRPCResponseOrRequest);
            // Check if it's a response (has id and result/error properties)
            if (
              Object.hasOwn(jsonrpc, "id") &&
              (Object.hasOwn(jsonrpc, "result") || Object.hasOwn(jsonrpc, "error"))
            ) {
              rawEndpoint.client.receive(jsonrpc);
            }
            // It's a request or notification (has method property)
            else if (Object.hasOwn(jsonrpc, "method")) {
              if (Object.hasOwn(jsonrpc, "id")) {
                rawEndpoint.emit(jsonrpc.method, jsonrpc.params, jsonrpc.id);
              } else {
                rawEndpoint.emit(jsonrpc.method, jsonrpc.params);
              }
            } else {
              rawEndpoint.emit(
                "error",
                new Error(
                  `[transform] Received invalid JSON-RPC message: ${jsonRPCResponseOrRequest}`,
                ),
              );
            }
          } catch (err) {
            rawEndpoint.emit("error", err);
          }
        });
      }

      this.endpoint.on(
        "textDocument/publishDiagnostics",
        (params: { uri: string; diagnostics: DiagnosticInfo[] }) => {
          if (params?.uri) {
            this.diagnostics.set(params.uri, params.diagnostics);
            const resolvers = this.diagnosticResolvers.get(params.uri);
            if (resolvers) {
              this.diagnosticResolvers.delete(params.uri);
              for (const resolve of resolvers) {
                resolve();
              }
            }
          }
        },
      );

      this.endpoint.on(
        "workspace/configuration",
        (params: { items: { scopeUri?: string; section?: string }[] }, id: unknown) => {
          const result = params.items.map((item) => {
            const section = item.section;
            if (!section) return null;

            if (section === "python" || section.startsWith("python.")) {
              return {
                analysis: {
                  diagnosticMode: "workspace",
                },
              };
            }
            if (section === "gopls" || section.startsWith("gopls.")) {
              return {
                "ui.diagnostic.mode": "workspace",
              };
            }
            if (section === "typescript" || section.startsWith("typescript.")) {
              return {
                tsserver: {
                  experimental: {
                    enableProjectDiagnostics: true,
                  },
                },
              };
            }
            if (section === "javascript" || section.startsWith("javascript.")) {
              return {
                tsserver: {
                  experimental: {
                    enableProjectDiagnostics: true,
                  },
                },
              };
            }
            if (section === "rust-analyzer" || section.startsWith("rust-analyzer.")) {
              return {
                checkOnSave: {
                  enable: true,
                  allTargets: true,
                },
                check: {
                  workspace: true,
                },
              };
            }
            return null;
          });

          // biome-ignore lint/suspicious/noExplicitAny: patching internal properties of JSONRPCEndpoint
          const rawEndpoint = this.endpoint as any;
          if (rawEndpoint && typeof rawEndpoint.respondToRequest === "function") {
            rawEndpoint.respondToRequest(id, result);
          }
        },
      );

      this.client = new LspClient(this.endpoint);

      // Send the standard initialize request with required client capabilities
      const rootUri = pathToFileURL(this.workspaceDir).href;
      await this.client.initialize({
        processId: process.pid,
        rootUri,
        rootPath: this.workspaceDir,
        capabilities: {
          workspace: {
            configuration: true,
            didChangeConfiguration: {
              dynamicRegistration: true,
            },
          },
          textDocument: {
            synchronization: {
              dynamicRegistration: true,
              willSave: false,
              willSaveWaitUntil: false,
              didSave: true,
            },
            publishDiagnostics: {
              relatedInformation: true,
            },
            hover: {
              contentFormat: ["markdown", "plaintext"],
            },
            definition: {
              dynamicRegistration: true,
              linkSupport: true,
            },
            references: {
              dynamicRegistration: true,
            },
            documentSymbol: {
              dynamicRegistration: true,
              hierarchicalDocumentSymbolSupport: true,
            },
          },
        },
      });

      this.client.initialized();
      this.isConnected = true;

      // Notify the server with workspace configuration changes to force workspace-wide diagnostics
      this.endpoint.notify("workspace/didChangeConfiguration", {
        settings: {
          python: {
            analysis: {
              diagnosticMode: "workspace",
            },
          },
          gopls: {
            "ui.diagnostic.mode": "workspace",
          },
          typescript: {
            tsserver: {
              experimental: {
                enableProjectDiagnostics: true,
              },
            },
          },
          javascript: {
            tsserver: {
              experimental: {
                enableProjectDiagnostics: true,
              },
            },
          },
          "rust-analyzer": {
            checkOnSave: {
              enable: true,
              allTargets: true,
            },
            check: {
              workspace: true,
            },
          },
        },
      });

      // Proactively sync at least one file to initialize the project context
      // in language servers like tsserver (typescript-language-server).
      this.findFirstLanguageFile(this.workspaceDir)
        .then((firstFile) => {
          if (firstFile) {
            this.syncFile(firstFile).catch(() => {});
          }
        })
        .catch(() => {});
    } catch (error) {
      this.isConnected = false;
      this.activeLanguage = null;
      throw new Error(`Failed to start language server (${command}): ${error}`);
    }
  }

  async stop(): Promise<void> {
    this.isConnected = false;
    this.activeLanguage = null;
    this.diagnostics.clear();
    this.openedFiles.clear();

    if (this.client) {
      try {
        await this.client.shutdown();
        this.client.exit();
      } catch {
        // Best effort shutdown
      }
      this.client = null;
    }

    if (this.process) {
      this.process.kill("SIGKILL");
      this.process = null;
    }

    this.endpoint = null;
  }

  /**
   * Sync document state with LSP server (used on file read/write/edit events)
   */
  async syncFile(filePath: string): Promise<void> {
    if (!this.isServerRunning() || !this.client) return;

    try {
      const absolutePath = path.isAbsolute(filePath)
        ? filePath
        : path.resolve(this.workspaceDir, filePath);

      // Ensure file exists
      const content = await fs.readFile(absolutePath, "utf8");
      const uri = pathToFileURL(absolutePath).href;
      const languageId = getLanguageId(filePath, this.config);

      // Re-open the file on the LSP server to force it to refresh contents
      if (this.openedFiles.has(uri)) {
        this.client.didClose({ textDocument: { uri } });
      }

      this.client.didOpen({
        textDocument: {
          uri,
          languageId,
          version: Date.now(),
          text: content,
        },
      });

      this.openedFiles.add(uri);
    } catch {
      // Ignore sync errors (e.g. file deleted or missing)
    }
  }

  /**
   * Helper to scan workspace and find the first file belonging to the active language
   */
  async findFirstLanguageFile(dir: string): Promise<string | null> {
    if (!this.activeLanguage) return null;
    const targetLang = this.activeLanguage;

    const walk = async (currentDir: string, depth = 0): Promise<string | null> => {
      if (depth > 4) return null;
      try {
        const entries = await fs.readdir(currentDir, { withFileTypes: true });
        // Check files in the current directory first
        for (const entry of entries) {
          if (entry.isFile()) {
            const fullPath = path.join(currentDir, entry.name);
            if (getLanguageId(fullPath, this.config) === targetLang) {
              return fullPath;
            }
          }
        }
        // Then recurse into subdirectories
        for (const entry of entries) {
          if (entry.isDirectory() && !IGNORE_DIRS.has(entry.name)) {
            const found = await walk(path.join(currentDir, entry.name), depth + 1);
            if (found) return found;
          }
        }
      } catch {
        // Ignore read errors
      }
      return null;
    };

    return walk(dir);
  }

  // --- LSP Operations ---

  async getHover(filePath: string, line: number, character: number): Promise<string> {
    if (!this.isServerRunning() || !this.client) {
      throw new Error("Language server is not running");
    }

    await this.syncFile(filePath);
    const uri = pathToUri(this.workspaceDir, filePath);

    // LSP positions are 0-indexed
    const result = await this.client.hover({
      textDocument: { uri },
      position: { line: line - 1, character: character - 1 },
    });

    if (!result?.contents) {
      return "No hover information available.";
    }

    const contents = result.contents;
    if (typeof contents === "string") {
      return contents;
    }

    if (Array.isArray(contents)) {
      return contents
        .map((c) => {
          if (typeof c === "string") return c;
          return `\`\`\`${c.language}\n${c.value}\n\`\`\``;
        })
        .join("\n\n");
    }

    if ("value" in contents) {
      return contents.value;
    }

    return JSON.stringify(contents);
  }

  async getDefinition(
    filePath: string,
    line: number,
    character: number,
  ): Promise<LocationInfo[] | null> {
    if (!this.isServerRunning() || !this.client) {
      throw new Error("Language server is not running");
    }

    await this.syncFile(filePath);
    const uri = pathToUri(this.workspaceDir, filePath);

    const result = await this.client.definition({
      textDocument: { uri },
      position: { line: line - 1, character: character - 1 },
    });

    if (!result) return null;

    const locations: LocationInfo[] = [];

    // Normalize Location | Location[] | LocationLink[]
    const items = Array.isArray(result) ? result : [result];
    for (const item of items) {
      if ("uri" in item) {
        // Location
        locations.push({
          filePath: path.relative(this.workspaceDir, fileURLToPath(item.uri)),
          line: item.range.start.line + 1,
          character: item.range.start.character + 1,
        });
      } else if ("targetUri" in item) {
        // LocationLink
        locations.push({
          filePath: path.relative(this.workspaceDir, fileURLToPath(item.targetUri)),
          line: item.targetSelectionRange.start.line + 1,
          character: item.targetSelectionRange.start.character + 1,
        });
      }
    }

    return locations;
  }

  async getReferences(filePath: string, line: number, character: number): Promise<LocationInfo[]> {
    if (!this.isServerRunning() || !this.client) {
      throw new Error("Language server is not running");
    }

    await this.syncFile(filePath);
    const uri = pathToUri(this.workspaceDir, filePath);

    const result = await this.client.references({
      textDocument: { uri },
      position: { line: line - 1, character: character - 1 },
      context: { includeDeclaration: true },
    });

    if (!result || "message" in result) return [];

    return result.map((loc) => ({
      filePath: path.relative(this.workspaceDir, fileURLToPath(loc.uri)),
      line: loc.range.start.line + 1,
      character: loc.range.start.character + 1,
    }));
  }

  async getSymbols(filePath: string): Promise<SymbolInfo[]> {
    if (!this.isServerRunning() || !this.client) {
      throw new Error("Language server is not running");
    }

    await this.syncFile(filePath);
    const uri = pathToUri(this.workspaceDir, filePath);

    const result = await this.client.documentSymbol({
      textDocument: { uri },
    });

    if (!result) return [];

    const symbols: SymbolInfo[] = [];

    // Normalize DocumentSymbol[] | SymbolInformation[]
    for (const sym of result) {
      if ("range" in sym) {
        // DocumentSymbol
        symbols.push({
          name: sym.name,
          kind: getSymbolKindName(sym.kind),
          line: sym.range.start.line + 1,
          detail: sym.detail,
        });
      } else {
        // SymbolInformation
        symbols.push({
          name: sym.name,
          kind: getSymbolKindName(sym.kind),
          line: sym.location.range.start.line + 1,
          containerName: sym.containerName,
        });
      }
    }

    return symbols;
  }

  getDiagnostics(filePath?: string): Record<string, FormattedDiagnostic[]> {
    const output: Record<string, FormattedDiagnostic[]> = {};

    for (const [uri, diagList] of this.diagnostics.entries()) {
      const relPath = path.relative(this.workspaceDir, fileURLToPath(uri));
      if (
        filePath &&
        relPath !== filePath &&
        path.resolve(this.workspaceDir, filePath) !== fileURLToPath(uri)
      ) {
        continue;
      }

      output[relPath] = diagList.map((d) => {
        let severityStr = "Info";
        if (d.severity === 1) severityStr = "Error";
        if (d.severity === 2) severityStr = "Warning";
        if (d.severity === 3) severityStr = "Information";
        if (d.severity === 4) severityStr = "Hint";

        return {
          severity: severityStr,
          line: d.range.start.line + 1,
          character: d.range.start.character + 1,
          message: d.message,
        };
      });
    }

    return output;
  }

  /**
   * Find matches of symbol name in file and return coordinates
   */
  async findSymbolCoordinates(
    filePath: string,
    symbolName: string,
    line?: number,
  ): Promise<{ line: number; character: number }[]> {
    const absolutePath = path.isAbsolute(filePath)
      ? filePath
      : path.resolve(this.workspaceDir, filePath);
    const content = await fs.readFile(absolutePath, "utf8");
    const lines = content.split("\n");

    // If line is provided, find symbol on that line
    if (line !== undefined) {
      const lineContent = lines[line - 1];
      if (lineContent === undefined) {
        return [];
      }

      // Escape regex characters
      const escapedName = symbolName.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
      const regex = new RegExp(`\\b${escapedName}\\b`, "g");
      const match = regex.exec(lineContent);
      if (match) {
        return [{ line, character: match.index + 1 }];
      }

      // Fallback: literal search on that line if word boundary regex fails (e.g. operators or special chars)
      const index = lineContent.indexOf(symbolName);
      if (index !== -1) {
        return [{ line, character: index + 1 }];
      }

      return [];
    }

    // Search the whole file line-by-line
    const escapedName = symbolName.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
    const regex = new RegExp(`\\b${escapedName}\\b`, "g");
    const coordinates: { line: number; character: number }[] = [];

    for (let i = 0; i < lines.length; i++) {
      const lineContent = lines[i];
      regex.lastIndex = 0;
      let match = regex.exec(lineContent);
      while (match !== null) {
        coordinates.push({
          line: i + 1,
          character: match.index + 1,
        });
        if (coordinates.length >= 10) break;
        match = regex.exec(lineContent);
      }
      if (coordinates.length >= 10) break;
    }

    // Fallback: search the whole file for literal matches if word boundary regex returned nothing
    if (coordinates.length === 0) {
      for (let i = 0; i < lines.length; i++) {
        const lineContent = lines[i];
        let index = lineContent.indexOf(symbolName);
        while (index !== -1) {
          coordinates.push({
            line: i + 1,
            character: index + 1,
          });
          if (coordinates.length >= 10) break;
          index = lineContent.indexOf(symbolName, index + symbolName.length);
        }
        if (coordinates.length >= 10) break;
      }
    }

    return coordinates;
  }

  /**
   * Search workspace symbols
   */
  async getWorkspaceSymbols(query: string): Promise<SymbolInfo[]> {
    if (!this.isServerRunning() || !this.endpoint) {
      throw new Error("Language server is not running");
    }

    const endpoint = this.endpoint;
    const executeRequest = async () => {
      // biome-ignore lint/suspicious/noExplicitAny: endpoint response type is not generic in library
      return (await endpoint.send("workspace/symbol", { query })) as any;
    };

    // biome-ignore lint/suspicious/noExplicitAny: result of JSON-RPC request
    let result: any;
    try {
      result = await executeRequest();
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      if (errMsg.includes("No Project") && this.openedFiles.size === 0) {
        console.log(
          `[LSP Client] Workspace symbol search failed with 'No Project'. Attempting to initialize project by syncing a workspace file...`,
        );
        const firstFile = await this.findFirstLanguageFile(this.workspaceDir);
        if (firstFile) {
          await this.syncFile(firstFile);
          // Retry the request once
          result = await executeRequest();
        } else {
          throw err;
        }
      } else {
        throw err;
      }
    }

    if (!result) return [];

    const symbols: SymbolInfo[] = [];

    for (const sym of result) {
      if (sym.location) {
        symbols.push({
          name: sym.name,
          kind: getSymbolKindName(sym.kind),
          line: sym.location.range.start.line + 1,
          containerName: sym.containerName,
          filePath: path.relative(this.workspaceDir, fileURLToPath(sym.location.uri)),
        });
      }
    }

    return symbols;
  }

  /**
   * Request rename symbol
   */
  async renameSymbol(
    filePath: string,
    line: number,
    character: number,
    newName: string,
  ): Promise<unknown> {
    if (!this.isServerRunning() || !this.endpoint) {
      throw new Error("Language server is not running");
    }

    await this.syncFile(filePath);
    const uri = pathToUri(this.workspaceDir, filePath);

    const result = await this.endpoint.send("textDocument/rename", {
      textDocument: { uri },
      position: { line: line - 1, character: character - 1 },
      newName,
    });

    return result;
  }

  /**
   * Apply workspace edits and return file changes statistics
   */
  // Parse and apply workspace edits sent by the LSP server (e.g. for rename)
  // biome-ignore lint/suspicious/noExplicitAny: edit payload parameter is external
  async applyWorkspaceEdit(edit: any): Promise<Record<string, { count: number; lines: number[] }>> {
    if (!edit) return {};

    // biome-ignore lint/suspicious/noExplicitAny: edits can contain complex ranges
    const fileEditsMap: Map<string, { range: any; newText: string }[]> = new Map();

    if (edit.changes) {
      for (const [uri, edits] of Object.entries(edit.changes)) {
        const filePath = fileURLToPath(uri);
        // biome-ignore lint/suspicious/noExplicitAny: casting edits array
        fileEditsMap.set(filePath, edits as any);
      }
    } else if (edit.documentChanges) {
      for (const docChange of edit.documentChanges) {
        if (docChange.textDocument && docChange.edits) {
          const filePath = fileURLToPath(docChange.textDocument.uri);
          fileEditsMap.set(filePath, docChange.edits);
        }
      }
    }

    const stats: Record<string, { count: number; lines: number[] }> = {};

    for (const [filePath, edits] of fileEditsMap.entries()) {
      if (edits.length === 0) continue;

      const content = await fs.readFile(filePath, "utf8");
      const fileLines = content.split("\n");

      // Sort edits in reverse order: by line descending, then character descending
      const sortedEdits = [...edits].sort((a, b) => {
        if (b.range.start.line !== a.range.start.line) {
          return b.range.start.line - a.range.start.line;
        }
        return b.range.start.character - a.range.start.character;
      });

      for (const editItem of sortedEdits) {
        const { range, newText } = editItem;
        const startLine = range.start.line;
        const startChar = range.start.character;
        const endLine = range.end.line;
        const endChar = range.end.character;

        const startLineContent = fileLines[startLine] || "";
        const prefix = startLineContent.slice(0, startChar);

        const endLineContent = fileLines[endLine] || "";
        const suffix = endLineContent.slice(endChar);

        const middle = newText;
        const replacementLines = (prefix + middle + suffix).split("\n");

        fileLines.splice(startLine, endLine - startLine + 1, ...replacementLines);
      }

      await fs.writeFile(filePath, fileLines.join("\n"), "utf8");
      await this.syncFile(filePath);

      const relPath = path.relative(this.workspaceDir, filePath);
      stats[relPath] = {
        count: edits.length,
        lines: [...new Set(edits.map((e) => e.range.start.line + 1))].sort((a, b) => a - b),
      };
    }

    return stats;
  }
}
