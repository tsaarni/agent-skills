/**
 * List subcommand — output all available tools and skills as JSON.
 *
 * Reads from the pi resource loader (extensions, built-in tools, skills).
 * The agent parses this to discover what tool/skill descriptions exist,
 * then iterates on rewriting them via the test subcommand.
 */

import {
  DefaultResourceLoader,
  getAgentDir,
} from "@earendil-works/pi-coding-agent";

export async function runList(): Promise<void> {
  const loader = new DefaultResourceLoader({
    cwd: process.cwd(),
    agentDir: getAgentDir(),
  });
  await loader.reload();

  // ---- Tools from extensions (built-in + MCP) ----
  const { extensions } = loader.getExtensions();
  const tools: Array<{ name: string; description: string; parameters: unknown }> = [];

  for (const ext of extensions) {
    for (const [, registered] of ext.tools) {
      const t = registered.definition;
      tools.push({
        name: t.name,
        description: t.description,
        parameters: JSON.parse(JSON.stringify(t.parameters)),
      });
    }
  }

  // ---- Skills ----
  const { skills } = loader.getSkills();
  const skillList = skills.map((s) => ({
    name: s.name,
    description: s.description,
  }));

  console.log(JSON.stringify({ tools, skills: skillList }, null, 2));
  process.exit(0);
}
