# Pi Session Analytics

Deterministic performance, cost economics, cache health, and habit analysis tool for Pi Coding Agent sessions.
Parses session JSONL logs directly from `~/.pi/agent/sessions/` with zero runtime dependencies.

## Key Features

- **Active Branch Traversal:** Resolves the conversation DAG tree to follow active conversation paths and prune abandoned turns.
- **Provider-Neutral Cost & Cache Analytics:** Derives per-turn model rates dynamically from Pi's logged usage and `~/.pi/agent/models-store.json`. Calculates actual cost, cost without cache, and exact net savings.
- **Rate-Limit & Velocity Tracking:** Computes rolling 60-second token throughput (TPM) and request rates (RPM) across all turns. Captures pre-error velocity leading into rate limits (e.g. 429 quota exhaustion).
- **In-Flight Model Switch Awareness:** Tracks model switches (`/model`), breaks down costs per model, and prevents false cache-bust alarms when models change.
- **Time-Range Queries (Macro & Micro Modes):**
  - **Macro Mode:** Daily, weekly, monthly, or historical overviews with model-segmented hourly and weekday activity heatmaps.
  - **Micro Mode:** Deep-dive chronological timelines for specific short windows or incidents ($\le$ 2 hours or `--timeline`).
- **Machine-Readable JSON Output:** `--json` emits structured data for programmatic inspection.

## Prerequisites

- Python 3.11+ (standard library only, no external packages required)

## Usage & Agent Instructions

All command recipes, CLI matrices, operational workflows, and LLM diagnostic guidelines are documented in [`AGENTS.md`](./AGENTS.md).

For the complete command-line parameter specification, run:
```bash
python3 analyze.py --help
```
