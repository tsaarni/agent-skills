// Tool functions to query the language server. Agent-agnostic.
import * as fs from "node:fs/promises";
import { isAbsolute, join, relative } from "node:path";
import { loadServersConfigSync } from "./config.js";
import type { LspClientManager, SymbolInfo } from "./manager.js";
import type { LSPCodeAction } from "./protocol.js";
import type { Diagnostic, FileChange, Location, SymbolMatch } from "./schemas.js";

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

/**
 * The value every LSP tool resolves to, and the shape of its `structuredContent`. Codemode
 * scripts receive this instead of the tool's text, so they can paginate and filter without
 * parsing prose.
 */
export interface LspPage<Item = unknown> {
  /** False when the call failed; `error` then says why. */
  ok: boolean;
  error?: string;
  /** Note for a successful but empty result, such as "symbol not found". */
  message?: string;
  /** The page of results. */
  items: Item[];
  /** Matches before pagination. */
  total: number;
  /** 1-based offset of `items[0]`. */
  offset: number;
  limit: number;
  hasMore: boolean;
  /** Offset to pass next; present only when `hasMore`. */
  nextOffset?: number;
  /** Tool-specific fields, such as `totalReplacements` for rename. */
  [key: string]: unknown;
}

export interface ToolResult<Item = unknown> {
  text: string;
  isError?: boolean;
  /** The `structuredContent` scripts receive; matches the tool's `outputSchema`. */
  structured: LspPage<Item>;
}

/** Page a full result list: a slice plus the fields a script needs to fetch the rest. */
function paginate<Item>(all: Item[], offset: number | undefined, limit: number): LspPage<Item> {
  const start = Math.max(1, offset ?? 1);
  const items = all.slice(start - 1, start - 1 + limit);
  const hasMore = start - 1 + items.length < all.length;
  return {
    ok: true,
    items,
    total: all.length,
    offset: start,
    limit,
    hasMore,
    ...(hasMore ? { nextOffset: start + limit } : {}),
  };
}

/** A successful but empty result, for "not found" rather than a failure. */
function empty<Item>(message?: string): LspPage<Item> {
  return {
    ok: true,
    ...(message ? { message } : {}),
    items: [],
    total: 0,
    offset: 1,
    limit: 0,
    hasMore: false,
  };
}

/** A failed call. `isError` shows the model an error; `ok: false` lets scripts branch. */
export function failure(text: string, error: string): ToolResult<never> {
  return {
    text,
    isError: true,
    structured: { ok: false, error, items: [], total: 0, offset: 1, limit: 0, hasMore: false },
  };
}

