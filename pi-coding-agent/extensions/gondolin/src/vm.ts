/**
 * VM lifecycle, configuration, status management, and slash commands.
 */

import childProcess from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

import { MemoryProvider, ReadonlyProvider, RealFSProvider, VM } from "@earendil-works/gondolin";
import {
  DynamicBorder,
  type ExtensionAPI,
  type ExtensionContext,
  getMarkdownTheme,
} from "@earendil-works/pi-coding-agent";
import { Container, Markdown, Spacer, Text } from "@earendil-works/pi-tui";
import type { GondolinVmInternals } from "./vfs.js";
import {
  getVfsRouter,
  getVmResolvedSandboxOptions,
  listRouterMounts,
  refreshMountRouterState,
  resolveHostPath,
  toPosix,
} from "./vfs.js";

export type VmStatus = ReturnType<typeof getVmStatus>;

export function resolveKernelConsoleAppend(defaultVmm: string): string {
  if (defaultVmm === "krun") {
    return "console=hvc0 root=/dev/vda rootfstype=ext4 rw init=/init";
  }
  const consoleDevice = process.arch === "arm64" ? "ttyAMA0" : "ttyS0";
  return `console=${consoleDevice} initramfs_async=1`;
}

/** Known keys in gondolin-vm.json. */
export interface UserVmConfig {
  cpus?: number;
  memory?: string;
  persist?: boolean;
  sandbox?: {
    vmm?: string;
    append?: string;
    qemuIdlePauseMs?: number;
  };
  vfs?: {
    mounts?: Record<string, JsonMountConfig>;
  };
}

export function readVmUserOptions(
  localCwd: string,
  ctx?: ExtensionContext,
): UserVmConfig {
  const configPath = path.join(localCwd, "gondolin-vm.json");
  if (!fs.existsSync(configPath)) return {};
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf8"));
  } catch (e) {
    ctx?.ui.notify(
      `Failed to parse gondolin-vm.json: ${e instanceof Error ? e.message : String(e)}`,
      "error",
    );
    return {};
  }
}

/** Mount entry from gondolin-vm.json (`vfs.mounts` values). */
export type JsonMountConfig =
  | string                   // "ro" | "rw" → RealFSProvider at key path
  | {
      /** Optional host path (defaults to the mount key / guest path). */
      path?: string;
      /** Wrap in ReadonlyProvider. Defaults to `true` for real mounts, `false` for virtual. */
      readonly?: boolean;
      /** Provider type; `"real"` (default) → RealFSProvider, `"virtual"` → MemoryProvider. */
      type?: "real" | "virtual";
    };

/** Parse a JSON mount value into a VirtualProvider instance. */
export function parseJsonMount(
  guestPath: string,
  config: JsonMountConfig,
): RealFSProvider | ReadonlyProvider | MemoryProvider {
  if (typeof config === "string") {
    // Short form: "ro" or "rw" → RealFSProvider at the same path
    const writable = config === "rw" || config === "read-write";
    const provider = new RealFSProvider(guestPath);
    return writable ? provider : new ReadonlyProvider(provider);
  }

  const hostPath = config.path ?? guestPath;
  const readonly = config.readonly ?? (config.type !== "virtual");
  const type = config.type ?? "real";

  if (type === "virtual") {
    const mem = new MemoryProvider();
    return readonly ? new ReadonlyProvider(mem) : mem;
  }

  // real provider
  const provider = new RealFSProvider(hostPath);
  return readonly ? new ReadonlyProvider(provider) : provider;
}

/** Convert a JSON vfs.mounts map (from gondolin-vm.json) into provider instances. */
export function convertJsonMounts(
  jsonMounts: Record<string, unknown> | undefined,
  ctx?: ExtensionContext,
): Record<string, RealFSProvider | ReadonlyProvider | MemoryProvider> {
  if (!jsonMounts) return {};
  const result: Record<string, RealFSProvider | ReadonlyProvider | MemoryProvider> = {};
  for (const [guestKey, rawValue] of Object.entries(jsonMounts)) {
    try {
      result[guestKey] = parseJsonMount(
        guestKey,
        rawValue as JsonMountConfig,
      );
    } catch (e) {
      ctx?.ui.notify(
        `Skipping mount "${guestKey}": ${e instanceof Error ? e.message : String(e)}`,
        "warning",
      );
    }
  }
  return result;
}

