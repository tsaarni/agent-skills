import * as fs from "node:fs/promises";
import { isAbsolute, join, relative } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import type { LspClientManager, SymbolInfo } from "./client.js";

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
        const relFile = isAbsolute(params.filePath)
          ? relative(ctx.cwd, params.filePath)
          : params.filePath;

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
              const relPath = isAbsolute(loc.filePath)
                ? relative(ctx.cwd, loc.filePath)
                : loc.filePath;
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
        const relFile = isAbsolute(params.filePath)
          ? relative(ctx.cwd, params.filePath)
          : params.filePath;

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

        const refStrings = references.map((ref) => {
          const relPath = isAbsolute(ref.filePath) ? relative(ctx.cwd, ref.filePath) : ref.filePath;
          return `${relPath}:${ref.line}`;
        });

        return {
          content: [{ type: "text", text: refStrings.join("\n") }],
          details: { references },
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
        let output = "";

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
          const relFile = isAbsolute(params.filePath)
            ? relative(ctx.cwd, params.filePath)
            : params.filePath;

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

          const symbolStrings = symbols.map((sym) => {
            const detailStr = sym.detail ? ` (${sym.detail})` : "";
            return `${relFile}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
          });
          output = symbolStrings.join("\n");
        } else if (params.query) {
          // Search workspace symbols
          const queryLower = params.query.toLowerCase().trim();
          const targetKinds = kindKeywords[queryLower];

          if (targetKinds) {
            // If the query is a kind keyword, retrieve all symbols and filter by kind
            const allSymbols = await lspManager.getWorkspaceSymbols("");
            symbols = allSymbols.filter((sym) => targetKinds.includes(sym.kind));

            // Fallback to standard query search if empty query was not supported/returned nothing
            if (symbols.length === 0) {
              symbols = await lspManager.getWorkspaceSymbols(params.query);
            }
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

          const symbolStrings = symbols.map((sym) => {
            const relPath = sym.filePath
              ? isAbsolute(sym.filePath)
                ? relative(ctx.cwd, sym.filePath)
                : sym.filePath
              : "unknown";
            const detailStr = sym.detail ? ` (${sym.detail})` : "";
            return `${relPath}:${sym.line}: ${sym.kind}: ${sym.name}${detailStr}`;
          });
          output = symbolStrings.join("\n");
        }

        return {
          content: [{ type: "text", text: output }],
          details: { symbols },
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
        if (params.filePath) {
          await lspManager.syncFile(params.filePath);
          await lspManager.waitForDiagnostics(params.filePath);
        }

        const diagMap = lspManager.getDiagnostics(params.filePath);

        const diagLines: string[] = [];
        for (const [relPath, list] of Object.entries(diagMap)) {
          if (list.length === 0) continue;
          const relFile = isAbsolute(relPath) ? relative(ctx.cwd, relPath) : relPath;
          for (const d of list) {
            diagLines.push(`${relFile}:${d.line}: ${d.severity}: ${d.message}`);
          }
        }

        if (diagLines.length === 0) {
          const target = params.filePath
            ? isAbsolute(params.filePath)
              ? relative(ctx.cwd, params.filePath)
              : params.filePath
            : "";
          const scope = target ? `file: ${target}` : "workspace";
          return {
            content: [{ type: "text", text: `No diagnostics (clean code!) for ${scope}.` }],
            details: {},
          };
        }

        return {
          content: [{ type: "text", text: diagLines.join("\n") }],
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
      line: Type.Integer({
        description: "1-indexed line number where the target symbol is declared.",
      }),
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
        const relFile = isAbsolute(params.filePath)
          ? relative(ctx.cwd, params.filePath)
          : params.filePath;

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
          const relFile = isAbsolute(file) ? relative(ctx.cwd, file) : file;
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
