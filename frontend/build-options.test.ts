import { spawnSync } from 'node:child_process';
import { describe, expect, it } from 'vitest';
import { clientBuildOptions } from './build-options';

describe('production navigation namespace', () => {
  it('preserves lazy and cached navigation exports with minification on and off', () => {
    const script = `
      import assert from 'node:assert/strict';
      import { createRequire } from 'node:module';
      import vm from 'node:vm';
      import { pathToFileURL } from 'node:url';
      const require = createRequire(import.meta.url);
      const { rolldown } = await import(pathToFileURL(require.resolve('rolldown', {
        paths: [require.resolve('vite')],
      })).href);
      const sources = {
        entry: "import { helper } from 'nav'; globalThis.helper = helper(); globalThis.run = () => import('link').then(m => m.action());",
        nav: "export function helper() { return 'helper'; } export function navigateClientSide() { return 'navigate'; } export function getPrefetchInterceptionContext() { return 'prefetch'; }",
        link: "let loaded = null; let promise = null; function load() { return promise ??= import('nav').then(m => { loaded = m; return m; }); } export async function action() { const { navigateClientSide, getPrefetchInterceptionContext } = loaded ?? await load(); return [navigateClientSide(), getPrefetchInterceptionContext()]; }",
      };
      for (const minify of [false, true]) {
        const bundle = await rolldown({
          input: 'entry', preserveEntrySignatures: false,
          treeshake: { moduleSideEffects: 'no-external' },
          experimental: ${JSON.stringify(clientBuildOptions.rolldownOptions.experimental)},
          plugins: [{
            name: 'memory-navigation-regression',
            resolveId(id) { if (Object.hasOwn(sources, id)) return '\\0' + id; },
            load(id) { return sources[id.slice(1)]; },
          }],
        });
        try {
          const { output } = await bundle.generate({
            format: 'es', minify,
            codeSplitting: { minSize: 10000, groups: [{ name() { return null; } }] },
          });
          const chunks = output.filter(c => c.type === 'chunk');
          const context = vm.createContext({});
          const cache = new Map();
          function moduleFor(id) {
            id = id.startsWith('./') ? id.slice(2) : id;
            if (cache.has(id)) return cache.get(id);
            const chunk = chunks.find(c => c.fileName === id);
            assert(chunk, 'Missing emitted chunk');
            const module = new vm.SourceTextModule(chunk.code, {
              context, identifier: id,
              async importModuleDynamically(specifier) {
                const child = moduleFor(specifier);
                if (child.status === 'unlinked') await child.link(moduleFor);
                if (child.status === 'linked') await child.evaluate();
                return child;
              },
            });
            cache.set(id, module);
            return module;
          }
          const entry = moduleFor(chunks.find(c => c.isEntry).fileName);
          await entry.link(moduleFor);
          await entry.evaluate();
          assert.equal(context.helper, 'helper');
          for (let attempt = 0; attempt < 2; attempt++) {
            assert.equal(JSON.stringify(await context.run()), '["navigate","prefetch"]');
          }
        } finally { await bundle.close(); }
      }
    `;
    const result = spawnSync(process.execPath, ['--experimental-vm-modules', '--input-type=module'], {
      input: script, encoding: 'utf8', timeout: 20000,
    });
    expect(result.status, result.stderr || result.error?.message).toBe(0);
  }, 25000);
});