/** The "showing X-Y of N" trailer shared by the paged tools. */
function pageTrailer(page: LspPage<unknown>, suffix = "."): string {
  const shown = `${page.offset}-${page.offset + page.items.length - 1} of ${page.total}`;
  if (page.hasMore) {
    return `\n\nShowing matches ${shown}${suffix} Use offset: ${page.nextOffset} to get more.`;
  }
  if (page.offset > 1 && page.items.length > 0) {
    return `\n\nShowing matches ${shown}${suffix}`;
  }
  return "";
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
): Promise<ToolResult<SymbolMatch>> {
  try {
    const relFile = toRelativePath(cwd, filePath);

    const coords = await lspManager.findSymbolCoordinates(filePath, symbolName, line);
    if (coords.length === 0) {
      const message = `Symbol '${symbolName}' not found in file ${relFile}.`;
      return { text: message, structured: empty(message) };
    }

    const matches: SymbolMatch[] = [];
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

      matches.push({ coord, hoverText, locations: locations ?? [] });

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
      structured: paginate(matches, 1, matches.length),
    };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return failure(`Error fetching symbol information: ${error.message}`, error.message);
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
): Promise<ToolResult<Location>> {
  try {
    const relFile = toRelativePath(cwd, filePath);

    const coords = await lspManager.findSymbolCoordinates(filePath, symbolName, line);
    if (coords.length === 0) {
      const message = `Symbol '${symbolName}' not found in file ${relFile}.`;
      return { text: message, structured: empty(message) };
    }
    const coord = coords[0];

    const references = await lspManager.getReferences(filePath, coord.line, coord.character);

    if (references.length === 0) {
      return { text: "No references found.", structured: empty("No references found.") };
    }

    const page = paginate(references, offset, defaultLimit);

    const refStrings: string[] = [];
    for (const ref of page.items) {
      const relPath = toRelativePath(cwd, ref.filePath);
      const codeLine = await getCodeSnippet(cwd, ref.filePath, ref.line, 1);
      const snippetPart = codeLine ? `: ${codeLine.trim()}` : "";
      refStrings.push(`${relPath}:${ref.line}${snippetPart}`);
    }

    return { text: refStrings.join("\n") + pageTrailer(page), structured: page };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return failure(`Error finding references: ${error.message}`, error.message);
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
): Promise<ToolResult<SymbolInfo>> {
  try {
    if (!filePath && !query) {
      return failure(
        "Error: Must provide either filePath, query, or both.",
        "must provide either filePath or query",
      );
    }

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

    let symbols: SymbolInfo[] = [];
    // `file` shows `filePath:` for every symbol; `workspace` uses each symbol's own path.
    let scope: "file" | "workspace" = "workspace";
    let relFile = "";

    if (filePath) {
      scope = "file";
      relFile = toRelativePath(cwd, filePath);

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
        const message = query
          ? `No symbols matching query '${query}' found in file ${relFile}.`
          : `No symbols found in file ${relFile}.`;
        return { text: message, structured: empty(message) };
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
        const message = `No symbols matching query '${query}' found.`;
        return { text: message, structured: empty(message) };
      }
    }

    const page = paginate(symbols, offset, defaultLimit);

    const symbolStrings = page.items.map((sym) => {
      const detailStr = sym.detail ? ` (${sym.detail})` : "";
      const where = scope === "file" ? relFile : toRelativePath(cwd, sym.filePath, "unknown");
      return `${where}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
    });

    const suffix = scope === "file" ? " symbols in this file." : ".";
    return { text: symbolStrings.join("\n") + pageTrailer(page, suffix), structured: page };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return failure(`Error searching symbols: ${error.message}`, error.message);
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
): Promise<ToolResult<Diagnostic>> {
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

    // Flatten the per-file map into one list, tagging each diagnostic with its file. This is
    // what makes `items` filterable in a script.
    const all: Diagnostic[] = [];
    for (const [relPath, list] of Object.entries(diagMap)) {
      const file = toRelativePath(cwd, relPath);
      for (const d of list) {
        all.push({
          file,
          line: d.line,
          character: d.character,
          severity: d.severity,
          message: d.message,
        });
      }
    }

    const page = paginate(all, offset, defaultLimit);

    const diagLines: string[] = [];
    for (const d of page.items) {
      // Filter out Info and Hint diagnostics by default to keep the text signal high
      if (d.severity === "Information" || d.severity === "Hint") {
        continue;
      }
      let block = `${d.file}:${d.line}: ${d.severity}: ${d.message}`;
      const codeLine = await getCodeSnippet(cwd, d.file, d.line, 1);
      if (codeLine) {
        block += `\n  > ${codeLine.trim()}`;
      }
      diagLines.push(block);
    }

    if (diagLines.length === 0) {
      const target = toRelativePath(cwd, filePath);
      const scope = target ? `file: ${target}` : "workspace";
      const message = `No diagnostics (clean code!) for ${scope}.`;
      return { text: message, structured: all.length === 0 ? empty(message) : page };
    }

    return { text: diagLines.join("\n") + pageTrailer(page), structured: page };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return failure(`Error fetching diagnostics: ${error.message}`, error.message);
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
): Promise<ToolResult<FileChange>> {
  try {
    const relFile = toRelativePath(cwd, filePath);

    const coords = await lspManager.findSymbolCoordinates(filePath, symbolName, line);
    if (coords.length === 0) {
      const message = `Symbol '${symbolName}' not found in file ${relFile}.`;
      return { text: message, structured: empty(message) };
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
      const message = "Rename failed or returned no changes from LSP server.";
      return { text: message, structured: empty(message) };
    }

    // Apply edits to disk automatically
    const stats = await lspManager.applyWorkspaceEdit(workspaceEdit);

    const changes: FileChange[] = Object.entries(stats).map(([file, stat]) => ({
      file: toRelativePath(cwd, file),
      count: stat.count,
      lines: stat.lines,
    }));
    const totalReplacements = changes.reduce((sum, change) => sum + change.count, 0);

    const structured: LspPage<FileChange> = {
      ...paginate(changes, 1, changes.length),
      totalReplacements,
      renamedFrom: originalName,
      renamedTo: newName,
    };

    if (changes.length === 0) {
      return { text: `Renamed "${originalName}" to "${newName}". No files modified.`, structured };
    }

    const filesCount = changes.length;
    const filesLabel = filesCount === 1 ? "file" : "files";
    const totalReplacementsLabel = totalReplacements === 1 ? "replacement" : "replacements";

    const changesLines = changes.map((change) => {
      const countLabel = change.count === 1 ? "replacement" : "replacements";
      const lineLabel = change.lines.length === 1 ? "line" : "lines";
      return `${change.file}: ${change.count} ${countLabel} on ${lineLabel} ${change.lines.join(", ")}`;
    });

    const summary = `Renamed "${originalName}" to "${newName}". ${totalReplacements} ${totalReplacementsLabel} across ${filesCount} ${filesLabel}:\n${changesLines.join("\n")}`;

    return { text: summary, structured };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return failure(`Error renaming symbol: ${error.message}`, error.message);
  }
}

/**
 * Request code actions for a file (quick fixes, format, organize imports, refactors) and apply
 * the selected one to disk.
 *
 * `kind` selects by LSP code-action kind (exact or parent, e.g. `source.fixAll.biome`,
 * `source.organizeImports`, `quickfix`); `title` selects by a case-insensitive substring of
 * the action title. When several actions match and neither disambiguates, the candidates are
 * listed instead of guessing, so the caller can narrow the request.
 */
export async function applyCodeAction(
  lspManager: LspClientManager,
  cwd: string,
  filePath: string,
  kind?: string,
  line?: number,
  title?: string,
): Promise<ToolResult<FileChange>> {
  try {
    const relFile = toRelativePath(cwd, filePath);
    const actions = await lspManager.requestCodeActions(filePath, kind, line);

    if (actions.length === 0) {
      const scope = kind ? `kind "${kind}"` : "any kind";
      const message = `No code action (${scope}) available for ${relFile}.`;
      return { text: message, structured: empty(message) };
    }

    const described = actions.map((action) => {
      const actionKind = "kind" in action && action.kind ? action.kind : "command";
      const applicable = "edit" in action && action.edit ? "" : " (needs server-side execution)";
      return `${actionKind} | ${action.title}${applicable}`;
    });

    const candidates = title
      ? actions.filter((action) => action.title.toLowerCase().includes(title.toLowerCase()))
      : actions;

    if (candidates.length === 0) {
      const message = `No code action of ${relFile} matches title "${title}". Available:
${described.join("\n")}`;
      return { text: message, structured: { ...empty(message), available: described } };
    }

    const preferred = candidates.filter((action) => (action as LSPCodeAction).isPreferred);
    const selected =
      preferred.length === 1 ? preferred[0] : candidates.length === 1 ? candidates[0] : undefined;

    if (!selected) {
      const message = `${candidates.length} code actions match for ${relFile}. Pass kind or title to choose one:
${described.join("\n")}`;
      return { text: message, structured: { ...empty(message), available: described } };
    }

    const selectedKind = "kind" in selected && selected.kind ? selected.kind : "command";
    const edit = "edit" in selected ? selected.edit : undefined;

    if (!edit) {
      const message = `Code action "${selected.title}" (${selectedKind}) must be executed on the server and is not supported.`;
      return {
        text: message,
        structured: {
          ...empty(message),
          available: described,
          actionTitle: selected.title,
          appliedKind: selectedKind,
        },
      };
    }

    const stats = await lspManager.applyWorkspaceEdit(edit);
    const changes: FileChange[] = Object.entries(stats).map(([file, stat]) => ({
      file: toRelativePath(cwd, file),
      count: stat.count,
      lines: stat.lines,
    }));
    const totalReplacements = changes.reduce((sum, change) => sum + change.count, 0);

    const structured: LspPage<FileChange> = {
      ...paginate(changes, 1, changes.length),
      totalReplacements,
      actionTitle: selected.title,
      appliedKind: selectedKind,
    };

    if (changes.length === 0) {
      const message = `Applied "${selected.title}" (${selectedKind}). No files modified.`;
      return { text: message, structured };
    }

    const changesLines = changes.map((change) => {
      const countLabel = change.count === 1 ? "edit" : "edits";
      return `${change.file}: ${change.count} ${countLabel} on line(s) ${change.lines.join(", ")}`;
    });
    const summary = `Applied "${selected.title}" (${selectedKind}): ${totalReplacements} edit(s) in ${changes.length} file(s):
${changesLines.join("\n")}`;

    return { text: summary, structured };
  } catch (err) {
    const error = err instanceof Error ? err : new Error(String(err));
    return failure(`Error applying code action: ${error.message}`, error.message);
  }
}
