import assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { LspClientManager } from "../src/manager.js";
import { registerLspTools } from "../src/tools.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Load lsp-config.json
const configPath = path.resolve(__dirname, "../lsp-config.json");
const configData = await fs.readFile(configPath, "utf8");
const config = JSON.parse(configData);

interface TestWorkspace {
  tempDir: string;
  manager: LspClientManager;
  // biome-ignore lint/suspicious/noExplicitAny: Mocking tools Map
  tools: Map<string, any>;
}

async function pollUntilReady(
  manager: LspClientManager,
  filePath: string,
  timeoutMs = 8000,
): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const symbols = await manager.getSymbols(filePath);
      if (symbols && symbols.length > 0) {
        return;
      }
    } catch {
      // ignore errors during initialization
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`LSP server not ready for ${filePath} after ${timeoutMs}ms`);
}

async function setupWorkspace(
  lang: "go" | "ts",
  files: Record<string, string>,
  serverCmd: string,
  serverArgs: string[],
  mainFile: string,
): Promise<TestWorkspace> {
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), `pi-lsp-${lang}-test-`));

  for (const [name, content] of Object.entries(files)) {
    await fs.writeFile(path.join(tempDir, name), content, "utf8");
  }

  const manager = new LspClientManager(tempDir, config);
  // biome-ignore lint/suspicious/noExplicitAny: Mocking tools Map
  const tools = new Map<string, any>();
  const mockPi = {
    // biome-ignore lint/suspicious/noExplicitAny: Mocking tool parameter
    registerTool(tool: any) {
      tools.set(tool.name, tool);
    },
  };
  // biome-ignore lint/suspicious/noExplicitAny: Mocking ExtensionAPI
  registerLspTools(mockPi as any, () => manager);

  await manager.start(serverCmd, serverArgs);

  for (const name of Object.keys(files)) {
    await manager.syncFile(name);
  }

  await pollUntilReady(manager, mainFile);

  return { tempDir, manager, tools };
}

async function teardownWorkspace(workspace: TestWorkspace) {
  if (workspace.manager) {
    await workspace.manager.stop();
  }
  if (workspace.tempDir) {
    await fs.rm(workspace.tempDir, { recursive: true, force: true });
  }
}

