// Detects the workspace programming language using project configuration files and file extensions.
import * as fs from "node:fs/promises";
import * as path from "node:path";

export interface LanguageConfig {
  command: string;
  args: string[];
  detection: {
    files: string[];
    extensions: string[];
  };
}

export interface ServersConfig {
  defaultLimit?: number;
  defaultTimeoutMs?: number;
  workspaceSearchTimeoutMs?: number;
  languages: Record<string, LanguageConfig>;
}

export const IGNORE_DIRS = new Set([
  "node_modules",
  ".git",
  ".venv",
  "venv",
  "dist",
  "build",
  "out",
  ".pi",
  ".gemini",
  ".cache",
]);

/**
 * Check if signature configuration files exist in workspace root
 */
async function checkRootFiles(workspaceDir: string, config: ServersConfig): Promise<string | null> {
  try {
    const rootFiles = await fs.readdir(workspaceDir);
    const fileSet = new Set(rootFiles);

    let bestLang: string | null = null;
    let maxMatchCount = 0;

    for (const [langName, langConfig] of Object.entries(config.languages)) {
      let matches = 0;
      for (const detFile of langConfig.detection.files) {
        if (fileSet.has(detFile)) {
          matches++;
        }
      }

      if (matches > maxMatchCount) {
        maxMatchCount = matches;
        bestLang = langName;
      }
    }

    return bestLang;
  } catch {
    return null;
  }
}

/**
 * Count file extensions in workspace to find the dominant language
 */
async function scanExtensions(workspaceDir: string, config: ServersConfig): Promise<string | null> {
  const extensionCounts: Record<string, number> = {};

  async function walk(dir: string, depth = 0) {
    if (depth > 4) return; // Prevent scanning too deeply

    try {
      const entries = await fs.readdir(dir, { withFileTypes: true });
      for (const entry of entries) {
        if (entry.isDirectory()) {
          if (!IGNORE_DIRS.has(entry.name)) {
            await walk(path.join(dir, entry.name), depth + 1);
          }
        } else if (entry.isFile()) {
          const ext = path.extname(entry.name).toLowerCase();
          if (ext) {
            extensionCounts[ext] = (extensionCounts[ext] || 0) + 1;
          }
        }
      }
    } catch {
      // Ignore errors reading directories
    }
  }

  await walk(workspaceDir);

  // Map file extension back to language configs
  let bestLang: string | null = null;
  let maxScore = 0;

  for (const [langName, langConfig] of Object.entries(config.languages)) {
    let score = 0;
    for (const ext of langConfig.detection.extensions) {
      score += extensionCounts[ext] || 0;
    }

    if (score > maxScore) {
      maxScore = score;
      bestLang = langName;
    }
  }

  return maxScore > 0 ? bestLang : null;
}

/**
 * Detect workspace language using root files or file extension counts
 */
export async function detectWorkspaceLanguage(
  workspaceDir: string,
  config: ServersConfig,
): Promise<string | null> {
  // 1. Try file signature detection at the root level (strongest signal)
  const rootMatch = await checkRootFiles(workspaceDir, config);
  if (rootMatch) {
    return rootMatch;
  }

  // 2. Fall back to file extension counting
  const extMatch = await scanExtensions(workspaceDir, config);
  return extMatch;
}
