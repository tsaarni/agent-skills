// Registers extension tools to query the language server.
import * as fsSync from "node:fs";
import * as fs from "node:fs/promises";
import { dirname, isAbsolute, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import type { LspClientManager, SymbolInfo } from "./manager.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

function loadConfigSync() {
  const pathsToTry = [join(__dirname, "../lsp-config.json"), join(__dirname, "lsp-config.json")];
  for (const p of pathsToTry) {
    try {
      const data = fsSync.readFileSync(p, "utf8");
      return JSON.parse(data);
    } catch {
      // Continue
    }
  }
  return null;
}

const configSync = loadConfigSync();
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

      try {
        const relFile = toRelativePath(ctx.cwd, params.filePath);

        const coords = await lspManager.findSymbolCoordinates(
          params.filePath,
          params.symbolName,
          params.line,
        );
        if (coords.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `Symbol '${params.symbolName}' not found in file ${relFile}.`,
              },
            ],
            details: {},
          };
        }

        const results: unknown[] = [];
        const outputLines: string[] = [];

        // Check up to 5 occurrences to keep the response length and API call time reasonable
        for (const coord of coords.slice(0, 5)) {
          const [hoverText, locations] = await Promise.all([
            lspManager
              .getHover(params.filePath, coord.line, coord.character)
              .then(compactHoverText)
              .catch((err) => `No doc: ${err.message}`),
            lspManager
              .getDefinition(params.filePath, coord.line, coord.character)
              .catch(() => null),
          ]);

          results.push({ coord, hoverText, locations });

          let block = `Match at line ${coord.line}, col ${coord.character}:\n`;
          block += `${hoverText.trim()}\n\n`;

          if (locations && locations.length > 0) {
            for (const loc of locations) {
              const snippet = await getCodeSnippet(ctx.cwd, loc.filePath, loc.line);
              const relPath = toRelativePath(ctx.cwd, loc.filePath);
              block += `Definition: ${relPath}:${loc.line}\n`;
              if (snippet) {
                const lang = getMarkdownLanguage(loc.filePath);
                block += `\`\`\`${lang}\n${snippet}\n\`\`\`\n\n`;
              }
            }
          } else {
            // If no definition, try to show the snippet at the reference itself
            const snippet = await getCodeSnippet(ctx.cwd, params.filePath, coord.line);
            if (snippet) {
              const lang = getMarkdownLanguage(params.filePath);
              block += `Code at reference:\n\`\`\`${lang}\n${snippet}\n\`\`\`\n\n`;
            }
          }
          outputLines.push(block.trim());
        }

        return {
          content: [{ type: "text", text: outputLines.join("\n\n---\n\n") }],
          details: { results },
        };
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        return {
          content: [{ type: "text", text: `Error fetching symbol information: ${error.message}` }],
          isError: true,
          details: {},
        };
      }
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

      try {
        const relFile = toRelativePath(ctx.cwd, params.filePath);

        const coords = await lspManager.findSymbolCoordinates(
          params.filePath,
          params.symbolName,
          params.line,
        );
        if (coords.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `Symbol '${params.symbolName}' not found in file ${relFile}.`,
              },
            ],
            details: {},
          };
        }
        const coord = coords[0];

        const references = await lspManager.getReferences(
          params.filePath,
          coord.line,
          coord.character,
        );

        if (references.length === 0) {
          return {
            content: [{ type: "text", text: "No references found." }],
            details: {},
          };
        }

        const limit = defaultLimit;
        const offset = Math.max(1, params.offset ?? 1);
        const totalCount = references.length;
        const paginatedReferences = references.slice(offset - 1, offset - 1 + limit);
        const remainingCount = totalCount - (offset - 1 + paginatedReferences.length);

        const refStrings: string[] = [];
        for (const ref of paginatedReferences) {
          const relPath = toRelativePath(ctx.cwd, ref.filePath);
          const codeLine = await getCodeSnippet(ctx.cwd, ref.filePath, ref.line, 1);
          const snippetPart = codeLine ? `: ${codeLine.trim()}` : "";
          refStrings.push(`${relPath}:${ref.line}${snippetPart}`);
        }

        let output = refStrings.join("\n");
        if (remainingCount > 0) {
          output += `\n\nShowing matches ${offset}-${offset + paginatedReferences.length - 1} of ${totalCount}. Use offset: ${offset + limit} to get more.`;
        } else if (offset > 1 && paginatedReferences.length > 0) {
          output += `\n\nShowing matches ${offset}-${offset + paginatedReferences.length - 1} of ${totalCount}.`;
        }

        return {
          content: [{ type: "text", text: output }],
          details: { references: paginatedReferences },
        };
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        return {
          content: [{ type: "text", text: `Error finding references: ${error.message}` }],
          isError: true,
          details: {},
        };
      }
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

      try {
        if (!params.filePath && !params.query) {
          return {
            content: [
              { type: "text", text: "Error: Must provide either filePath, query, or both." },
            ],
            isError: true,
            details: {},
          };
        }

        let symbols: SymbolInfo[] = [];
        let paginatedSymbols: SymbolInfo[] = [];
        let output = "";
        const limit = defaultLimit;
        const offset = Math.max(1, params.offset ?? 1);

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

        if (params.filePath) {
          const relFile = toRelativePath(ctx.cwd, params.filePath);

          // List / search symbols within a specific file
          const fileSymbols = await lspManager.getSymbols(params.filePath);
          if (params.query) {
            const queryLower = params.query.toLowerCase().trim();
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
              content: [
                {
                  type: "text",
                  text: params.query
                    ? `No symbols matching query '${params.query}' found in file ${relFile}.`
                    : `No symbols found in file ${relFile}.`,
                },
              ],
              details: {},
            };
          }

          paginatedSymbols = symbols.slice(offset - 1, offset - 1 + limit);
          const totalCount = symbols.length;
          const remainingCount = totalCount - (offset - 1 + paginatedSymbols.length);

          const symbolStrings = paginatedSymbols.map((sym) => {
            const detailStr = sym.detail ? ` (${sym.detail})` : "";
            return `${relFile}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
          });
          output = symbolStrings.join("\n");
          if (remainingCount > 0) {
            output += `\n\nShowing matches ${offset}-${offset + paginatedSymbols.length - 1} of ${totalCount} symbols in this file. Use offset: ${offset + limit} to get more.`;
          } else if (offset > 1 && paginatedSymbols.length > 0) {
            output += `\n\nShowing matches ${offset}-${offset + paginatedSymbols.length - 1} of ${totalCount} symbols in this file.`;
          }
        } else if (params.query) {
          // Search workspace symbols
          const queryLower = params.query.toLowerCase().trim();
          const targetKinds = kindKeywords[queryLower];

          if (targetKinds) {
            // Retrieve symbols matching the keyword query itself, and filter by kind.
            // Avoid querying with empty string ("") as it hangs/fails on large workspaces.
            const querySymbols = await lspManager.getWorkspaceSymbols(params.query);
            symbols = querySymbols.filter((sym) => targetKinds.includes(sym.kind));
          } else {
            symbols = await lspManager.getWorkspaceSymbols(params.query);
          }

          if (symbols.length === 0) {
            return {
              content: [
                { type: "text", text: `No symbols matching query '${params.query}' found.` },
              ],
              details: {},
            };
          }

          paginatedSymbols = symbols.slice(offset - 1, offset - 1 + limit);
          const totalCount = symbols.length;
          const remainingCount = totalCount - (offset - 1 + paginatedSymbols.length);

          const symbolStrings = paginatedSymbols.map((sym) => {
            const relPath = toRelativePath(ctx.cwd, sym.filePath, "unknown");
            const detailStr = sym.detail ? ` (${sym.detail})` : "";
            return `${relPath}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
          });
          output = symbolStrings.join("\n");
          if (remainingCount > 0) {
            output += `\n\nShowing matches ${offset}-${offset + paginatedSymbols.length - 1} of ${totalCount}. Use offset: ${offset + limit} to get more.`;
          } else if (offset > 1 && paginatedSymbols.length > 0) {
            output += `\n\nShowing matches ${offset}-${offset + paginatedSymbols.length - 1} of ${totalCount}.`;
          }
        }

        return {
          content: [{ type: "text", text: output }],
          details: { symbols: paginatedSymbols },
        };
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        return {
          content: [{ type: "text", text: `Error searching symbols: ${error.message}` }],
          isError: true,
          details: {},
        };
      }
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

      try {
        await lspManager.triggerWorkspaceDiagnostics(params.filePath);
        if (params.filePath) {
          await lspManager.syncFile(params.filePath);
          const pulled = await lspManager.pullDiagnostics(params.filePath);
          if (!pulled) {
            await lspManager.waitForDiagnostics(params.filePath);
          }
        } else {
          // Wait a short duration for the background diagnostics compilation to process
          await new Promise((resolve) => setTimeout(resolve, 2000));
        }

        const diagMap = lspManager.getDiagnostics(params.filePath);

        const diagLines: string[] = [];
        for (const [relPath, list] of Object.entries(diagMap)) {
          if (list.length === 0) continue;
          const relFile = toRelativePath(ctx.cwd, relPath);
          for (const d of list) {
            // Filter out Info and Hint diagnostics by default to keep signal high
            if (d.severity === "Information" || d.severity === "Hint") {
              continue;
            }
            let block = `${relFile}:${d.line}: ${d.severity}: ${d.message}`;
            const codeLine = await getCodeSnippet(ctx.cwd, relPath, d.line, 1);
            if (codeLine) {
              block += `\n  > ${codeLine.trim()}`;
            }
            diagLines.push(block);
          }
        }

        if (diagLines.length === 0) {
          const target = toRelativePath(ctx.cwd, params.filePath);
          const scope = target ? `file: ${target}` : "workspace";
          return {
            content: [{ type: "text", text: `No diagnostics (clean code!) for ${scope}.` }],
            details: {},
          };
        }

        const limit = defaultLimit;
        const offset = Math.max(1, params.offset ?? 1);
        const totalCount = diagLines.length;
        const paginatedDiags = diagLines.slice(offset - 1, offset - 1 + limit);
        const remainingCount = totalCount - (offset - 1 + paginatedDiags.length);

        let output = paginatedDiags.join("\n");
        if (remainingCount > 0) {
          output += `\n\nShowing matches ${offset}-${offset + paginatedDiags.length - 1} of ${totalCount}. Use offset: ${offset + limit} to get more.`;
        } else if (offset > 1 && paginatedDiags.length > 0) {
          output += `\n\nShowing matches ${offset}-${offset + paginatedDiags.length - 1} of ${totalCount}.`;
        }

        return {
          content: [{ type: "text", text: output }],
          details: { diagnostics: diagMap },
        };
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        return {
          content: [{ type: "text", text: `Error fetching diagnostics: ${error.message}` }],
          isError: true,
          details: {},
        };
      }
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

      try {
        const relFile = toRelativePath(ctx.cwd, params.filePath);

        const coords = await lspManager.findSymbolCoordinates(
          params.filePath,
          params.symbolName,
          params.line,
        );
        if (coords.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `Symbol '${params.symbolName}' not found in file ${relFile}.`,
              },
            ],
            details: {},
          };
        }

        const coord = coords[0];
        const originalName = params.symbolName;
        const workspaceEdit = await lspManager.renameSymbol(
          params.filePath,
          coord.line,
          coord.character,
          params.newName,
        );

        if (!workspaceEdit) {
          return {
            content: [
              { type: "text", text: "Rename failed or returned no changes from LSP server." },
            ],
            details: {},
          };
        }

        // Apply edits to disk automatically
        const stats = await lspManager.applyWorkspaceEdit(workspaceEdit);

        const modifiedFiles = Object.entries(stats);
        if (modifiedFiles.length === 0) {
          return {
            content: [
              {
                type: "text",
                text: `Renamed "${originalName}" to "${params.newName}". No files modified.`,
              },
            ],
            details: { stats, workspaceEdit },
          };
        }

        const totalReplacements = modifiedFiles.reduce((sum, [_, stat]) => sum + stat.count, 0);
        const filesCount = modifiedFiles.length;

        const filesLabel = filesCount === 1 ? "file" : "files";
        const totalReplacementsLabel = totalReplacements === 1 ? "replacement" : "replacements";

        const changesLines = modifiedFiles.map(([file, stat]) => {
          const relFile = toRelativePath(ctx.cwd, file);
          const countLabel = stat.count === 1 ? "replacement" : "replacements";
          const lineLabel = stat.lines.length === 1 ? "line" : "lines";
          return `${relFile}: ${stat.count} ${countLabel} on ${lineLabel} ${stat.lines.join(", ")}`;
        });

        const summary = `Renamed "${originalName}" to "${params.newName}". ${totalReplacements} ${totalReplacementsLabel} across ${filesCount} ${filesLabel}:\n${changesLines.join("\n")}`;

        return {
          content: [{ type: "text", text: summary }],
          details: { stats, workspaceEdit },
        };
      } catch (err) {
        const error = err instanceof Error ? err : new Error(String(err));
        return {
          content: [{ type: "text", text: `Error renaming symbol: ${error.message}` }],
          isError: true,
          details: {},
        };
      }
    },
  });
}
