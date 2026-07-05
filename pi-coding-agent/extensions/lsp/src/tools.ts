// Tool functions to query the language server. Agent-agnostic.
import * as fs from "node:fs/promises";
import { isAbsolute, join, relative } from "node:path";
import { loadServersConfigSync } from "./config.js";
import type { LspClientManager, SymbolInfo } from "./manager.js";

const configSync = loadServersConfigSync();
const defaultLimit = configSync?.defaultLimit ?? 100;

async function getCodeSnippet(
  workspaceDir: string,
  filePath: string,
  line: number,
  numLines = 7,
): Promise<string | null> {
  try {
    const absolutePath = isAbsolute(filePath) ? filePath : join(workspaceDir, filePath);
    const content = await fs.readFile(absolutePath, "utf8");
    const lines = content.split("\n");
    const startIdx = Math.max(0, line - 1);
    const endIdx = Math.min(lines.length, startIdx + numLines);
    return lines.slice(startIdx, endIdx).join("\n");
  } catch {
    return null;
  }
}

function getMarkdownLanguage(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase();
  if (ext === "ts" || ext === "tsx") return "typescript";
  if (ext === "js" || ext === "jsx") return "javascript";
  if (ext === "py") return "python";
  if (ext === "rs") return "rust";
  if (ext === "go") return "go";
  return "";
}

function toRelativePath(cwd: string, filePath?: string, fallback = ""): string {
  if (!filePath) {
    return fallback;
  }
  return isAbsolute(filePath) ? relative(cwd, filePath) : filePath;
}

function compactHoverText(hoverText: string): string {
  if (!hoverText) return "";

  const lines = hoverText.split(/\r?\n/);
  const compactedLines: string[] = [];
  let inCodeBlock = false;
  let codeBlockLineCount = 0;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();

    if (line.startsWith("```")) {
      inCodeBlock = !inCodeBlock;
      codeBlockLineCount = 0;
      compactedLines.push(line);
      continue;
    }

    if (inCodeBlock) {
      if (codeBlockLineCount < 15) {
        compactedLines.push(lines[i]);
        codeBlockLineCount++;
      } else if (codeBlockLineCount === 15) {
        compactedLines.push("  ... [signature truncated]");
        codeBlockLineCount++;
      }
      continue;
    }

    if (!line) {
      if (compactedLines.length > 0 && compactedLines[compactedLines.length - 1] !== "") {
        compactedLines.push("");
      }
      continue;
    }

    if (line.length > 300) {
      compactedLines.push(`${line.slice(0, 300)} ... [truncated]`);
    } else {
      compactedLines.push(line);
    }
  }

  return compactedLines.join("\n").trim();
}

export interface ToolResult {
  text: string;
  isError?: boolean;
  details?: unknown;
}

/**
 * Query hover definition and code snippets for a symbol.
 */
export async function getSymbolInfo(
  lspManager: LspClientManager,
  cwd: string,
  filePath: string,
  symbolName: string,
  line?: number,
): Promise<ToolResult> {
  try {
    const relFile = toRelativePath(cwd, filePath);

    const coords = await lspManager.findSymbolCoordinates(filePath, symbolName, line);
    if (coords.length === 0) {
      return {
        text: `Symbol '${symbolName}' not found in file ${relFile}.`,
      };
    }

    const results: unknown[] = [];
    const outputLines: string[] = [];

    // Check up to 5 occurrences to keep the response length and API call time reasonable
    for (const coord of coords.slice(0, 5)) {
      const [hoverText, locations] = await Promise.all([
        lspManager
          .getHover(filePath, coord.line, coord.character)
          .then(compactHoverText)
          .catch((err) => `No doc: ${err.message}`),
        lspManager.getDefinition(filePath, coord.line, coord.character).catch(() => null),
      ]);

      results.push({ coord, hoverText, locations });

      let block = `Match at line ${coord.line}, col ${coord.character}:\n`;
      block += `${hoverText.trim()}\n\n`;

      if (locations && locations.length > 0) {
        for (const loc of locations) {
          const snippet = await getCodeSnippet(cwd, loc.filePath, loc.line);
          const relPath = toRelativePath(cwd, loc.filePath);
          block += `Definition: ${relPath}:${loc.line}\n`;
          if (snippet) {
            const lang = getMarkdownLanguage(loc.filePath);
            block += `\`\`\`${lang}\n${snippet}\n\`\`\`\n\n`;
          }
        }
      } else {
        // If no definition, try to show the snippet at the reference itself
        const snippet = await getCodeSnippet(cwd, filePath, coord.line);
        if (snippet) {
          const lang = getMarkdownLanguage(filePath);
          block += `Code at reference:\n\`\`\`${lang}\n${snippet}\n\`\`\`\n\n`;
        }
      }
      outputLines.push(block.trim());
    }

    return {
      text: outputLines.join("\n\n---\n\n"),
      details: { results },
    };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return {
      text: `Error fetching symbol information: ${error.message}`,
      isError: true,
    };
  }
}

