// plugins/chain-trigger/index.js — audio-backfill chain trigger (S12)
//
// Runs AFTER build.command (the backfill runner). Fail-safe by construction:
//   - no /tmp/backfill-summary.json (runner crashed) -> STOP (no chain)
//   - summary.completed -> STOP (all files in the repo)
//   - kill switch (public repo file audio-backfill/CHAIN != "on") -> STOP
//   - otherwise -> POST the build hook (queues the next build on this site)
//
// No npm dependencies — node builtins only. The kill switch lives in the PUBLIC
// monitor repo so the orchestrator can stop the chain without netlify API access:
//   https://raw.githubusercontent.com/wesisad5/aizhuiguang-site-monitor/main/audio-backfill/CHAIN

import fs from 'node:fs';

const KILL_URL = 'https://raw.githubusercontent.com/wesisad5/aizhuiguang-site-monitor/main/audio-backfill/CHAIN';

export default {
  async onPostBuild() {
    const summaryPath = '/tmp/backfill-summary.json';
    if (!fs.existsSync(summaryPath)) {
      console.log('[chain] no summary (runner failed) — chain STOPPED (fail-safe)');
      return;
    }
    const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
    console.log(`[chain] summary: ${JSON.stringify(summary).slice(0, 300)}`);

    if (summary.completed) {
      console.log('[chain] BACKFILL COMPLETE — chain stops here.');
      return;
    }

    let chain = 'on';
    try {
      const r = await fetch(KILL_URL, { signal: AbortSignal.timeout(15000) });
      if (r.ok) chain = (await r.text()).trim().toLowerCase();
    } catch (e) {
      console.log(`[chain] kill switch check failed (assuming on): ${e.message}`);
    }
    if (chain !== 'on') {
      console.log(`[chain] kill switch = "${chain}" — chain STOPPED`);
      return;
    }

    const hook = process.env.BUILD_HOOK_URL;
    if (!hook) {
      console.log('[chain] BUILD_HOOK_URL not set — chain STOPPED');
      return;
    }
    try {
      const r = await fetch(hook, { method: 'POST', signal: AbortSignal.timeout(30000) });
      console.log(`[chain] next build triggered via hook (HTTP ${r.status})`);
    } catch (e) {
      console.log(`[chain] hook POST FAILED: ${e.message}`);
      // fail-safe: stop. The sandbox orchestrator (guarding this run) will
      // re-trigger manually after diagnosis.
    }
  }
};
