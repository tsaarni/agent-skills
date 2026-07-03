/**
 * Operation adapters that route pi's built-in tools inside the Gondolin VM.
 */

import type { VM } from "@earendil-works/gondolin";
import type {
  BashOperations,
  EditOperations,
  FindOperations,
  GrepToolDetails,
  GrepToolInput,
  LsOperations,
  ReadOperations,
  WriteOperations,
} from "@earendil-works/pi-coding-agent";
import {
  DEFAULT_MAX_BYTES,
  formatSize,
  truncateHead,
  truncateLine,
} from "@earendil-works/pi-coding-agent";
import type { CachedToolOps } from "./vfs.js";
import { detectImageMimeFromExtension, toGuestPath } from "./vfs.js";

const DEFAULT_GREP_LIMIT = 100;

export type TextToolResult<TDetails> = {
  content: Array<{ type: "text"; text: string }>;
  details: TDetails | undefined;
};

export function createGondolinReadOps(vm: VM, localCwd: string): ReadOperations {
  return {
    readFile: async (filePath) => vm.fs.readFile(toGuestPath(localCwd, filePath)),
    access: async (filePath) => {
      await vm.fs.access(toGuestPath(localCwd, filePath));
    },
    detectImageMimeType: async (filePath) => {
      const guestPath = toGuestPath(localCwd, filePath);
      return detectImageMimeFromExtension(guestPath);
    },
  };
}

export function createGondolinWriteOps(vm: VM, localCwd: string): WriteOperations {
  return {
    writeFile: async (filePath, content) => {
      await vm.fs.writeFile(toGuestPath(localCwd, filePath), content, {
        encoding: "utf8",
      });
    },
    mkdir: async (dirPath) => {
      await vm.fs.mkdir(toGuestPath(localCwd, dirPath), { recursive: true });
    },
  };
}

export function createGondolinEditOps(vm: VM, localCwd: string): EditOperations {
  const readOps = createGondolinReadOps(vm, localCwd);
  const writeOps = createGondolinWriteOps(vm, localCwd);
  return {
    readFile: readOps.readFile,
    writeFile: writeOps.writeFile,
    access: readOps.access,
  };
}

export function createGondolinLsOps(vm: VM, localCwd: string): LsOperations {
  return {
    exists: async (filePath) => {
      try {
        await vm.fs.access(toGuestPath(localCwd, filePath));
        return true;
      } catch {
        return false;
      }
    },
    stat: async (filePath) => vm.fs.stat(toGuestPath(localCwd, filePath)),
    readdir: async (dirPath) => vm.fs.listDir(toGuestPath(localCwd, dirPath)),
  };
}

export function createGondolinFindOps(vm: VM, localCwd: string): FindOperations {
  return {
    exists: async (filePath) => {
      try {
        await vm.fs.access(toGuestPath(localCwd, filePath));
        return true;
      } catch {
        return false;
      }
    },
    glob: async (pattern, cwd, options) => {
      const root = toGuestPath(localCwd, cwd);
      const proc = await vm.exec(
        [
          "fd",
          "--type",
          "f",
          "--color",
          "never",
          "--max-results",
          String(options.limit),
          pattern,
          root,
        ],
        { stdout: "pipe", stderr: "pipe" },
      );
      const stdout = proc.stdout.trim();
      if (stdout) return stdout.split("\n").slice(0, options.limit);
      return [];
    },
  };
}

export function createGondolinBashOps(vm: VM, localCwd: string, shellPath: string): BashOperations {
  return {
    exec: async (command, cwd, { onData, signal, timeout, env: _env }) => {
      if (signal?.aborted) throw new Error("aborted");
      const guestCwd = toGuestPath(localCwd, cwd);
      const controller = new AbortController();
      const onAbort = () => controller.abort();
      signal?.addEventListener("abort", onAbort, { once: true });

      let timedOut = false;
      const timer =
        timeout && timeout > 0
          ? setTimeout(() => {
              timedOut = true;
              controller.abort();
            }, timeout * 1000)
          : undefined;

      try {
        // Don't forward host env vars — the VM's login shell sources
        // /etc/profile which sets PATH, HOME, USER, etc.
        const proc = vm.exec([shellPath, "-lc", command], {
          cwd: guestCwd,
          signal: controller.signal,
          stdout: "pipe",
          stderr: "pipe",
        });
        for await (const chunk of proc.output()) onData(chunk.data);
        const result = await proc;
        return { exitCode: result.exitCode };
      } catch (error) {
        if (signal?.aborted) throw new Error("aborted");
        if (timedOut) throw new Error(`timeout:${timeout}`);
        throw error;
      } finally {
        if (timer) clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
      }
    },
  };
}

