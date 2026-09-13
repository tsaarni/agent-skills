/**
 * gemini-optimizer.ts
 *
 * Pi extension that optimizes the agent for Google Gemini models:
 *
 *   1. Bash tool replacement & truncation — replaces Pi's built-in bash tool
 *      with a memory-bounded streaming executor. When Gemini is active, output is
 *      capped at 8 KB (20% head / 80% tail) and streamed directly to a unique file
 *      in the OS temp directory (os.tmpdir()/pi-bash-<id>.log). Memory usage is strictly
 *      bounded to ~15 KB regardless of how large the command output is.
 *
 *   2. TPM pacer — tracks a sliding 60-second token window (fresh input + cached input)
 *      and sleeps before turns when the rolling total would exceed 90% of Gemini's
 *      2 M TPM quota, rendering an active 1-second countdown in the Pi footer.
 *
 *   3. 429 retry fix — intercepts fetch for Gemini endpoints, parses the structured
 *      google.rpc.RetryInfo backoff, and waits the server-requested duration before
 *      retrying with a fresh request signal to bypass client timeout burnout.
 *
 * All features dynamically gate on `ctx.model?.provider === "google"`.
 *
 * Configuration (~/.pi/agent/settings.json under "geminiOptimizer"):
 *
 *   "geminiOptimizer": {
 *     "truncation": {
 *       "enabled": true,
 *       "maxChars": 8000,
 *       "headRatio": 0.2
 *     },
 *     "pacer": {
 *       "enabled": true,
 *       "tpmLimit": 2000000,
 *       "safetyMargin": 0.9,
 *       "windowSeconds": 60
 *     },
 *     "retryFix": {
 *       "enabled": true,
 *       "defaultRetryDelayMs": 60000
 *     }
 *   }
 */

import { randomBytes } from "node:crypto";
import { createWriteStream, type WriteStream } from "node:fs";
import { readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  createBashToolDefinition,
  createLocalBashOperations,
  getAgentDir,
  type ExtensionAPI,
  type ExtensionContext,
  type MessageEndEvent,
  type ModelSelectEvent,
  type TurnEndEvent,
  type TurnStartEvent,
} from "@earendil-works/pi-coding-agent";

// ---------------------------------------------------------------------------
// Configuration types & defaults
// ---------------------------------------------------------------------------

interface TruncationConfig {
  enabled: boolean;
  maxChars: number;
  headRatio: number;
}

interface PacerConfig {
  enabled: boolean;
  tpmLimit: number;
  safetyMargin: number;
  windowSeconds: number;
}

interface RetryFixConfig {
  enabled: boolean;
  defaultRetryDelayMs: number;
}

interface OptimizerConfig {
  truncation: TruncationConfig;
  pacer: PacerConfig;
  retryFix: RetryFixConfig;
}

const DEFAULTS: OptimizerConfig = {
  truncation: {
    enabled: true,
    maxChars: 8000,
    headRatio: 0.2,
  },
  pacer: {
    enabled: true,
    tpmLimit: 2_000_000,
    safetyMargin: 0.9,
    windowSeconds: 60,
  },
  retryFix: {
    enabled: true,
    defaultRetryDelayMs: 60_000,
  },
};

