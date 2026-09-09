# Pi Session Analytics: Agent Operating Guide

Operating instructions for AI coding agents auditing Pi session logs using `analyze.py`.

---

## 1. Core Directives & Execution

When asked to audit a session, check costs, review developer habits, or diagnose an API failure:

1. **Never parse raw `.jsonl` files manually.** Always run `analyze.py`—it resolves the conversation DAG, prunes abandoned branches, tracks cache state transitions across errors, and calculates exact cost and token metrics.
2. **Execution Safety**: `analyze.py` is read-only and safe to execute on active, running sessions.
3. **Command Invocation**:
   ```bash
   # Run via uv (preferred) or python3:
   uv run <path-to>/analyze.py [target] [presentation modifiers]
   # or
   python3 <path-to>/analyze.py [target] [presentation modifiers]
   ```
4. **Target Selection vs. Presentation Modifiers**:
   * **Targets**: Target a single session (e.g. `1`, `<UUID>`, `--latest`, `-g`) OR a time window (`--today`, `--yesterday`, `--since`).
   * **Presentation Modifiers**:
     * **Default (Terminal Text)**: Compact 5-section executive scorecard.
     * **`--verbose`**: Full unabridged detail (all spikes, all tool error snippets, complete custom node listings).
     * **`--timeline` (or `--delve`)**: Chronological turn-by-turn stream table (Time, Turn, Model, Fresh Inp, Total Ctx, Rolling TPM, Note/Tools).
     * **`--json`**: Machine-readable JSON metrics. When combined with `--verbose` or `--timeline`, includes the complete `turns` array.
5. **Synthesize Insights, Do Not Parrot Raw Numbers**: Explain *why* context inflated, *why* a rate limit hit, and provide *concrete remedies* following the synthesis structure in Section 3.

---

## 2. Command Selection Matrix

Map the user's intent to the appropriate command:

| User Intent / Request | Command |
|---|---|
| **Discovery & Navigation** | |
| "List recent sessions" / "What ran today?" | `analyze.py -l` |
| "Show failing sessions" / "List errors" | `analyze.py -l --errors-only` |
| "Filter sessions to a specific project" | `analyze.py -l --workspace /path/to/project` |
| "Show all workspace projects" | `analyze.py --workspaces` |
| "Audit all-time habits and model usage" | `analyze.py --habits` |
| **Single Session Audits** | |
| "Audit current workspace session" / "Check costs" | `analyze.py` (or `analyze.py --latest`) |
| "Audit latest session machine-wide" | `analyze.py -g` (or `analyze.py --latest -g`) |
| "Analyze session #N from the list" | `analyze.py N` (e.g. `analyze.py 1` for latest listed) |
| "Analyze session by UUID" | `analyze.py <UUID_PREFIX>` (e.g. `analyze.py 01a08446`) |
| "Audit specific session file" | `analyze.py ~/.pi/agent/sessions/<workspace-hash>/session.jsonl` |
| "Unabridged audit (all spikes & tool errors)" | Add `--verbose` to any single-session command |
| "Turn-by-turn timeline of session" | Add `--timeline` (e.g. `analyze.py 1 --timeline` or `analyze.py -g --timeline`) |
| "Complete un-sampled turn stream" | Add `--timeline --verbose` (e.g. `analyze.py 1 --timeline --verbose`) |
| **Macro Reviews (Time Windows)** | |
| "What did I spend today?" / "Today's review" | `analyze.py --today` |
| "How did yesterday go?" | `analyze.py --yesterday` |
| "Review this week / month" | `analyze.py --this-week` (or `--this-month`) |
| "Review last week / month" | `analyze.py --last-week` (or `--last-month`) |
| "Review trailing N days" | `analyze.py --days <N>` (e.g. `--days 30`) |
| "Isolate macro review to one workspace" | Add `--workspace /path/to/project` to any time-window command |
| **Micro Incident Delves (Cross-Session Timelines)** | |
| "Show today's incident timeline / failures" | `analyze.py --today --timeline` |
| "Why did an error occur around HH:MM?" | `analyze.py --since "YYYY-MM-DD HH:MM" --until "YYYY-MM-DD HH:MM"` *(windows $\le 2\text{h}$ auto-delve)* |
| **Programmatic Output** | |
| "Machine-readable data" | Add `--json` to any command above |
| "Full turns array in JSON" | Add `--json --verbose` or `--json --timeline` |

> [!NOTE]
> Global `--timeline` across multiple sessions requires an explicit time boundary (such as `--today` or `--since`) to prevent unbounded machine-wide disk scans. When targeting a specific session (e.g. `analyze.py 1 --timeline`), no time filter is needed.

