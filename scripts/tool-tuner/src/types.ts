export interface ToolDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface TestVerdict {
  called: boolean;
  sessionFile: string;
}

export interface PromptResult {
  prompt: string;
  should_call: boolean;
  called: boolean;
  passed: boolean;
  sessionFile: string;
}

export interface TestOutput {
  results: PromptResult[];
  tp: number;
  fp: number;
  fn: number;
  tn: number;
  precision: number;
  recall: number;
  f1: number;
}
