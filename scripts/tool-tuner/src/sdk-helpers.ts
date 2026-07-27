/**
 * SDK helpers — create isolated pi sessions with a stub tool/skill,
 * run a prompt, and report whether the model called/activated it.
 */

import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Api, Model } from "@earendil-works/pi-ai";
import {
  DefaultResourceLoader,
  ModelRuntime,
  SessionManager,
  createAgentSession,
  defineTool,
  getAgentDir,
  resolveCliModel,
} from "@earendil-works/pi-coding-agent";
import type { AgentSession, ResourceLoader } from "@earendil-works/pi-coding-agent";
import { type TSchema, Type } from "typebox";
import type { TestVerdict, ToolDef } from "./types.js";

// Default built-in tool names included in every test session
const BUILTIN_TOOLS = ["read", "bash", "edit", "write", "grep", "find", "ls"];

let _modelRuntime: ModelRuntime | null = null;
let _noExtLoader: ResourceLoader | null = null;

async function getModelRuntime(): Promise<ModelRuntime> {
  if (!_modelRuntime) {
    _modelRuntime = await ModelRuntime.create();
  }
  return _modelRuntime;
}

function getToolTunerSessionsDir(): string {
  return `${getAgentDir()}/sessions/tool-tuner`;
}

async function getNoExtLoader(): Promise<ResourceLoader> {
  if (!_noExtLoader) {
    const loader = new DefaultResourceLoader({
      cwd: process.cwd(),
      agentDir: getAgentDir(),
      noExtensions: true,
      noSkills: true,
      noPromptTemplates: true,
      noThemes: true,
      noContextFiles: true,
    });
    await loader.reload();
    _noExtLoader = loader;
  }
  return _noExtLoader;
}

export async function resolveModel(modelStr: string): Promise<Model<Api> | undefined> {
  const runtime = await getModelRuntime();
  const resolved = resolveCliModel({ cliModel: modelStr, modelRuntime: runtime });

  if (resolved.error) {
    const [provider, id] = modelStr.split("/");
    if (provider && id) {
      const model = runtime.getModel(provider, id);
      if (model) return model;
    }
    throw new Error(`Cannot resolve model "${modelStr}": ${resolved.error}`);
  }

  return resolved.model;
}

// ---------------------------------------------------------------------------
// Tool session
// ---------------------------------------------------------------------------

async function createToolSession(toolDef: ToolDef, modelStr: string): Promise<AgentSession> {
  const model = await resolveModel(modelStr);
  const modelRuntime = await getModelRuntime();

  const parameters = jsonSchemaToTypeBox(toolDef.parameters);

  const testTool = defineTool({
    name: toolDef.name,
    label: toolDef.name,
    description: toolDef.description,
    parameters,
    execute: async () => ({
      content: [{ type: "text" as const, text: "ok" }],
      details: {},
    }),
  });

  const { session } = await createAgentSession({
    model,
    modelRuntime,
    customTools: [testTool],
    tools: [...BUILTIN_TOOLS, toolDef.name],
    sessionManager: SessionManager.create(process.cwd(), getToolTunerSessionsDir()),
    resourceLoader: await getNoExtLoader(),
  });

  return session;
}

// ---------------------------------------------------------------------------
// Skill session
// ---------------------------------------------------------------------------

const stubReadTool = defineTool({
  name: "read",
  label: "read",
  description: "Read the contents of a file.",
  parameters: Type.Object({
    path: Type.String({ description: "Path to the file to read" }),
    offset: Type.Optional(Type.Number({ description: "Line number to start reading from" })),
    limit: Type.Optional(Type.Number({ description: "Maximum number of lines to read" })),
  }),
  execute: async () => ({
    content: [{ type: "text" as const, text: "ok" }],
    details: {},
  }),
});

interface SkillSession {
  session: AgentSession;
  skillFilePath: string;
}

