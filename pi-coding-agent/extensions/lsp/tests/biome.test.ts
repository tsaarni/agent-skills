import assert from "node:assert/strict";
import * as fs from "node:fs/promises";
import * as os from "node:os";
import * as path from "node:path";
import { after, before, describe, test } from "node:test";
import { fileURLToPath } from "node:url";

import { detectWorkspaceLanguage, type ServersConfig } from "../src/detector.js";
import { getLanguageId, LspClientManager, resolveServerCommand } from "../src/manager.js";
import { applyCodeAction } from "../src/tools.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const extensionDir = path.resolve(__dirname, "..");
const biomeBin = path.join(extensionDir, "node_modules", ".bin", "biome");

const shippedConfig: ServersConfig = JSON.parse(
  await fs.readFile(path.join(extensionDir, "lsp-config.json"), "utf8"),
);

async function makeWorkspace(files: string[]): Promise<string> {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "pi-lsp-biome-"));
  for (const file of files) {
    await fs.writeFile(path.join(dir, file), "\n", "utf8");
  }
  return dir;
}

describe("language detection with Biome", () => {
  const cleanups: string[] = [];

  after(async () => {
    for (const dir of cleanups) {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });

  test("biome.json alone selects biome", async () => {
    const dir = await makeWorkspace(["biome.json", "config.ts"]);
    cleanups.push(dir);

    assert.equal(await detectWorkspaceLanguage(dir, shippedConfig), "biome");
  });

  test("a TypeScript project still selects typescript", async () => {
    const dir = await makeWorkspace(["biome.json", "package.json", "tsconfig.json", "config.ts"]);
    cleanups.push(dir);

    assert.equal(await detectWorkspaceLanguage(dir, shippedConfig), "typescript");
  });

  test("priority overrides match count", async () => {
    const dir = await makeWorkspace(["biome.json", "package.json", "tsconfig.json", "config.ts"]);
    cleanups.push(dir);

    const biased: ServersConfig = {
      ...shippedConfig,
      languages: {
        ...shippedConfig.languages,
        typescript: { ...shippedConfig.languages.typescript, priority: 0 },
        biome: { ...shippedConfig.languages.biome, priority: 1 },
      },
    };

    assert.equal(await detectWorkspaceLanguage(dir, biased), "biome");
  });

  test("extension scan is used when no signature file exists", async () => {
    const dir = await makeWorkspace(["styles.css", "theme.css"]);
    cleanups.push(dir);

    assert.equal(await detectWorkspaceLanguage(dir, shippedConfig), "biome");
  });
});

describe("language ids with Biome configured", () => {
  test("biome never leaks its config key as a document language id", () => {
    const config = shippedConfig;
    assert.equal(getLanguageId("config.ts", config), "typescript");
    assert.equal(getLanguageId("config.tsx", config), "typescript");
    assert.equal(getLanguageId("config.js", config), "javascript");
    assert.equal(getLanguageId("biome.json", config), "json");
    assert.equal(getLanguageId("biome.jsonc", config), "jsonc");
    assert.equal(getLanguageId("styles.css", config), "css");
    assert.equal(getLanguageId("main.cpp", config), "cpp");
    assert.equal(getLanguageId("notes.txt", config), "plaintext");
  });
});

describe("server command resolution", () => {
  test("workspace-local node_modules/.bin wins", async () => {
    const dir = await makeWorkspace([]);
    try {
      const binDir = path.join(dir, "node_modules", ".bin");
      await fs.mkdir(binDir, { recursive: true });
      const fake = path.join(binDir, "biome");
      await fs.writeFile(fake, "#!/bin/sh\n", "utf8");
      await fs.chmod(fake, 0o755);

      assert.equal(await resolveServerCommand("biome", dir), fake);
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });

  test("falls back to PATH lookup for bare names and resolves paths", async () => {
    const dir = await makeWorkspace([]);
    try {
      assert.equal(await resolveServerCommand("gopls", dir), "gopls");
      assert.equal(await resolveServerCommand("bin/biome", dir), path.join(dir, "bin", "biome"));
    } finally {
      await fs.rm(dir, { recursive: true, force: true });
    }
  });
});

describe("Biome language server", {
  skip: !(await exists(biomeBin)) && "biome binary not installed",
}, () => {
  let workspaceDir: string;
  let manager: LspClientManager;

  before(async () => {
    workspaceDir = await fs.mkdtemp(path.join(os.tmpdir(), "pi-lsp-biome-live-"));
    await fs.writeFile(
      path.join(workspaceDir, "biome.json"),
      JSON.stringify({ linter: { enabled: true, rules: { recommended: true } } }),
      "utf8",
    );
    await fs.writeFile(
      path.join(workspaceDir, "config.ts"),
      "export const unusedValue = 1;\nconst other = 2;\n",
      "utf8",
    );

    manager = new LspClientManager(workspaceDir, shippedConfig);
    await manager.start(biomeBin, ["lsp-proxy"]);
    await manager.syncFile("config.ts");
  });

  after(async () => {
    if (manager) await manager.stop();
    if (workspaceDir) await fs.rm(workspaceDir, { recursive: true, force: true });
  });

  test("reports lint diagnostics for opened files", async () => {
    const deadline = Date.now() + 15000;
    let diagnostics = manager.getDiagnostics("config.ts");
    while (Object.keys(diagnostics).length === 0 && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      diagnostics = manager.getDiagnostics("config.ts");
    }

    const reported = Object.values(diagnostics).flat();
    assert.ok(reported.length > 0, "expected Biome to publish diagnostics");
    assert.ok(
      reported.some((d) => d.message.toLowerCase().includes("unused")),
      `expected an unused-variable diagnostic, got: ${JSON.stringify(diagnostics)}`,
    );
  });

  test("applies a quick fix selected by kind", async () => {
    // The variable must not be exported: a local, unused one is what the rule flags.
    await fs.writeFile(
      path.join(workspaceDir, "fixme.ts"),
      "export const used = 1;\nconst unusedThing = 2;\n",
      "utf8",
    );
    await manager.syncFile("fixme.ts");

    const result = await applyCodeAction(
      manager,
      workspaceDir,
      "fixme.ts",
      "quickfix.biome.correctness.noUnusedVariables",
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("Applied"), `unexpected result: ${result.text}`);
    assert.ok(result.structured.total > 0);

    const content = await fs.readFile(path.join(workspaceDir, "fixme.ts"), "utf8");
    assert.ok(
      content.includes("_unusedThing"),
      `expected Biome's quick fix to rename the variable, got: ${content}`,
    );
  });

  test("lists candidates when a parent kind is ambiguous", async () => {
    await fs.writeFile(
      path.join(workspaceDir, "ambiguous.ts"),
      "export const used = 1;\nconst anotherUnused = 2;\n",
      "utf8",
    );
    await manager.syncFile("ambiguous.ts");

    const result = await applyCodeAction(manager, workspaceDir, "ambiguous.ts", "quickfix");

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("code actions match"), `unexpected result: ${result.text}`);
    const available = result.structured.available as string[] | undefined;
    assert.ok((available?.length ?? 0) > 1, `expected several candidates, got: ${available}`);
  });

  test("applies a whole-file source action (organize imports)", async () => {
    await fs.writeFile(path.join(workspaceDir, "a.ts"), "export const a = 1;\n", "utf8");
    await fs.writeFile(path.join(workspaceDir, "b.ts"), "export const b = 2;\n", "utf8");
    await fs.writeFile(
      path.join(workspaceDir, "unsorted.ts"),
      `import { b } from "./b";
import { a } from "./a";
export const sum = a + b;
`,
      "utf8",
    );
    await manager.syncFile("unsorted.ts");

    const result = await applyCodeAction(
      manager,
      workspaceDir,
      "unsorted.ts",
      "source.organizeImports.biome",
    );

    assert.equal(result.isError, undefined);
    assert.ok(result.text.includes("Applied"), `unexpected result: ${result.text}`);

    const content = await fs.readFile(path.join(workspaceDir, "unsorted.ts"), "utf8");
    assert.ok(
      content.indexOf('from "./a"') < content.indexOf('from "./b"'),
      `expected sorted imports, got: ${content}`,
    );
  });
});

async function exists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}
