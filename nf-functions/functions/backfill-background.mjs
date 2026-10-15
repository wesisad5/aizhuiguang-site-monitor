// backfill-background.mjs — aizhuiguang audio backfill (S12, netlify background function).
//
// Downloads missing audio binaries from aizhuiguang.tech into the private scrape
// repo (wesisad5/aizhuiguang-scrape) via the GitHub Git DATA API, in batches.
//
// Mechanics (all proven by probe 2026-10-15):
//   - background function: 15-min async runtime (202 on invoke)
//   - egress to aizhuiguang.tech ~10MB/s; magic-byte verified downloads
//   - state = GIT (the scrape repo tree IS the todo list — recomputed every run)
//   - self-chain: POST own URL (awaited, cheap) before returning
//   - kill switch: public repo file audio-backfill/CHAIN != "on" stops the chain
//   - no-progress guard: 4 consecutive runs with 0 pushed files -> stop
//
// Env (site-scoped): GH_PAT (wesisad5, repo scope), SELF_URL (this function URL).

const MANIFEST_URL = 'https://raw.githubusercontent.com/wesisad5/aizhuiguang-site-monitor/main/audio-backfill/files.json';
const KILL_URL = 'https://raw.githubusercontent.com/wesisad5/aizhuiguang-site-monitor/main/audio-backfill/CHAIN';
const STATUS_PATH = '.backfill-status.json';
const MONITOR_API = 'https://api.github.com/repos/wesisad5/aizhuiguang-site-monitor';
const SCRAPE_API = 'https://api.github.com/repos/wesisad5/aizhuiguang-scrape';
const UA = 'aizg-backfill-fn/1.0';

const TIME_BUDGET_MS = parseInt(process.env.TIME_BUDGET_MS || '660000', 10); // 11 min downloads
const MAX_BATCH_FILES = parseInt(process.env.MAX_BATCH_FILES || '220', 10);
const MAX_BATCH_BYTES = parseInt(process.env.MAX_BATCH_BYTES || '320000000', 10); // 320MB
const DL_CONCURRENCY = 6;
const BLOB_CONCURRENCY = 4;

const MP3_MAGIC = ['ID3', '\ufffd\ufffd', '\ufffd\ufffd', '\ufffd\ufffd', '\ufffd\ufffd', 'OggS', 'RIFF', 'ftyp'];
const MIN_BYTES = 4096;

const log = (...a) => console.log('[backfill-fn]', ...a);

function ghHeaders(extra = {}) {
  return { Authorization: `token ${process.env.GH_PAT}`, 'User-Agent': UA, ...extra };
}

async function ghJson(path, opts = {}) {
  const r = await fetch(`https://api.github.com${path}`, { ...opts, headers: ghHeaders(opts.headers || {}) });
  const j = await r.json().catch(() => ({}));
  return { status: r.status, json: j };
}

function isAudio(buf) {
  const b = new Uint8Array(buf, 0, 8);
  const head3 = String.fromCharCode(b[0], b[1], b[2]);
  const head4 = String.fromCharCode(b[0], b[1], b[2], b[3]);
  if (head3 === 'ID3' || head3 === 'OggS' || head4 === 'RIFF' || head4 === 'ftyp') return true;
  if (b[0] === 0xff && (b[1] & 0xe0) === 0xe0) return true; // mpeg frame sync
  return false;
}

async function downloadOne(entry, deadline) {
  for (const url of entry.urls) {
    for (let attempt = 0; attempt < 2; attempt++) {
      if (Date.now() > deadline) return { file: entry.file, ok: false, err: 'deadline' };
      try {
        const r = await fetch(url, { signal: AbortSignal.timeout(90000) });
        if (r.status !== 200) throw new Error(`HTTP ${r.status}`);
        const buf = await r.arrayBuffer();
        if (buf.byteLength < MIN_BYTES) throw new Error(`size ${buf.byteLength}`);
        if (!isAudio(buf)) throw new Error(`not audio: ${new Uint8Array(buf, 0, 4)}`);
        return { file: entry.file, ok: true, buf, url };
      } catch (e) {
        if (attempt === 1) log(`  FAIL ${entry.file} via ${url}: ${String(e).slice(0, 80)}`);
        await new Promise(res => setTimeout(res, 2000 * (attempt + 1)));
      }
    }
  }
  return { file: entry.file, ok: false, err: 'all-candidates-failed' };
}

