/**
 * Cache Monitor Extension
 *
 * Monitors LLM provider prompt caching at a detailed level.
 * Renders a compact single-line card after each assistant response.
 *
 * Command:  /cache-stats  for cumulative session report
 * Log file: .pi/cache-monitor.log
 */

import { appendFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { CONFIG_DIR_NAME, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Box, Text, visibleWidth } from "@earendil-works/pi-tui";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CacheCardData {
  turnIndex: number;
  input: number;
  output: number;
  cacheRead: number;
  cacheWrite: number;
  cacheWrite1h?: number;
  hitRate: number;
  costTotal: number;
  turnSaved: number;
  // cumulative
  cumTurns: number;
  cumCacheRead: number;
  cumCacheWrite: number;
  cumHitRate: number;
  cumCost: number;
  cumSaved: number;
  cumDrops: number;
  dropDetected: boolean;
  hitRateTrend: string;
  inputCost: number;
  outputCost: number;
  cacheReadCost: number;
  markerCount?: number;
  responseStatus?: number;
}

interface CumulativeStats {
  turns: number;
  totalInput: number;
  totalOutput: number;
  totalCacheRead: number;
  totalCacheWrite: number;
  totalCacheWrite1h: number;
  totalCost: number;
  totalInputCost: number;
  totalOutputCost: number;
  totalCacheReadCost: number;
  totalSavings: number;
  cacheDrops: number;
  lastCacheRead: number;
  turnsSinceLastCacheRead: number;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${n}`;
}

function fmtCost(n: number): string {
  if (n >= 1) return `$${n.toFixed(2)}`;
  if (n >= 0.001) return `$${n.toFixed(4)}`;
  return `$${n.toFixed(6)}`;
}

function pct(n: number): string {
  return `${(n * 100).toFixed(0)}%`;
}

function ts(): string {
  return new Date().toISOString().replace("T", " ").slice(0, 19);
}

// ---------------------------------------------------------------------------
// Extension
// ---------------------------------------------------------------------------

export default function (pi: ExtensionAPI) {
  const stats: CumulativeStats = {
    turns: 0,
    totalInput: 0,
    totalOutput: 0,
    totalCacheRead: 0,
    totalCacheWrite: 0,
    totalCacheWrite1h: 0,
    totalCost: 0,
    totalInputCost: 0,
    totalOutputCost: 0,
    totalCacheReadCost: 0,
    totalSavings: 0,
    cacheDrops: 0,
    lastCacheRead: -1,
    turnsSinceLastCacheRead: 0,
  };

  let currentTurnIndex = 0;
  let prevTurnHitRate = -1;
  let pendingMarkerCount: number | undefined;
  let pendingResponseStatus: number | undefined;
  let pendingCardData: CacheCardData | undefined;
  let lastTurnData: CacheCardData | undefined;

  function log(ctx: { cwd: string }, line: string) {
    const dir = join(ctx.cwd, CONFIG_DIR_NAME);
    mkdirSync(dir, { recursive: true });
    const logFile = join(dir, "cache-monitor.log");
    appendFileSync(logFile, `[${ts()}] ${line}\n`, "utf8");
  }

  /** Rebuild cumulative stats from persisted session entries after reload */
  function restoreFromSession(ctx: { sessionManager: { getEntries: () => Array<{ type: string; customType?: string; data?: CacheCardData }> } }): void {
    let lastData: CacheCardData | undefined;
    for (const entry of ctx.sessionManager.getEntries()) {
      if (entry.type === "custom" && entry.customType === "cache-stats" && entry.data) {
        lastData = entry.data;
      }
    }
    if (!lastData) return;

    // Cumulative fields from the last entry (running totals)
    stats.turns = lastData.cumTurns;
    stats.totalSavings = lastData.cumSaved;
    stats.cacheDrops = lastData.cumDrops;
    prevTurnHitRate = lastData.hitRate;

    // Sum per-turn fields from all entries
    stats.totalInput = 0;
    stats.totalOutput = 0;
    stats.totalCacheRead = 0;
    stats.totalCacheWrite = 0;
    stats.totalCacheWrite1h = 0;
    stats.totalCost = 0;
    stats.totalInputCost = 0;
    stats.totalOutputCost = 0;
    stats.totalCacheReadCost = 0;
    for (const e of ctx.sessionManager.getEntries()) {
      if (e.type === "custom" && e.customType === "cache-stats" && e.data) {
        const t = e.data;
        stats.totalInput += t.input;
        stats.totalOutput += t.output;
        stats.totalCacheRead += t.cacheRead;
        stats.totalCacheWrite += t.cacheWrite;
        stats.totalCacheWrite1h += t.cacheWrite1h ?? 0;
        stats.totalCost += t.costTotal;
    stats.totalInputCost += t.inputCost ?? 0;
    stats.totalOutputCost += t.outputCost ?? 0;
    stats.totalCacheReadCost += t.cacheReadCost ?? 0;
      }
    }

    // Approximate drop-tracking state from last entry
    stats.lastCacheRead = lastData.cacheRead;
    stats.turnsSinceLastCacheRead = lastData.cacheRead === 0 ? 1 : 0;
  }

  // ── Inline renderer ──
  pi.registerEntryRenderer<CacheCardData>("cache-stats", (_entry, _renderCtx, theme) => {
    const d = _entry.data;
    if (!d) return new Box(1, 1, (t) => theme.bg("customMessageBg", t));

    const dim = (s: string) => theme.fg("dim", s);
    const good = (s: string) => theme.fg("success", s);
    const warn = (s: string) => theme.fg("warning", s);

    const trendStr = d.hitRateTrend === "up" ? good("↑") : d.hitRateTrend === "down" ? warn("↓") : dim("→");
    const hitRateStr = d.hitRate > 0.3 ? good(pct(d.hitRate) + trendStr) : dim(pct(d.hitRate) + trendStr);

    // Left: what was sent and what was cached
    const left = [
      `sent:${dim(fmt(d.input))}`,
      d.cacheRead > 0 ? good(`cached:${fmt(d.cacheRead)}`) : dim(`cached:0`),
      hitRateStr,
    ].join(" ");

    // Context: total input size
    const totalCtx = d.input + d.cacheRead + d.cacheWrite;
    const ctx = dim(`ctx:${fmt(totalCtx)}`);

    // Mid: what the model replied
    const mid = `reply:${dim(fmt(d.output))}`;

    // Right: cost impact
    const right = [
      dim(`cost:${fmtCost(d.costTotal)}`),
      d.turnSaved > 0 ? good(`saved:${fmtCost(d.turnSaved)}`) : "",
    ].filter(Boolean).join("  ");

    const alerts: string[] = [];
    if (d.dropDetected) alerts.push(warn("CACHE DROP"));
    if (d.markerCount !== undefined && d.markerCount > 0) alerts.push(dim(`m:${d.markerCount}`));

    const line = [dim("cache-monitor:"), left, dim("|"), ctx, dim("|"), mid, dim("|"), right, ...alerts].join("  ");
    const box = new Box(1, 1, (t) => theme.bg("customMessageBg", t));
    box.addChild(new Text(line, 0, 0));
    return box;
  });

  // ── Restore cumulative stats from session on reload ──
  pi.on("session_start", (_event, ctx) => {
    restoreFromSession(ctx);
  });

  // ── before_provider_request: count cache_control markers ──
  pi.on("before_provider_request", (event, _ctx) => {
    const payload = event.payload as Record<string, unknown> | undefined;
    if (!payload) return;
    let count = 0;
    const json = JSON.stringify(payload);
    const m = json.match(/"cache_control"\s*:/g);
    if (m) count = m.length;
    if (payload.prompt_cache_key && count === 0) count = 1;
    pendingMarkerCount = count;
  });

  // ── after_provider_response: capture status + log cache headers ──
  pi.on("after_provider_response", (event, ctx) => {
    pendingResponseStatus = event.status;
    const cacheHeaders: string[] = [];
    for (const [key, value] of Object.entries(event.headers)) {
      const lower = key.toLowerCase();
      if (lower.includes("cache") || lower.includes("x-ratelimit") || lower.includes("x-request-id") || lower === "retry-after") {
        cacheHeaders.push(`  ${key}: ${value}`);
      }
    }
    if (cacheHeaders.length > 0) {
      log(ctx, `Response headers (${event.status}):\n${cacheHeaders.join("\n")}`);
    }
  });

  // ── message_end: accumulate usage (defer card render to turn_end) ──
  pi.on("message_end", (event, ctx) => {
    if (event.message.role !== "assistant") return;

    const usage = event.message.usage;
    const model = event.message.model;

    // Log raw usage for debugging (check what the provider actually returns)
    log(ctx, `RAW usage: ${JSON.stringify(usage)}`);

    // Drop detection
    let dropDetected = false;
    if (stats.lastCacheRead > 0 && usage.cacheRead === 0 && stats.turnsSinceLastCacheRead <= 2) {
      dropDetected = true;
      stats.cacheDrops++;
    }

    // Per-turn
    const totalIn = usage.input + usage.cacheRead + usage.cacheWrite;
    const hitRate = totalIn > 0 ? usage.cacheRead / totalIn : 0;

    // Savings
    const inputPricePerM = ((usage.cost?.input ?? 0) / Math.max(1, usage.input + usage.cacheWrite)) * 1_000_000;
    const cacheReadPricePerM = usage.cost?.cacheRead
      ? (usage.cost.cacheRead / Math.max(1, usage.cacheRead)) * 1_000_000
      : inputPricePerM * 0.1;
    const saved = ((inputPricePerM - cacheReadPricePerM) * usage.cacheRead) / 1_000_000;

    // Cumulative
    stats.turns++;
    stats.totalInput += usage.input;
    stats.totalOutput += usage.output;
    stats.totalCacheRead += usage.cacheRead;
    stats.totalCacheWrite += usage.cacheWrite;
    stats.totalCacheWrite1h += usage.cacheWrite1h ?? 0;
    stats.totalCost += usage.cost?.total ?? 0;
    stats.totalInputCost += usage.cost?.input ?? 0;
    stats.totalOutputCost += usage.cost?.output ?? 0;
    stats.totalCacheReadCost += usage.cost?.cacheRead ?? 0;
    stats.totalSavings += Math.max(0, saved);
    stats.lastCacheRead = usage.cacheRead;
    stats.turnsSinceLastCacheRead = usage.cacheRead === 0 ? stats.turnsSinceLastCacheRead + 1 : 0;

    const cumTotalIn = stats.totalInput + stats.totalCacheRead + stats.totalCacheWrite;
    const cumHitRate = cumTotalIn > 0 ? stats.totalCacheRead / cumTotalIn : 0;

    // Trend: compare to previous turn's hit rate
    let hitRateTrend = "same";
    if (prevTurnHitRate >= 0) {
      const diff = hitRate - prevTurnHitRate;
      hitRateTrend = diff > 0.05 ? "up" : diff < -0.05 ? "down" : "same";
    }
    prevTurnHitRate = hitRate;

    // Save per-turn card data but don't render yet — wait for turn_end (final response)
    pendingCardData = {
      turnIndex: currentTurnIndex + 1,
      input: usage.input,
      output: usage.output,
      cacheRead: usage.cacheRead,
      cacheWrite: usage.cacheWrite,
      cacheWrite1h: usage.cacheWrite1h,
      hitRate,
      costTotal: usage.cost?.total ?? 0,
      turnSaved: Math.max(0, saved),
      cumTurns: stats.turns,
      cumCacheRead: stats.totalCacheRead,
      cumCacheWrite: stats.totalCacheWrite,
      cumHitRate,
      cumCost: stats.totalCost,
      cumSaved: stats.totalSavings,
      cumDrops: stats.cacheDrops,
      dropDetected,
      hitRateTrend,
      inputCost: usage.cost?.input ?? 0,
      outputCost: usage.cost?.output ?? 0,
      cacheReadCost: usage.cost?.cacheRead ?? 0,
      markerCount: pendingMarkerCount,
      responseStatus: pendingResponseStatus,
    };

    // Log
    const w1h = usage.cacheWrite1h ? ` (1h:${fmt(usage.cacheWrite1h)})` : "";
    log(
      ctx,
      [
        `T${pendingCardData.turnIndex} ${model}`,
        `  in: ↑${fmt(usage.input)} R${fmt(usage.cacheRead)} W${fmt(usage.cacheWrite)}${w1h}  out: ↓${fmt(usage.output)}  hit:${pct(hitRate)}  ${fmtCost(pendingCardData.costTotal)}`,
        `  cum: ${stats.turns}t  R${fmt(stats.totalCacheRead)} W${fmt(stats.totalCacheWrite)}  hit:${pct(cumHitRate)}  ${fmtCost(stats.totalCost)}  saved:${fmtCost(stats.totalSavings)}  drops:${stats.cacheDrops}${dropDetected ? " DROP" : ""}`,
      ].join("\n"),
    );

    pendingMarkerCount = undefined;
    pendingResponseStatus = undefined;
  });

  // ── turn_end: render the cache card after the final LLM response ──
  pi.on("turn_end", (event, _ctx) => {
    if (pendingCardData) {
      pi.appendEntry<CacheCardData>("cache-stats", pendingCardData);
      lastTurnData = pendingCardData;
      pendingCardData = undefined;
    }
  });

  // ── turn_start ──
  pi.on("turn_start", (event, _ctx) => {
    currentTurnIndex = event.turnIndex;
    pendingCardData = undefined;
  });

  // ── /cache-stats command ──
  // Renders report as an inline card in the chat (durable, not sent to LLM)
  pi.registerCommand("cache-stats", {
    description: "Show detailed cache monitoring report",
    handler: async (_args, ctx) => {
      const hitRate = stats.totalCacheRead / Math.max(1, stats.totalInput + stats.totalCacheRead + stats.totalCacheWrite);
      pi.appendEntry("cache-report", {
        turns: stats.turns,
        totalInput: stats.totalInput,
        totalCacheRead: stats.totalCacheRead,
        totalCacheWrite: stats.totalCacheWrite,
        totalCacheWrite1h: stats.totalCacheWrite1h,
        totalOutput: stats.totalOutput,
        hitRate,
        totalCost: stats.totalCost,
        totalSavings: stats.totalSavings,
        cacheDrops: stats.cacheDrops,
        timestamp: Date.now(),
      });
      log(ctx, `REPORT — ${stats.turns} turns, hit:${pct(hitRate)}, ${fmtCost(stats.totalCost)}, drops:${stats.cacheDrops}`);
    },
  });

  /** Format three aligned columns: label | number | description */
  function threeCol(label: string, value: string, desc: string, labelW = 10, valW = 8): string {
    const c1 = "  " + label + " ".repeat(Math.max(0, labelW - visibleWidth(label)));
    const c2 = value + " ".repeat(Math.max(0, valW - visibleWidth(value)));
    return c1 + c2 + "  " + desc;
  }

  // ── Report renderer (multi-line with explanations) ──
  pi.registerEntryRenderer<{
    turns: number; totalInput: number; totalCacheRead: number; totalCacheWrite: number;
    totalCacheWrite1h: number; totalOutput: number; hitRate: number;
    totalCost: number; totalSavings: number; cacheDrops: number; timestamp: number;
  }>("cache-report", (_entry, _renderCtx, theme) => {
    const d = _entry.data;
    if (!d) return new Box(1, 1, (t) => theme.bg("customMessageBg", t));

    const dim = (s: string) => theme.fg("dim", s);
    const good = (s: string) => theme.fg("success", s);
    const warn = (s: string) => theme.fg("warning", s);
    const accent = (s: string) => theme.fg("accent", s);

    if (d.turns === 0) {
      const box = new Box(1, 1, (t) => theme.bg("customMessageBg", t));
      box.addChild(new Text(dim("cache: no LLM calls in this session yet"), 0, 0));
      return box;
    }

    const hitPct = pct(d.hitRate);
    const writeExtra = d.totalCacheWrite > 0 ? `  store:${dim(fmt(d.totalCacheWrite))}${d.totalCacheWrite1h > 0 ? ` (1h:${fmt(d.totalCacheWrite1h)})` : ""}` : "";
    const dropLine = d.cacheDrops > 0
      ? warn(`  drops: ${d.cacheDrops}  — cache was unexpectedly emptied`)
      : dim("  drops: 0");

    const header = accent(`cache report — ${d.turns} turns`);

    // Session totals: three aligned columns (label / number / description)
    const totalForHitRate = d.totalInput + d.totalCacheRead + d.totalCacheWrite;
    const l1 = threeCol("sent:",   dim(fmt(d.totalInput)),           "fresh tokens you sent (not cached)");
    const l2 = threeCol("hit:",    good(fmt(d.totalCacheRead)),      "tokens reused from cache  ← saves money");
    const l3 = writeExtra || "";
    const l4 = threeCol("reply:",  dim(fmt(d.totalOutput)),          "tokens the model replied");

    // Summary rows
    const l5 = threeCol("hit rate:", d.hitRate > 0.5 ? good(hitPct) : hitPct, `${fmt(d.totalCacheRead)} of ${fmt(totalForHitRate)} tokens — higher is cheaper`);
    const l6 = threeCol("cost:",   dim(fmtCost(d.totalCost)),        "total spent this session (tokens × price)");
    const l7 = d.totalSavings > 0
      ? threeCol("saved:",  good(fmtCost(d.totalSavings)),    "money saved because cache reads cost less")
      : threeCol("saved:",  dim("$0"),          dim("no savings yet — cache reuse reduces cost"));
    const l8 = d.cacheDrops > 0
      ? threeCol("drops:", warn(fmt(d.cacheDrops)), "cache was unexpectedly emptied")
      : threeCol("drops:", dim("0"), "");

    // Explainers at the bottom
    const l9 = dim("  ──");
    const l10 = dim("  “hit”    = tokens you already sent before — provider reuses them, charges less");
    const l11 = d.totalCacheWrite > 0
      ? dim("  “store”  = new tokens saved for future reuse (only some providers report this)")
      : dim("  “sent”   = fresh tokens not found in cache — charged at full price");
    const l12 = dim("  “drop”   = cache unexpectedly emptied (server restart, timeout, etc.)");
    const l13 = dim("  tokens ≠ characters. 1 token ≈ 4 chars or 0.75 words in English");

    // Color legend
    const colLegend = dim("  ") + good("●") + dim(" hit/saved  ") + warn("●") + dim(" problem  ") + dim("●") + dim(" neutral");

    const allLines = [header, l1, l2, l3, l4, l5, l6, l7, l8, l9, l10, l11, l12, l13, colLegend].filter(Boolean);
    const box = new Box(1, 1, (t) => theme.bg("customMessageBg", t));
    for (const line of allLines) {
      box.addChild(new Text(line, 0, 0));
    }
    return box;
  });
}
