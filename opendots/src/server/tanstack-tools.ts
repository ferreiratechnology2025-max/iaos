import { toolDefinition } from '@tanstack/ai';
import type {
  BuiltInAgentFactoryContext,
  ToolDefinition,
} from '@copilotkit/runtime/v2';
import { z } from 'zod';
import { governToolExecution } from './tool-governance.js';

export type TanstackToolGovernanceMetadata = {
  effectClass: 'OBSERVE' | 'EFFECT';
  evidenceMode: 'DIGEST_ONLY' | 'STRUCTURED';
};

// This is trusted host-side metadata, not model input. Research tools are
// intentionally classified here so tanstackTools is their sole governance
// wrapper when they enter the model-facing tool set.
const TOOL_GOVERNANCE_METADATA: Readonly<
  Record<string, TanstackToolGovernanceMetadata>
> = {
  list_authorized_spaces: {
    effectClass: 'OBSERVE',
    evidenceMode: 'DIGEST_ONLY',
  },
  search_web: { effectClass: 'OBSERVE', evidenceMode: 'STRUCTURED' },
  read_public_page: { effectClass: 'OBSERVE', evidenceMode: 'STRUCTURED' },
};

export function governanceMetadataForTool(
  name: string,
): TanstackToolGovernanceMetadata {
  return (
    TOOL_GOVERNANCE_METADATA[name] ?? {
      effectClass: 'EFFECT',
      evidenceMode: 'DIGEST_ONLY',
    }
  );
}

export function tanstackTools(tools: ToolDefinition[]) {
  return tools.map(({ name, description, parameters, execute }) => {
    if (!execute) throw new Error(`Server tool has no executor: ${name}`);
    const governance = governanceMetadataForTool(name);
    return toolDefinition({
      name,
      description,
      inputSchema: parameters,
    }).server((input, context) =>
      governToolExecution({
        toolName: name,
        args: input,
        context: {
          resource:
            input &&
            typeof input === 'object' &&
            typeof (input as Record<string, unknown>).spaceId === 'string'
              ? `space/${(input as Record<string, unknown>).spaceId}`
              : `tool/${name}`,
          effectClass: governance.effectClass,
          evidenceMode: governance.evidenceMode,
        },
        executeOriginal: () => execute(input),
      }),
    );
  });
}

// CopilotKit supplies snapshot-bound executors in its factory context. Keep
// their execution tied to that verified snapshot, with validated inputs.
export function learnedSkillTools(
  { learnedSkills, abortSignal }: BuiltInAgentFactoryContext,
  check: () => void,
) {
  return Object.entries(learnedSkills.tools).map(([name, tool]) => {
    const inputSchema =
      name === 'copilotkit_load_skill'
        ? z.object({ skill_name: z.string() })
        : name === 'copilotkit_read_skill_file'
          ? z.object({ skill_name: z.string(), path: z.string() })
          : undefined;
    const execute = tool.execute;
    if (!inputSchema || !execute)
      throw new Error(`Unsupported learned-skill tool: ${name}`);
    return toolDefinition({
      name,
      description: tool.description ?? name,
      inputSchema,
    }).server((input, context) =>
      governToolExecution({
        toolName: name,
        args: input,
        context: { resource: `skill/${name}` },
        executeOriginal: () => {
          check();
          abortSignal.throwIfAborted();
          return execute(input, {
            toolCallId: context?.toolCallId ?? name,
            messages: [],
            abortSignal,
          });
        },
      }),
    );
  });
}