/**
 * Search for all references to a symbol across files.
 */
export async function findReferences(
  lspManager: LspClientManager,
  cwd: string,
  filePath: string,
  symbolName: string,
  line?: number,
  offset?: number,
): Promise<ToolResult> {
  try {
    const relFile = toRelativePath(cwd, filePath);

    const coords = await lspManager.findSymbolCoordinates(filePath, symbolName, line);
    if (coords.length === 0) {
      return {
        text: `Symbol '${symbolName}' not found in file ${relFile}.`,
      };
    }
    const coord = coords[0];

    const references = await lspManager.getReferences(filePath, coord.line, coord.character);

    if (references.length === 0) {
      return {
        text: "No references found.",
      };
    }

    const limit = defaultLimit;
    const offsetVal = Math.max(1, offset ?? 1);
    const totalCount = references.length;
    const paginatedReferences = references.slice(offsetVal - 1, offsetVal - 1 + limit);
    const remainingCount = totalCount - (offsetVal - 1 + paginatedReferences.length);

    const refStrings: string[] = [];
    for (const ref of paginatedReferences) {
      const relPath = toRelativePath(cwd, ref.filePath);
      const codeLine = await getCodeSnippet(cwd, ref.filePath, ref.line, 1);
      const snippetPart = codeLine ? `: ${codeLine.trim()}` : "";
      refStrings.push(`${relPath}:${ref.line}${snippetPart}`);
    }

    let output = refStrings.join("\n");
    if (remainingCount > 0) {
      output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedReferences.length - 1} of ${totalCount}. Use offset: ${offsetVal + limit} to get more.`;
    } else if (offsetVal > 1 && paginatedReferences.length > 0) {
      output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedReferences.length - 1} of ${totalCount}.`;
    }

    return {
      text: output,
      details: { references: paginatedReferences },
    };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return {
      text: `Error finding references: ${error.message}`,
      isError: true,
    };
  }
}

/**
 * Find files containing symbol definitions or list outline.
 */