async function loadConfig(): Promise<OptimizerConfig> {
  try {
    const settingsPath = join(getAgentDir(), "settings.json");
    const raw = await readFile(settingsPath, "utf8");
    const settings = JSON.parse(raw);
    const userCfg = settings?.geminiOptimizer;
    if (!userCfg || typeof userCfg !== "object") {
      return DEFAULTS;
    }
    return {
      truncation: {
        ...DEFAULTS.truncation,
        ...(typeof userCfg.truncation === "object" ? userCfg.truncation : {}),
      },
      pacer: {
        ...DEFAULTS.pacer,
        ...(typeof userCfg.pacer === "object" ? userCfg.pacer : {}),
      },
      retryFix: {
        ...DEFAULTS.retryFix,
        ...(typeof userCfg.retryFix === "object" ? userCfg.retryFix : {}),
      },
    };
  } catch {
    return DEFAULTS;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(resolve, Math.max(0, ms));
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function isGoogleActive(ctx?: ExtensionContext): boolean {
  return ctx?.model?.provider === "google";
}

// ---------------------------------------------------------------------------
// Feature 1: Memory-bounded Head/Tail Bash Accumulator & Tool Replacement
// ---------------------------------------------------------------------------

const BASH_UPDATE_THROTTLE_MS = 100;

class BoundedHeadTailAccumulator {
  private readonly maxChars: number;
  private readonly headMaxChars: number;
  private readonly tailMaxChars: number;
  private readonly tailRollingLimit: number;
  private readonly decoder = new TextDecoder();

  private headText = "";
  private tailText = "";
  private totalChars = 0;
  private totalBytes = 0;
  private finished = false;

  private tempFilePath?: string;
  private tempFileStream?: WriteStream;
  private rawChunks: Buffer[] = [];

  constructor(maxChars: number, headRatio: number = 0.2) {
    this.maxChars = maxChars;
    this.headMaxChars = Math.max(1, Math.floor(maxChars * headRatio));
    this.tailMaxChars = Math.max(1, maxChars - this.headMaxChars);
    this.tailRollingLimit = this.tailMaxChars * 2;
  }

  append(data: Buffer): void {
    if (this.finished) {
      throw new Error("Cannot append to a finished output accumulator");
    }
    this.totalBytes += data.length;

    const text = this.decoder.decode(data, { stream: true });
    if (text.length > 0) {
      this.appendDecodedText(text);
    }

    if (this.tempFileStream || this.shouldUseTempFile()) {
      this.ensureTempFile();
      this.tempFileStream?.write(data);
    } else if (data.length > 0) {
      this.rawChunks.push(data);
    }
  }

  private appendDecodedText(text: string): void {
    this.totalChars += text.length;

    // Capture head up to headMaxChars once
    if (this.headText.length < this.headMaxChars) {
      const needed = this.headMaxChars - this.headText.length;
      this.headText += text.slice(0, needed);
    }

    // Capture rolling tail
    this.tailText += text;
    if (this.tailText.length > this.tailRollingLimit) {
      this.tailText = this.tailText.slice(-this.tailMaxChars);
    }
  }

  finish(): void {
    if (this.finished) return;
    this.finished = true;

    const remaining = this.decoder.decode();
    if (remaining.length > 0) {
      this.appendDecodedText(remaining);
    }

    if (this.shouldUseTempFile()) {
      this.ensureTempFile();
    }
  }

  private shouldUseTempFile(): boolean {
    return this.totalChars > this.maxChars;
  }

  private ensureTempFile(): void {
    if (this.tempFilePath) return;
    const id = randomBytes(8).toString("hex");
    this.tempFilePath = join(tmpdir(), `pi-bash-${id}.log`);
    this.tempFileStream = createWriteStream(this.tempFilePath);
    for (const chunk of this.rawChunks) {
      this.tempFileStream.write(chunk);
    }
    this.rawChunks = [];
  }

  async closeTempFile(): Promise<void> {
    if (!this.tempFileStream) return;
    const stream = this.tempFileStream;
    this.tempFileStream = undefined;
    await new Promise<void>((resolve, reject) => {
      stream.once("error", reject);
      stream.once("finish", resolve);
      stream.end();
    });
  }

  getSnapshot(): { content: string; fullOutputPath?: string; truncated: boolean } {
    if (this.totalChars <= this.maxChars) {
      return {
        content: this.tailText,
        truncated: false,
      };
    }

    let tailSlice = this.tailText.slice(-this.tailMaxChars);
    const firstNewline = tailSlice.indexOf("\n");
    if (firstNewline !== -1 && firstNewline < 200) {
      tailSlice = tailSlice.slice(firstNewline + 1);
    }

    const omitted = this.totalChars - (this.headText.length + tailSlice.length);
    const content = `${this.headText}\n\n[... ${omitted.toLocaleString()} chars omitted. Full output: ${this.tempFilePath} ...]\n\n${tailSlice}`;

    return {
      content,
      fullOutputPath: this.tempFilePath,
      truncated: true,
    };
  }
}

function getBashEnv(ctx?: ExtensionContext): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.PI_SESSION_ID;
  delete env.PI_SESSION_FILE;
  delete env.PI_PROVIDER;
  delete env.PI_MODEL;
  delete env.PI_REASONING_LEVEL;

  if (ctx) {
    if (ctx.sessionManager) {
      env.PI_SESSION_ID = ctx.sessionManager.getSessionId();
      const sessionFile = ctx.sessionManager.getSessionFile?.();
      if (sessionFile) env.PI_SESSION_FILE = sessionFile;
    }
    if (ctx.model) {
      env.PI_PROVIDER = ctx.model.provider;
      env.PI_MODEL = ctx.model.id;
    }
    if (ctx.thinkingLevel) {
      env.PI_REASONING_LEVEL = ctx.thinkingLevel;
    }
  }
  return env;
}

function registerOptimizedBashTool(pi: ExtensionAPI, cfg: TruncationConfig): void {
  const baseDef = createBashToolDefinition(process.cwd());
  const ops = createLocalBashOperations();

  pi.registerTool({
    ...baseDef,
    name: "bash",
    async execute(toolCallId, params: { command: string; timeout?: number }, signal, onUpdate, ctx) {
      const command = params.command;
      const timeout = params.timeout;
      const effectiveCwd = ctx?.cwd || process.cwd();

      // If the active model is not Google Gemini, delegate directly to Pi's built-in bash tool
      if (!isGoogleActive(ctx)) {
        return baseDef.execute(toolCallId, params, signal, onUpdate, ctx);
      }

      const accumulator = new BoundedHeadTailAccumulator(cfg.maxChars, cfg.headRatio);
      let acceptingOutput = true;
      let updateTimer: NodeJS.Timeout | undefined;
      let updateDirty = false;
      let lastUpdateAt = 0;

      const emitOutputUpdate = () => {
        if (!onUpdate || !updateDirty) return;
        updateDirty = false;
        lastUpdateAt = Date.now();
        const snap = accumulator.getSnapshot();
        onUpdate({
          content: [{ type: "text", text: snap.content }],
          details: snap.fullOutputPath ? { fullOutputPath: snap.fullOutputPath } : undefined,
        });
      };

      const clearUpdateTimer = () => {
        if (updateTimer) {
          clearTimeout(updateTimer);
          updateTimer = undefined;
        }
      };

      const scheduleOutputUpdate = () => {
        if (!onUpdate) return;
        updateDirty = true;
        const delay = BASH_UPDATE_THROTTLE_MS - (Date.now() - lastUpdateAt);
        if (delay <= 0) {
          clearUpdateTimer();
          emitOutputUpdate();
          return;
        }
        updateTimer ??= setTimeout(() => {
          updateTimer = undefined;
          emitOutputUpdate();
        }, delay);
      };

      if (onUpdate) {
        onUpdate({ content: [], details: undefined });
      }

      const handleData = (data: Buffer) => {
        if (!acceptingOutput) return;
        accumulator.append(data);
        scheduleOutputUpdate();
      };

      const finishOutput = async () => {
        acceptingOutput = false;
        accumulator.finish();
        clearUpdateTimer();
        emitOutputUpdate();
        const snap = accumulator.getSnapshot();
        await accumulator.closeTempFile();
        return snap;
      };

      const appendStatus = (text: string, status: string) =>
        `${text ? `${text}\n\n` : ""}${status}`;

      try {
        let exitCode: number | null = null;
        try {
          const result = await ops.exec(command, effectiveCwd, {
            onData: handleData,
            signal,
            timeout,
            env: getBashEnv(ctx),
          });
          exitCode = result.exitCode;
        } catch (err) {
          const snap = await finishOutput();
          const text = snap.content;
          if (err instanceof Error && err.message === "aborted") {
            throw new Error(appendStatus(text, "Command aborted"));
          }
          if (err instanceof Error && err.message.startsWith("timeout:")) {
            const timeoutSecs = err.message.split(":")[1];
            throw new Error(appendStatus(text, `Command timed out after ${timeoutSecs} seconds`));
          }
          throw err;
        }

        const snap = await finishOutput();
        const outputText = snap.content || "(no output)";
        if (exitCode !== 0 && exitCode !== null) {
          throw new Error(appendStatus(outputText, `Command exited with code ${exitCode}`));
        }

        return {
          content: [{ type: "text", text: outputText }],
          details: snap.fullOutputPath ? { fullOutputPath: snap.fullOutputPath } : {},
        };
      } finally {
        clearUpdateTimer();
      }
    },
  });
}

// ---------------------------------------------------------------------------
// Feature 3: 429 fetch interceptor
// ---------------------------------------------------------------------------

const GEMINI_HOST = "generativelanguage.googleapis.com";
const FETCH_PATCHED = Symbol.for("pi.gemini-optimizer.fetch-patched");

function installFetchInterceptor(
  cfg: RetryFixConfig,
  getCtx: () => ExtensionContext | undefined,
): () => void {
  if ((globalThis as Record<symbol, unknown>)[FETCH_PATCHED]) {
    return () => {};
  }

  const originalFetch = globalThis.fetch;
  (globalThis as Record<symbol, unknown>)[FETCH_PATCHED] = true;

  globalThis.fetch = async function patchedFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const isGeminiEndpoint = url.includes(GEMINI_HOST);
    const ctx = getCtx();

    if (!isGeminiEndpoint || !isGoogleActive(ctx)) {
      return originalFetch(input, init);
    }

    const response = await originalFetch(input, init);
    if (response.status !== 429) {
      return response;
    }

    // Parse retry delay from RetryInfo, error message, or Retry-After header
    let delayMs = cfg.defaultRetryDelayMs;

    const retryAfterHeader = response.headers.get("retry-after");
    if (retryAfterHeader) {
      const secs = parseFloat(retryAfterHeader);
      if (!Number.isNaN(secs) && secs > 0) delayMs = secs * 1000;
    }

    try {
      const cloned = response.clone();
      const body = (await cloned.json()) as {
        error?: {
          message?: string;
          details?: Array<{
            "@type"?: string;
            retryDelay?: string;
          }>;
        };
      };

      const retryInfo = body?.error?.details?.find(
        (d) => d["@type"] === "type.googleapis.com/google.rpc.RetryInfo",
      );

      if (retryInfo?.retryDelay) {
        const match = retryInfo.retryDelay.match(/^([0-9.]+)s$/);
        if (match) delayMs = parseFloat(match[1]) * 1000;
      } else if (body?.error?.message) {
        const match = body.error.message.match(/retry in ([0-9.]+)s/i);
        if (match) delayMs = parseFloat(match[1]) * 1000;
      }
    } catch {
      // Body was not JSON or unreadable
    }

    // Active live countdown in footer
    const endTime = Date.now() + delayMs;
    while (Date.now() < endTime) {
      if (ctx?.signal?.aborted) {
        ctx?.ui.setStatus("gemini-retry", undefined);
        return response;
      }
      const remainingSec = Math.max(1, Math.ceil((endTime - Date.now()) / 1000));
      ctx?.ui.setStatus(
        "gemini-retry",
        `⏳ Gemini 429 — waiting ${remainingSec}s as requested by server...`,
      );
      const chunk = Math.min(1000, endTime - Date.now());
      try {
        await sleep(chunk, ctx?.signal);
      } catch {
        ctx?.ui.setStatus("gemini-retry", undefined);
        return response;
      }
    }

    ctx?.ui.setStatus("gemini-retry", undefined);

    // Retry with ctx?.signal (replacing internal SDK timeout controller which may have elapsed)
    const retryInit: RequestInit = {
      ...init,
      signal: ctx?.signal,
    };

    return originalFetch(input, retryInit);
  } as typeof globalThis.fetch;

  return () => {
    globalThis.fetch = originalFetch;
    delete (globalThis as Record<symbol, unknown>)[FETCH_PATCHED];
  };
}