const _require = createRequire(import.meta.url);

export function isKrunAvailable(): boolean {
  if (process.env.GONDOLIN_KRUN_RUNNER) return true;
  try {
    const packageName = `@earendil-works/gondolin-krun-runner-${process.platform}-${process.arch}/package.json`;
    _require.resolve(packageName);
    return true;
  } catch {
    /* not installed as dependency */
  }
  try {
    childProcess.execSync("command -v gondolin-krun-runner", {
      stdio: "ignore",
    });
    return true;
  } catch {
    /* not on PATH */
  }
  return false;
}

export async function startVm(
  pi: ExtensionAPI,
  localCwd: string,
  ctx?: ExtensionContext,
): Promise<{ vm: VM; shellPath: string }> {
  const userOptions = readVmUserOptions(localCwd, ctx);

  const hostHome = os.homedir();
  const defaultVmm = isKrunAvailable() ? "krun" : "qemu";
  const userSandbox = userOptions.sandbox ?? {};

  const kernelConsoleAppend = resolveKernelConsoleAppend(defaultVmm);
  const homeAppend = `${kernelConsoleAppend} gondolin.home=${hostHome}`;
  const currentAppend = userSandbox.append || "";
  const mergedAppend = currentAppend ? `${currentAppend} ${homeAppend}` : homeAppend;

  let created: VM | undefined;
  try {
    const persistOverlay =
      pi.getFlag("persist") === true || userOptions.persist === true;

    const jsonMounts = userOptions.vfs?.mounts;

    const overlayDir = persistOverlay
      ? path.join(
          os.homedir(),
          ".gondolin",
          "overlays",
          path.basename(localCwd).replace(/[^a-zA-Z0-9]/g, "_"),
        )
      : undefined;

    const vmOptions = {
      sessionLabel: `pi ${path.basename(localCwd)}`,
      cpus: userOptions.cpus,
      memory: userOptions.memory,
      persistent: persistOverlay || undefined,
      overlayDir,
      sandbox: {
        vmm: defaultVmm,
        ...userSandbox,
        append: mergedAppend,
      },
      vfs: {
        mounts: {
          ...convertJsonMounts(jsonMounts, ctx),
          [localCwd]: new RealFSProvider(localCwd),
        },
      },
    };

    created = await VM.create(vmOptions);
    const bashProbe = await created.exec(["/bin/sh", "-lc", "command -v bash || true"]);
    const shellPath = bashProbe.stdout.trim() || "/bin/sh";

    return { vm: created, shellPath };
  } catch (error) {
    if (created) {
      try {
        await created.close();
      } catch {
        /* best-effort cleanup */
      }
    }
    throw error;
  }
}

export function getVmStatus(activeVm: VM) {
  const opts = getVmResolvedSandboxOptions(activeVm);
  const internals = activeVm as unknown as GondolinVmInternals;
  const vmState = internals.state ?? "unknown";
  const vmPid = internals.pid;

  let cowOverlayPath = "(none)";
  const overlayDir = internals.overlayDir;
  if (typeof overlayDir === "string") {
    cowOverlayPath = overlayDir;
  }

  const append = opts.append ?? "";
  const egressOff =
    append.includes("net=none") ||
    append.includes("networking=off") ||
    append.includes("gondolin.egress=off");
  const egressOn = !egressOff;

  let fuseMount = "(unknown)";
  const fuseMountVal = internals.fuseMount;
  if (typeof fuseMountVal === "string") {
    fuseMount = fuseMountVal;
  }

  const router = getVfsRouter(activeVm);
  const mounts = listRouterMounts(router);
  const idlePause = opts.qemuIdlePauseMs === undefined ? "disabled" : `${opts.qemuIdlePauseMs}ms`;

  return {
    id: activeVm.id,
    state: vmState,
    pid: vmPid,
    uptime: Date.now(),
    vmm: opts.vmm ?? "default",
    cpus: opts.cpus,
    memory: opts.memory ?? "default",
    machineType: opts.machineType || "default",
    accel: opts.accel || "default",
    cpu: opts.cpu || "default",
    idlePause,
    kernelPath: opts.kernelPath,
    initrdPath: opts.initrdPath,
    rootfsPath: opts.rootfsPath,
    shell: "(set after boot)",
    cowOverlayPath,
    egressOn,
    fuseMount,
    mounts,
  };
}

