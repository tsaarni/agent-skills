import assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { LspClientManager } from "../src/manager.js";
import {
  findReferences,
  getDiagnostics,
  getSymbolInfo,
  renameSymbol,
  searchSymbols,
} from "../src/tools.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Load lsp-config.json
const configPath = path.resolve(__dirname, "../lsp-config.json");
const configData = await fs.readFile(configPath, "utf8");
const config = JSON.parse(configData);

interface TestWorkspace {
  tempDir: string;
  manager: LspClientManager;
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
  lang: "go" | "ts" | "cpp",
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
  await manager.start(serverCmd, serverArgs);

  for (const name of Object.keys(files)) {
    await manager.syncFile(name);
  }

  await pollUntilReady(manager, mainFile);

  return { tempDir, manager };
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
    const result = await getSymbolInfo(ws.manager, ws.tempDir, "main.go", "Add", 6);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("func Add(a int, b int) int"));
    assert.ok(result.text.includes("main.go:6"));
  });

  test("lsp_find_references", async () => {
    const result = await findReferences(ws.manager, ws.tempDir, "main.go", "Add", 6);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("main.go:6"));
    assert.ok(result.text.includes("main.go:11"));
  });

  test("lsp_search_symbols", async () => {
    // Search in main.go
    const resultFile = await searchSymbols(ws.manager, ws.tempDir, "main.go");
    assert.equal(resultFile.isError, undefined);
    assert.ok(resultFile.text.includes("Function: Add"));
    assert.ok(resultFile.text.includes("Function: main"));

    // Search workspace
    const resultQuery = await searchSymbols(ws.manager, ws.tempDir, undefined, "Add");
    assert.equal(resultQuery.isError, undefined);
    assert.ok(resultQuery.text.includes("Add"));
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

    // Call getDiagnostics
    const result = await getDiagnostics(ws.manager, ws.tempDir, "main.go");

    assert.equal(result.isError, undefined);
    assert.ok(
      result.text.includes("not enough arguments") ||
        result.text.includes("too few arguments") ||
        result.text.includes("multiple-value"),
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
    await manager.start("gopls", []);

    // Call getDiagnostics workspace-wide without syncing files first
    const result = await getDiagnostics(manager, tempDir);

    assert.equal(result.isError, undefined);
    assert.ok(
      result.text.includes("main.go"),
      "Output should report startup error in main.go without manual syncFile",
    );

    await manager.stop();
    await fs.rm(tempDir, { recursive: true, force: true });
  });

  test("lsp_rename_symbol", async () => {
    const result = await renameSymbol(ws.manager, ws.tempDir, "main.go", "Add", "Sum", 6);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("Renamed"));
    assert.ok(result.text.includes("Sum"));

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
    const result = await getSymbolInfo(ws.manager, ws.tempDir, "main.ts", "multiply", 3);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("multiply"));
    assert.ok(result.text.includes("math.ts:1"));
  });

  test("lsp_find_references", async () => {
    const result = await findReferences(ws.manager, ws.tempDir, "math.ts", "multiply", 1);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("math.ts:1"));
    assert.ok(result.text.includes("main.ts:1"));
    assert.ok(result.text.includes("main.ts:3"));
  });

  test("lsp_search_symbols", async () => {
    // Search in math.ts
    const resultFile = await searchSymbols(ws.manager, ws.tempDir, "math.ts");
    assert.equal(resultFile.isError, undefined);
    assert.ok(resultFile.text.includes("Function: multiply"));

    // Search workspace
    const resultQuery = await searchSymbols(ws.manager, ws.tempDir, undefined, "multiply");
    assert.equal(resultQuery.isError, undefined);
    assert.ok(resultQuery.text.includes("multiply"));
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

    // Call getDiagnostics
    const result = await getDiagnostics(ws.manager, ws.tempDir, "main.ts");

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("Argument of type") || result.text.includes("not assignable"));

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
    await manager.start("typescript-language-server", ["--stdio"]);

    // Call getDiagnostics workspace-wide without syncing files first
    const result = await getDiagnostics(manager, tempDir);

    assert.equal(result.isError, undefined);
    assert.ok(
      result.text.includes("main.ts"),
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

    const result = await renameSymbol(ws.manager, ws.tempDir, "main.ts", "res", "result", 2);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("Renamed"));
    assert.ok(result.text.includes("result"));

    // Verify main.ts was updated
    const mainContent = await fs.readFile(path.join(ws.tempDir, "main.ts"), "utf8");
    assert.ok(mainContent.includes("const result ="));
    assert.ok(mainContent.includes("return result + 5;"));
    assert.ok(!/\bres\b/.test(mainContent));
  });
});

