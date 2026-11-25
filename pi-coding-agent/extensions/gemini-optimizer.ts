/**
 * gemini-optimizer.ts
 *
 * Pi extension that optimizes the agent for Google Gemini (`provider: "google"`):
 *
 *   1. TPM pacer — tracks a per-model sliding 60-second token window (fresh input +
 *      cached input) and sleeps in `before_provider_request` when the rolling total
 *      would exceed `safetyMargin` (default 90%) of the model's TPM limit, showing
 *      a live 1-second countdown in the Pi footer.
 *
 *   2. 429 retry fix & auto-calibration — wraps the built-in `google` provider's
 *      `streamSimple` via `pi.registerProvider()` to inject a scoped `options.fetch`
 *      (no global fetch monkey-patching). Parses `google.rpc.RetryInfo.retryDelay`,
 *      waits the server-requested duration with a live footer countdown, retries up
 *      to `maxRetries` times using the request's `AbortSignal`, and auto-calibrates
 *      the active model's TPM limit from `google.rpc.QuotaFailure.violations[].quotaValue`.
 *
 * Activation:
 *   Auto-loaded by Pi at startup, but only active when the selected model uses
 *   `provider: "google"` (e.g. `google/gemini-*`). Complete no-op for other
 *   providers (including Gemini via `openrouter` or `google-vertex`). Switching
 *   models mid-session enables/disables both features dynamically.
 *
 * Typical Google 429 Errors & How to Observe Them:
 *   Google's Paid Tier / AI Pro limits are dynamic per project and model. Check your
 *   live limits in Google AI Studio -> Dashboard -> Rate Limits.
 *
 *   1) Per-minute TPM exhaustion (retryable — triggers retry + auto-calibration):
 *      HTTP 429 RESOURCE_EXHAUSTED
 *      {
 *        "error": {
 *          "code": 429,
 *          "status": "RESOURCE_EXHAUSTED",
 *          "message": "You exceeded your current quota... Quota exceeded for metric:
 *            generativelanguage.googleapis.com/generate_content_paid_tier_input_token_count,
 *            limit: 2000000, model: gemini-4-argon\nPlease retry in 58.4s.",
 *          "details": [
 *            {
 *              "@type": "type.googleapis.com/google.rpc.QuotaFailure",
 *              "violations": [{
 *                "quotaMetric": "generativelanguage.googleapis.com/generate_content_paid_tier_input_token_count",
 *                "quotaId": "GenerateContentPaidTierInputTokensPerModelPerMinute",
 *                "quotaValue": "2000000"
 *              }]
 *            },
 *            { "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "58s" }
 *          ]
 *        }
 *      }
 *      Note: Google discounts cached input tokens 90% for billing, but 0% for TPM.
 *      Pi's built-in retry skips this because `google-shared.js` drops headers and
 *      `retry.js` treats "quota exceeded" as non-retryable.
 *
 *   2) Per-minute RPM exhaustion (retryable — waits `retryDelay` and retries):
 *      `quotaId` contains `RequestsPerMinute` / `PerMinute`.
 *
 *   3) Daily quota / hard spend cap (NOT retryable — fails fast without sleeping):
 *      `quotaId` contains `PerDay` (e.g. `GenerateRequestsPerDayPerProjectPerModel-FreeTier`).
 *
 *   Observing in Pi:
 *   - Footer shows `⏳ Pacing Gemini (Xs ...)` during proactive TPM pacing.
 *   - Footer shows `⏳ Gemini 429 — waiting Xs ... (attempt/max)` during 429 backoff.
 *   - Toast notification reports when `QuotaFailure.quotaValue` auto-calibrates a model's TPM limit.
 *
 * Configuration (~/.pi/agent/settings.json under "geminiOptimizer"):
 *
 *   "geminiOptimizer": {
 *     "pacer": {
 *       "enabled": true,
 *       "tpmLimit": 2000000,
 *       "tpmLimits": {
 *         "gemini-4-argon": 2000000,
 *         "gemini-4-neon": 2000000
 *       },
 *       "safetyMargin": 0.9,
 *       "windowSeconds": 60
 *     },
 *     "retryFix": {
 *       "enabled": true,
 *       "maxRetries": 3,
 *       "defaultRetryDelayMs": 60000
 *     }
 *   }
 */

