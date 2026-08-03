You are evaluating and improving tool and skill descriptions so that models
correctly decide when to call or activate them.  Use the `tool-tuner` CLI. **Always run commands via `pnpm tool-tuner`** (from the project root — the CLI is not installed globally.

## ⚠️ HARD RULES

### NEVER pick a model yourself

**You must always ask the user which model to test with.**

If the user says something like "use deepseek-v4-flash" and you are unsure which
provider it maps to (e.g. `deepseek/deepseek-v4-flash` vs `openrouter/deepseek/deepseek-v4-flash`),
**ask the user to clarify** rather than guessing.

Do not:
- Assume a model from context
- Pick one you think is "close enough"
- Use a default model
- Proceed with `tool-tuner test` without a clear, confirmed model string from the user

Run `pi --list-models` to show available models, then present the relevant options
to the user and let them decide.

## Commands

### `list` — discover tools and skills

```bash
pnpm tool-tuner list
```

Returns JSON with two arrays:

```json
{
  "tools": [
    {"name": "read", "description": "...", "parameters": {"type": "object", ...}},
    ...
  ],
  "skills": [
    {"name": "c64ctl", "description": "..."},
    ...
  ]
}
```

Ask the user which to evaluate.

### `test` — evaluate a definition against a set of prompts

```bash
pnpm tool-tuner test --tool /full/path/tool.json --prompts /full/path/prompts.json --model <provider/model>
```

- `--tool` — a JSON file with the tool or skill definition. **Use an absolute path** (e.g. `/full/path/tool.json`) to avoid cwd confusion.
- `--prompts` — a JSON file with an array of prompts and expected outcomes. **Use an absolute path** (e.g. `/full/path/prompts.json`).
- `--model` — required. **See Hard Rules above: you must always ask the user which model to test with.** Do not pick one yourself. Use `pi --list-models` to show available models to the user.

#### tool.json — tool or skill definition

**Tool:**
```json
{
  "kind": "tool",
  "name": "mcp_mcpuppet_search",
  "description": "Run web search and return page content as markdown.",
  "parameters": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {"type": "string"}
    }
  }
}
```

**Skill:**
```json
{
  "kind": "skill",
  "name": "c64ctl",
  "description": "Use to control C64 hardware: run PRG, T64, D64, CRT, or SID files, mount disks, inspect screen/RAM, type text, or execute 6502 assembly."
}
```

- `kind` — `"tool"` or `"skill"` (required). Tools have `parameters` (JSON Schema object). Skills do not.
- `name` — matches what the model sees in the system prompt
- `description` — the text the model evaluates to decide whether to call/activate

#### prompts.json — test prompts

```json
[
  {"prompt": "What is the latest React version?", "should_call": true},
  {"prompt": "What is 2+2?", "should_call": false}
]
```

- `prompt` — a realistic user message
- `should_call` — `true` if the tool/skill should be triggered, `false` otherwise

#### Output

Creates an isolated pi session per prompt, runs each, and outputs plain text:

```
prompt[1]: PASS  should_call=true called=true  "What is the latest React version?"  session=~/.pi/agent/sessions/tool-tuner/...jsonl
prompt[2]: PASS  should_call=false called=false  "What is 2+2?"  session=~/.pi/agent/sessions/tool-tuner/...jsonl
...
TP: 1  FP: 0  FN: 0  TN: 1  Precision: 1.00  Recall: 1.00  F1: 1.00
```

- `called` — the model invoked the tool (`kind: "tool"`) or read the skill file (`kind: "skill"`)
- `passed` — `called === should_call`
- `session` — path to the full session log, readable with the `read` tool to inspect model thinking

#### Understanding the metrics

The output shows four counters and three derived metrics:

| Counter | Meaning |
|---------|---------|
| **TP** (True Positives) | Prompts that SHOULD call the tool AND the model DID call it ✅ |
| **FP** (False Positives) | Prompts that should NOT call the tool BUT the model DID call it ❌ |
| **FN** (False Negatives) | Prompts that SHOULD call the tool BUT the model did NOT call it ❌ |
| **TN** (True Negatives) | Prompts that should NOT call the tool AND the model did NOT call it ✅ |

| Metric | Formula | Range | What it tells you |
|--------|---------|-------|-------------------|
| **Precision** | TP / (TP + FP) | 0–1 | How often does *calling the tool* mean it was the right call? 1.00 = never calls when it shouldn't. |
| **Recall** | TP / (TP + FN) | 0–1 | How often does the model *actually call* the tool when it should? 1.00 = always calls when needed. |
| **F1** | 2×(P×R)/(P+R) | 0–1 | Harmonic mean of precision & recall. The single-number summary of tool description quality. 1.00 = perfect. |

**Goal: F1 = 1.00** (every prompt gets the right verdict).

- **FP > 0** → description is too broad (model calls the tool when it should not). Narrow the description with exclusions and boundaries.
- **FN > 0** → description is too narrow or vague (model misses cases where it should call). Widen or rephrase it.

### Schema files

The project includes JSON Schema files for documentation:

- `schemas/tool.schema.json` — validates the tool/skill definition file
- `schemas/prompts.schema.json` — validates the prompts file

The code does not validate against these. They exist so the agent and user understand the format.

## Workflow

### 1. Gather test prompts

Generate a proposed set of prompts yourself and present them to the user.
Think critically about what makes a good set:

- **Positive prompts** — user messages that clearly need the tool/skill to answer correctly.
- **Negative prompts** — tricky ones that are domain-adjacent but answerable without the
tool/skill, or meta-questions about the tool/skill itself.

Aim for 4–8 total. For each prompt, decide a verdict (`should_call: true/false`).
Be thoughtful: a good negative prompt is one that looks like it *could* trigger the
tool but shouldn't. A good positive prompt is unambiguous that it needs the tool.

Present your proposed set to the user as a list with reasoning for each prompt
(why it should or should not trigger). Ask the user if the set looks good or if
they want to add, remove, or change any prompts. Do not proceed until the user
confirms.

### 2. Write the definition file

If extracting from the system, use `tool-tuner list` to get the current definition.
Create a `tool.json` file with `kind`, `name`, `description`, and `parameters` (for tools).

Write `tool.json` and `prompts.json` to a **temporary directory** (e.g., `mktemp -d`),
not the project root. This keeps scratch files out of the repo.

### 3. Run the evaluation

```bash
pnpm tool-tuner test --tool /full/path/tool.json --prompts /full/path/prompts.json --model <provider/model>
```

Use absolute paths for the JSON files.

Review the output. Focus on `failures` — results where `passed` is `false`:

| `should_call` | `called` | Verdict        |
|---------------|----------|----------------|
| true          | false    | ❌ false negative — description is too narrow or vague |
| false         | true     | ❌ false positive — description is too broad   |

### 4. Analyse failures

For each failed prompt, read its session log to see the model's reasoning:

```bash
read ~/.pi/agent/sessions/tool-tuner/<session-id>.jsonl
```

Look at the model's `thinking` field to understand why it decided to call (or not
call) the tool. Common failure patterns:

