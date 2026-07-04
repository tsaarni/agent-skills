import * as fs from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { CONFIG_DIR_NAME, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Container, Text } from "@earendil-works/pi-tui";
import { LspClientManager } from "./client.js";
import { detectWorkspaceLanguage, type ServersConfig } from "./heuristics.js";
import { registerLspTools } from "./tools.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Global active manager instance
let lspManager: LspClientManager | null = null;
let serversConfig: ServersConfig | null = null;

// Read configuration mapping file
async function loadServersConfig(): Promise<ServersConfig> {
  if (serversConfig) return serversConfig;

  // Search in extension dir or adjacent dir
  const pathsToTry = [join(__dirname, "../lsp-servers.json"), join(__dirname, "lsp-servers.json")];

  for (const p of pathsToTry) {
    try {
      const data = await fs.readFile(p, "utf8");
      serversConfig = JSON.parse(data) as ServersConfig;
      return serversConfig;
    } catch {
      // Continue
    }
  }

  throw new Error("Could not load lsp-servers.json config file");
}

// Load project initialization config if exists
async function loadProjectLspConfig(workspaceDir: string): Promise<{
  initialized: boolean;
  language: string;
  command: string;
  args: string[];
} | null> {
  const projectConfigPath = join(workspaceDir, CONFIG_DIR_NAME, "lsp-project.json");
  try {
    const data = await fs.readFile(projectConfigPath, "utf8");
    return JSON.parse(data);
  } catch {
    return null;
  }
}

// Save project initialization config
async function saveProjectLspConfig(
  workspaceDir: string,
  language: string,
  command: string,
  args: string[],
): Promise<void> {
  const configDir = join(workspaceDir, CONFIG_DIR_NAME);
  await fs.mkdir(configDir, { recursive: true });

  const projectConfigPath = join(configDir, "lsp-project.json");
  const payload = {
    initialized: true,
    initializedAt: new Date().toISOString(),
    language,
    command,
    args,
  };

  await fs.writeFile(projectConfigPath, JSON.stringify(payload, null, 2), "utf8");
}

export interface LspStatus {
  language: string;
  command: string;
  args: string[];
  status: "running" | "error" | "not_initialized";
  error?: string;
  pid?: number | null;
  syncedFilesCount?: number;
  diagnosticsSummary?: string;
}

export function buildLspStatusMarkdown(s: LspStatus): string {
  if (s.status === "running") {
    let msg = `LSP: running (${s.language})`;
    if (s.pid) msg += ` | pid: ${s.pid}`;
    if (s.syncedFilesCount !== undefined) msg += ` | synced: ${s.syncedFilesCount}`;
    if (s.diagnosticsSummary) msg += ` | ${s.diagnosticsSummary}`;
    msg += ` — ${s.command} ${s.args.join(" ")}`;
    return msg;
  } else if (s.status === "error") {
    return `LSP: failed to start (${s.language}) — ${s.error}`;
  } else {
    return `LSP: not initialized`;
  }
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
    if (projectConfig?.initialized) {
      try {
        await lspManager.start(projectConfig.language, projectConfig.command, projectConfig.args);
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

  // Sync file content to LSP server whenever the agent reads or writes files
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
      const input = event.input as Record<string, unknown> | undefined;
      if (input) {
        const filePath = input.path || input.AbsolutePath || input.TargetFile;
        if (typeof filePath === "string") {
          await lspManager.syncFile(filePath);
        }
      }
    }
  });

  // --- Slash Command ---

  // Register '/lsp' command to query status or start/stop/restart the server
  pi.registerCommand("lsp", {
    description: "Manage language server connection (status, init, start, stop, restart)",
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
              "LSP Heuristics: Could not detect workspace language. No matching file pattern found.",
              "warning",
            );
            return;
          }

          const langConfig = config.languages[detectedLang];
          const confirm = await ctx.ui.confirm(
            "LSP Initialization",
            `Heuristics detected: ${detectedLang}.\nDo you want to initialize LSP for this language using command: '${langConfig.command}'?`,
          );

          if (!confirm) {
            ctx.ui.notify("Initialization cancelled.", "info");
            return;
          }

          await saveProjectLspConfig(ctx.cwd, detectedLang, langConfig.command, langConfig.args);

          ctx.ui.notify(`LSP initialized for ${detectedLang}! Starting server...`, "info");
          await lspManager.start(detectedLang, langConfig.command, langConfig.args);
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

      if (subCommand === "stop") {
        if (!lspManager.isServerRunning()) {
          ctx.ui.notify("LSP server is already stopped.", "info");
          return;
        }
        await lspManager.stop();
        ctx.ui.notify("LSP server stopped.", "info");
        return;
      }

      if (subCommand === "start") {
        if (lspManager.isServerRunning()) {
          ctx.ui.notify("LSP server is already running.", "info");
          return;
        }

        const projectConfig = await loadProjectLspConfig(ctx.cwd);
        if (!projectConfig) {
          ctx.ui.notify("LSP not initialized. Run '/lsp init' first.", "warning");
          return;
        }

        ctx.ui.notify(`LSP: Starting server for ${projectConfig.language}...`, "info");
        try {
          await lspManager.start(projectConfig.language, projectConfig.command, projectConfig.args);
          ctx.ui.notify("LSP started.", "info");
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
          ctx.ui.notify(`Failed to start: ${error.message}`, "error");
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
          await lspManager.start(projectConfig.language, projectConfig.command, projectConfig.args);
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
        `Unknown LSP sub-command: ${subCommand}. Available: status, init, start, stop, restart`,
        "error",
      );
    },
  });

  // Register LLM-exposed tools
  registerLspTools(pi, () => lspManager);
}
