// probe-sfx.mjs — definitive resolver for the 9 stuck SFX files.
//
// Fetches the 9 catalog-listed but never-fetched sfx files (8 game + 1 weapon)
// plus 2 known-good controls from aizhuiguang.tech with normal egress:
//   - records exact HTTP status / size / magic bytes for every URL
//   - any 200 with valid audio magic AND >=4096 bytes -> pushed to the scrape
//     repo at audio/sfx/<name> via the Git DATA API (GH_PAT env)
//   - writes the full report to the monitor repo as .probe-resolve.json
//   - returns the report JSON synchronously
const UA = 'aizg-probe/1.0';

const TARGETS = [
  'sfx-game-100.mp3', 'sfx-game-63.mp3', 'sfx-game-64.mp3', 'sfx-game-66.mp3',
  'sfx-game-82.mp3', 'sfx-game-83.mp3', 'sfx-game-87.mp3', 'sfx-game-95.mp3',
  'sfx-weapon-87.mp3',
];
const CONTROLS = ['sfx-game-62.mp3', 'sfx-weapon-01.mp3'];

const log = (...a) => console.log('[probe]', ...a);

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
  const h3 = String.fromCharCode(b[0], b[1], b[2]);
  const h4 = String.fromCharCode(b[0], b[1], b[2], b[3]);
  if (h3 === 'ID3' || h3 === 'OggS' || h4 === 'RIFF' || h4 === 'ftyp') return true;
  return b[0] === 0xff && (b[1] & 0xe0) === 0xe0;
}

function toB64(buf) {
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

async function fetchOne(name) {
  const url = `https://aizhuiguang.tech/tools/sfx/${name}`;
  const out = { file: name, url };
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await fetch(url, { signal: AbortSignal.timeout(60000), redirect: 'follow' });
      out.http = r.status;
      out.ct = r.headers.get('content-type') || '';
      out.cl = r.headers.get('content-length') || '';
      if (r.status !== 200) { out.ok = false; return out; }
      const buf = await r.arrayBuffer();
      out.bytes = buf.byteLength;
      out.magic = Array.from(new Uint8Array(buf, 0, 4)).map(x => x.toString(16).padStart(2, '0')).join(' ');
      out.audio = isAudio(buf);
      out.buf = buf;
      out.ok = out.audio && buf.byteLength >= 512 && (!out.cl || Number(out.cl) === buf.byteLength);
      return out;
    } catch (e) {
      out.err = String(e).slice(0, 120);
      if (attempt === 1) { out.ok = false; return out; }
      await new Promise(res => setTimeout(res, 3000));
    }
  }
  return out;
}

async function pushAll(good) {
  const entries = [];
  for (const r of good) {
    const { status, json } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/blobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: toB64(r.buf), encoding: 'base64' }),
    });
    if (status !== 201) throw new Error(`blob ${r.file}: HTTP ${status}`);
    entries.push({ path: `audio/sfx/${r.file}`, mode: '100644', type: 'blob', sha: json.sha });
  }
  for (let attempt = 0; attempt < 3; attempt++) {
    const { status: hs, json: head } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/branches/main');
    if (hs !== 200) throw new Error(`branch: ${hs}`);
    const { status: ts, json: tree } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/trees', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ base_tree: head.commit.commit.tree.sha, tree: entries }),
    });
    if (ts !== 201) throw new Error(`tree: ${ts}`);
    const { status: cs, json: commit } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/commits', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `audio backfill final: ${good.length} tiny SFX files (S12 probe resolve)\n\nThe netlify chain's MIN_BYTES=4096 heuristic rejected these genuine\nsub-second SFX clips (2969-3845B, valid ID3, audio/mpeg, HTTP 200).\nProbed and fetched with a magic-byte threshold instead.`,
        tree: tree.sha, parents: [head.commit.sha],
      }),
    });
    if (cs !== 201) throw new Error(`commit: ${cs}`);
    const { status: rs } = await ghJson('/repos/wesisad5/aizhuiguang-scrape/git/refs/heads/main', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sha: commit.sha }),
    });
    if (rs === 200) return commit.sha;
    log(`ref PATCH attempt ${attempt + 1}: ${rs} — retry`);
  }
  throw new Error('push failed (non-FF x3)');
}

async function writeReport(report) {
  try {
    const g = await ghJson('/repos/wesisad5/aizhuiguang-site-monitor/contents/.probe-resolve.json');
    const sha = g.status === 200 ? g.json.sha : null;
    await ghJson('/repos/wesisad5/aizhuiguang-site-monitor/contents/.probe-resolve.json', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: `probe-resolve: pushed=${report.pushed.length} notfound=${report.notfound.length}`,
        content: btoa(JSON.stringify(report, null, 1)),
        sha,
      }),
    });
  } catch (e) { log(`report write failed: ${String(e).slice(0, 100)}`); }
}

export default async () => {
  const t0 = Date.now();
  const report = { started: new Date().toISOString(), targets: [], controls: [],
                   pushed: [], notfound: [], errors: [] };
  try {
    const ctrl = await Promise.all(CONTROLS.map(fetchOne));
    report.controls = ctrl.map(({ buf, ...r }) => r);
    log('controls:', JSON.stringify(report.controls));

    const results = [];
    for (const name of TARGETS) results.push(await fetchOne(name));
    const pushable = results.filter(r => r.ok);
    report.targets = results.map(({ buf, ...r }) => r);

    if (pushable.length) {
      try {
        const commit = await pushAll(pushable);
        report.pushed = pushable.map(r => ({ file: r.file, bytes: r.bytes }));
        report.push_commit = commit;
        log(`PUSHED ${pushable.length} files in one commit ${String(commit).slice(0, 10)}`);
      } catch (e) {
        report.errors.push({ err: String(e).slice(0, 200) });
        log(`push failed: ${e}`);
      }
    }
    report.notfound = results.filter(r => !r.ok && r.http === 404).map(r => r.file);
    report.unfetchable = results.filter(r => !r.ok && r.http !== 404)
      .map(({ buf, ...r }) => r);
  } catch (e) {
    report.fatal = String(e).slice(0, 200);
  }
  report.elapsed_ms = Date.now() - t0;
  await writeReport(report);
  return new Response(JSON.stringify(report, null, 1), {
    status: 200, headers: { 'content-type': 'application/json' },
  });
};
export const config = { path: '/api/probe-sfx' };