export async function searchSymbols(
  lspManager: LspClientManager,
  cwd: string,
  filePath?: string,
  query?: string,
  offset?: number,
): Promise<ToolResult> {
  try {
    if (!filePath && !query) {
      return {
        text: "Error: Must provide either filePath, query, or both.",
        isError: true,
      };
    }

    let symbols: SymbolInfo[] = [];
    let paginatedSymbols: SymbolInfo[] = [];
    let output = "";
    const limit = defaultLimit;
    const offsetVal = Math.max(1, offset ?? 1);

    const kindKeywords: Record<string, string[]> = {
      class: ["Class"],
      interface: ["Interface"],
      function: ["Function", "Method"],
      method: ["Method"],
      variable: ["Variable", "Constant", "Field"],
      const: ["Constant", "Variable"],
      constant: ["Constant"],
      type: ["Interface", "Struct", "TypeParameter", "Class"],
      enum: ["Enum"],
      struct: ["Struct"],
    };

    if (filePath) {
      const relFile = toRelativePath(cwd, filePath);

      // List / search symbols within a specific file
      const fileSymbols = await lspManager.getSymbols(filePath);
      if (query) {
        const queryLower = query.toLowerCase().trim();
        const targetKinds = kindKeywords[queryLower] || [];

        symbols = fileSymbols.filter((sym) => {
          const nameMatches = sym.name.toLowerCase().includes(queryLower);
          const kindMatches = targetKinds.includes(sym.kind);
          return nameMatches || kindMatches;
        });
      } else {
        symbols = fileSymbols;
      }

      if (symbols.length === 0) {
        return {
          text: query
            ? `No symbols matching query '${query}' found in file ${relFile}.`
            : `No symbols found in file ${relFile}.`,
        };
      }

      paginatedSymbols = symbols.slice(offsetVal - 1, offsetVal - 1 + limit);
      const totalCount = symbols.length;
      const remainingCount = totalCount - (offsetVal - 1 + paginatedSymbols.length);

      const symbolStrings = paginatedSymbols.map((sym) => {
        const detailStr = sym.detail ? ` (${sym.detail})` : "";
        return `${relFile}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
      });
      output = symbolStrings.join("\n");
      if (remainingCount > 0) {
        output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedSymbols.length - 1} of ${totalCount} symbols in this file. Use offset: ${offsetVal + limit} to get more.`;
      } else if (offsetVal > 1 && paginatedSymbols.length > 0) {
        output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedSymbols.length - 1} of ${totalCount} symbols in this file.`;
      }
    } else if (query) {
      // Search workspace symbols
      const queryLower = query.toLowerCase().trim();
      const targetKinds = kindKeywords[queryLower];

      if (targetKinds) {
        // Retrieve symbols matching the keyword query itself, and filter by kind.
        // Avoid querying with empty string ("") as it hangs/fails on large workspaces.
        const querySymbols = await lspManager.getWorkspaceSymbols(query);
        symbols = querySymbols.filter((sym) => targetKinds.includes(sym.kind));
      } else {
        symbols = await lspManager.getWorkspaceSymbols(query);
      }

      if (symbols.length === 0) {
        return {
          text: `No symbols matching query '${query}' found.`,
        };
      }

      paginatedSymbols = symbols.slice(offsetVal - 1, offsetVal - 1 + limit);
      const totalCount = symbols.length;
      const remainingCount = totalCount - (offsetVal - 1 + paginatedSymbols.length);

      const symbolStrings = paginatedSymbols.map((sym) => {
        const relPath = toRelativePath(cwd, sym.filePath, "unknown");
        const detailStr = sym.detail ? ` (${sym.detail})` : "";
        return `${relPath}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
      });
      output = symbolStrings.join("\n");
      if (remainingCount > 0) {
        output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedSymbols.length - 1} of ${totalCount}. Use offset: ${offsetVal + limit} to get more.`;
      } else if (offsetVal > 1 && paginatedSymbols.length > 0) {
        output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedSymbols.length - 1} of ${totalCount}.`;
      }
    }

    return {
      text: output,
      details: { symbols: paginatedSymbols },
    };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return {
      text: `Error searching symbols: ${error.message}`,
      isError: true,
    };
  }
}

/**
 * Query compiler / linter messages in workspace or for a specific file.
 */