// ---------------------------------------------------------------------------
// Extension entry point
// ---------------------------------------------------------------------------

interface TokenRecord {
  timestamp: number;
  tokens: number;
}

export default async function (pi: ExtensionAPI) {
  const cfg = await loadConfig();

  // Current session/turn context, dynamically populated across events
  let currentCtx: ExtensionContext | undefined;

  // Install fetch interceptor with clean uninstaller
  const uninstallFetch = cfg.retryFix.enabled
    ? installFetchInterceptor(cfg.retryFix, () => currentCtx)
    : () => {};

  // Clean up on session shutdown
  pi.on("session_shutdown", () => {
    uninstallFetch();
    currentCtx = undefined;
  });

  // Track context on model changes and clear any stale Google statuses if switching away
  pi.on("model_select", (event: ModelSelectEvent, ctx: ExtensionContext) => {
    currentCtx = ctx;
    if (event.model.provider !== "google") {
      ctx.ui.setStatus("gemini-pacer", undefined);
      ctx.ui.setStatus("gemini-retry", undefined);
    }
  });

  // ---------------------------------------------------------------------------
  // Feature 1: Bash tool replacement & truncation
  // ---------------------------------------------------------------------------
  if (cfg.truncation.enabled) {
    registerOptimizedBashTool(pi, cfg.truncation);
  }

  // ---------------------------------------------------------------------------
  // Feature 2: TPM pacer
  // ---------------------------------------------------------------------------
  if (cfg.pacer.enabled) {
    const tokenQueue: TokenRecord[] = [];
    const windowMs = cfg.pacer.windowSeconds * 1000;
    const ceiling = cfg.pacer.tpmLimit * cfg.pacer.safetyMargin;

    // Record total input tokens (fresh input + context cache reads) after each assistant response
    pi.on("message_end", (event: MessageEndEvent, ctx: ExtensionContext) => {
      currentCtx = ctx;
      if (!isGoogleActive(ctx) || event.message.role !== "assistant") return;

      const usage = event.message.usage;
      if (!usage) return;

      // Google's 2M TPM quota metric includes both fresh and cached input tokens
      const tokens = (usage.input ?? 0) + (usage.cacheRead ?? 0);
      if (tokens > 0) {
        tokenQueue.push({ timestamp: Date.now(), tokens });
      }
    });

    // Before each turn, check rolling TPM against ceiling and pace if necessary
    pi.on("turn_start", async (_event: TurnStartEvent, ctx: ExtensionContext) => {
      currentCtx = ctx;
      if (!isGoogleActive(ctx)) return;

      const now = Date.now();

      // Prune records older than the sliding window
      while (tokenQueue.length > 0 && now - tokenQueue[0].timestamp > windowMs) {
        tokenQueue.shift();
      }

      const rollingTokens = tokenQueue.reduce((sum, r) => sum + r.tokens, 0);
      const usageTokens = ctx.getContextUsage()?.tokens;
      const estimated =
        usageTokens && usageTokens > 0
          ? usageTokens
          : (tokenQueue.at(-1)?.tokens ?? 100_000);

      if (rollingTokens + estimated <= ceiling) {
        return;
      }

      // Determine recovery target: aim to leave headroom (at least one extra turn's
      // worth of tokens, up to 25% of ceiling) so we don't immediately stutter again
      // on the very next turn.
      const headroom = Math.min(estimated, ceiling * 0.25);
      const targetCeiling = Math.max(estimated, ceiling - headroom);

      let projected = rollingTokens + estimated;
      let targetExpiry = now;
      let bestExpiry = now;

      for (const record of tokenQueue) {
        projected -= record.tokens;
        // First record that gets projected under the ceiling
        if (projected <= ceiling && bestExpiry === now) {
          bestExpiry = record.timestamp + windowMs + 1000;
        }
        // Record that reaches our target with headroom
        if (projected <= targetCeiling) {
          targetExpiry = record.timestamp + windowMs + 1000;
          break;
        }
      }

      // If targetCeiling couldn't be reached with the records in the queue,
      // fall back to bestExpiry (or wait for the entire queue to clear)
      if (targetExpiry === now) {
        targetExpiry =
          bestExpiry !== now
            ? bestExpiry
            : (tokenQueue.at(-1)?.timestamp ?? now) + windowMs + 1000;
      }

      const waitMs = targetExpiry - Date.now();
      if (waitMs <= 0) return;

      // Real live countdown in Pi footer (only show when wait is noticeable to avoid flicker)
      const endTime = Date.now() + waitMs;
      while (Date.now() < endTime) {
        if (ctx.signal?.aborted) break;
        const remainingMs = endTime - Date.now();
        if (remainingMs >= 300) {
          const remainingSec = Math.max(1, Math.ceil(remainingMs / 1000));
          ctx.ui.setStatus(
            "gemini-pacer",
            `⏳ Pacing Gemini (${remainingSec}s to clear 2M TPM window)...`,
          );
        }
        const chunk = Math.min(1000, remainingMs);
        try {
          await sleep(chunk, ctx.signal);
        } catch {
          break;
        }
      }

      ctx.ui.setStatus("gemini-pacer", undefined);

      // Prune records that expired during the wait
      const afterWait = Date.now();
      while (tokenQueue.length > 0 && afterWait - tokenQueue[0].timestamp > windowMs) {
        tokenQueue.shift();
      }
    });

    // Clear pacer status at the end of each turn
    pi.on("turn_end", (_event: TurnEndEvent, ctx: ExtensionContext) => {
      currentCtx = ctx;
      ctx.ui.setStatus("gemini-pacer", undefined);
    });
  }
}