import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { getApiProvider } from "@earendil-works/pi-ai/compat";
import {
  getAgentDir,
  type BeforeProviderRequestEvent,
  type ExtensionAPI,
  type ExtensionContext,
  type MessageEndEvent,
  type ModelSelectEvent,
  type SessionStartEvent,
  type TurnEndEvent,
} from "@earendil-works/pi-coding-agent";

// ---------------------------------------------------------------------------
// Configuration types & defaults
// ---------------------------------------------------------------------------

interface PacerConfig {
  enabled: boolean;
  tpmLimit: number;
  tpmLimits: Record<string, number>;
  safetyMargin: number;
  windowSeconds: number;
}

interface RetryFixConfig {
  enabled: boolean;
  maxRetries: number;
  defaultRetryDelayMs: number;
}

interface OptimizerConfig {
  pacer: PacerConfig;
  retryFix: RetryFixConfig;
}

const DEFAULTS: OptimizerConfig = {
  pacer: {
    enabled: true,
    tpmLimit: 2_000_000,
    tpmLimits: {},
    safetyMargin: 0.9,
    windowSeconds: 60,
  },
  retryFix: {
    enabled: true,
    maxRetries: 3,
    defaultRetryDelayMs: 60_000,
  },
};

async function loadConfig(): Promise<OptimizerConfig> {
  try {
    const raw = await readFile(join(getAgentDir(), "settings.json"), "utf8");
    const userCfg = JSON.parse(raw)?.geminiOptimizer;
    if (!userCfg || typeof userCfg !== "object") {
      return DEFAULTS;
    }
    const userPacer = typeof userCfg.pacer === "object" && userCfg.pacer ? userCfg.pacer : {};
    const userTpmLimits =
      typeof userPacer.tpmLimits === "object" && userPacer.tpmLimits ? userPacer.tpmLimits : {};
    return {
      pacer: {
        ...DEFAULTS.pacer,
        ...userPacer,
        tpmLimits: { ...DEFAULTS.pacer.tpmLimits, ...userTpmLimits },
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

function isGoogleActive(ctx?: ExtensionContext): boolean {
  return ctx?.model?.provider === "google";
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, Math.max(0, ms));
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function sleepWithCountdown(
  waitMs: number,
  statusKey: string,
  formatStatus: (remainingSec: number) => string,
  ctx?: ExtensionContext,
  signal?: AbortSignal,
): Promise<boolean> {
  const endTime = Date.now() + waitMs;
  try {
    while (Date.now() < endTime) {
      if (signal?.aborted) return false;
      const remainingMs = endTime - Date.now();
      if (remainingMs >= 300) {
        ctx?.ui.setStatus(statusKey, formatStatus(Math.max(1, Math.ceil(remainingMs / 1000))));
      }
      await sleep(Math.min(1000, remainingMs), signal);
    }
    return true;
  } catch {
    return false;
  } finally {
    ctx?.ui.setStatus(statusKey, undefined);
  }
}

// ---------------------------------------------------------------------------
// 429 parsing & scoped fetch wrapper
// ---------------------------------------------------------------------------

interface Parsed429 {
  delayMs: number;
  learnedTpmLimit?: number;
  isDailyQuota: boolean;
}

interface GoogleErrorBody {
  error?: {
    message?: string;
    details?: Array<{
      "@type"?: string;
      retryDelay?: string;
      violations?: Array<{
        quotaMetric?: string;
        quotaId?: string;
        quotaValue?: string;
      }>;
    }>;
  };
}

async function parseGoogle429(response: Response, fallbackMs: number): Promise<Parsed429> {
  let delayMs = fallbackMs;
  let learnedTpmLimit: number | undefined;
  let isDailyQuota = false;

  const retryAfter = response.headers.get("retry-after");
  if (retryAfter) {
    const secs = Number.parseFloat(retryAfter);
    if (Number.isFinite(secs) && secs > 0) delayMs = secs * 1000;
  }

  try {
    const body = (await response.clone().json()) as GoogleErrorBody;
    const details = body?.error?.details ?? [];

    const retryInfo = details.find(
      (d) => d["@type"] === "type.googleapis.com/google.rpc.RetryInfo",
    );
    if (retryInfo?.retryDelay) {
      const match = retryInfo.retryDelay.match(/^([0-9.]+)s$/);
      if (match) delayMs = Number.parseFloat(match[1]) * 1000;
    } else if (body?.error?.message) {
      const match = body.error.message.match(/retry in ([0-9.]+)s/i);
      if (match) delayMs = Number.parseFloat(match[1]) * 1000;
    }

    const quotaFailure = details.find(
      (d) => d["@type"] === "type.googleapis.com/google.rpc.QuotaFailure",
    );
    for (const v of quotaFailure?.violations ?? []) {
      const id = v.quotaId ?? "";
      const metric = v.quotaMetric ?? "";
      if (/PerDay/i.test(id)) {
        isDailyQuota = true;
      }
      if (/InputTokens.*PerMinute/i.test(id) || /input_token_count/i.test(metric)) {
        const parsed = Number.parseInt(v.quotaValue ?? "", 10);
        if (Number.isFinite(parsed) && parsed > 0) {
          learnedTpmLimit = parsed;
        }
      }
    }
  } catch {
    // Non-JSON or unreadable body; use header or fallback
  }

  return { delayMs, learnedTpmLimit, isDailyQuota };
}

function createGeminiRetryFetch(
  baseFetch: typeof globalThis.fetch,
  modelId: string,
  cfg: RetryFixConfig,
  requestSignal: AbortSignal | undefined,
  getCtx: () => ExtensionContext | undefined,
  onLearnTpmLimit: (modelId: string, limit: number) => void,
): typeof globalThis.fetch {
  return async function geminiRetryFetch(
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> {
    let currentInit = init;

    for (let attempt = 0; ; attempt++) {
      const response = await baseFetch(input, currentInit);
      if (response.status !== 429 || attempt >= cfg.maxRetries) {
        return response;
      }

      const parsed = await parseGoogle429(response, cfg.defaultRetryDelayMs);
      if (parsed.learnedTpmLimit !== undefined) {
        onLearnTpmLimit(modelId, parsed.learnedTpmLimit);
      }
      if (parsed.isDailyQuota) {
        return response;
      }

      const signal = requestSignal ?? currentInit?.signal ?? undefined;
      const completed = await sleepWithCountdown(
        parsed.delayMs,
        "gemini-retry",
        (secs) =>
          `⏳ Gemini 429 — waiting ${secs}s as requested by server (${attempt + 1}/${cfg.maxRetries})...`,
        getCtx(),
        signal,
      );
      if (!completed) {
        return response;
      }

      // Use requestSignal on retry in case the SDK's per-attempt timeout signal expired while waiting
      currentInit = { ...init, signal: requestSignal };
    }
  } as typeof globalThis.fetch;
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
  let currentCtx: ExtensionContext | undefined;

  // Per-model TPM limits learned dynamically from Google 429 QuotaFailure responses
  const learnedTpmLimits = new Map<string, number>();

  const getEffectiveTpmLimit = (modelId: string): number =>
    learnedTpmLimits.get(modelId) ?? cfg.pacer.tpmLimits[modelId] ?? cfg.pacer.tpmLimit;

  const onLearnTpmLimit = (modelId: string, limit: number) => {
    const prev = getEffectiveTpmLimit(modelId);
    learnedTpmLimits.set(modelId, limit);
    if (limit !== prev) {
      currentCtx?.ui.notify(
        `Gemini pacer: calibrated ${modelId} TPM limit to ${limit.toLocaleString()} from API 429 ` +
          `(set geminiOptimizer.pacer.tpmLimits["${modelId}"] in settings.json to persist)`,
        "info",
      );
    }
  };

  pi.on("session_start", (_event: SessionStartEvent, ctx: ExtensionContext) => {
    currentCtx = ctx;
  });

  pi.on("session_shutdown", () => {
    currentCtx?.ui.setStatus("gemini-pacer", undefined);
    currentCtx?.ui.setStatus("gemini-retry", undefined);
    currentCtx = undefined;
  });

  pi.on("model_select", (event: ModelSelectEvent, ctx: ExtensionContext) => {
    currentCtx = ctx;
    if (event.model.provider !== "google") {
      ctx.ui.setStatus("gemini-pacer", undefined);
      ctx.ui.setStatus("gemini-retry", undefined);
    }
  });

  // -------------------------------------------------------------------------
  // 429 retry fix via scoped provider streamSimple wrapper
  // -------------------------------------------------------------------------
  if (cfg.retryFix.enabled) {
    pi.registerProvider("google", {
      api: "google-generative-ai",
      streamSimple(model, context, options) {
        const baseApi = getApiProvider("google-generative-ai");
        if (!baseApi) {
          throw new Error('Built-in API "google-generative-ai" is not registered');
        }
        return baseApi.streamSimple(model, context, {
          ...options,
          fetch: createGeminiRetryFetch(
            options?.fetch ?? globalThis.fetch,
            model.id,
            cfg.retryFix,
            options?.signal,
            () => currentCtx,
            onLearnTpmLimit,
          ),
        });
      },
    });
  }

  // -------------------------------------------------------------------------
  // Per-model sliding-window TPM pacer
  // -------------------------------------------------------------------------
  if (cfg.pacer.enabled) {
    const queuesByModel = new Map<string, TokenRecord[]>();
    const windowMs = cfg.pacer.windowSeconds * 1000;

    const getQueue = (modelId: string): TokenRecord[] => {
      let queue = queuesByModel.get(modelId);
      if (!queue) {
        queue = [];
        queuesByModel.set(modelId, queue);
      }
      return queue;
    };

    const pruneExpired = (queue: TokenRecord[], now: number) => {
      while (queue.length > 0 && now - queue[0].timestamp > windowMs) {
        queue.shift();
      }
    };

    pi.on("message_end", (event: MessageEndEvent, ctx: ExtensionContext) => {
      currentCtx = ctx;
      if (!isGoogleActive(ctx) || event.message.role !== "assistant") return;

      const usage = event.message.usage;
      const modelId = ctx.model?.id;
      if (!usage || !modelId) return;

      // Google's TPM quota counts both fresh and cached input tokens
      const tokens = (usage.input ?? 0) + (usage.cacheRead ?? 0);
      if (tokens > 0) {
        getQueue(modelId).push({ timestamp: Date.now(), tokens });
      }
    });

    pi.on("before_provider_request", async (_event: BeforeProviderRequestEvent, ctx: ExtensionContext) => {
      currentCtx = ctx;
      if (!isGoogleActive(ctx) || !ctx.model) return;

      const modelId = ctx.model.id;
      const tpmLimit = getEffectiveTpmLimit(modelId);
      const ceiling = tpmLimit * cfg.pacer.safetyMargin;
      const queue = getQueue(modelId);

      const now = Date.now();
      pruneExpired(queue, now);

      const rollingTokens = queue.reduce((sum, r) => sum + r.tokens, 0);
      const usageTokens = ctx.getContextUsage()?.tokens;
      const estimated =
        usageTokens && usageTokens > 0 ? usageTokens : (queue.at(-1)?.tokens ?? 100_000);

      if (rollingTokens + estimated <= ceiling) return;

      // Wait until enough old records expire to leave headroom for at least one extra turn
      const headroom = Math.min(estimated, ceiling * 0.25);
      const targetCeiling = Math.max(estimated, ceiling - headroom);

      let projected = rollingTokens + estimated;
      let targetExpiry = now;
      let bestExpiry = now;

      for (const record of queue) {
        projected -= record.tokens;
        if (projected <= ceiling && bestExpiry === now) {
          bestExpiry = record.timestamp + windowMs + 1000;
        }
        if (projected <= targetCeiling) {
          targetExpiry = record.timestamp + windowMs + 1000;
          break;
        }
      }

      if (targetExpiry === now) {
        targetExpiry =
          bestExpiry !== now ? bestExpiry : (queue.at(-1)?.timestamp ?? now) + windowMs + 1000;
      }

      const waitMs = targetExpiry - Date.now();
      if (waitMs <= 0) return;

      const tpmLabel = `${(tpmLimit / 1_000_000).toFixed(tpmLimit % 1_000_000 === 0 ? 0 : 1)}M`;
      await sleepWithCountdown(
        waitMs,
        "gemini-pacer",
        (secs) => `⏳ Pacing Gemini (${secs}s to clear ${tpmLabel} TPM window)...`,
        ctx,
        ctx.signal,
      );
      pruneExpired(queue, Date.now());
    });

    pi.on("turn_end", (_event: TurnEndEvent, ctx: ExtensionContext) => {
      currentCtx = ctx;
      ctx.ui.setStatus("gemini-pacer", undefined);
    });
  }
}
