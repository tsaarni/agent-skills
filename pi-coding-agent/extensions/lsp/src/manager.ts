// Manages the language server process, file syncing, and diagnostics.
import { type ChildProcess, spawn } from "node:child_process";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { IGNORE_DIRS, type ServersConfig } from "./detector.js";
import {
  type LSPSymbol,
  type LSPTextEdit,
  type LSPWorkspaceEdit,
  LspClient,
  LspJSONRPCEndpoint,
} from "./protocol.js";

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

interface LanguageAdapter {
  triggerDiagnostics?: (endpoint: LspJSONRPCEndpoint, absolutePath: string) => Promise<void>;
}

const LANGUAGE_ADAPTERS: Record<string, LanguageAdapter> = {};

export class LspClientManager {
  private process: ChildProcess | null = null;
  private endpoint: LspJSONRPCEndpoint | null = null;
  private client: LspClient | null = null;
  private readonly diagnostics: Map<string, DiagnosticInfo[]> = new Map();
  private readonly openedFiles: Set<string> = new Set();
  private readonly documentVersions: Map<string, number> = new Map();
  private readonly syncQueue: Map<string, Promise<void>> = new Map();
  private readonly workspaceDir: string;
  private isConnected = false;
  public config: ServersConfig | null = null;
  private readonly diagnosticResolvers: Map<string, (() => void)[]> = new Map();

