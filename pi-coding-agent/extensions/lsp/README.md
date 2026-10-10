# LSP Extension for Pi Coding Agent

An LSP extension for the [Pi Coding Agent](https://github.com/earendil-works/pi-coding-agent).
It runs a real language server next to your session and exposes it to the agent as tools, so the
model can read type signatures, find references, list symbols, check diagnostics, rename symbols
and apply code actions instead of guessing from grep.

**One language server is active per workspace.**

Pi loads the extension from `~/.pi/agent/extensions/` (here a symlink to
`pi-coding-agent/extensions/lsp` in this repository); the package declares its entry point under
`pi.extensions` in `package.json`.

## What it does

1. **Detects the workspace language** from signature files (`tsconfig.json`, `go.mod`, ...) or,
   when there is none, from a file-extension census.
2. **Starts the matching language server** as a child process, resolving the binary from the
   project-local `node_modules/.bin` first and the system `PATH` second.
3. **Keeps the server in sync** with no manual effort: after every file the agent reads, writes
   or edits, the file is forwarded to the server (`textDocument/didOpen`, then
   `textDocument/didChange`), serialised per file so writes cannot arrive out of order.
4. **Exposes tools and slash commands** for navigation, diagnostics, refactoring and code
   actions, plus `/lsp` commands to inspect and control the server.

## Quick start

1. Start pi in the workspace you want code intelligence for.
2. Run `/lsp init`. The extension detects the language, asks for confirmation and starts the
   server. The choice is saved, so later sessions in that workspace start it automatically.
3. Ask the agent for what you need — "where is `parseConfig` used?", "any errors in this
   file?", "rename this symbol" — or call the tools directly from a script.
4. `/lsp status` shows what is running, how many files are synced and the current error and
   warning counts.

If detection picks a server you do not want, or nothing is detected, write the configuration
yourself — see [Configuration](#configuration).

## How a server is chosen

Detection runs in two passes and picks **one** language:

1. **Signature files in the workspace root** (strongest signal). Every language counts how many
   of its `detection.files` exist. The language with the highest `priority` wins; among equal
   priorities the one with the most matching files wins; remaining ties follow the order in
   [`lsp-config.json`](lsp-config.json).
2. **File extensions** (fallback when no signature file matches). The tree is walked to depth 4,
   skipping `node_modules`, `.git`, `.venv`, `venv`, `dist`, `build`, `out`, `.pi`, `.gemini`,
   `.cache`, and the most common extension wins, with the same priority rule.

`priority` is an optional field on a language entry and defaults to `0`. A stock configuration
therefore behaves as "the most specific config file wins". Raise it for a language to make that
server win whenever its signature file is present — for example to prefer Biome over
`typescript-language-server`, see [Biome](#biome).

## Supported languages

| Language | Language server | `command` | Detection signals |
|---|---|---|---|
| TypeScript | `typescript-language-server` | `typescript-language-server --stdio` | `tsconfig.json`, `package.json`, `.ts/.tsx/.cts/.mts` |
| JavaScript | `typescript-language-server` | `typescript-language-server --stdio` | `jsconfig.json`, `package.json`, `.js/.jsx/.cjs/.mjs` |
| Python | `pyright-langserver` | `pyright-langserver --stdio` | `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile`, `.py` |
| Go | `gopls` | `gopls` | `go.mod`, `go.work`, `.go` |
| Rust | `rust-analyzer` | `rust-analyzer` | `Cargo.toml`, `.rs` |
| Java | `jdtls` | `jdtls` | `pom.xml`, `build.gradle(.kts)`, `settings.gradle(.kts)`, `.java` |
| C/C++ | `clangd` | `clangd` | `compile_commands.json`, `compile_flags.txt`, `CMakeLists.txt`, `.cpp/.c/.h/.hpp/.cc/.cxx/.hxx` |
| Biome (JS/TS/JSON/CSS) | `biome` | `biome lsp-proxy` | `biome.json`, `biome.jsonc`, `.json/.jsonc/.css` |

Binaries are looked up in `<workspace>/node_modules/.bin` before `PATH`, so a project-pinned
server (Biome, or a language server installed as a devDependency) is preferred over a globally
installed one. Commands, arguments, detection rules, page size and timeouts live in
[`lsp-config.json`](lsp-config.json).

The test suite in this repository exercises Go, TypeScript, C/C++ and Biome; the other entries
are configuration only.

## Configuration

On every session start the extension loads **one** configuration file, project-local first:

1. `<workspace-root>/.pi/lsp.json`
2. `~/.pi/agent/lsp-extension/--<encoded-workspace-path>--/lsp.json` (global; written by `/lsp init`)

| Field | Required | Meaning |
|---|---|---|
| `command` | yes | Server executable, a bare name (`biome`) or a path (`./bin/server`) |
| `args` | no | Arguments passed to it (default `[]`) |
| `autostart` | no | `true` starts the server on session start (alias: `initialized`) |
| `language` | no | Label for the status line; derived from `command` when omitted |

`command` and `args` are what actually run — `language` never changes behavior, it only keeps the
status line readable. Set it explicitly when one command serves several languages, or when you
point the workspace at a server that detection would not pick.

Project-local `.pi/lsp.json` (overrides the global file, and keeps the workspace
self-describing):

```json
{
  "autostart": true,
  "language": "typescript",
  "command": "typescript-language-server",
  "args": ["--stdio"]
}
```

Run a server other than the detected one — Biome in a TypeScript project:

```json
{
  "autostart": true,
  "language": "biome",
  "command": "biome",
  "args": ["lsp-proxy"]
}
```

Prefer a server globally, in every workspace, by raising its `priority` in `lsp-config.json`:

```json
"biome": { "priority": 1, "command": "biome", "args": ["lsp-proxy"], "detection": { ... } }
```

## Slash commands

| Command | Description |
|---|---|
| `/lsp` or `/lsp status` | Status line with language, pid, synced file count and error/warning totals, plus up to five files with details. Reports "not initialized" when no configuration exists. |
| `/lsp init` | Detect the language, ask for confirmation, save the choice to the **global** config, start the server and register the tools. |
| `/lsp clean` | Delete the **global** config and stop the server. A project-local `.pi/lsp.json` is left untouched. |
| `/lsp restart` | Stop and start the server using the current configuration. |

## Tools

The tools are registered as soon as a server starts, and only while it runs. If a language
server does not implement an operation, the tool reports the server's error (Biome, for example,
answers "Method not found" for everything except diagnostics and code actions).

| Tool | Parameters | Returns |
|---|---|---|
| `lsp_get_symbol_info` | `filePath`, `symbolName`, `line?` | Type signature, docs, definition location and a code snippet |
| `lsp_find_references` | `filePath`, `symbolName`, `line?`, `offset?` | Every usage across the workspace, as `file:line: code` |
| `lsp_search_symbols` | `filePath?`, `query?`, `offset?` | A file outline, or workspace symbols by name/kind |
| `lsp_get_diagnostics` | `filePath?`, `offset?` | Errors and warnings, per file or workspace-wide (Information/Hint are hidden) |
| `lsp_rename_symbol` | `filePath`, `symbolName`, `newName`, `line?` | Global rename, applied to disk; per-file edit counts |
| `lsp_apply_code_action` | `filePath`, `kind?`, `title?`, `line?` | Quick fix, format, organize imports or refactor, applied to disk |

Notes:

* Passing `line` (1-indexed) disambiguates symbols that share a name in different scopes; for
  `lsp_rename_symbol` you should supply the declaration line to avoid renaming an unrelated
  symbol.
* `lsp_apply_code_action` accepts `kind` exactly or as a parent (`quickfix` matches
  `quickfix.biome.correctness.noUnusedVariables`) and `title` as a case-insensitive substring.
  When several actions match and neither narrows it to one, the candidates are listed instead of
  guessing. Actions that must be executed by the server (`command`-carrying, as some clangd
  fixes are) are reported as unsupported.
* Results are also returned as structured data — `ok`, `items`, `total`, `offset`, `limit`,
  `hasMore`, `nextOffset` and tool-specific fields (`totalReplacements`, `appliedKind`, ...) —
  so scripts can paginate with `offset` instead of parsing prose. The default page size comes
  from `defaultLimit` in `lsp-config.json`.
* Workspace-wide `lsp_get_diagnostics` waits briefly (~2 s) for servers that compile the
  project in the background; per-file requests wait for that file's diagnostics instead.

## Biome

[Biome](https://biomejs.dev) is reached through its LSP proxy. It is usually a project
devDependency rather than a global binary:

```sh
pnpm add -D @biomejs/biome   # npm install --save-dev @biomejs/biome, or: brew install biome
```

Activation, in order of increasing scope:

1. **Detection** — a project with `biome.json` and no `package.json`/`tsconfig.json` (which score
   higher for TypeScript) selects Biome, so `/lsp init` offers `biome lsp-proxy` and saves it.
2. **Explicitly, per workspace** — put the `.pi/lsp.json` from
   [Configuration](#configuration) in the project. Overrides detection, so this is how you run
   Biome in a TypeScript project.
3. **Globally, by preference** — give `biome` a `priority` in `lsp-config.json`; it then wins in
   every workspace that has a `biome.json`.

What it provides (verified against Biome 2.5.2):

| Capability | Status |
|---|---|
| Diagnostics (syntax + lint) for files the agent opens | yes, pushed by the server |
| `lsp_apply_code_action`: `quickfix` (rename an unused variable, insert a suppression comment), `source.fixAll.biome` (all safe fixes plus formatting), `source.organizeImports.biome` (sort imports/exports), `refactor.biome` | yes |
| Hover, references, document/workspace symbols, rename, definition | no — Biome does not implement them |

Limitations and quirks:

* Navigation tools are inert while Biome runs (`lsp_get_symbol_info`, `lsp_find_references`,
  `lsp_search_symbols`, `lsp_rename_symbol`). Biome is a linter/formatter, not a type-aware
  server; this is why detection prefers `typescript-language-server` in TypeScript projects.
* Diagnostics are published per opened file only, so workspace-wide `lsp_get_diagnostics` has
  nothing to report beyond the files the agent has touched.
* Biome rejects a request range that extends past the end of the document, so whole-file
  requests are clamped to the last line.
* Biome's own `context.only` filtering is uneven: it answers `source`, `source.organizeImports`
  and `quickfix`, but returns nothing for the shortened `source.fixAll`. Use the full kind
  (`source.fixAll.biome`).

## Development

The extension is a pnpm project in this repository; `~/.pi/agent/extensions/lsp` is a symlink to
it. It requires Node 23.6 or newer.

```sh
pnpm install    # also installs the toolchain the tests need (see below)
pnpm test       # all suites, via tsx and Node's test runner
pnpm check      # Biome format + lint over the extension's own sources
```

About the test suite:

* The servers under test must be on `PATH` (`gopls`, `clangd`, `typescript-language-server`). A
  suite whose server is missing fails rather than being skipped. The Biome suites skip
  themselves when `node_modules/.bin/biome` is absent.
* Tests create throwaway workspaces in the OS temp directory.
  `typescript-language-server` resolves its compiler from the workspace, so the TypeScript
  suites symlink this package's `node_modules/typescript` into the temp workspace
  (`linkWorkspaceDependency`). Without `pnpm install` they fail with "Could not find a valid
  TypeScript installation".
* `pnpm test` runs the TypeScript sources directly and therefore needs the `tsx` devDependency
  (`pnpm add -D tsx`). Compiling with `tsc` and running the emitted JavaScript under
  `node --test` works too, but is not wired into a script.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Failed to start language server (cmd): ... ENOENT` | The binary is neither in `node_modules/.bin` nor on `PATH`. Install it, or set an explicit `command` path in the config. Startup fails within milliseconds instead of timing out. |
| `Could not find a valid TypeScript installation` | `typescript-language-server` resolves `typescript` from the workspace. Add `typescript` as a dependency of that project. |
| `Method not found` from a tool | The running server does not implement that operation — Biome, for instance, has no symbols or rename. Switch server via the config if you need navigation. |
| `LSP not initialized. Run '/lsp init' first.` | No `.pi/lsp.json` and no global config for this workspace. |
| Tools are not offered at all | They are registered when a server starts. Check `/lsp status` and the startup notification. |
| Nothing detected by `/lsp init` | No signature file and no files with known extensions. Write `.pi/lsp.json` by hand. |
| Diagnostics stay empty | Many servers only report for files that have been opened; ask the agent to read the file, or request diagnostics for that file explicitly. |

## Limitations

* One server per workspace. Running `typescript-language-server` and Biome at the same time is
  not supported; pick one, or run the other through a shell.
* Servers are started with the workspace root as working directory and a `rootUri` pointing at it;
  multi-root workspaces are not supported.
* Only `edit`-carrying code actions can be applied; server-executed commands are reported as
  unsupported.