---

## 3. Response Delivery & Archetypes

When reporting findings to the user, follow this 5-point template:

### Standardized Response Template

1. **Executive Scorecard & Archetype**:
   * State the diagnosed session archetype (table below) and overall health verdict.
   * State total spend, cache hit rate, and active turns.
2. **Economic Breakdown & Counterfactual Savings**:
   * Compare actual cost vs. cost without cache.
   * Quantify avoidable waste (e.g., *"3 idle expirations re-billed $0.45 in fresh tokens"*).
3. **Velocity & Rate-Limit Diagnostics**:
   * Peak 60s TPM/RPM vs. safe capacity ceiling for the context size.
   * Root cause of any HTTP 429 quota exhaustion or cache evictions.
4. **Tool Hygiene & Context Friction**:
   * Identify the largest context spikes (from *Top Context Spikes*) and tool failure rates.
   * Note whether noisy shell commands or un-sliced file reads bloated context.
5. **Top 2 High-Leverage Prescriptions**:
   * Deliver exactly two concrete, actionable commands/tips for the user's next turn (e.g. run `/compact` immediately, wait 60s to drain bucket, or use line-slice reads).

---

### Session Archetype Reference

Classify sessions into one of these archetypes to ground your diagnosis:

| Archetype | Key Signals | Root Cause | High-Leverage Prescription |
|---|---|---|---|
| **The Context Snowball** | Context $> 250\text{k}$, high cache hit ($>90\%$), repeated 429 quota errors during tool bursts. | Unchecked accumulation of tool output history; autonomous execution speed ($<6\text{s}$/turn) exceeds provider sliding-window TPM ceiling. | Run `/compact` or `/new` before launching next multi-turn task; enforce line-slice reading. |
| **The Tool Thrasher** | High autonomy ratio ($> 12\times$) with high tool error rate ($> 10\%$) or repetitive edits to the same file. | Agent is looping blindly through failing tool calls without making progress. | Introduce an early circuit breaker (prompt user after 2 consecutive tool errors); improve error feedback. |
| **The Idle Cache Hemorrhage** | Cache hit drops where `time_gap_sec > 300s`, re-billing $150\text{k}\text{--}400\text{k}$ fresh tokens after human pauses. | User takes breaks or joins meetings between turns, repeatedly expiring provider 5-minute cache TTL. | Run `/compact` before stepping away so post-break re-seeds are inexpensive. |
| **The Model-Hopping Trap** | In-flight `/model` switches on deep context ($>100\text{k}$), triggering massive fresh input re-seeds. | LLM caches cannot be shared across model architectures; switching models invalidates KV cache state. | Commit to one model for the active execution loop; switch models only at clean session boundaries. |
| **The Focused Implementation** | High autonomy ratio ($10\text{--}25\times$), low tool errors ($< 5\%$), high cache hit ($>95\%$), task completed. | Agent performed complex multi-file engineering autonomously with clean tool hygiene. | Healthy autonomous execution; keep tasks bounded. |
| **The Lean Flow** | Total context $< 100\text{k}$, hit rate $> 95\%$, low tool errors, rapid interactive dialogue. | Well-scoped requests, prompt compaction, minimal context bloat. | Exemplary interactive session; maintain discipline. |

---

## 4. Diagnostic Rules & Technical Thresholds

### Rate-Limit Dynamics & The Machine-Speed Trap
* **The Sliding Window Formula**:
  $$\text{Rolling Throughput (TPM)} = \text{Context Size} \times \text{Turn Frequency (RPM)}$$
* **Autonomous Machine Speed**:
  In tool loops (`assistant` $\rightarrow$ `toolResult` $\rightarrow$ `assistant`), turns execute automatically in $2\text{--}6$ seconds without human intervention.
* **Why Compaction is the Only Lever**:
  Telling a user to "slow down" does not work during autonomous execution. If context is $400\text{k}$, 5 autonomous turns in 30 seconds dispatch $2,000,000$ tokens, guaranteeing a 429 quota crash. Compacting to $100\text{k}$ allows 10 rapid turns without exceeding quota.