async function pool(items, n, worker) {
  const results = [];
  let i = 0;
  const runners = Array.from({ length: Math.min(n, items.length) }, async () => {
    while (i < items.length) {
      const idx = i++;
      try { results[idx] = await worker(items[idx]); }
      catch (e) { results[idx] = { ok: false, err: String(e).slice(0, 80) }; }
    }
  });
  await Promise.all(runners);
  return results;
}

async function getHaveSet() {
  const { status, json } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/trees/main?recursive=1');
  if (status !== 200 || json.truncated) throw new Error(`tree query failed: ${status} truncated=${json.truncated}`);
  const have = new Set();
  for (const e of json.tree) {
    if (e.type === 'blob' && e.path.startsWith('audio/')) have.add(e.path.slice('audio/'.length));
  }
  return have;
}

function toB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(bin);
}

async function pushBatch(good, headInfo) {
  // 1. create blobs (4-way pool)
  const blobs = await pool(good, BLOB_CONCURRENCY, async (r) => {
    const b64 = toB64(r.buf);
    const { status, json } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/blobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: b64, encoding: 'base64' }),
    });
    if (status !== 201) throw new Error(`blob ${r.file}: HTTP ${status}`);
    return { path: `audio/${r.file}`, mode: '100644', type: 'blob', sha: json.sha };
  });
  const treeEntries = blobs.filter(Boolean);
  if (treeEntries.length !== good.length) throw new Error(`blob creation incomplete: ${treeEntries.length}/${good.length}`);

  // 2. tree + commit + ref update with non-FF retry
  for (let attempt = 0; attempt < 3; attempt++) {
    const { status: hs, json: head } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/branches/main');
    if (hs !== 200) throw new Error(`branch query: ${hs}`);
    const baseCommit = head.commit.sha;
    const baseTree = head.commit.commit.tree.sha;
    const { status: ts, json: tree } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/trees', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_tree: baseTree, tree: treeEntries }),
    });
    if (ts !== 201) throw new Error(`tree: ${ts}`);
    const { status: cs, json: commit } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/commits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: `audio backfill batch: ${good.length} files (S12 netlify fn chain)`, tree: tree.sha, parents: [baseCommit] }),
    });
    if (cs !== 201) throw new Error(`commit: ${cs}`);
    const { status: rs } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/refs/heads/main', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha: commit.sha }),
    });
    if (rs === 200) return good.length;
    log(`ref PATCH attempt ${attempt + 1}: ${rs} — refetch and retry`);
  }
  throw new Error('push failed after 3 attempts (non-FF?)');
}

