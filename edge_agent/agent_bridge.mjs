// Pi owns agent execution, tools, compaction and recovery. This host only
// supplies the local model, RAM storage and the application's approval policy.
import { realpathSync, existsSync } from 'node:fs';
import { dirname, resolve, relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';

const packageURL = new URL('../.runtime/pi/node_modules/@earendil-works/pi-coding-agent/dist/', import.meta.url);
const sdk = await import(new URL('index.js', packageURL));
const { createLocalPowerShellOperations, createPowerShellToolDefinition } = await import(new URL('core/tools/powershell.js', packageURL));
const { truncateTail } = await import(new URL('core/tools/truncate.js', packageURL));

// Read precisely one LF-delimited initialization record. Leave subsequent
// bytes for Pi's own RPC reader; Unicode line separators are ordinary data.
const init = await new Promise((accept, reject) => {
  let bytes = Buffer.alloc(0);
  function readable() {
    let chunk;
    while ((chunk = process.stdin.read()) !== null) {
      bytes = Buffer.concat([bytes, chunk]);
      const end = bytes.indexOf(10);
      if (end < 0) continue;
      process.stdin.off('readable', readable);
      if (end + 1 < bytes.length) process.stdin.unshift(bytes.subarray(end + 1));
      try { accept(JSON.parse(bytes.subarray(0, end).toString('utf8'))); }
      catch (error) { reject(error); }
      return;
    }
  }
  process.stdin.on('readable', readable);
  process.stdin.once('end', () => reject(new Error('Missing agent initialization')));
});

const root = realpathSync(init.cwd);
const agentDir = fileURLToPath(new URL('../.runtime/pi-state', import.meta.url));
// Pi's exported ModelRuntime supports a CredentialStore. No provider secrets
// or sessions are written by this host.
const { AuthStorage } = await import(new URL('core/auth-storage.js', packageURL));
const modelRuntime = await sdk.ModelRuntime.create({
  credentials: AuthStorage.inMemory(), modelsPath: null,
  allowModelNetwork: false, refreshOnCreate: false,
});
modelRuntime.registerProvider('local', {
  api: 'openai-completions', baseUrl: init.model_url + '/v1', apiKey: 'local',
  models: [{ id: init.model, name: init.model_name, reasoning: true, input: ['text'],
    contextWindow: init.context_size, maxTokens: Math.min(4096, Math.floor(init.context_size / 4)),
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    compat: { supportsDeveloperRole: false, supportsReasoningEffort: true, supportsStrictMode: false },
  }],
});
const settings = sdk.SettingsManager.inMemory({
  enableInstallTelemetry: false, enableAnalytics: false, cacheWarming: 'off',
  compaction: { enabled: true, reserveTokens: Math.floor(init.context_size / 4),
    keepRecentTokens: Math.floor(init.context_size / 5) },
  retry: { enabled: true, maxRetries: 1, baseDelayMs: 500, maxAgentDelayMs: 2000 },
  httpIdleTimeoutMs: 120000,
  defaultProjectTrust: 'never',
});

function policy(pi) {
  let turnApproved = false;
  let calls = 0;
  const repeats = new Map();
  pi.on('before_agent_start', () => { turnApproved = false; calls = 0; repeats.clear(); });
  pi.on('before_provider_request', event => init.agent
    ? { ...event.payload, temperature: 0.2 }
    : event.payload);
  function stop(ctx, reason) {
    ctx.ui.notify('edge-stop:' + reason, 'warning');
    ctx.abort();
    return { block: true, reason };
  }
  pi.registerCommand('edge-snapshot', {
    description: 'Host-only RAM checkpoint',
    handler: async (_args, ctx) => {
      ctx.ui.notify('edge-snapshot:' + JSON.stringify([
        ctx.sessionManager.getHeader(), ...ctx.sessionManager.getEntries(),
      ]), 'info');
    },
  });
  pi.on('tool_call', async (event, ctx) => {
    const { toolName, input } = event;
    if (++calls > 64) return stop(ctx, '本輪已執行 64 次工具操作；停止並保留進度，請縮小範圍或繼續下一輪。');
    if (['edit', 'write', 'powershell', 'bash'].includes(toolName)) {
      const signature = JSON.stringify([toolName, input]);
      const count = (repeats.get(signature) ?? 0) + 1;
      repeats.set(signature, count);
      if (count >= 3) return stop(ctx, '同一操作重複 3 次而未推進工作；已停止，修改與上下文仍保留。');
    }
    if (typeof input.path === 'string') {
      const path = resolve(root, input.path);
      let parent = path;
      while (!existsSync(parent) && dirname(parent) !== parent) parent = dirname(parent);
      const actual = resolve(realpathSync(parent), relative(parent, path));
      const inside = relative(root, actual);
      if (inside === '..' || inside.startsWith('..' + (process.platform === 'win32' ? '\\' : '/')) || isAbsolute(inside)) {
        return { block: true, reason: 'File access must stay inside the opened workspace.' };
      }
      if (['edit', 'write'].includes(toolName) && /(?:^|[\\/])(?:\.env(?:\..*)?|settings\.json|personas\.json|character_state\.json)$/i.test(path)) {
        return { block: true, reason: 'This user-owned configuration is protected.' };
      }
    }
    if (!['edit', 'write', 'powershell', 'bash'].includes(toolName)) return;
    if (init.plan || !init.agent) return { block: true, reason: 'This mode allows inspection only.' };
    if (toolName === 'edit' && input.edits?.every(edit => edit.oldText === edit.newText)) {
      return { block: true, reason: 'The replacement is identical to the original text. Make an actual correction or run validation.' };
    }
    if (turnApproved) return;
    const choice = await ctx.ui.select(JSON.stringify({ tool: toolName, arguments: input }),
      ['Allow this turn', 'Allow once', 'Deny'], { timeout: 300000 });
    if (choice === 'Allow this turn') turnApproved = true;
    if (!choice || choice === 'Deny') return { block: true, reason: 'Operation was not approved.' };
  });
  pi.on('tool_result', event => {
    if (!event.isError && event.toolName === 'edit' && event.details?.patch) repeats.clear();
    if (!event.isError && event.toolName === 'write') {
      const ownSignature = JSON.stringify(['write', event.input]);
      const count = repeats.get(ownSignature);
      repeats.clear();
      if (count) repeats.set(ownSignature, count);
    }
  });
}

// Pi normally spools large shell output to disk. Its documented operations
// hook lets this application retain a bounded tail in RAM instead, while Pi
// continues to own shell execution, exit codes, cancellation and timeouts.
const localShell = createLocalPowerShellOperations();
const shell = createPowerShellToolDefinition(root, { operations: {
  async exec(command, cwd, options) {
    let tail = Buffer.alloc(0), total = 0;
    const result = await localShell.exec(command, cwd, { ...options, onData(chunk) {
      total += chunk.length;
      tail = Buffer.concat([tail, chunk]);
      if (tail.length > 65536) tail = tail.subarray(tail.length - 65536);
    }});
    const clipped = truncateTail(tail.toString('utf8'), { maxBytes: 44000, maxLines: 1800 });
    const omitted = total > tail.length || clipped.truncated;
    options.onData(Buffer.from((omitted ? '[Earlier output omitted; retained in RAM only]\n' : '') + clipped.content));
    return result;
  },
}});
const factory = async ({ cwd, sessionManager, sessionStartEvent }) => {
  const services = await sdk.createAgentSessionServices({
    cwd, agentDir, modelRuntime, settingsManager: settings,
    resourceLoaderOptions: { noExtensions: true, noSkills: true,
      noPromptTemplates: true, noThemes: true, extensionFactories: [policy],
      appendSystemPrompt: ['Answer in the user\'s language. Preserve user-owned configuration. After edits run relevant checks, repair failures, and report actual results. Conversation and prompts must remain in memory.'],
    },
  });
  return { ...await sdk.createAgentSessionFromServices({ services, sessionManager,
    sessionStartEvent, model: modelRuntime.getModel('local', init.model),
    thinkingLevel: init.thinking_effort,
    tools: init.agent ? (init.plan ? ['read', 'grep', 'find', 'ls'] : ['read', 'grep', 'find', 'ls', 'edit', 'write', 'powershell']) : [],
    customTools: [shell],
  }), services, diagnostics: services.diagnostics };
};
const manager = sdk.SessionManager.inMemory(root, undefined, init.entries?.length ? init.entries : undefined);
for (const message of init.pending_messages ?? []) manager.appendMessage(message);
const runtime = await sdk.createAgentSessionRuntime(factory, { cwd: root, agentDir, sessionManager: manager });
process.stdout.write(JSON.stringify({ type: 'ready' }) + '\n');
await sdk.runRpcMode(runtime);
