import * as fsSync from "node:fs";
import * as fs from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { ServersConfig } from "./detector.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

let serversConfig: ServersConfig | null = null;

export function loadServersConfigSync(): ServersConfig | null {
  const pathsToTry = [join(__dirname, "../lsp-config.json"), join(__dirname, "lsp-config.json")];
  for (const p of pathsToTry) {
    try {
      const data = fsSync.readFileSync(p, "utf8");
      return JSON.parse(data) as ServersConfig;
    } catch {
      // Continue
    }
  }
  return null;
}

export async function loadServersConfig(): Promise<ServersConfig> {
  if (serversConfig) return serversConfig;

  // Search in extension dir or adjacent dir
  const pathsToTry = [join(__dirname, "../lsp-config.json"), join(__dirname, "lsp-config.json")];

  for (const p of pathsToTry) {
    try {
      const data = await fs.readFile(p, "utf8");
      serversConfig = JSON.parse(data) as ServersConfig;
      return serversConfig;
    } catch {
      // Continue
    }
  }

  throw new Error("Could not load lsp-config.json config file");
}

export async function resolveLanguageFromCommand(command: string): Promise<string> {
  try {
    const config = await loadServersConfig();
    for (const [langId, langConfig] of Object.entries(config.languages)) {
      if (langConfig.command === command || command.endsWith(langConfig.command)) {
        return langId;
      }
    }
  } catch {
    // ignore
  }
  return command;
}

export async function loadProjectLspConfig(
  projectConfigPath: string,
  globalConfigPath: string,
): Promise<{
  autostart: boolean;
  language: string;
  command: string;
  args: string[];
} | null> {
  const parseConfig = async (
    data: string,
  ): Promise<{
    autostart: boolean;
    language: string;
    command: string;
    args: string[];
  } | null> => {
    const parsed = JSON.parse(data);
    if (!parsed.command) return null;
    let language = parsed.language;
    if (!language) {
      language = await resolveLanguageFromCommand(parsed.command);
    }
    return {
      autostart: parsed.autostart ?? parsed.initialized ?? false,
      language,
      command: parsed.command,
      args: parsed.args || [],
    };
  };

  // 1. Try project-local config first
  try {
    const data = await fs.readFile(projectConfigPath, "utf8");
    return await parseConfig(data);
  } catch {
    // 2. Fallback to global config
    try {
      const data = await fs.readFile(globalConfigPath, "utf8");
      return await parseConfig(data);
    } catch {
      return null;
    }
  }
}

export async function saveProjectLspConfig(
  globalConfigPath: string,
  command: string,
  args: string[],
): Promise<void> {
  const globalConfigDir = dirname(globalConfigPath);
  await fs.mkdir(globalConfigDir, { recursive: true });

  const payload = {
    autostart: true,
    initializedAt: new Date().toISOString(),
    command,
    args,
  };

  await fs.writeFile(globalConfigPath, JSON.stringify(payload, null, 2), "utf8");
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