function buildGrepArgs(
  params: GrepToolInput,
  contextLines: number,
  effectiveLimit: number,
  root: string,
): string[] {
  return [
    "--no-heading",
    "--line-number",
    "--color",
    "never",
    "--max-columns",
    "4096",
    "--max-columns-preview",
    "--max-filesize",
    "1M",
    ...(params.literal ? ["--fixed-strings"] : []),
    ...(params.ignoreCase ? ["--ignore-case"] : []),
    ...(params.glob ? ["--glob", params.glob] : []),
    ...(contextLines > 0 ? ["--context", String(contextLines)] : []),
    "--max-count",
    String(effectiveLimit),
    "--",
    params.pattern,
    root,
  ];
}

function toRelativeDisplayPath(filePath: string, root: string): string {
  if (!filePath.startsWith(root)) return filePath;
  const relativePath = filePath.substring(root.length);
  return relativePath.startsWith("/") ? relativePath.substring(1) : relativePath;
}

function formatGrepOutput(stdout: string, root: string) {
  const lines = stdout.split("\n");
  const outputLines: string[] = [];
  let matchCount = 0;
  let linesTruncated = false;

  for (const line of lines) {
    const colonIdx = line.indexOf(":");
    if (colonIdx <= 0) {
      outputLines.push(line);
      continue;
    }

    const filePath = line.substring(0, colonIdx);
    const rest = line.substring(colonIdx);
    const displayPath = toRelativeDisplayPath(filePath, root);
    const { text, wasTruncated } = truncateLine(rest);
    if (wasTruncated) linesTruncated = true;
    outputLines.push(`${displayPath}${text}`);
    if (rest.startsWith(":")) matchCount++;
  }

  return {
    rawOutput: outputLines.join("\n"),
    matchCount,
    linesTruncated,
  };
}

export async function executeGondolinGrep(
  vm: VM,
  localCwd: string,
  params: GrepToolInput,
  signal?: AbortSignal,
): Promise<TextToolResult<GrepToolDetails>> {
  const root = toGuestPath(localCwd, params.path ?? ".");
  const rootStat = await vm.fs.stat(root, { signal });
  void rootStat.isDirectory();

  const contextLines = params.context && params.context > 0 ? params.context : 0;
  const effectiveLimit = Math.max(1, params.limit ?? DEFAULT_GREP_LIMIT);
  const args = buildGrepArgs(params, contextLines, effectiveLimit, root);

  const proc = await vm.exec(["rg", ...args], {
    stdout: "pipe",
    stderr: "pipe",
    signal,
  });

  const stdout = proc.stdout.trim();
  if (!stdout) {
    return {
      content: [{ type: "text", text: "No matches found" }],
      details: undefined,
    };
  }

  const { rawOutput, matchCount, linesTruncated } = formatGrepOutput(stdout, root);
  const truncation = truncateHead(rawOutput, {
    maxLines: Number.MAX_SAFE_INTEGER,
  });
  const details: GrepToolDetails = {};
  const notices: string[] = [];
  let output = truncation.content;

  if (matchCount >= effectiveLimit) {
    details.matchLimitReached = effectiveLimit;
    notices.push(`${effectiveLimit} matches limit reached`);
  }
  if (linesTruncated) {
    details.linesTruncated = true;
    notices.push("long lines truncated");
  }
  if (truncation.truncated) {
    details.truncation = truncation;
    notices.push(`${formatSize(DEFAULT_MAX_BYTES)} limit reached`);
  }
  if (notices.length > 0) output += `\n\n[${notices.join(". ")}]`;

  return {
    content: [{ type: "text", text: output }],
    details: Object.keys(details).length > 0 ? details : undefined,
  };
}

export function getOrCreateToolOps(
  cachedToolOps: CachedToolOps | undefined,
  activeVm: VM,
  currentCwd: string,
  shellPath: string,
): CachedToolOps {
  if (cachedToolOps?.cwd === currentCwd) {
    return cachedToolOps;
  }

  return {
    readOps: createGondolinReadOps(activeVm, currentCwd),
    writeOps: createGondolinWriteOps(activeVm, currentCwd),
    editOps: createGondolinEditOps(activeVm, currentCwd),
    lsOps: createGondolinLsOps(activeVm, currentCwd),
    findOps: createGondolinFindOps(activeVm, currentCwd),
    bashOps: createGondolinBashOps(activeVm, currentCwd, shellPath),
    cwd: currentCwd,
  };
}