export function buildStatusMarkdown(s: VmStatus, shellPath: string): string {
  const stateEmoji = s.state === "running" ? "●" : s.state === "stopped" ? "○" : "◌";
  const egress = s.egressOn ? "on" : "off";
  const lines: string[] = [];
  lines.push(
    `${stateEmoji} **${s.state}** — vmm \`${s.vmm}\`, ${s.cpus} cpu, \`${s.memory}\` memory, egress \`${egress}\`, shell \`${shellPath}\``,
  );
  lines.push("");
  lines.push(`- **Overlay**: \`${s.cowOverlayPath}\``);
  lines.push(`- **Idle pause**: ${s.idlePause}`);
  if (s.mounts.length > 0) {
    lines.push("- **Mounts**:");
    for (const m of s.mounts) {
      lines.push(`  - \`${m.guest}\` ← \`${m.host}\` (${m.mode})`);
    }
  } else {
    lines.push("- **Mounts**: (none)");
  }
  return lines.join("\n");
}

export function registerStatusRenderer(pi: ExtensionAPI): void {
  pi.registerMessageRenderer<VmStatus>("gondolin-status", (message, _options, theme) => {
    const text =
      typeof message.content === "string"
        ? message.content
        : (message.content
            ?.filter((c) => c.type === "text")
            .map((c) => c.text)
            .join("\n") ?? "");
    const border = (str: string) => theme.fg("border", str);
    const container = new Container();
    container.addChild(new Spacer(1));
    container.addChild(new DynamicBorder(border));
    container.addChild(new Text(theme.bold(theme.fg("accent", "Gondolin VM")), 1, 0));
    container.addChild(new Spacer(1));
    container.addChild(new Markdown(text, 1, 1, getMarkdownTheme()));
    container.addChild(new DynamicBorder(border));
    return container;
  });
}

export async function sendGondolinStatus(
  pi: ExtensionAPI,
  activeVm: VM,
  shellPath: string,
): Promise<void> {
  const status = getVmStatus(activeVm);
  pi.sendMessage({
    customType: "gondolin-status",
    content: buildStatusMarkdown(status, shellPath),
    display: true,
    details: status,
  });
}

