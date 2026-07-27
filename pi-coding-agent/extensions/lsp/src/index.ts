// Extension entry point that integrates the manager and registers event listeners.
import * as fs from "node:fs/promises";
import { join, resolve } from "node:path";

import { CONFIG_DIR_NAME, type ExtensionAPI, getAgentDir } from "@earendil-works/pi-coding-agent";
import { Container, Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";
import {
  buildLspStatusMarkdown,
  type LspStatus,
  loadProjectLspConfig as loadProjectLspConfigGeneric,
  loadServersConfig,
  saveProjectLspConfig as saveProjectLspConfigGeneric,
} from "./config.js";
import { detectWorkspaceLanguage } from "./detector.js";
import { LspClientManager } from "./manager.js";
import {
  findReferences,
  getDiagnostics,
  getSymbolInfo,
  renameSymbol,
  searchSymbols,
} from "./tools.js";

export type { LspStatus };

// Global active manager instance
let lspManager: LspClientManager | null = null;

function getLspProjectConfigPath(workspaceDir: string): string {
  const agentDir = getAgentDir();
  const resolvedCwd = resolve(workspaceDir);
  // Replicate safe encoding: replace leading slash/backslash and substitute remaining slashes/backslashes/colons with dashes
  const safePath = `--${resolvedCwd.replace(/^[/\\]/, "").replace(/[/\\:]/g, "-")}--`;
  return join(agentDir, "lsp-extension", safePath, "lsp.json");
}

// Load project initialization config if exists
async function loadProjectLspConfig(workspaceDir: string) {
  const projectConfigPath = join(workspaceDir, CONFIG_DIR_NAME, "lsp.json");
  const globalConfigPath = getLspProjectConfigPath(workspaceDir);
  return loadProjectLspConfigGeneric(projectConfigPath, globalConfigPath);
}

// Save project initialization config
async function saveProjectLspConfig(
  workspaceDir: string,
  command: string,
  args: string[],
): Promise<void> {
  const globalConfigPath = getLspProjectConfigPath(workspaceDir);
  return saveProjectLspConfigGeneric(globalConfigPath, command, args);
}

export function sendLspStatus(pi: ExtensionAPI, status: LspStatus): void {
  pi.sendMessage({
    customType: "lsp-status",
    content: buildLspStatusMarkdown(status),
    display: true,
    details: status,
  });
}

export function registerStatusRenderer(pi: ExtensionAPI): void {
  pi.registerMessageRenderer<LspStatus>("lsp-status", (message, _options, theme) => {
    const s = message.details;
    if (!s) return undefined;

    const container = new Container();
    const lspLabel = theme.bold(theme.fg("accent", "LSP"));

    if (s.status === "running") {
      const infoParts: string[] = [];
      infoParts.push(`running (${theme.fg("text", s.language)})`);
      if (s.pid) {
        infoParts.push(`pid: ${theme.fg("dim", String(s.pid))}`);
      }
      if (s.syncedFilesCount !== undefined) {
        infoParts.push(`synced: ${theme.fg("dim", String(s.syncedFilesCount))}`);
      }
      if (s.diagnosticsSummary) {
        infoParts.push(s.diagnosticsSummary);
      }
      const commandStr = theme.fg("muted", `${s.command} ${s.args.join(" ")}`);
      container.addChild(new Text(`${lspLabel}: ${infoParts.join(" | ")} — ${commandStr}`, 0, 0));
    } else if (s.status === "error") {
      container.addChild(
        new Text(
          `${lspLabel}: failed to start (${theme.fg("text", s.language)}) — ${theme.fg("error", s.error || "unknown error")}`,
          0,
          0,
        ),
      );
    } else {
      container.addChild(new Text(`${lspLabel}: not initialized`, 0, 0));
    }
    return container;
  });
}

export default function lspExtension(pi: ExtensionAPI) {
  registerStatusRenderer(pi);
  // Initialize manager when extension is registered
  pi.on("session_start", async (_event, ctx) => {
    const config = await loadServersConfig().catch(() => null);
    lspManager = new LspClientManager(ctx.cwd, config);

    // Auto-start if initialized in this workspace
    const projectConfig = await loadProjectLspConfig(ctx.cwd);
    if (projectConfig?.autostart) {
      try {
        await lspManager.start(projectConfig.command, projectConfig.args);
        registerLspTools(pi, () => lspManager);
        sendLspStatus(pi, {
          language: projectConfig.language,
          command: projectConfig.command,
          args: projectConfig.args,
          status: "running",
          pid: lspManager.getPid(),
          syncedFilesCount: lspManager.getSyncedFilesCount(),
          diagnosticsSummary: "0 errors, 0 warnings",
        });
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        ctx.ui.notify(`LSP: Failed to auto-start server: ${error.message}`, "error");
        sendLspStatus(pi, {
          language: projectConfig.language,
          command: projectConfig.command,
          args: projectConfig.args,
          status: "error",
          error: error.message,
        });
      }
    }
  });

  pi.on("session_shutdown", async () => {
    if (lspManager) {
      await lspManager.stop();
      lspManager = null;
    }
  });

  pi.on("tool_result", async (event, _ctx) => {
    if (!lspManager?.isServerRunning()) return;

    const targetTools = [
      "read",
      "write",
      "edit",
      "view_file",
      "write_to_file",
      "replace_file_content",
      "multi_replace_file_content",
    ];
    if (targetTools.includes(event.toolName) && !event.isError) {
      const input = event.input;
      if (input) {
        const filePath = input.path || input.AbsolutePath || input.TargetFile;
        if (typeof filePath === "string") {
          let text: string | undefined;
          if (event.toolName === "write" && typeof input.content === "string") {
            text = input.content;
          } else if (event.toolName === "write_to_file" && typeof input.CodeContent === "string") {
            text = input.CodeContent;
          }
          await lspManager.syncFile(filePath, text);
        }
      }
    }
  });

  // --- Slash Command ---

  // Register '/lsp' command to query status or start/stop/restart the server
  pi.registerCommand("lsp", {
    description: "Manage language server connection (status, init, clean, restart)",
    handler: async (args, ctx) => {
      const subCommand = args?.trim().toLowerCase();

      if (!lspManager) {
        ctx.ui.notify("LSP extension state not initialized", "error");
        return;
      }

      if (!subCommand || subCommand === "status") {
        // Print Status
        const isRunning = lspManager.isServerRunning();
        const projectConfig = await loadProjectLspConfig(ctx.cwd);

        if (!projectConfig) {
          ctx.ui.notify("LSP not initialized. Run '/lsp init' first.", "warning");
          return;
        }

        if (isRunning) {
          // Show summary of diagnostics
          const diagMap = lspManager.getDiagnostics();
          let errorsCount = 0;
          let warningsCount = 0;
          const detailsLines: string[] = [];

          for (const [file, list] of Object.entries(diagMap)) {
            let fileErrors = 0;
            let fileWarnings = 0;
            for (const d of list) {
              if (d.severity === "Error") fileErrors++;
              if (d.severity === "Warning") fileWarnings++;
            }
            errorsCount += fileErrors;
            warningsCount += fileWarnings;
            if (fileErrors > 0 || fileWarnings > 0) {
              detailsLines.push(`  - ${file}: ${fileErrors} errors, ${fileWarnings} warnings`);
            }
          }

          sendLspStatus(pi, {
            language: projectConfig.language,
            command: projectConfig.command,
            args: projectConfig.args,
            status: "running",
            pid: lspManager.getPid(),
            syncedFilesCount: lspManager.getSyncedFilesCount(),
            diagnosticsSummary: `${errorsCount} errors, ${warningsCount} warnings`,
          });

          if (detailsLines.length > 0) {
            const limit = 5;
            const displayed = detailsLines.slice(0, limit);
            ctx.ui.notify(
              `Active Diagnostics Details:\n${displayed.join("\n")}${
                detailsLines.length > limit
                  ? `\n  ... and ${detailsLines.length - limit} more files`
                  : ""
              }`,
              "info",
            );
          }
        } else {
          sendLspStatus(pi, {
            language: projectConfig.language,
            command: projectConfig.command,
            args: projectConfig.args,
            status: "not_initialized",
          });
        }
        return;
      }

      if (subCommand === "init") {
        ctx.ui.notify("LSP: Initializing workspace...", "info");
        try {
          const config = await loadServersConfig();
          const detectedLang = await detectWorkspaceLanguage(ctx.cwd, config);

          if (!detectedLang) {
            ctx.ui.notify(
              "LSP Detection: Could not detect workspace language. No matching file pattern found.",
              "warning",
            );
            return;
          }

          const langConfig = config.languages[detectedLang];
          const confirm = await ctx.ui.confirm(
            "LSP Initialization",
            `Language detected: ${detectedLang}.\nDo you want to initialize LSP for this language using command: '${langConfig.command}'?`,
          );

          if (!confirm) {
            ctx.ui.notify("Initialization cancelled.", "info");
            return;
          }

          await saveProjectLspConfig(ctx.cwd, langConfig.command, langConfig.args);

          ctx.ui.notify(`LSP initialized for ${detectedLang}! Starting server...`, "info");
          await lspManager.start(langConfig.command, langConfig.args);
          registerLspTools(pi, () => lspManager);
          ctx.ui.notify("LSP started successfully.", "info");
          sendLspStatus(pi, {
            language: detectedLang,
            command: langConfig.command,
            args: langConfig.args,
            status: "running",
            pid: lspManager.getPid(),
            syncedFilesCount: lspManager.getSyncedFilesCount(),
          });
        } catch (err) {
          const error = err instanceof Error ? err : new Error(String(err));
          ctx.ui.notify(`Initialization failed: ${error.message}`, "error");
        }
        return;
      }

      if (subCommand === "clean") {
        const globalConfigPath = getLspProjectConfigPath(ctx.cwd);
        try {
          await fs.rm(globalConfigPath, { force: true });
          if (lspManager.isServerRunning()) {
            await lspManager.stop();
          }
          ctx.ui.notify("Global LSP configuration removed.", "info");
        } catch (err) {
          const error = err instanceof Error ? err : new Error(String(err));
          ctx.ui.notify(`Failed to remove global configuration: ${error.message}`, "error");
        }
        return;
      }

      if (subCommand === "restart") {
        const projectConfig = await loadProjectLspConfig(ctx.cwd);
        if (!projectConfig) {
          ctx.ui.notify("LSP not initialized. Run '/lsp init' first.", "warning");
          return;
        }

        ctx.ui.notify("Restarting LSP server...", "info");
        await lspManager.stop();
        try {
          await lspManager.start(projectConfig.command, projectConfig.args);
          ctx.ui.notify("LSP restarted successfully.", "info");
          sendLspStatus(pi, {
            language: projectConfig.language,
            command: projectConfig.command,
            args: projectConfig.args,
            status: "running",
            pid: lspManager.getPid(),
            syncedFilesCount: lspManager.getSyncedFilesCount(),
          });
        } catch (err) {
          const error = err instanceof Error ? err : new Error(String(err));
          ctx.ui.notify(`Failed to restart: ${error.message}`, "error");
        }
        return;
      }

      ctx.ui.notify(
        `Unknown LSP sub-command: ${subCommand}. Available: status, init, clean, restart`,
        "error",
      );
    },
  });

}

export function registerLspTools(pi: ExtensionAPI, getLspManager: () => LspClientManager | null) {
  // Tool: lsp_get_symbol_info - query hover definition and code snippets for a symbol
  pi.registerTool({
    name: "lsp_get_symbol_info",
    label: "LSP: Get Symbol Info",
    description:
      "Retrieves type signatures, documentation, definition locations, and source code of the definition/declaration for a symbol.",
    parameters: Type.Object({
      filePath: Type.String({
        description: "Path to the file containing the symbol reference.",
      }),
      symbolName: Type.String({
        description: "The name of the symbol to query.",
      }),
      line: Type.Optional(
        Type.Integer({
          description: "1-indexed line number of the symbol.",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const lspManager = getLspManager();
      if (!lspManager?.isServerRunning()) {
        return {
          content: [{ type: "text", text: "Error: Language server is not running." }],
          isError: true,
          details: {},
        };
      }

      const res = await getSymbolInfo(
        lspManager,
        ctx.cwd,
        params.filePath,
        params.symbolName,
        params.line,
      );
      return {
        content: [{ type: "text", text: res.text }],
        isError: res.isError,
        details: res.details ?? {},
      };
    },
  });

  // Tool: lsp_find_references - search for all references to a symbol across files
  pi.registerTool({
    name: "lsp_find_references",
    label: "LSP: Find References",
    description: "Finds all references and usages of a symbol across the workspace.",
    parameters: Type.Object({
      filePath: Type.String({
        description: "Path to the file containing the symbol reference.",
      }),
      symbolName: Type.String({
        description: "The name of the symbol.",
      }),
      line: Type.Optional(
        Type.Integer({
          description: "1-indexed line number of the symbol.",
        }),
      ),
      offset: Type.Optional(
        Type.Integer({
          description: "1-based starting index (default is 1).",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const lspManager = getLspManager();
      if (!lspManager?.isServerRunning()) {
        return {
          content: [{ type: "text", text: "Error: Language server is not running." }],
          isError: true,
          details: {},
        };
      }

      const res = await findReferences(
        lspManager,
        ctx.cwd,
        params.filePath,
        params.symbolName,
        params.line,
        params.offset,
      );
      return {
        content: [{ type: "text", text: res.text }],
        isError: res.isError,
        details: res.details ?? {},
      };
    },
  });

  // Tool: lsp_search_symbols - find files containing symbol definitions or list outline
  pi.registerTool({
    name: "lsp_search_symbols",
    label: "LSP: Search / List Symbols",
    description:
      "Lists symbols defined in a specific file, or searches the entire workspace for symbols matching a query (provide either filePath, query, or both).",
    parameters: Type.Object({
      filePath: Type.Optional(
        Type.String({
          description: "Path to a file to outline or search within.",
        }),
      ),
      query: Type.Optional(
        Type.String({
          description:
            "Query to search for symbols by name or kind (supported kinds: class, interface, function, method, variable, const, constant, type, enum, struct).",
        }),
      ),
      offset: Type.Optional(
        Type.Integer({
          description: "1-based starting index (default is 1).",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const lspManager = getLspManager();
      if (!lspManager?.isServerRunning()) {
        return {
          content: [{ type: "text", text: "Error: Language server is not running." }],
          isError: true,
          details: {},
        };
      }

      const res = await searchSymbols(
        lspManager,
        ctx.cwd,
        params.filePath,
        params.query,
        params.offset,
      );
      return {
        content: [{ type: "text", text: res.text }],
        isError: res.isError,
        details: res.details ?? {},
      };
    },
  });

  // Tool: lsp_get_diagnostics - query compiler / linter messages in workspace or for a specific file
  pi.registerTool({
    name: "lsp_get_diagnostics",
    label: "LSP: Get Diagnostics",
    description:
      "Retrieves active linter and compiler diagnostics (errors, warnings) for the workspace or a specific file.",
    parameters: Type.Object({
      filePath: Type.Optional(
        Type.String({
          description:
            "Path to a file to get diagnostics for. If omitted, returns all workspace diagnostics.",
        }),
      ),
      offset: Type.Optional(
        Type.Integer({
          description: "1-based starting index (default is 1).",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const lspManager = getLspManager();
      if (!lspManager?.isServerRunning()) {
        return {
          content: [{ type: "text", text: "Error: Language server is not running." }],
          isError: true,
          details: {},
        };
      }

      const res = await getDiagnostics(lspManager, ctx.cwd, params.filePath, params.offset);
      return {
        content: [{ type: "text", text: res.text }],
        isError: res.isError,
        details: res.details ?? {},
      };
    },
  });

  // Tool: lsp_rename_symbol - rename a symbol and apply workspace edit automatically
  pi.registerTool({
    name: "lsp_rename_symbol",
    label: "LSP: Rename Symbol",
    description:
      "Renames a symbol globally across all files in the workspace. To uniquely identify the target symbol and avoid renaming an unrelated symbol with the same name in another scope, you must locate its declaration line and provide it.",
    parameters: Type.Object({
      filePath: Type.String({
        description: "Path to the file containing the symbol reference.",
      }),
      symbolName: Type.String({
        description: "The name of the symbol to rename.",
      }),
      newName: Type.String({
        description: "The new name for the symbol.",
      }),
      line: Type.Optional(
        Type.Integer({
          description: "1-indexed line number where the target symbol is declared.",
        }),
      ),
    }),
    async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
      const lspManager = getLspManager();
      if (!lspManager?.isServerRunning()) {
        return {
          content: [{ type: "text", text: "Error: Language server is not running." }],
          isError: true,
          details: {},
        };
      }

      const res = await renameSymbol(
        lspManager,
        ctx.cwd,
        params.filePath,
        params.symbolName,
        params.newName,
        params.line,
      );
      return {
        content: [{ type: "text", text: res.text }],
        isError: res.isError,
        details: res.details ?? {},
      };
    },
  });
}
