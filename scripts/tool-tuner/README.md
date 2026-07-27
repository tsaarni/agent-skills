# tool-tuner

Helps improve descriptions of tools and skills so the AI model calls them
at the right time.

## How it works

1. You ask the agent to read [`AGENTS.md`](AGENTS.md) from this repo.
2. The agent shows you what tools and skills are available. You pick one.
3. You tell it which model to test with.
4. The agent proposes test prompts (some that should trigger the tool,
   some that should not). You approve or tweak them.
5. The agent runs the test and shows you the results.
6. If something fails, the agent reads the model's reasoning, suggests a
   better description, and waits for your approval.
7. Repeat steps 5-6 until everything passes.
