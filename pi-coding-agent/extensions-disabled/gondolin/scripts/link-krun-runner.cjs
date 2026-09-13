/**
 * Postinstall script: symlinks gondolin's krun-runner optional dependency
 * into the extension's top-level node_modules so that `require.resolve()`
 * can find it regardless of pnpm's hoisting configuration.
 *
 * The krun runner is an optional dependency of @earendil-works/gondolin.
 * pnpm does not hoist transitive optional dependencies to the top-level
 * node_modules, so this script resolves through gondolin's own require
 * context and creates the missing symlink.
 */
const { createRequire } = require("module");
const fs = require("fs");
const path = require("path");

// Resolve @earendil-works/gondolin's main entry point
const gondolinMain = require.resolve("@earendil-works/gondolin");
const gondolinRequire = createRequire(gondolinMain);

const pkgName = `@earendil-works/gondolin-krun-runner-${process.platform}-${process.arch}`;
const specifier = `${pkgName}/package.json`;

try {
  const pkgJsonPath = gondolinRequire.resolve(specifier);
  const srcDir = path.dirname(pkgJsonPath); // e.g. .../gondolin-krun-runner-darwin-arm64
  const dstDir = path.resolve(__dirname, "..", "node_modules", pkgName.replace("/", path.sep));
  const parentDir = path.dirname(dstDir);

  fs.mkdirSync(parentDir, { recursive: true });

  if (!fs.existsSync(dstDir)) {
    const relativePath = path.relative(parentDir, srcDir);
    fs.symlinkSync(relativePath, dstDir);
    console.log(`[link-krun-runner] Linked: ${dstDir}`);
  } else {
    console.log(`[link-krun-runner] Already linked: ${dstDir}`);
  }
} catch (e) {
  console.log(
    `[link-krun-runner] No krun runner for ${process.platform}-${process.arch} (this is expected on unsupported platforms)`,
  );
}