describe("LSP Tools for C/C++", { timeout: 30000 }, () => {
  let ws: TestWorkspace;

  before(async () => {
    ws = await setupWorkspace(
      "cpp",
      {
        "compile_flags.txt": "-std=c++17\n-I.\n",
        "math.h": `int multiply(int a, int b);
`,
        "math.cpp": `#include "math.h"
int multiply(int a, int b) {
  return a * b;
}
`,
        "main.cpp": `#include "math.h"
#include <iostream>

int main() {
  int res = multiply(2, 5);
  std::cout << res << std::endl;
  return 0;
}
`,
      },
      "clangd",
      [],
      "main.cpp",
    );
  });

  after(async () => {
    await teardownWorkspace(ws);
  });

  test("lsp_get_symbol_info", async () => {
    const result = await getSymbolInfo(ws.manager, ws.tempDir, "main.cpp", "multiply", 5);

    assert.equal(result.isError, undefined);
    const text = result.text;
    assert.ok(text.includes("multiply"));
    assert.ok(
      text.includes("math.cpp:2") || text.includes("math.h:1"),
      `Expected symbol info to point to math.cpp:2 or math.h:1, but got:\n${text}`,
    );
  });

  test("lsp_find_references", async () => {
    const result = await findReferences(ws.manager, ws.tempDir, "math.h", "multiply", 1);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("math.h:1"));
    assert.ok(result.text.includes("math.cpp:2"));
    assert.ok(result.text.includes("main.cpp:5"));
  });

  test("lsp_search_symbols", async () => {
    // Search in math.cpp
    const resultFile = await searchSymbols(ws.manager, ws.tempDir, "math.cpp");
    assert.equal(resultFile.isError, undefined);
    assert.ok(resultFile.text.includes("multiply") || resultFile.text.includes("Function"));

    // Search workspace
    const resultQuery = await searchSymbols(ws.manager, ws.tempDir, undefined, "multiply");
    assert.equal(resultQuery.isError, undefined);
    assert.ok(resultQuery.text.includes("multiply"));
  });

  test("lsp_get_diagnostics", async () => {
    // Write invalid content to main.cpp on disk
    const badCpp = `#include "math.h"
#include <iostream>

int main() {
  int res = multiply(2, "hello");
  std::cout << res << std::endl;
  return 0;
}
`;
    const diagPromise = ws.manager.waitForDiagnostics("main.cpp", 5000);
    await fs.writeFile(path.join(ws.tempDir, "main.cpp"), badCpp, "utf8");
    await ws.manager.syncFile("main.cpp");
    await diagPromise;

    // Call getDiagnostics
    const result = await getDiagnostics(ws.manager, ws.tempDir, "main.cpp");

    assert.equal(result.isError, undefined);
    const text = result.text.toLowerCase();
    assert.ok(
      text.includes("conversion") ||
        text.includes("cannot initialize") ||
        text.includes("no matching function") ||
        text.includes("invalid") ||
        text.includes("type") ||
        text.includes("parameter") ||
        text.includes("viable"),
      `Expected diagnostic error about type mismatch/invalid argument, but got:\n${result.text}`,
    );

    // Restore clean file on disk
    const cleanCpp = `#include "math.h"
#include <iostream>

int main() {
  int res = multiply(2, 5);
  std::cout << res << std::endl;
  return 0;
}
`;
    const restorePromise = ws.manager.waitForDiagnostics("main.cpp", 5000);
    await fs.writeFile(path.join(ws.tempDir, "main.cpp"), cleanCpp, "utf8");
    await ws.manager.syncFile("main.cpp");
    await restorePromise;
  });

  test("lsp_rename_symbol", async () => {
    // Write clean self-contained content to main.cpp
    const simpleCpp = `int calculate() {
  int res = 10;
  return res + 5;
}
`;
    await fs.writeFile(path.join(ws.tempDir, "main.cpp"), simpleCpp, "utf8");
    await ws.manager.syncFile("main.cpp");
    await pollUntilReady(ws.manager, "main.cpp");

    const result = await renameSymbol(ws.manager, ws.tempDir, "main.cpp", "res", "result", 2);

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("Renamed"));
    assert.ok(result.text.includes("result"));

    // Verify main.cpp was updated
    const mainContent = await fs.readFile(path.join(ws.tempDir, "main.cpp"), "utf8");
    assert.ok(mainContent.includes("int result ="));
    assert.ok(mainContent.includes("return result + 5;"));
    assert.ok(!/\bres\b/.test(mainContent));
  });
});