export async function getDiagnostics(
  lspManager: LspClientManager,
  cwd: string,
  filePath?: string,
  offset?: number,
): Promise<ToolResult> {
  try {
    await lspManager.triggerWorkspaceDiagnostics(filePath);
    if (filePath) {
      await lspManager.syncFile(filePath);
      const pulled = await lspManager.pullDiagnostics(filePath);
      if (!pulled) {
        await lspManager.waitForDiagnostics(filePath);
      }
    } else {
      // Wait a short duration for the background diagnostics compilation to process
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }

    const diagMap = lspManager.getDiagnostics(filePath);

    const diagLines: string[] = [];
    for (const [relPath, list] of Object.entries(diagMap)) {
      if (list.length === 0) continue;
      const relFile = toRelativePath(cwd, relPath);
      for (const d of list) {
        // Filter out Info and Hint diagnostics by default to keep signal high
        if (d.severity === "Information" || d.severity === "Hint") {
          continue;
        }
        let block = `${relFile}:${d.line}: ${d.severity}: ${d.message}`;
        const codeLine = await getCodeSnippet(cwd, relPath, d.line, 1);
        if (codeLine) {
          block += `\n  > ${codeLine.trim()}`;
        }
        diagLines.push(block);
      }
    }

    if (diagLines.length === 0) {
      const target = toRelativePath(cwd, filePath);
      const scope = target ? `file: ${target}` : "workspace";
      return {
        text: `No diagnostics (clean code!) for ${scope}.`,
      };
    }

    const limit = defaultLimit;
    const offsetVal = Math.max(1, offset ?? 1);
    const totalCount = diagLines.length;
    const paginatedDiags = diagLines.slice(offsetVal - 1, offsetVal - 1 + limit);
    const remainingCount = totalCount - (offsetVal - 1 + paginatedDiags.length);

    let output = paginatedDiags.join("\n");
    if (remainingCount > 0) {
      output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedDiags.length - 1} of ${totalCount}. Use offset: ${offsetVal + limit} to get more.`;
    } else if (offsetVal > 1 && paginatedDiags.length > 0) {
      output += `\n\nShowing matches ${offsetVal}-${offsetVal + paginatedDiags.length - 1} of ${totalCount}.`;
    }

    return {
      text: output,
      details: { diagnostics: diagMap },
    };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return {
      text: `Error fetching diagnostics: ${error.message}`,
      isError: true,
    };
  }
}

/**
 * Rename a symbol and apply workspace edit automatically.
 */
export async function renameSymbol(
  lspManager: LspClientManager,
  cwd: string,
  filePath: string,
  symbolName: string,
  newName: string,
  line?: number,
): Promise<ToolResult> {
  try {
    const relFile = toRelativePath(cwd, filePath);

    const coords = await lspManager.findSymbolCoordinates(filePath, symbolName, line);
    if (coords.length === 0) {
      return {
        text: `Symbol '${symbolName}' not found in file ${relFile}.`,
      };
    }

    const coord = coords[0];
    const originalName = symbolName;
    const workspaceEdit = await lspManager.renameSymbol(
      filePath,
      coord.line,
      coord.character,
      newName,
    );

    if (!workspaceEdit) {
      return {
        text: "Rename failed or returned no changes from LSP server.",
      };
    }

    // Apply edits to disk automatically
    const stats = await lspManager.applyWorkspaceEdit(workspaceEdit);

    const modifiedFiles = Object.entries(stats);
    if (modifiedFiles.length === 0) {
      return {
        text: `Renamed "${originalName}" to "${newName}". No files modified.`,
        details: { stats, workspaceEdit },
      };
    }

    const totalReplacements = modifiedFiles.reduce((sum, [_, stat]) => sum + stat.count, 0);
    const filesCount = modifiedFiles.length;

    const filesLabel = filesCount === 1 ? "file" : "files";
    const totalReplacementsLabel = totalReplacements === 1 ? "replacement" : "replacements";

    const changesLines = modifiedFiles.map(([file, stat]) => {
      const relFile = toRelativePath(cwd, file);
      const countLabel = stat.count === 1 ? "replacement" : "replacements";
      const lineLabel = stat.lines.length === 1 ? "line" : "lines";
      return `${relFile}: ${stat.count} ${countLabel} on ${lineLabel} ${stat.lines.join(", ")}`;
    });

    const summary = `Renamed "${originalName}" to "${newName}". ${totalReplacements} ${totalReplacementsLabel} across ${filesCount} ${filesLabel}:\n${changesLines.join("\n")}`;

    return {
      text: summary,
      details: { stats, workspaceEdit },
    };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return {
      text: `Error renaming symbol: ${error.message}`,
      isError: true,
    };
  }
}
