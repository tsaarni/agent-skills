#!/usr/bin/env node
/**
 * tool-tuner — LLM agent toolkit for evaluating tool descriptions.
 *
 * The agent discovers tools/skills via `list`, then iterates on tool
 * descriptions by running `test` against a set of prompts.
 */

import { runList } from "./list.js";
import { runTest } from "./test.js";

function usage(): never {
  console.log(`tool-tuner — evaluate tool & skill descriptions via isolated pi sessions

USAGE:
  tool-tuner list
      Print all available tools and skills as JSON.

  tool-tuner test --tool <path.json> --prompts <path.json> --model <provider/model>
      Read tool/skill definition and prompts from JSON files, create isolated
      pi sessions, run each prompt, and print aggregate results as JSON.

      tool.json format:  {"kind":"tool|skill","name":"...","description":"...",...}
      prompts.json format:  [{"prompt":"...","should_call":true|false}, ...]

      stdout:
        {"results":[...],"tp":N,"fp":N,"fn":N,"tn":N,"precision":N,"recall":N,"f1":N}

Examples:
  tool-tuner list
  tool-tuner test --tool tool.json --prompts prompts.json --model deepseek/deepseek-v4-flash
`);
  process.exit(1);
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length === 0 || args.includes("--help") || args.includes("-h")) {
    usage();
  }

  const subcommand = args[0];
  const options: Record<string, string> = {};

  for (let i = 1; i < args.length; i++) {
    if (args[i].startsWith("--")) {
      const key = args[i].slice(2);
      const next = args[i + 1];
      if (next && !next.startsWith("-")) {
        options[key] = next;
        i++;
      } else {
        options[key] = "true";
      }
    }
  }

  switch (subcommand) {
    case "list": {
      await runList();
      break;
    }

    case "test": {
      if (!options.tool) {
        console.error("Error: --tool <path> is required");
        process.exit(1);
      }
      if (!options.prompts) {
        console.error("Error: --prompts <path> is required");
        process.exit(1);
      }
      if (!options.model) {
        console.error("Error: --model <provider/model> is required (use `pi --list-models` to see available models)");
        process.exit(1);
      }

      await runTest({
        model: options.model,
        toolPath: options.tool,
        promptsPath: options.prompts,
      });
      break;
    }

    default:
      console.error(`Unknown subcommand: "${subcommand}"`);
      usage();
  }
}

main().catch((err) => {
  console.error("Fatal error:", err);
  process.exit(1);
});