* **Deterministic Safe Capacity Table** (calibrated for 2,000,000 TPM limit):

  $$\text{Maximum Safe RPM} \le \frac{\text{TPM Quota}}{\text{Context Size}}$$

  | Context Size | Maximum Safe RPM | Min Turn Interval | Autonomous Burst Risk |
  |---|---|---|---|
  | **$50\text{k}$ tokens** | 40 turns/min | $\ge 1.5\text{s}$ | **Zero risk**: Safe at maximum machine speed. |
  | **$100\text{k}$ tokens** | 20 turns/min | $\ge 3.0\text{s}$ | **Very low**: Safe for almost all automated tool chains. |
  | **$200\text{k}$ tokens** | 10 turns/min | $\ge 6.0\text{s}$ | **Moderate**: Consecutive file edits can saturate quota. |
  | **$300\text{k}$ tokens** | 6 turns/min | $\ge 10.0\text{s}$ | **High**: Fast tool execution will trip 429. |
  | **$400\text{k}$ tokens** | 4 turns/min | $\ge 15.0\text{s}$ | **Guaranteed collision**: Any burst $>4$ calls crashes. |

* **The 429 Re-Prompt Trap**:
  Pi halts immediately on 429 errors (`stopReason: error`). If the user re-prompts immediately, the 60-second sliding window has not drained, dispatching another massive turn and re-tripping the quota.
  * *Prescription*: Instruct user to wait **60 seconds** before re-prompting, or run `/compact`.

---

### Context Caching Mechanics & Error State Persistence
* **Minimum Cache Breakpoints**:
  * **Gemini**: Requires $\ge 32,768$ tokens. Turns below 32k report `cacheRead: 0`. This is expected.
  * **Claude**: Requires $\ge 1,024$ tokens.
* **5-Minute Idle TTL**:
  Server-side context cache expires after $\sim 5$ minutes (300s) of inactivity.
  * If `time_gap_sec > 300s`, `cacheRead: 0` is expected idle expiration, not prefix corruption.
* **Cache State Across Failed 429 Turns**:
  When a turn fails with a 429 error, 0 tokens are billed, but the session history remains warm at the provider until the 5-minute TTL expires. `analyze.py` preserves `last_successful_total_ctx` and `last_successful_assistant_ts` across error turns so that human pauses following 429 crashes correctly diagnose idle expirations vs. clean prefix evictions.
* **Server Evictions vs. Prefix Busts**:
  * **Prefix Bust (Client-side)**: Any edit to prior conversation history, system prompt, or message nodes invalidates cache from token 0.
  * **Server Eviction (Provider-side)**: Provider cluster drops cache or routes to an unprimed pod at deep context ($>300\text{k}$), tagged as `[WARN] Server Evictions`.
  * **Double Penalty**: An eviction re-bills full context at fresh rates ($0.15–$0.30/turn) *and* injects all tokens into the 60-second sliding window, often causing a subsequent 429.
* **Model Switches**:
  Switching models (`/model`) invalidates KV cache across architectures; the first turn re-seeds fresh input (`[INFO] Model Switch Re-seed`).

---

### Tool Hygiene & Compaction Economics
* **Line-Slice Reading vs. Whole-File Dumps**:
  * *Symptom*: Spikes in `Top Context Spikes` caused by `read` dumping $> 15\text{k}$ tokens in one turn.
  * *Prescription*: Instruct agent/user to use line slicing (`StartLine`/`EndLine` or `head`/`grep`).
* **Noisy Shell Command Filtering**:
  * *Symptom*: Spikes from `bash` tool dumping compiler, linter, or test runner output.
  * *Prescription*: Filter commands (`tail -n 30`, `grep -E "ERROR|FAIL"`, or silent flags).
* **Compaction ROI Threshold**:
  * When session context exceeds **$200\text{k}$ tokens** and task requires $\ge 5$ more turns, compacting to $\sim 20\text{k}$ saves **$> 70\%$** of remaining spend, cuts latency, and prevents 429 quota exhaustion.

---

### Collaboration & Habit Metrics
* **Autonomy Ratio** ($\frac{\text{Assistant Turns}}{\text{User Prompts}}$):
  * **$1\text{--}3\times$ (Interactive Steering)**: Clarification, Q&A, or strict step-by-step guidance.
  * **$4\text{--}9\times$ (Balanced Feature Flow)**: Standard collaborative engineering.
  * **$10\text{--}25\times$ (Deep Autonomous Flow)**: Healthy for multi-file refactoring and test-driven fixes. Only problematic if coupled with tool error rate $> 10\%$ (*Tool Thrasher*).
* **User Review / Think Time** (gap between assistant finish and user prompt):
  * **$< 30\text{s}$**: Active pairing; keeps cache primed.
  * **$30\text{s}\text{--}3\text{m}$**: Thorough diff inspection; safely within 5-minute TTL.
  * **$> 5\text{m}$**: Human break/meeting; drops cache. If context $>200\text{k}$, warn user to `/compact` before long pauses.