describe("LSP Tools for Go", { timeout: 30000 }, () => {
  let ws: TestWorkspace;

  before(async () => {
    ws = await setupWorkspace(
      "go",
      {
        "go.mod": "module lspcheck\n\ngo 1.21\n",
        "main.go": `package main

import "fmt"

// Add computes the sum of two integers.
func Add(a int, b int) int {
	return a + b
}

func main() {
	result := Add(3, 4)
	fmt.Println(result)
}
`,
      },
      "gopls",
      [],
      "main.go",
    );
  });

  after(async () => {
    await teardownWorkspace(ws);
  });

  test("lsp_get_symbol_info", async () => {
    const tool = ws.tools.get("lsp_get_symbol_info");
    const result = await tool.execute(
      "call-1",
      { filePath: "main.go", symbolName: "Add", line: 6 },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.content[0].text.includes("func Add(a int, b int) int"));
    assert.ok(result.content[0].text.includes("main.go:6"));
  });

  test("lsp_find_references", async () => {
    const tool = ws.tools.get("lsp_find_references");
    const result = await tool.execute(
      "call-2",
      { filePath: "main.go", symbolName: "Add", line: 6 },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.content[0].text.includes("main.go:6"));
    assert.ok(result.content[0].text.includes("main.go:11"));
  });

  test("lsp_search_symbols", async () => {
    const tool = ws.tools.get("lsp_search_symbols");
    // Search in main.go
    const resultFile = await tool.execute(
      "call-3a",
      { filePath: "main.go" },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );
    assert.equal(resultFile.isError, undefined);
    assert.ok(resultFile.content[0].text.includes("Function: Add"));
    assert.ok(resultFile.content[0].text.includes("Function: main"));

    // Search workspace
    const resultQuery = await tool.execute(
      "call-3b",
      { query: "Add" },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );
    assert.equal(resultQuery.isError, undefined);
    assert.ok(resultQuery.content[0].text.includes("Add"));
  });

  test("lsp_get_diagnostics", async () => {
    // Write invalid content to main.go (call Add with 1 arg)
    const badGo = `package main

import "fmt"

func Add(a int, b int) int {
	return a + b
}

func main() {
	result := Add(3)
	fmt.Println(result)
}
`;
    // Register diagnostics resolver before syncing to capture the event
    const diagPromise = ws.manager.waitForDiagnostics("main.go", 5000);
    await fs.writeFile(path.join(ws.tempDir, "main.go"), badGo, "utf8");
    await ws.manager.syncFile("main.go");
    await diagPromise;

    // Call lsp_get_diagnostics
    const tool = ws.tools.get("lsp_get_diagnostics");
    const result = await tool.execute(
      "call-4",
      { filePath: "main.go" },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(
      result.content[0].text.includes("not enough arguments") ||
        result.content[0].text.includes("too few arguments") ||
        result.content[0].text.includes("multiple-value"),
    );

    // Restore clean file on disk
    const cleanGo = `package main

import "fmt"

// Add computes the sum of two integers.
func Add(a int, b int) int {
	return a + b
}

func main() {
	result := Add(3, 4)
	fmt.Println(result)
}
`;
    const restorePromise = ws.manager.waitForDiagnostics("main.go", 5000);
    await fs.writeFile(path.join(ws.tempDir, "main.go"), cleanGo, "utf8");
    await ws.manager.syncFile("main.go");
    await restorePromise;
  });

  test("lsp_diagnostics_on_startup_error", async () => {
    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "pi-lsp-go-startup-diag-"));
    await fs.writeFile(path.join(tempDir, "go.mod"), "module lspcheck\n\ngo 1.21\n", "utf8");

    const badGo = `package main
func main() {
	result := Add(3
}
`;
    await fs.writeFile(path.join(tempDir, "main.go"), badGo, "utf8");

    const manager = new LspClientManager(tempDir, config);
    // biome-ignore lint/suspicious/noExplicitAny: Mocking tools Map
    const tools = new Map<string, any>();
    const mockPi = {
      // biome-ignore lint/suspicious/noExplicitAny: Mocking tool parameter
      registerTool(tool: any) {
        tools.set(tool.name, tool);
      },
    };
    // biome-ignore lint/suspicious/noExplicitAny: Mocking ExtensionAPI
    registerLspTools(mockPi as any, () => manager);

    await manager.start("gopls", []);

    // Call lsp_get_diagnostics workspace-wide without syncing files first
    const tool = tools.get("lsp_get_diagnostics");
    const result = await tool.execute(
      "call-startup-diag",
      {}, // no filePath
      new AbortController().signal,
      () => {},
      { cwd: tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(
      result.content[0].text.includes("main.go"),
      "Output should report startup error in main.go without manual syncFile",
    );

    await manager.stop();
    await fs.rm(tempDir, { recursive: true, force: true });
  });

  test("lsp_rename_symbol", async () => {
    const tool = ws.tools.get("lsp_rename_symbol");
    const result = await tool.execute(
      "call-5",
      { filePath: "main.go", symbolName: "Add", newName: "Sum", line: 6 },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.content[0].text.includes("Renamed"));
    assert.ok(result.content[0].text.includes("Sum"));

    // Verify file content was updated on disk
    const content = await fs.readFile(path.join(ws.tempDir, "main.go"), "utf8");
    assert.ok(content.includes("func Sum("));
    assert.ok(content.includes("Sum(3, 4)"));
    assert.ok(!content.includes("func Add("));
  });
});

describe("LSP Tools for TypeScript", { timeout: 30000 }, () => {
  let ws: TestWorkspace;

  before(async () => {
    ws = await setupWorkspace(
      "ts",
      {
        "package.json": JSON.stringify({ name: "ts-test", private: true, type: "module" }),
        "tsconfig.json": JSON.stringify({
          compilerOptions: {
            target: "ES2022",
            module: "CommonJS",
            moduleResolution: "Node",
            strict: true,
            skipLibCheck: true,
          },
          exclude: ["node_modules"],
        }),
        "math.ts": `export function multiply(a: number, b: number): number {
  return a * b;
}
`,
        "main.ts": `import { multiply } from "./math";

const res = multiply(2, 5);
export { res };
`,
      },
      "typescript-language-server",
      ["--stdio"],
      "main.ts",
    );
  });

  after(async () => {
    await teardownWorkspace(ws);
  });

  test("lsp_get_symbol_info", async () => {
    const tool = ws.tools.get("lsp_get_symbol_info");
    const result = await tool.execute(
      "call-ts-1",
      { filePath: "main.ts", symbolName: "multiply", line: 3 },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.content[0].text.includes("multiply"));
    assert.ok(result.content[0].text.includes("math.ts:1"));
  });

  test("lsp_find_references", async () => {
    const tool = ws.tools.get("lsp_find_references");
    const result = await tool.execute(
      "call-ts-2",
      { filePath: "math.ts", symbolName: "multiply", line: 1 },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.content[0].text.includes("math.ts:1"));
    assert.ok(result.content[0].text.includes("main.ts:1"));
    assert.ok(result.content[0].text.includes("main.ts:3"));
  });

  test("lsp_search_symbols", async () => {
    const tool = ws.tools.get("lsp_search_symbols");
    // Search in math.ts
    const resultFile = await tool.execute(
      "call-ts-3a",
      { filePath: "math.ts" },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );
    assert.equal(resultFile.isError, undefined);
    assert.ok(resultFile.content[0].text.includes("Function: multiply"));

    // Search workspace
    const resultQuery = await tool.execute(
      "call-ts-3b",
      { query: "multiply" },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );
    assert.equal(resultQuery.isError, undefined);
    assert.ok(resultQuery.content[0].text.includes("multiply"));
  });

  test("lsp_get_diagnostics", async () => {
    // Write invalid content to main.ts on disk
    const badTs = `import { multiply } from "./math";

const res = multiply(2, "hello");
export { res };
`;
    const diagPromise = ws.manager.waitForDiagnostics("main.ts", 5000);
    await fs.writeFile(path.join(ws.tempDir, "main.ts"), badTs, "utf8");
    await ws.manager.syncFile("main.ts");
    await diagPromise;

    // Call lsp_get_diagnostics
    const tool = ws.tools.get("lsp_get_diagnostics");
    const result = await tool.execute(
      "call-ts-4",
      { filePath: "main.ts" },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(
      result.content[0].text.includes("Argument of type") ||
        result.content[0].text.includes("not assignable"),
    );

    // Restore clean file on disk
    const cleanTs = `import { multiply } from "./math";

const res = multiply(2, 5);
export { res };
`;
    const restorePromise = ws.manager.waitForDiagnostics("main.ts", 5000);
    await fs.writeFile(path.join(ws.tempDir, "main.ts"), cleanTs, "utf8");
    await ws.manager.syncFile("main.ts");
    await restorePromise;
  });

  test("lsp_diagnostics_on_startup_error", async () => {
    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "pi-lsp-ts-startup-diag-"));
    await fs.writeFile(
      path.join(tempDir, "package.json"),
      JSON.stringify({ name: "ts-test", private: true, type: "module" }),
      "utf8",
    );
    await fs.writeFile(
      path.join(tempDir, "tsconfig.json"),
      JSON.stringify({
        compilerOptions: {
          target: "ES2022",
          module: "CommonJS",
          moduleResolution: "Node",
          strict: true,
          skipLibCheck: true,
        },
        exclude: ["node_modules"],
      }),
      "utf8",
    );

    const badTs = 'const x: number = "hello";\n';
    await fs.writeFile(path.join(tempDir, "main.ts"), badTs, "utf8");

    const manager = new LspClientManager(tempDir, config);
    // biome-ignore lint/suspicious/noExplicitAny: Mocking tools Map
    const tools = new Map<string, any>();
    const mockPi = {
      // biome-ignore lint/suspicious/noExplicitAny: Mocking tool parameter
      registerTool(tool: any) {
        tools.set(tool.name, tool);
      },
    };
    // biome-ignore lint/suspicious/noExplicitAny: Mocking ExtensionAPI
    registerLspTools(mockPi as any, () => manager);

    await manager.start("typescript-language-server", ["--stdio"]);

    // Call lsp_get_diagnostics workspace-wide without syncing files first
    const tool = tools.get("lsp_get_diagnostics");
    const result = await tool.execute(
      "call-startup-diag",
      {}, // no filePath
      new AbortController().signal,
      () => {},
      { cwd: tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(
      result.content[0].text.includes("main.ts"),
      "Output should report startup error in main.ts without manual syncFile",
    );

    await manager.stop();
    await fs.rm(tempDir, { recursive: true, force: true });
  });

  test("lsp_rename_symbol", async () => {
    // Write clean self-contained content to main.ts
    const simpleTs = `function calculate() {
  const res = 10;
  return res + 5;
}
`;
    await fs.writeFile(path.join(ws.tempDir, "main.ts"), simpleTs, "utf8");
    await ws.manager.syncFile("main.ts");
    await pollUntilReady(ws.manager, "main.ts");

    const tool = ws.tools.get("lsp_rename_symbol");
    const result = await tool.execute(
      "call-ts-5",
      { filePath: "main.ts", symbolName: "res", newName: "result", line: 2 },
      new AbortController().signal,
      () => {},
      { cwd: ws.tempDir },
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.content[0].text.includes("Renamed"));
    assert.ok(result.content[0].text.includes("result"));

    // Verify main.ts was updated
    const mainContent = await fs.readFile(path.join(ws.tempDir, "main.ts"), "utf8");
    assert.ok(mainContent.includes("const result ="));
    assert.ok(mainContent.includes("return result + 5;"));
    assert.ok(!/\bres\b/.test(mainContent));
  });
});
