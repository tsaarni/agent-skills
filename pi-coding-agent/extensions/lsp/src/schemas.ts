/**
 * TypeBox schemas for the structured output of every LSP tool.
 *
 * This is the single source of truth for what a tool resolves to. The same schema is declared
 * to the model as `outputSchema` and returned to codemode scripts as `structuredContent`, so a
 * script reads `items` and the pagination fields instead of parsing the tool's text.
 */
import { type Static, type TObject, type TSchema, Type } from "typebox";

/** A 1-indexed position. */
export const Position = Type.Object({
  line: Type.Number({ description: "1-indexed line." }),
  character: Type.Number({ description: "1-indexed column." }),
});
export type Position = Static<typeof Position>;

/** A location the language server reported. */
export const Location = Type.Object({
  filePath: Type.String({ description: "Path as the language server reported it." }),
  line: Type.Number(),
  character: Type.Number(),
});
export type Location = Static<typeof Location>;

/** A symbol declaration. */
export const SymbolDeclaration = Type.Object({
  name: Type.String(),
  kind: Type.String({ description: "class, interface, function, method, struct, ..." }),
  line: Type.Number(),
  detail: Type.Optional(Type.String()),
  containerName: Type.Optional(Type.String()),
  filePath: Type.Optional(Type.String()),
});
export type SymbolDeclaration = Static<typeof SymbolDeclaration>;

/** One diagnostic. `file` is added so a flat page stays readable. */
export const Diagnostic = Type.Object({
  file: Type.String(),
  line: Type.Number(),
  character: Type.Number(),
  severity: Type.String({ description: "Error, Warning, Information, or Hint." }),
  message: Type.String(),
});
export type Diagnostic = Static<typeof Diagnostic>;

/** One place a queried symbol occurs, with its hover text and definitions. */
export const SymbolMatch = Type.Object({
  coord: Position,
  hoverText: Type.String({ description: "Type signature and docs, if any." }),
  locations: Type.Array(Location, { description: "Definition locations." }),
});
export type SymbolMatch = Static<typeof SymbolMatch>;

/** How one file changed during a rename. */
export const FileChange = Type.Object({
  file: Type.String(),
  count: Type.Number({ description: "Replacements in this file." }),
  lines: Type.Array(Type.Number(), { description: "Lines that changed." }),
});
export type FileChange = Static<typeof FileChange>;

/**
 * The envelope every LSP tool resolves to: a page of `items`, a status, and machine-readable
 * pagination. Scripts page with `nextOffset` and branch on `ok` instead of catching errors.
 */
export function paged<Item extends TSchema>(
  item: Item,
  extra: Record<string, TSchema> = {},
): TObject {
  return Type.Object({
    ok: Type.Boolean({ description: "False when the call failed; see error." }),
    error: Type.Optional(Type.String({ description: "Failure reason when ok is false." })),
    message: Type.Optional(
      Type.String({
        description: "Note for a successful but empty result, such as 'symbol not found'.",
      }),
    ),
    items: Type.Array(item),
    total: Type.Number({ description: "Matches before pagination." }),
    offset: Type.Number({ description: "1-based offset of the first item." }),
    limit: Type.Number(),
    hasMore: Type.Boolean(),
    nextOffset: Type.Optional(Type.Number({ description: "Offset for the next page." })),
    ...extra,
  });
}

export const SymbolInfoOutput = paged(SymbolMatch);
export const ReferencesOutput = paged(Location);
export const SymbolsOutput = paged(SymbolDeclaration);
export const DiagnosticsOutput = paged(Diagnostic);
/** A code-action request: file edits applied, or the candidates when the choice is ambiguous. */
export const CodeActionOutput = paged(FileChange, {
  totalReplacements: Type.Number({ description: "Edits applied across all files." }),
  actionTitle: Type.Optional(Type.String({ description: "Title of the applied action." })),
  appliedKind: Type.Optional(Type.String({ description: "LSP kind of the applied action." })),
  available: Type.Optional(
    Type.Array(Type.String(), {
      description: "Candidate actions as '<kind> | <title>' when no single action matched.",
    }),
  ),
});

export const RenameOutput = paged(FileChange, {
  totalReplacements: Type.Number({ description: "Replacements across all files." }),
  renamedFrom: Type.Optional(Type.String()),
  renamedTo: Type.Optional(Type.String()),
});