export function registerGondolinCommands(
  pi: ExtensionAPI,
  isSandboxEnabled: () => boolean,
  ensureVm: (ctx?: ExtensionContext) => Promise<{ vm: VM; shellPath: string }>,
  getLocalCwd: () => string,
): void {
  // /gondolin
  pi.registerCommand("gondolin", {
    description: "Print Gondolin VM status (scrolls with the conversation)",
    handler: async (_args, ctx) => {
      if (!isSandboxEnabled()) {
        ctx.ui.notify("Gondolin not active. Start with: pi --gondolin", "warning");
        return;
      }
      const { vm, shellPath } = await ensureVm(ctx);
      await sendGondolinStatus(pi, vm, shellPath);
    },
  });

  // /mounts
  pi.registerCommand("mounts", {
    description: "List active Gondolin VM mount mappings (guest→host, ro/rw)",
    handler: async (_args, ctx) => {
      if (!isSandboxEnabled()) {
        ctx.ui.notify("Gondolin not active. Start with: pi --gondolin", "warning");
        return;
      }
      const { vm } = await ensureVm(ctx);
      const router = getVfsRouter(vm);

      if (!(router?.mountMap instanceof Map) || router.mountMap.size === 0) {
        ctx.ui.notify("No active mounts.", "info");
        return;
      }

      const lines = listRouterMounts(router).map(
        (mount) => `  ${mount.guest}  ←  ${mount.host}  [${mount.mode}]`,
      );
      ctx.ui.notify(lines.join("\n"), "info");
    },
  });

  // /mount
  pi.registerCommand("mount", {
    description:
      "Mount a host directory inside the Gondolin VM with 1-to-1 path mirroring (Default: read-only). Usage: /mount <host-path> [--writable]",
    handler: async (args, ctx) => {
      if (!isSandboxEnabled()) {
        ctx.ui.notify("Gondolin not active. Start with: pi --gondolin", "warning");
        return;
      }
      const parts = args.trim().split(/\s+/);
      const hostPath = parts[0];
      if (!hostPath) {
        ctx.ui.notify("Usage: /mount <host-path> [--writable]", "error");
        return;
      }
      const writable =
        parts.includes("--writable") || parts.includes("--rw") || parts.includes("-rw");
      const readonly = !writable;

      const absHostPath = resolveHostPath(hostPath, getLocalCwd());
      if (!fs.existsSync(absHostPath)) {
        ctx.ui.notify(`Host directory does not exist: ${absHostPath}`, "error");
        return;
      }

      const { vm } = await ensureVm(ctx);
      const router = getVfsRouter(vm);

      if (typeof router.mountMap?.set !== "function") {
        ctx.ui.notify("Error: VM VFS router does not support dynamic mounting.", "error");
        return;
      }

      const guestDir = toPosix(absHostPath);
      const newProvider = new RealFSProvider(absHostPath);
      const finalProvider = readonly ? new ReadonlyProvider(newProvider) : newProvider;

      router.mountMap.set(guestDir, finalProvider);
      refreshMountRouterState(router);

      const source = `/data${guestDir}`;
      const mountCmd = readonly
        ? 'mkdir -p "$1" && mount -o ro,bind "$2" "$1"'
        : 'mkdir -p "$1" && mount --bind "$2" "$1"';

      const execResult = await vm.exec(["/bin/sh", "-c", mountCmd, "sh", guestDir, source]);
      if (execResult.exitCode === 0) {
        ctx.ui.notify(
          `Successfully mapped host path: ${absHostPath} (${readonly ? "read-only" : "read-write"})`,
          "info",
        );
      } else {
        ctx.ui.notify(`Failed to mount inside guest VM: ${execResult.stderr}`, "error");
      }
    },
  });

  // /unmount + /umount
  const unmountHandler = async (args: string, ctx: ExtensionContext) => {
    if (!isSandboxEnabled()) {
      ctx.ui.notify("Gondolin not active. Start with: pi --gondolin", "warning");
      return;
    }
    const guestPath = args.trim();
    if (!guestPath) {
      ctx.ui.notify("Usage: /unmount <guest-path>", "error");
      return;
    }

    const { vm } = await ensureVm(ctx);
    const router = getVfsRouter(vm);

    if (!(router?.mountMap instanceof Map)) {
      ctx.ui.notify("Error: VM VFS router does not support dynamic mounting.", "error");
      return;
    }

    const normalisedGuest = toPosix(path.resolve(guestPath));

    if (router.mountMap.has(normalisedGuest)) {
      router.mountMap.delete(normalisedGuest);
    } else if (router.mountMap.has(guestPath)) {
      router.mountMap.delete(guestPath);
    } else {
      ctx.ui.notify(`No mount found for guest path: ${guestPath}`, "error");
      return;
    }
    refreshMountRouterState(router);

    // Unmount inside the guest VM (best-effort)
    try {
      await vm.exec(["/bin/sh", "-c", 'umount "$1" 2>/dev/null || true', "sh", normalisedGuest]);
    } catch {
      /* best-effort */
    }

    ctx.ui.notify(`Unmounted guest path: ${normalisedGuest}`, "info");
  };

  pi.registerCommand("unmount", {
    description:
      "Unmount a previously mounted host directory from the Gondolin VM. Usage: /unmount <guest-path>",
    handler: unmountHandler,
  });

  pi.registerCommand("umount", {
    description: "Alias for /unmount",
    handler: unmountHandler,
  });
}