async function createSkillSession(
  skillDef: { name: string; description: string },
  modelStr: string,
): Promise<SkillSession> {
  const model = await resolveModel(modelStr);
  const modelRuntime = await getModelRuntime();

  const skillDir = mkdtempSync(join(tmpdir(), "tool-tuner-skill-"));
  const skillFile = join(skillDir, "SKILL.md");
  writeFileSync(skillFile, `---\nname: ${skillDef.name}\ndescription: ${skillDef.description}\n---\n`);

  const loader = new DefaultResourceLoader({
    cwd: process.cwd(),
    agentDir: getAgentDir(),
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    additionalSkillPaths: [skillDir],
  });
  await loader.reload();

  const { session } = await createAgentSession({
    model,
    modelRuntime,
    customTools: [stubReadTool],
    tools: BUILTIN_TOOLS,
    sessionManager: SessionManager.create(process.cwd(), getToolTunerSessionsDir()),
    resourceLoader: loader,
  });

  return { session, skillFilePath: skillFile };
}

// ---------------------------------------------------------------------------
// Shared: run a prompt and detect tool/skill call
// ---------------------------------------------------------------------------

interface Detector {
  name: string;
  argsMatch: (args: Record<string, unknown>) => boolean;
}

export async function runTestPrompt(
  kind: "tool" | "skill",
  def: { name: string; description: string; parameters?: Record<string, unknown> },
  modelStr: string,
  prompt: string,
): Promise<TestVerdict> {
  let session: AgentSession;
  let detector: Detector;

  if (kind === "skill") {
    const result = await createSkillSession(def, modelStr);
    session = result.session;
    detector = { name: "read", argsMatch: (a) => a.path === result.skillFilePath };
  } else {
    const toolDef: ToolDef = { name: def.name, description: def.description, parameters: def.parameters! };
    session = await createToolSession(toolDef, modelStr);
    detector = { name: def.name, argsMatch: () => true };
  }

  return runSession(session, prompt, detector);
}

function runSession(session: AgentSession, prompt: string, detector: Detector): Promise<TestVerdict> {
  const sessionFile = session.sessionFile ?? "";

  return new Promise((resolve, reject) => {
    let called = false;

    const unsubscribe = session.subscribe((event) => {
      if (
        event.type === "tool_execution_start" &&
        event.toolName === detector.name &&
        detector.argsMatch(event.args as Record<string, unknown>)
      ) {
        called = true;
      }
    });

    session
      .prompt(prompt)
      .then(() => {
        unsubscribe();
        session.dispose();
        resolve({ called, sessionFile });
      })
      .catch((err) => {
        unsubscribe();
        session.dispose();
        reject(err);
      });
  });
}

// ---------------------------------------------------------------------------
// JSON Schema → TypeBox converter
// ---------------------------------------------------------------------------

function jsonSchemaToTypeBox(schema: Record<string, unknown>): TSchema {
  const type = schema.type;
  const description = schema.description as string | undefined;

  switch (type) {
    case "string": {
      const enumValues = schema.enum as string[] | undefined;
      if (enumValues) return Type.Union(enumValues.map((v) => Type.Literal(v)), { description });
      return Type.String({ description });
    }
    case "number":
      return Type.Number({ description });
    case "integer":
      return Type.Integer({ description });
    case "boolean":
      return Type.Boolean({ description });
    case "array": {
      const items = schema.items as Record<string, unknown> | undefined;
      return Type.Array(items ? jsonSchemaToTypeBox(items) : Type.Unknown(), { description });
    }
    case "object": {
      const properties = schema.properties as Record<string, Record<string, unknown>> | undefined;
      const required = (schema.required as string[]) ?? [];
      const additional = schema.additionalProperties as boolean | undefined;

      if (!properties) return Type.Record(Type.String(), Type.Unknown(), { description });

      const props: Record<string, TSchema> = {};
      for (const [key, propSchema] of Object.entries(properties)) {
        props[key] = jsonSchemaToTypeBox(propSchema);
      }

      if (additional === false) return Type.Object(props, { additionalProperties: false, description });
      return Type.Object(props, { required, description });
    }
    default:
      return Type.Unknown({ description });
  }
}
