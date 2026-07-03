/**
 * Gondolin Tool Routing Extension
 *
 * Runs pi's built-in tools inside a local Gondolin micro-VM.
 * Only activates when --gondolin flag is passed or GONDOLIN=1 environment
 * variable is set.
 */

import type { VM } from "@earendil-works/gondolin";
import type {
  AgentToolUpdateCallback,
  ExtensionAPI,
  ExtensionContext,
  GrepToolInput,
} from "@earendil-works/pi-coding-agent";
import {
  createBashTool,
  createEditTool,
  createFindTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
} from "@earendil-works/pi-coding-agent";

import { executeGondolinGrep, getOrCreateToolOps } from "./tools.js";
import type { CachedToolOps } from "./vfs.js";
import {
  registerGondolinCommands,
  registerStatusRenderer,
  sendGondolinStatus,
  startVm,
} from "./vm.js";

export default function gondolinExtension(pi: ExtensionAPI) {
  pi.registerFlag("gondolin", {
    description: "Run pi's built-in tools inside a local Gondolin VM",
    type: "boolean",
    default: false,
  });

  pi.registerFlag("persist", {
    description: "Keep Gondolin VM overlay across sessions (persistent CoW layer)",
    type: "boolean",
    default: false,
  });

  const isSandboxEnabled = () => pi.getFlag("gondolin") === true || process.env.GONDOLIN === "1";

  const getLocalCwd = () => process.cwd();

  const localCwd = getLocalCwd();
  const localRead = createReadTool(localCwd);
  const localWrite = createWriteTool(localCwd);
  const localEdit = createEditTool(localCwd);
  const localBash = createBashTool(localCwd);
  const localGrep = createGrepTool(localCwd);
  const localFind = createFindTool(localCwd);
  const localLs = createLsTool(localCwd);

  let vm: VM | undefined;
  let vmStarting: Promise<{ vm: VM; shellPath: string }> | undefined;
  let vmStartError: Error | undefined;
  let shellPath = "/bin/sh";
  const inflightCalls = new Set<Promise<unknown>>();

  let cachedToolOps: CachedToolOps | undefined;

  function invalidateToolOpsCache(): void {
    cachedToolOps = undefined;
  }

  function getOps(activeVm: VM): CachedToolOps {
    const result = getOrCreateToolOps(cachedToolOps, activeVm, getLocalCwd(), shellPath);
    cachedToolOps = result;
    return result;
  }

  async function ensureVm(ctx?: ExtensionContext): Promise<{ vm: VM; shellPath: string }> {
    if (vm) return { vm, shellPath };
    if (vmStartError) throw vmStartError;

    if (!vmStarting) {
      ctx?.ui.notify("Starting Gondolin VM...", "info");

      vmStarting = (async () => {
        try {
          const result = await startVm(pi, getLocalCwd(), ctx);
          vm = result.vm;
          shellPath = result.shellPath;
          vmStartError = undefined;
          invalidateToolOpsCache();
          ctx?.ui.notify("Gondolin VM started successfully", "info");
          return result;
        } catch (error) {
          vmStartError = error instanceof Error ? error : new Error(String(error));

          const errorMsg = vmStartError.message;
          if (errorMsg.includes("ENOENT") || errorMsg.includes("not found")) {
            ctx?.ui.notify(
              "QEMU not found — install with: brew install qemu (or apt install qemu-system-x86 for Linux)",
              "error",
            );
          } else if (errorMsg.includes("EACCES") || errorMsg.includes("permission")) {
            ctx?.ui.notify(
              "Permission denied starting VM — check QEMU installation and permissions",
              "error",
            );
          } else {
            ctx?.ui.notify(`Failed to start Gondolin VM: ${errorMsg}`, "error");
          }

          throw error;
        } finally {
          vmStarting = undefined;
        }
      })();
    }
    return vmStarting;
  }

  registerStatusRenderer(pi);

  registerGondolinCommands(pi, isSandboxEnabled, ensureVm, getLocalCwd);

  pi.on("session_start", async (_event, ctx) => {
    if (!isSandboxEnabled()) return;
    ctx.ui.notify("Initializing Gondolin VM environment...", "info");
    try {
      const { vm: activeVm } = await ensureVm(ctx);
      await sendGondolinStatus(pi, activeVm, shellPath);
    } catch (error) {
      console.error("VM startup failed:", error);
    }
  });

  pi.on("session_shutdown", async () => {
    const activeVm = vm;
    vm = undefined;
    vmStarting = undefined;
    vmStartError = undefined;
    cachedToolOps = undefined;
    if (!activeVm) return;
    if (inflightCalls.size > 0) {
      try {
        await Promise.allSettled(inflightCalls);
      } catch {
        /* best-effort drain */
      }
    }
    await activeVm.close();
  });

  pi.on("user_bash", async (_event, ctx) => {
    if (!isSandboxEnabled()) return;
    const { vm: activeVm } = await ensureVm(ctx);
    const ops = getOps(activeVm);
    return { operations: ops.bashOps };
  });

  pi.on("before_agent_start", async (_event, ctx) => {
    if (!isSandboxEnabled()) return;
    await ensureVm(ctx);
    return {
      message: {
        customType: "gondolin",
        content: `Current working directory: ${getLocalCwd()} (Gondolin VM; direct 1-to-1 host filesystem path mapping enabled)`,
        display: false,
      },
    };
  });

  const toolWrapper = <T extends { execute: (...args: never[]) => Promise<R> }, P, R, Ops>(
    localTool: T,
    createTool: (
      cwd: string,
      opts: { operations: Ops },
    ) => { execute: (...args: never[]) => Promise<R> },
    opsKey: keyof CachedToolOps,
  ) => ({
    ...localTool,
    async execute(
      ...args: [string, P, AbortSignal | undefined, unknown, ExtensionContext | undefined]
    ) {
      const [id, params, signal, onUpdate, ctx] = args;
      if (!isSandboxEnabled()) {
        return (localTool.execute as (...args: unknown[]) => Promise<R>)(
          id,
          params,
          signal,
          onUpdate,
        );
      }

      const { vm: activeVm } = await ensureVm(ctx);
      const ops = getOps(activeVm);
      const tool = createTool(getLocalCwd(), {
        operations: ops[opsKey] as Ops,
      });
      const p = (tool.execute as (...args: unknown[]) => Promise<R>)(
        id,
        params,
        signal,
        onUpdate,
        ctx,
      );

      inflightCalls.add(p);
      try {
        return await p;
      } finally {
        inflightCalls.delete(p);
      }
    },
  });

  pi.registerTool(toolWrapper(localRead, createReadTool, "readOps"));
  pi.registerTool(toolWrapper(localWrite, createWriteTool, "writeOps"));
  pi.registerTool(toolWrapper(localEdit, createEditTool, "editOps"));
  pi.registerTool(toolWrapper(localBash, createBashTool, "bashOps"));
  pi.registerTool(toolWrapper(localLs, createLsTool, "lsOps"));
  pi.registerTool(toolWrapper(localFind, createFindTool, "findOps"));

  // Grep has its own execution path (uses rg inside the VM directly).
  pi.registerTool({
    ...localGrep,
    async execute(
      _id: string,
      params: GrepToolInput,
      signal: AbortSignal | undefined,
      _onUpdate: AgentToolUpdateCallback<unknown> | undefined,
      ctx: ExtensionContext | undefined,
    ) {
      if (!isSandboxEnabled()) return localGrep.execute(_id, params, signal, _onUpdate);
      const { vm: activeVm } = await ensureVm(ctx);
      const p = executeGondolinGrep(activeVm, getLocalCwd(), params, signal);
      inflightCalls.add(p);
      try {
        return await p;
      } finally {
        inflightCalls.delete(p);
      }
    },
  });
}