  constructor(workspaceDir: string, config?: ServersConfig | null) {
    this.workspaceDir = workspaceDir;
    if (config) {
      this.config = config;
    }
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

  async pullDiagnostics(filePath: string): Promise<boolean> {
    if (!this.isServerRunning() || !this.endpoint) return false;

    const uri = pathToUri(this.workspaceDir, filePath);
    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    try {
      const result = await this.executeWithTimeout<{
        kind: string;
        items: DiagnosticInfo[];
      }>(
        this.endpoint.send("textDocument/diagnostic", {
          textDocument: { uri },
        }),
        timeout,
      );

      if (result && result.kind === "full" && Array.isArray(result.items)) {
        this.diagnostics.set(uri, result.items);
        return true;
      }
    } catch {
      // ignore
    }
    return false;
  }

  // Spawn the LSP server process and initialize the RPC connection
  async start(command: string, args: string[]): Promise<void> {
    if (this.isServerRunning()) {
      await this.stop();
    }

    this.diagnostics.clear();
    this.openedFiles.clear();
    this.documentVersions.clear();
    this.syncQueue.clear();

    try {
      const env = { ...process.env };
      delete env.NODE_OPTIONS;

      this.process = spawn(command, args, {
        cwd: this.workspaceDir,
        env,
        stdio: ["pipe", "pipe", "pipe"],
      });

      this.process.on("error", (err) => {
        console.error(`[LSP Client] Process spawn error:`, err);
      });

      this.process.stderr?.on("data", (data) => {
        console.error(`[LSP Server Stderr] ${data.toString().trim()}`);
      });

      // Avoid blocking process exits
      this.process.unref();

      if (!this.process.stdin || !this.process.stdout) {
        throw new Error("LSP process spawned without stdin or stdout pipes.");
      }

      this.endpoint = new LspJSONRPCEndpoint(this.process.stdin, this.process.stdout);

      // Handle general JSON-RPC endpoint errors to prevent crashing the extension process
      this.endpoint.on("error", (err) => {
        console.error(`[LSP Client] JSONRPC Endpoint error:`, err);
      });

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

          if (this.endpoint) {
            this.endpoint.respondToRequest(id, result);
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
        workspaceFolders: [
          {
            uri: rootUri,
            name: path.basename(this.workspaceDir),
          },
        ],
        capabilities: {
          workspace: {
            workspaceFolders: true,
            configuration: true,
            didChangeConfiguration: {
              dynamicRegistration: true,
            },
            symbol: {
              dynamicRegistration: true,
            },
            workspaceEdit: {
              documentChanges: true,
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
            rename: {
              dynamicRegistration: true,
              prepareSupport: true,
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
    } catch (error) {
      this.isConnected = false;
      throw new Error(`Failed to start language server (${command}): ${error}`);
    }
  }

  async stop(): Promise<void> {
    this.isConnected = false;
    this.diagnostics.clear();
    this.openedFiles.clear();
    this.documentVersions.clear();
    this.syncQueue.clear();

    if (this.client) {
      try {
        const shutdownPromise = this.client.shutdown();
        let timeoutId: NodeJS.Timeout | undefined;
        const timeoutPromise = new Promise<void>((resolve) => {
          timeoutId = setTimeout(resolve, 2000);
        });
        await Promise.race([shutdownPromise, timeoutPromise]);
        if (timeoutId) clearTimeout(timeoutId);
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
  async syncFile(filePath: string, text?: string): Promise<void> {
    const absolutePath = path.isAbsolute(filePath)
      ? filePath
      : path.resolve(this.workspaceDir, filePath);
    const uri = pathToFileURL(absolutePath).href;

    const currentPromise = this.syncQueue.get(uri) ?? Promise.resolve();

    const nextPromise = currentPromise
      .then(async () => {
        await this.doSyncFile(filePath, uri, absolutePath, text);
      })
      .catch((err) => {
        console.error(`[LSP Client] Error syncing file ${filePath}:`, err);
      });

    this.syncQueue.set(uri, nextPromise);
    return nextPromise;
  }

  private async doSyncFile(
    filePath: string,
    uri: string,
    absolutePath: string,
    text?: string,
  ): Promise<void> {
    if (!this.isServerRunning() || !this.client) return;

    try {
      const languageId = getLanguageId(filePath, this.config);
      const content = text ?? (await fs.readFile(absolutePath, "utf8"));

      if (this.openedFiles.has(uri)) {
        const version = (this.documentVersions.get(uri) || 0) + 1;
        this.documentVersions.set(uri, version);

        this.endpoint?.notify("textDocument/didChange", {
          textDocument: {
            uri,
            version,
          },
          contentChanges: [{ text: content }],
        });
      } else {
        const version = 1;
        this.documentVersions.set(uri, version);
        this.openedFiles.add(uri);

        this.client.didOpen({
          textDocument: {
            uri,
            languageId,
            version,
            text: content,
          },
        });
      }
    } catch {
      // Ignore sync errors (e.g. file deleted or missing)
    }
  }

  // Helper to execute any promise with a timeout
  private async executeWithTimeout<T>(
    promise: PromiseLike<T> | Promise<T>,
    timeoutMs = 15000,
    errorMessage = "LSP request timed out",
  ): Promise<T> {
    let timeoutId: NodeJS.Timeout | undefined;
    const timeoutPromise = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(() => {
        reject(new Error(errorMessage));
      }, timeoutMs);
    });
    try {
      return await Promise.race([promise, timeoutPromise]);
    } finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
  }

  // --- LSP Operations ---

  async getHover(filePath: string, line: number, character: number): Promise<string> {
    if (!this.isServerRunning() || !this.client) {
      throw new Error("Language server is not running");
    }

    await this.syncFile(filePath);
    const uri = pathToUri(this.workspaceDir, filePath);

    // LSP positions are 0-indexed
    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    const result = await this.executeWithTimeout(
      this.client.hover({
        textDocument: { uri },
        position: { line: line - 1, character: character - 1 },
      }),
      timeout,
      `Hover request timed out after ${timeout / 1000} seconds`,
    );

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

    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    const result = await this.executeWithTimeout(
      this.client.definition({
        textDocument: { uri },
        position: { line: line - 1, character: character - 1 },
      }),
      timeout,
      `Go-to-definition request timed out after ${timeout / 1000} seconds`,
    );

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

    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    const result = await this.executeWithTimeout(
      this.client.references({
        textDocument: { uri },
        position: { line: line - 1, character: character - 1 },
        context: { includeDeclaration: true },
      }),
      timeout,
      `Find-references request timed out after ${timeout / 1000} seconds`,
    );

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

    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    const result = await this.executeWithTimeout(
      this.client.documentSymbol({
        textDocument: { uri },
      }),
      timeout,
      `Document-symbols request timed out after ${timeout / 1000} seconds`,
    );

    if (!result) return [];

    const symbols: SymbolInfo[] = [];

    // Normalize DocumentSymbol[] | SymbolInformation[]
    for (const sym of result) {
      if (sym.range) {
        // DocumentSymbol
        symbols.push({
          name: sym.name,
          kind: getSymbolKindName(sym.kind),
          line: sym.range.start.line + 1,
          detail: sym.detail,
        });
      } else if (sym.location) {
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
    try {
      const lspCoords = await this.findCoordinatesFromLsp(filePath, symbolName, line);
      if (lspCoords.length > 0) {
        return lspCoords;
      }
    } catch (err) {
      console.warn(`[LSP Client] AST coordinate lookup failed, falling back to text scan:`, err);
    }

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
    const timeout = this.config?.workspaceSearchTimeoutMs ?? 45000;
    const result = await this.executeWithTimeout(
      endpoint.send<LSPSymbol[]>("workspace/symbol", { query }),
      timeout,
      `Workspace symbol search timed out after ${timeout / 1000} seconds`,
    );

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

    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    const result = await this.executeWithTimeout(
      this.endpoint.send("textDocument/rename", {
        textDocument: { uri },
        position: { line: line - 1, character: character - 1 },
        newName,
      }),
      timeout,
      `Rename-symbol request timed out after ${timeout / 1000} seconds`,
    );

    return result;
  }

  /**
   * Apply workspace edits and return file changes statistics
   */
  // Parse and apply workspace edits sent by the LSP server (e.g. for rename)
  async applyWorkspaceEdit(
    edit: LSPWorkspaceEdit,
  ): Promise<Record<string, { count: number; lines: number[] }>> {
    if (!edit) return {};

    const fileEditsMap: Map<string, LSPTextEdit[]> = new Map();

    if (edit.changes) {
      for (const [uri, edits] of Object.entries(edit.changes)) {
        const filePath = fileURLToPath(uri);
        fileEditsMap.set(filePath, edits);
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

      const updatedText = fileLines.join("\n");
      await fs.writeFile(filePath, updatedText, "utf8");
      await this.syncFile(filePath, updatedText);

      const relPath = path.relative(this.workspaceDir, filePath);
      stats[relPath] = {
        count: edits.length,
        lines: [...new Set(edits.map((e) => e.range.start.line + 1))].sort((a, b) => a - b),
      };
    }

    return stats;
  }

  /**
   * Resolve precise symbol coordinates from LSP AST symbols rather than regex
   */
  private async findCoordinatesFromLsp(
    filePath: string,
    symbolName: string,
    line?: number,
  ): Promise<{ line: number; character: number }[]> {
    if (!this.isServerRunning() || !this.client) {
      return [];
    }

    await this.syncFile(filePath);
    const absolutePath = path.isAbsolute(filePath)
      ? filePath
      : path.resolve(this.workspaceDir, filePath);
    const uri = pathToFileURL(absolutePath).href;

    const timeout = this.config?.defaultTimeoutMs ?? 15000;
    const rawSymbols = (await this.executeWithTimeout(
      this.client.documentSymbol({
        textDocument: { uri },
      }),
      timeout,
      `Document-symbols coordinate lookup timed out`,
    ).catch(() => null)) as LSPSymbol[] | null;

    if (!rawSymbols) return [];

    interface LspCoordinate {
      line: number;
      character: number;
    }

    interface FlattenedSymbol {
      name: string;
      kind: number;
      start: LspCoordinate;
    }

    const flattened: FlattenedSymbol[] = [];

    function traverse(sym: LSPSymbol) {
      if (sym.selectionRange) {
        flattened.push({
          name: sym.name,
          kind: sym.kind,
          start: {
            line: sym.selectionRange.start.line + 1,
            character: sym.selectionRange.start.character + 1,
          },
        });
        if (sym.children && Array.isArray(sym.children)) {
          for (const child of sym.children) {
            traverse(child);
          }
        }
      } else if (sym.location) {
        flattened.push({
          name: sym.name,
          kind: sym.kind,
          start: {
            line: sym.location.range.start.line + 1,
            character: sym.location.range.start.character + 1,
          },
        });
      }
    }

    for (const sym of rawSymbols) {
      traverse(sym);
    }

    const matchSymbol = (symName: string, targetName: string) => {
      if (symName === targetName) return true;
      if (symName.endsWith(`.${targetName}`)) return true;
      if (symName.endsWith(`::${targetName}`)) return true;
      return false;
    };

    const matches: { line: number; character: number }[] = [];
    for (const sym of flattened) {
      if (matchSymbol(sym.name, symbolName)) {
        if (line === undefined || sym.start.line === line) {
          matches.push(sym.start);
        }
      }
    }

    return matches;
  }

  private async findFirstWorkspaceFile(): Promise<string | null> {
    const extensions = new Set<string>();
    if (this.config?.languages) {
      for (const langConfig of Object.values(this.config.languages)) {
        for (const ext of langConfig.fileExtensions) {
          extensions.add(ext.toLowerCase());
        }
      }
    } else {
      for (const ext of Object.keys(DEFAULT_LANGUAGE_IDS)) {
        extensions.add(ext.toLowerCase());
      }
    }

    const walk = async (dir: string, depth = 0): Promise<string | null> => {
      if (depth > 4) return null;
      try {
        const entries = await fs.readdir(dir, { withFileTypes: true });
        for (const entry of entries) {
          if (entry.isFile()) {
            const ext = path.extname(entry.name).toLowerCase();
            if (extensions.has(ext)) {
              return path.join(dir, entry.name);
            }
          }
        }
        for (const entry of entries) {
          if (entry.isDirectory() && !IGNORE_DIRS.has(entry.name)) {
            const found = await walk(path.join(dir, entry.name), depth + 1);
            if (found) return found;
          }
        }
      } catch {
        // ignore
      }
      return null;
    };

    return walk(this.workspaceDir);
  }

  /**
   * Triggers diagnostics compilation for the workspace.
   * Adapts dynamically for lazy servers (e.g. TypeScript).
   */
  async triggerWorkspaceDiagnostics(filePath?: string): Promise<void> {
    if (!this.isServerRunning() || !this.endpoint) return;

    let target = filePath;
    if (!target) {
      const openedUri = this.openedFiles.values().next().value;
      if (openedUri) {
        target = uriToPath(openedUri);
      }
    }

    if (!target) {
      const firstFile = await this.findFirstWorkspaceFile();
      if (firstFile) {
        await this.syncFile(firstFile);
        target = firstFile;
      }
    }

    if (!target) return;

    const absolutePath = path.isAbsolute(target) ? target : path.resolve(this.workspaceDir, target);
    const langId = getLanguageId(absolutePath, this.config);

    const adapter = LANGUAGE_ADAPTERS[langId];
    if (adapter?.triggerDiagnostics) {
      try {
        await adapter.triggerDiagnostics(this.endpoint, absolutePath);
      } catch (err) {
        console.warn(`[LSP Client] Diagnostics trigger failed for ${langId}:`, err);
      }
    }
  }
}
