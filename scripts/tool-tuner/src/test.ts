/**
 * Test subcommand — evaluate a tool or skill definition against a set of prompts.
 *
 * Reads --tool and --prompts files, runs each prompt in an isolated session,
 * and outputs aggregate results as JSON.
 */

import { readFileSync } from "node:fs";
import { runTestPrompt } from "./sdk-helpers.js";
import type { PromptResult, TestOutput } from "./types.js";

export async function runTest(config: {
  model: string;
  toolPath: string;
  promptsPath: string;
}): Promise<void> {
  // Read and parse tool definition
  let toolDef: {
    kind: string;
    name: string;
    description: string;
    parameters?: Record<string, unknown>;
  };

  try {
    toolDef = JSON.parse(readFileSync(config.toolPath, "utf-8"));
  } catch {
    console.error(`Error: cannot read or parse tool file: ${config.toolPath}`);
    process.exit(1);
  }

  if (!toolDef.kind || !["tool", "skill"].includes(toolDef.kind)) {
    console.error(`Error: tool file must have "kind": "tool" or "kind": "skill"`);
    process.exit(1);
  }
  if (!toolDef.name || typeof toolDef.name !== "string") {
    console.error(`Error: tool file missing "name"`);
    process.exit(1);
  }
  if (!toolDef.description || typeof toolDef.description !== "string") {
    console.error(`Error: tool file missing "description"`);
    process.exit(1);
  }
  if (toolDef.kind === "tool") {
    if (!toolDef.parameters || typeof toolDef.parameters !== "object") {
      console.error(`Error: tool definition requires "parameters" (JSON Schema object)`);
      process.exit(1);
    }
  }

  const kind = toolDef.kind as "tool" | "skill";

  // Read and parse prompts
  let prompts: Array<{ prompt: string; should_call: boolean }>;

  try {
    prompts = JSON.parse(readFileSync(config.promptsPath, "utf-8"));
  } catch {
    console.error(`Error: cannot read or parse prompts file: ${config.promptsPath}`);
    process.exit(1);
  }

  if (!Array.isArray(prompts) || prompts.length === 0) {
    console.error(`Error: prompts file must be a non-empty array`);
    process.exit(1);
  }
  for (const p of prompts) {
    if (typeof p.prompt !== "string" || typeof p.should_call !== "boolean") {
      console.error(`Error: each prompt must have "prompt" (string) and "should_call" (boolean)`);
      process.exit(1);
    }
  }

  // Run all prompts
  const results: PromptResult[] = [];
  let tp = 0, fp = 0, fn = 0, tn = 0;

  for (const p of prompts) {
    const { called, sessionFile } = await runTestPrompt(
      kind,
      { name: toolDef.name, description: toolDef.description, parameters: toolDef.parameters },
      config.model,
      p.prompt,
    );

    const passed = called === p.should_call;

    if (called && p.should_call) tp++;
    else if (called && !p.should_call) fp++;
    else if (!called && p.should_call) fn++;
    else tn++;

    results.push({
      prompt: p.prompt,
      should_call: p.should_call,
      called,
      passed,
      sessionFile,
    });
  }

  // Build output
  const output: TestOutput = {
    results,
    tp,
    fp,
    fn,
    tn,
    precision: tp + fp > 0 ? tp / (tp + fp) : 0,
    recall: tp + fn > 0 ? tp / (tp + fn) : 0,
    f1: 0,
  };

  output.f1 =
    output.precision + output.recall > 0
      ? (2 * output.precision * output.recall) / (output.precision + output.recall)
      : 0;

  // Print results
  const status = (passed: boolean) => passed ? "PASS" : "FAIL";
  for (let i = 0; i < results.length; i++) {
    const r = results[i];
    console.log(
      `prompt[${i + 1}]: ${status(r.passed)}  ` +
      `should_call=${r.should_call} called=${r.called}  ` +
      JSON.stringify(r.prompt) +
      `  session=${r.sessionFile}`
    );
  }
  console.log("");
  console.log(
    `TP: ${output.tp}  FP: ${output.fp}  FN: ${output.fn}  TN: ${output.tn}  ` +
    `Precision: ${output.precision.toFixed(2)}  Recall: ${output.recall.toFixed(2)}  F1: ${output.f1.toFixed(2)}`
  );
  process.exit(0);
}
