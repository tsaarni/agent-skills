# Pi Session Analytics

Deterministic performance, cost economics, cache health, and habit analysis tool for Pi Coding Agent sessions.
Parses session JSONL logs directly from `~/.pi/agent/sessions/` and streams them instead of loading whole files into memory.

## Key Features

- **Active Branch Traversal:** Resolves the conversation DAG tree to follow active conversation paths and prune abandoned turns.
- **Provider-Neutral Cost & Cache Analytics:** Computes actual cost, counterfactual cost without cache, and exact net savings from Pi's logged token usage, with optional time-aware re-pricing via `pricing.json`.
- **Time-Aware Cost Overrides:** A cost-only `pricing.json` (shipped with the tool) re-prices each turn from its own raw token counts against absolute peak/off-peak, weekday, holiday, date-range and promotional rates. Peak/off-peak spend is reported separately. See [Time-aware pricing](#time-aware-pricing-pricingjson).
- **Rate-Limit & Velocity Tracking:** Computes rolling 60-second token throughput (TPM) and request rates (RPM) across all turns. Captures pre-error velocity leading into rate limits (e.g. 429 quota exhaustion).
- **In-Flight Model Switch Awareness:** Tracks model switches (`/model`), breaks down costs per model, and prevents false cache-bust alarms when models change.
- **Time-Range Queries (Macro & Micro Modes):**
  - **Macro Mode:** Daily, weekly, monthly, or historical overviews with model-segmented hourly and weekday activity heatmaps.
  - **Micro Mode:** Deep-dive chronological timelines for specific short windows or incidents ($\le$ 2 hours or `--timeline`).
- **Machine-Readable JSON Output:** `--json` emits structured data for programmatic inspection.

## Build & run

Requires Go 1.24+ (no separate runtime). Run from this directory:

```bash
cd scripts/pi-session-analytics

go run . --latest
go run . /path/to/session.jsonl --timeline
go run . --today
go run . --habits --json
go run . --help
```

The implicit workspace for `--latest` and the bare invocation is the current
working directory. Run from this directory it falls back to the newest session
machine-wide; pass a session path/UUID/rank or `-g` to target a specific one.

## Time-aware pricing (`pricing.json`)

Pi computes cost client-side from a single flat rate set per model and stores the raw token counts in every session turn. That means an analytics tool can **re-price history** against rates that vary by time and date. The Go `pricing` package does this and is applied automatically.

- The repo ships [`pricing.json`](./pricing.json) in this directory; it is the actual override used by default.
- Precedence: `--pricing PATH` → `~/.pi/agent/pricing.json` → the shipped `pricing.json`.
- Disable with `--no-pricing`.

Rates are **absolute** USD per 1M tokens, never a ratio: the peak/off-peak relationship is not guaranteed to scale uniformly across input, output, cache-read and cache-write, so each window states its own numbers. A rule must state all four.

```jsonc
{
  "timezone": "UTC",
  "calendars": { "cn-holidays": ["2026-10-01"] },
  "overrides": {
    "deepseek": {
      "deepseek-flash": {
        "rules": [
          { "id": "peak", "when": {
              "weekly": [ { "days": ["Mon","Tue","Wed","Thu","Fri"], "start": "01:00", "end": "04:00" } ],
              "excludeDates": ["$cn-holidays"] },
            "rates": { "input": 0.30, "output": 1.20, "cacheRead": 0.006, "cacheWrite": 0.30 } },
          { "id": "offpeak",
            "rates": { "input": 0.15, "output": 0.60, "cacheRead": 0.003, "cacheWrite": 0.15 } }
        ]
      }
    }
  }
}
```

A rule matches when it satisfies its optional, ANDed constraints:

| Key | Meaning |
|---|---|
| `weekly` | Recurring windows, each `{days, start, end}`. Omit `start`/`end` for whole days; omit `days` for every day; windows that wrap past midnight are handled. |
| `dates` | Date(s) the rule applies to: `"YYYY-MM-DD"`, `{from,to}`, or `"$calendar"`. |
| `excludeDates` | Date(s) the rule never applies to, same forms (e.g. holidays). |

No `when` block means "always". Rules are evaluated top-to-bottom and the **first match wins**. Calendars are named date lists referenced as `"$name"`. Malformed files, unknown keys, incomplete rates and unknown calendar references fail loudly with the offending path.

When a rule matches, the tool reports the re-priced total next to Pi's logged total and splits spend by matched rule, in the terminal report and in `--json` (`cost.pi_reported_total`, `cost.by_rule`, `pricing`). Models with no matching rule keep Pi's logged cost. `pricing.ComputeCost` reproduces Pi's per-component billing (including 1-hour cache writes at 2x input), so a turn matches Pi exactly when the applicable rate equals the logged one.

## Usage & Agent Instructions

All command recipes, CLI matrices, operational workflows, and LLM diagnostic guidelines are documented in [`AGENTS.md`](./AGENTS.md).

For the complete command-line parameter specification, run:
```bash
go run . --help
```