- **False negative** (`should_call: true`, `called: false`): the description may be
  too vague, too narrow, or miss the specific trigger words the prompt contains.

- **False positive** (`should_call: false`, `called: true`): the description is too
  broad — add exclusions, boundary cases, or explicit "DO NOT use" language.

### 5. Present findings to user

Before rewriting, **summarise the results for the user**:

- Show which prompts passed and which failed (PASS/FAIL, should_call vs called).
- For each failure, read the session log, then explain your analysis (speculate on
  why the model got it wrong based on the thinking trace).
- Propose a specific rewrite to the `description` field and ask for approval.

Do NOT rewrite the description or re-run the evaluation without user confirmation.

### 6. Rewrite and retest

Once the user approves, rewrite the `description` field in `tool.json`. Keep it
concise (1–3 sentences). Then run the evaluation again. Repeat until all prompts pass
(f1 = 1.0).

### 7. Final sweep

After iterating, re-run the full set of prompts against the final description
to confirm everything still passes.

## Tips

- **No need for real tools.** This is simulated test, there is no need actually set up e.g.
  MCP server to run the simulation of the tool calls.
- **Be specific about boundaries.** A good description says what the tool/skill does
  AND what it does not do.
- **Use example triggers.** Phrases like "for real-time lookups such as news or
  weather" help the model pattern-match.
- **Exclude meta-questions.** If prompts like "How do I use tool X?" are false
  positives, add "Do NOT use for questions about tools themselves."
- **Keep `name` and `parameters` stable** while iterating on `description` only.
- **Test against the model that will use the tool in production.** Different
  models react differently to the same description.
