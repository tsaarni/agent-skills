# LSP Server Extension for Pi Coding Agent

An LSP extension for the [Pi Coding Agent](https://github.com/earendil-works/pi-coding-agent).
Connects to local language servers and provides code intelligence (navigation, diagnostics, refactoring) to the agent.

## What It Does

1. **Detects the workspace language** by checking for config files (`tsconfig.json`, `go.mod`, `Cargo.toml`, etc.) or counting file extensions.
2. **Starts and manages** the matching language server process.
3. **Syncs file changes** to the server whenever the agent reads, writes, or edits a file.
4. **Exposes tools and commands** so the agent (and user) can navigate code, check for errors, and refactor.

## Supported Languages

Language server binaries must be in system `PATH`.

| Language | Language Server Binary | Config Files | File Extensions |
|---|---|---|---|
| TypeScript | `typescript-language-server` | `tsconfig.json`, `package.json` | `.ts`, `.tsx`, `.cts`, `.mts` |
| JavaScript | `typescript-language-server` | `jsconfig.json`, `package.json` | `.js`, `.jsx`, `.cjs`, `.mjs` |
| Python | `pyright-langserver` | `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile` | `.py` |
| Go | `gopls` | `go.mod`, `go.work` | `.go` |
| Rust | `rust-analyzer` | `Cargo.toml` | `.rs` |
| Java | `jdtls` | `pom.xml`, `build.gradle`, `build.gradle.kts`, `settings.gradle`, `settings.gradle.kts` | `.java` |

Language server commands are configurable in [`lsp-servers.json`](lsp-servers.json).

## Slash Commands

| Command | Description |
|---|---|
| `/lsp` or `/lsp status` | Show server status, config, and diagnostics summary |
| `/lsp init` | Detect language, confirm with user, save config to `.pi/lsp-project.json`, and start server |
| `/lsp start` | Start the server (requires prior `init`) |
| `/lsp stop` | Stop the running server |
| `/lsp restart` | Stop and restart the server |

On session start, the extension reads `.pi/lsp-project.json` and auto-starts the server if a previous configuration exists.

## Tools

These are registered as callable tools for the agent:

| Tool | Description |
|---|---|
| `lsp_get_symbol_info` | Type signatures, docs, definition location, and code snippet for a symbol |
| `lsp_find_references` | All usages of a symbol across the workspace |
| `lsp_search_symbols` | List symbols in a file, or search workspace symbols by name/kind |
| `lsp_get_diagnostics` | Compiler and linter errors/warnings for a file or the whole workspace |
| `lsp_rename_symbol` | Global rename across all files, auto-applies edits to disk |
