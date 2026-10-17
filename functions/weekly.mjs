// weekly.mjs — scheduled weekly trigger for the aizg drift monitor build.
//
// Netlify scheduled function (cron Mon 09:00 UTC). Fires an authenticated
// build on the lane branch (POST /sites/{id}/builds — build hooks are
// platform-broken since 2026-10-02). The build runs the python scanner +
// drift issue alerting (monitor/netlify_run.sh).
//
// Manual trigger anytime: POST this function's URL.
const PAT = process.env.NETLIFY_PAT;
const SITE_ID = process.env.NETLIFY_SITE_ID;
const BRANCH = 'lane/monitor';

export default async (req) => {
  const out = { fired: new Date().toISOString(), site: SITE_ID, branch: BRANCH };
  if (!PAT || !SITE_ID) {
    out.error = 'NETLIFY_PAT / NETLIFY_SITE_ID not set';
    return new Response(JSON.stringify(out), { status: 500 });
  }
  try {
    const r = await fetch(`https://api.netlify.com/api/v1/sites/${SITE_ID}/builds`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${PAT}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ branch: BRANCH }),
      signal: AbortSignal.timeout(30000),
    });
    const j = await r.json().catch(() => ({}));
    out.build_id = j.id;
    out.http = r.status;
  } catch (e) {
    out.error = String(e).slice(0, 120);
  }
  return new Response(JSON.stringify(out), { status: 200 });
};

export const config = { schedule: '0 9 * * 1', path: '/api/weekly' };