> **Provider & Model Notice**: Numerical rate limit thresholds (2M TPM) and cache breakpoints (32k) above are calibrated for Google Gemini Flash. When auditing Claude, DeepSeek, or OpenAI sessions, adapt expected TPM caps and cache thresholds to that provider's specifications.

---

## 5. Output Format & Data Reference

### Terminal Report Presentation Modes
1. **Executive Scorecard (Default)**:
   Compact 5-section report: Cost Economics, Cache Health & Velocity (top 8 errors), Tool Efficiency (top 4 failures), Top Spikes (top 5), and User Habits.
2. **Unabridged Report (`--verbose`)**:
   Expands the scorecard to display all API rate errors, all failed tool snippets, all custom graph nodes, and top 15 spikes.
3. **Chronological Turn Timeline (`--timeline`)**:
   Renders a tabular stream of turns showing timestamp, turn index, model, fresh tokens, total context, rolling 60s TPM, and contextual notes (e.g. `[FAIL] 429 RATE LIMIT`, `[IDLE TTL]`, `[SERVER EVICT]`, spikes, tools). Sampled by default; append `--verbose` to view every turn without sampling.

---

### Programmatic JSON Schema (`--json`)
When `--json` is supplied, `analyze.py` returns an object with the following exact key structure:

```json
{
  "meta": {
    "id": "string (UUID)",
    "cwd": "string (workspace path)",
    "timestamp": "string (ISO timestamp)"
  },
  "first_ts": "string (ISO timestamp)",
  "last_ts": "string (ISO timestamp)",
  "wall_duration_sec": 4235.0,
  "total_raw_entries": 526,
  "active_branch_entries": 525,
  "abandoned_entries": 1,
  "models": {
    "gemini-3.8-flash": 261
  },
  "assistant_turns_count": 261,
  "user_prompts_count": 32,
  "autonomy_ratio": 8.16,
  "tokens": {
    "fresh_input": 2711412,
    "cache_read": 56974936,
    "output": 168671,
    "reasoning": 70686,
    "reasoning_pct": 41.9,
    "total": 59855019,
    "hit_rate_pct": 95.46
  },
  "cost": {
    "total": 6.9392,
    "fresh_input": 2.0336,
    "cache_read": 4.2731,
    "output": 0.6325,
    "without_cache": 45.3973,
    "savings": 38.4581,
    "savings_pct": 84.71
  },
  "cost_by_model": {
    "gemini-3.8-flash": 6.9392
  },
  "velocity": {
    "peak_tpm_60s": 3477382,
    "peak_tpm_turn": 232,
    "peak_rpm_60s": 21,
    "api_errors": [
      {
        "turn": 149,
        "ts": "string (ISO timestamp)",
        "total_ctx": 0,
        "rolling_tpm": 2209693,
        "rolling_rpm": 8,
        "snippet": "429 Too Many Requests (Rate Limit)",
        "raw_error": "..."
      }
    ]
  },
  "cache_health": {
    "durable_custom_nodes": 0,
    "custom_nodes_details": [],
    "cache_busts": [],
    "prefix_busts": [],
    "server_evictions": [
      {
        "turn": 109,
        "total_ctx": 193957,
        "fresh_input": 193957,
        "cost": 0.1457,
        "time_gap_sec": 6.84,
        "reason": "clean_prefix_eviction"
      }
    ],
    "idle_expirations": [
      {
        "turn": 37,
        "total_ctx": 110615,
        "fresh_input": 110615,
        "cost": 0.0834,
        "time_gap_sec": 400.27
      }
    ],
    "model_switches": []
  },
  "tools": {
    "total_calls": 231,
    "by_name": { "bash": 136, "read": 53, "edit": 22, "grep": 13, "write": 5 },
    "error_count": 9,
    "error_rate_pct": 3.9,
    "errors": [
      { "tool": "read", "snippet": "..." }
    ]
  },
  "user_habits": {
    "avg_prompt_len": 401.0,
    "think_times_count": 31,
    "avg_think_sec": 91.2,
    "median_think_sec": 54.2,
    "max_think_sec": 437.0
  },
  "top_spikes": [
    {
      "turn": 246,
      "fresh_input": 403799,
      "total_ctx": 403799,
      "cost": 0.3058,
      "trigger": "toolResult:read"
    }
  ],
  "recent_turns": [ ... ],
  "turns": [ ... ]
}
```

*(Note: The complete `turns` array is included in `--json` output when `--verbose` or `--timeline` is active).*
