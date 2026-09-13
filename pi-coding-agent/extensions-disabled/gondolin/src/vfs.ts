/**
 * Path resolutions, POSIX path conversion, and VFS provider/router internals mappings.
 */

import os from "node:os";
import path from "node:path";
import type {
  BashOperations,
  EditOperations,
  FindOperations,
  LsOperations,
  ReadOperations,
  WriteOperations,
} from "@earendil-works/pi-coding-agent";

export interface VfsRouterLike {
  backend?: unknown;
  mountMap?: Map<string, unknown>;
  mountPaths?: string[];
  allReadonly?: boolean;
  allSymlinks?: boolean;
  allWatch?: boolean;
}

export interface VmResolvedOptions {
  append?: string;
  vmm?: string;
  cpus?: number;
  memory?: string;
  machineType?: string;
  accel?: string;
  cpu?: string;
  qemuIdlePauseMs?: number;
  kernelPath?: string;
  initrdPath?: string;
  rootfsPath?: string;
}

export interface GondolinVmInternals {
  state: string;
  pid?: number;
  overlayDir?: string;
  fuseMount?: string;
  resolvedSandboxOptions?: VmResolvedOptions;
  vfs?: unknown;
}

export interface CachedToolOps {
  readOps: ReadOperations;
  writeOps: WriteOperations;
  editOps: EditOperations;
  lsOps: LsOperations;
  findOps: FindOperations;
  bashOps: BashOperations;
  cwd: string;
}

export function stripAtPrefix(value: string): string {
  return value.startsWith("@") ? value.slice(1) : value;
}

export function toPosix(value: string): string {
  return value.split(path.sep).join(path.posix.sep);
}

export function resolveHostPath(inputPath: string, localCwd: string): string {
  let resolved = inputPath.trim();
  if (resolved.startsWith("~/") || resolved === "~") {
    resolved = path.join(os.homedir(), resolved.slice(1));
  }
  return path.resolve(localCwd, resolved);
}

export function toGuestPath(localCwd: string, inputPath: string): string {
  const trimmed = stripAtPrefix(inputPath.trim());
  if (!trimmed) return toPosix(localCwd);
  const resolved = resolveHostPath(trimmed, localCwd);
  return toPosix(resolved);
}

export function detectImageMimeFromExtension(filePath: string): string | null {
  const ext = path.posix.extname(filePath).toLowerCase();
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".gif") return "image/gif";
  if (ext === ".webp") return "image/webp";
  return null;
}

export function getProviderBooleanFlag(provider: unknown, key: string): boolean {
  if (!provider || typeof provider !== "object") return false;
  return (provider as Record<string, unknown>)[key] === true;
}

export function getVfsRouter(activeVm: unknown): VfsRouterLike {
  const internals = activeVm as GondolinVmInternals;
  let router: unknown = internals.vfs;
  while (
    router &&
    typeof router === "object" &&
    !((router as VfsRouterLike).mountMap instanceof Map) &&
    "backend" in router
  ) {
    router = (router as VfsRouterLike).backend;
  }
  return (router as VfsRouterLike) ?? {};
}

export function recomputeMountPaths(router: VfsRouterLike): void {
  if (!(router.mountMap instanceof Map)) return;
  router.mountPaths = Array.from(router.mountMap.keys()).sort((a, b) => b.length - a.length);
}

export function recomputeMountAggregateFlags(router: VfsRouterLike): void {
  if (!(router.mountMap instanceof Map)) return;
  const providers = Array.from(router.mountMap.values());
  router.allReadonly = providers.every((provider) => getProviderBooleanFlag(provider, "readonly"));
  router.allSymlinks = providers.every((provider) => getProviderBooleanFlag(provider, "symlinks"));
  router.allWatch = providers.every((provider) => getProviderBooleanFlag(provider, "watch"));
}

export function refreshMountRouterState(router: VfsRouterLike): void {
  recomputeMountPaths(router);
  recomputeMountAggregateFlags(router);
}

/** Minimal shape for traversing a provider chain to find `rootPath`. */
interface ProviderLike {
  rootPath?: string;
  backend?: ProviderLike;
}

export function readProviderHostPath(provider: unknown): string {
  let current = provider as ProviderLike | undefined;
  while (current) {
    if (typeof current.rootPath === "string") return current.rootPath;
    current = current.backend;
  }
  return "virtual";
}

export function listRouterMounts(
  router: VfsRouterLike,
): Array<{ guest: string; host: string; mode: string }> {
  if (!(router.mountMap instanceof Map)) return [];
  const mounts: Array<{ guest: string; host: string; mode: string }> = [];
  for (const [guestPath, provider] of router.mountMap.entries()) {
    mounts.push({
      guest: guestPath,
      host: readProviderHostPath(provider),
      mode: getProviderBooleanFlag(provider, "readonly") ? "ro" : "rw",
    });
  }
  return mounts;
}

export function getVmResolvedSandboxOptions(activeVm: unknown): VmResolvedOptions {
  const internals = activeVm as GondolinVmInternals;
  return internals.resolvedSandboxOptions ?? {};
}