async function writeStatus(status) {
  try {
    const g = await ghJson(`/repos/wesisad5/aizhuiguang-site-monitor/contents/${STATUS_PATH}`);
    const sha = g.status === 200 ? g.json.sha : null;
    await ghJson(`/repos/wesisad5/aizhuiguang-site-monitor/contents/${STATUS_PATH}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: `backfill status: remaining=${status.remaining ?? '?'}`, content: btoa(JSON.stringify(status, null, 1)), sha }),
    });
  } catch (e) { log(`status write failed: ${String(e).slice(0, 100)}`); }
}

export default async (req) => {
  const t0 = Date.now();
  const out = { started: new Date().toISOString() };
  try {
    const manR = await fetch(MANIFEST_URL);
    const manifest = await manR.json();
    const wanted = [
      ...manifest.bgm.map(e => ({ file: e.file, urls: e.urls })),
      ...manifest.sfx.map(e => ({ file: e.file, urls: e.urls })),
      ...manifest.lib.map(e => ({ file: e.file, urls: e.urls })),
    ];

    const have = await getHaveSet();
    const missing = wanted.filter(e => !have.has(e.file));
    out.total_wanted = wanted.length;
    out.missing_before = missing.length;
    log(`wanted=${wanted.length} missing=${missing.length}`);

    if (missing.length === 0) {
      out.completed = true;
      await writeStatus({ ...out, kind: 'COMPLETE', ts: new Date().toISOString() });
      log('ALREADY COMPLETE');
      return new Response(JSON.stringify(out), { status: 202 });
    }

    // kill switch
    try {
      const k = await fetch(KILL_URL);
      if (k.ok && (await k.text()).trim().toLowerCase() !== 'on') {
        out.killed = true;
        await writeStatus({ ...out, kind: 'KILLED', ts: new Date().toISOString() });
        log('kill switch OFF — stopping');
        return new Response(JSON.stringify(out), { status: 202 });
      }
    } catch { /* assume on */ }

    // batch by count + bytes
    const batch = [];
    let bytes = 0;
    for (const e of missing) {
      if (batch.length >= MAX_BATCH_FILES || bytes >= MAX_BATCH_BYTES) break;
      batch.push(e);
      bytes += 3_000_000; // rough per-file estimate
    }
    out.batch_size = batch.length;

    // download
    const deadline = Date.now() + TIME_BUDGET_MS;
    const results = await pool(batch, DL_CONCURRENCY, e => downloadOne(e, deadline));
    const good = results.filter(r => r && r.ok);
    const failed = results.filter(r => r && !r.ok && r.err !== 'deadline').map(r => r.file);
    out.downloaded = good.length;
    out.failed = failed.slice(0, 15);
    out.dl_ms = Date.now() - t0;
    log(`downloaded ${good.length}/${batch.length} in ${out.dl_ms}ms`);

    // push
    let pushed = 0;
    if (good.length) {
      pushed = await pushBatch(good);
      out.pushed = pushed;
    }
    out.push_ms = Date.now() - t0 - out.dl_ms;
    log(`pushed ${pushed} files (${out.push_ms}ms)`);

    // recompute remaining
    const have2 = await getHaveSet();
    const remaining = wanted.filter(e => !have2.has(e.file)).length;
    out.remaining = remaining;
    out.completed = remaining === 0;

    // no-progress guard (chain dies after 4 consecutive zero-progress runs)
    let prev = {};
    try {
      const s = await fetch('https://raw.githubusercontent.com/wesisad5/aizhuiguang-site-monitor/main/.backfill-status.json');
      if (s.ok) prev = await s.json();
    } catch {}
    const noprogress = pushed === 0 ? (prev.runs_without_progress || 0) + 1 : 0;
    out.runs_without_progress = noprogress;

    await writeStatus({ ...out, kind: 'RUN', ts: new Date().toISOString() });

    // self-chain
    if (out.completed) {
      log('BACKFILL COMPLETE');
    } else if (noprogress >= 4) {
      log('NO-PROGRESS guard tripped — stopping chain');
      out.stopped = 'no-progress';
    } else {
      const self = process.env.SELF_URL;
      if (self) {
        try {
          const r = await fetch(self, { method: 'POST', signal: AbortSignal.timeout(20000) });
          out.chained = r.status;
          log(`self-chain fired: ${r.status}`);
        } catch (e) {
          out.chained = 'ERR ' + String(e).slice(0, 80);
          log('self-chain FAILED (orchestrator must re-kick)');
        }
      } else {
        log('SELF_URL not set — cannot chain');
        out.chained = 'no-self-url';
      }
    }
    out.elapsed_ms = Date.now() - t0;
    return new Response(JSON.stringify(out), { status: 202 });
  } catch (e) {
    out.fatal = String(e).slice(0, 200);
    out.elapsed_ms = Date.now() - t0;
    await writeStatus({ ...out, kind: 'FATAL', ts: new Date().toISOString() }).catch(() => {});
    log('FATAL', out.fatal);
    return new Response(JSON.stringify(out), { status: 202 });
  }
};
export const config = { path: '/api/backfill-background' };
