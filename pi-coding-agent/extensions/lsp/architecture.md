# LSP Extension Architecture

This document describes how the LSP extension coordinates file synchronization, workspace diagnostics, and language intelligence between the Pi Coding Agent and the Language Server Protocol (LSP) server.

---

## 1. Automatic File Synchronization

To keep the LSP server updated without requiring the agent to manually sync files, the extension automatically intercepts agent tool executions.

### The Interception Hook
The extension listens to the `tool_result` event emitted by the agent runner:
* **Trigger**: Emitted after any filesystem tool (`write`, `edit`, `write_to_file`, `replace_file_content`, etc.) executes successfully.
* **Sync Pipeline**:
  1. **Path Extraction**: The hook extracts the file path from the tool parameters.
  2. **Serialization**: The sync request is queued in an **async serialization queue** (one queue per file) to prevent concurrent writes from arriving out-of-order.
  3. **In-Memory Payload Sync**: For mutations like `write` and `write_to_file`, the extension extracts the file text directly from the tool's input arguments instead of performing a disk read. This completely eliminates the read-after-write disk buffering race.
  4. **First-time Sync**: If the file is not yet tracked, the client opens it on the server using `textDocument/didOpen`.
  5. **Subsequent Syncs**: The client sends updates using `textDocument/didChange` with a monotonically incrementing version counter (`version++`).

---

## 2. Workspace Diagnostics Warming (Adapter Pattern)

Language servers are lazy by default; they only compile and report errors for files actively opened by the client. To get workspace-wide errors without opening every file, the extension implements an **Adapter Pattern** when diagnostics are requested:

* **TypeScript/JavaScript**: The extension executes the custom `typescript.tsserverRequest` workspace command with `geterrForProject`. This forces the server to compile and type-check the entire project matched by `tsconfig.json` and push diagnostics for all files automatically.
* **Go (gopls)**: Natively handled by the Go compiler (no-op). `gopls` type-checks the workspace automatically in the background on startup.
* **Other Languages**: Rely on background indexing configs passed during server initialization (e.g. Pyright workspace diagnostic mode).

---

## 3. Concrete Tool Sequences

### A. `lsp_get_symbol_info`
* **Purpose**: Retrieves type signatures, documentation, definition locations, and definition source snippets.
* **Sequence**:
  1. **Coordinate Discovery**: The client queries `textDocument/documentSymbol` to resolve the symbol name to its exact compiler-resolved line and character coordinates, bypassing regex substring errors.
  2. **LSP Queries**: Sends `textDocument/hover` and `textDocument/definition` requests to the server in parallel.
  3. **Hover Compaction**: Compiles and cleans up hover documentation, truncating long paragraphs and redundant signature lines to save token budget.
  4. **Snippet Extraction**: Reads the definition file from disk and extracts the surrounding lines of code to return to the agent.

### B. `lsp_find_references`
* **Purpose**: Locates all usages of a symbol across the workspace.
* **Sequence**:
  1. **Coordinate Discovery**: Resolves the target symbol coordinates using the document symbols index.
  2. **References Query**: Sends a `textDocument/references` request.
  3. **Grep-Style Preview**: Instead of loading full multi-line snippets from disk, the client extracts the single line of code matching each reference coordinate and formats it as `file:line: code snippet` (paginated).

### C. `lsp_search_symbols`
* **Purpose**: Outlines symbols in a single file or searches the workspace for symbols.
* **Sequence**:
  * **File Outline**: Sends `textDocument/documentSymbol` and flattens the hierarchy into an indented overview text block.
  * **Workspace Search**: Sends `workspace/symbol` for global matches.

### D. `lsp_get_diagnostics`
* **Purpose**: Retrieves active compile errors and warnings.
* **Sequence**:
  1. **Warmup**: Triggers the workspace diagnostics adapter to ensure unopened file diagnostics are compiled and populated.
  2. **Sync & Pull**: Sends the current file content to the server, then requests its diagnostics. If the server does not support pulling diagnostics directly, the client waits for the server to send them.
  3. **Filter**: Filters out `Information` and `Hint` diagnostics, leaving only `Error` and `Warning` items.
  4. **Context Injection**: Retrieves the target line of code for each diagnostic and embeds it inline below the error message.

### E. `lsp_rename_symbol`
* **Purpose**: Renames a symbol globally across all files.
* **Sequence**:
  1. **Rename Query**: Sends `textDocument/rename` with coordinates and the new name.
  2. **Apply & Sync**: Applies edits atomically to files on disk, then calls `didChange` to keep the server in sync.

---

## 4. Standard LSP Operations Reference

* **`initialize` / `initialized`**: Connects and configures server capabilities (e.g. diagnostics configurations).
* **`shutdown` / `exit`**: Stops the server process cleanly.
* **`textDocument/didOpen`**: Registers a file and sends its content to the server cache.
* **`textDocument/didChange`**: Sends document modifications incrementally.
* **`textDocument/diagnostic`**: Pulls file diagnostics synchronously (LSP 3.17).
* **`textDocument/hover`**: Requests type and documentation details at a cursor coordinate.
* **`textDocument/definition`**: Resolves declaration location for a coordinate.
* **`textDocument/references`**: Resolves all usage coordinates for a symbol.
* **`textDocument/documentSymbol`**: Returns all declarations within a file.
* **`workspace/symbol`**: Searches the global workspace index for matching names.
* **`textDocument/rename`**: Computes text edits to safely rename a symbol workspace-wide.
