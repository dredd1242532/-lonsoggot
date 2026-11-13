// The Longshot Launcher's report inbox: POST /report files a problem report as an issue on git.magicbean.dev.
//
// The launcher (LongshotLauncher, Core/ReportSender.cs) sends one JSON request when a player chooses Send report; the
// format is in the launcher's docs/launcher-reports.md:
//
//   { "format": 1, "kind": "GameCrashed", "signature": "0xc0000005 nvoglv64.dll+0x1234" | null, "version": "v2.2.0",
//     "text": "<Markdown, at most 256 KB>", "picture_png": "<base64, only when the player ticked it>" }
//
// and gets back 200 { "id": "#123", "url": "<the issue>" }, any other status meaning "not sent".
//
// - One issue per problem: the kind and the signature (the same crash on any PC) make a short key in the issue's title,
//   [report <key>]. A report whose key has an open issue is a comment there; another opens a new issue.
// - Filed as the bot account whose token is the secret REPORT_FORGEJO_TOKEN (scope write:issue), in REPORT_REPO
//   (default MagicBeanDev/Longshot), with the labels in REPORT_LABELS (comma-separated) that the repository has.
//   Without the token the inbox answers 503 and the launcher offers the report to copy instead.
// - Only on the site's own domain: on a preview (pr-<n>-...workers.dev) or anywhere else the request is checked and
//   answered as a dry run ({"id": "dry-run"}), and nothing is filed.
// - Limits: the body at most MAX_BODY; per address and for everyone, the rate limiters REPORT_LIMIT and REPORT_LIMIT_ALL
//   (wrangler.jsonc), 429 beyond them. Addresses are only a rate limiter's key: nothing is logged or kept.
// - Nothing in a report is trusted: the kind must be one the launcher knows, the version and the signature plain text
//   of a bounded length, @mentions are broken so a report can't ping anyone, and a picture must be a PNG.

export const SITE_HOST = "longshot.magicbean.dev";
export const FORGE = "https://git.magicbean.dev";
export const KINDS = ["Unknown", "Antivirus", "WrongGameVersion", "IncompleteDump", "DiskFull", "BuildMemory",
                      "NoMeshShaders", "GpuLost", "GameCrashed"];
export const MAX_TEXT = 256 * 1024;        // (the launcher's log tail is 192 KB, CrashCheck.LogTailBytes)
export const MAX_PICTURE = 4 * 1024 * 1024; // (ReportSender.PictureMaxBytes)
export const MAX_BODY = 6 * 1024 * 1024;   // (the picture in base64, the text and the rest)
const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const UA = "longshot-website report inbox (+https://longshot.magicbean.dev)";

const json = (status, o, headers = {}) =>
  new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers } });

/** Text a report can't use to ping people or break the page: @name loses its link, control characters go */
export function defuse(s) {
  return String(s).replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").replace(/@(?=[A-Za-z0-9_.-])/g, "@\u200b");
}

/** One line of at most n characters: no line breaks, no control characters, no mentions */
export function oneLine(s, n) {
  return defuse(String(s).replace(/\s+/g, " ").trim()).slice(0, n);
}

/** The report from the request's JSON, checked; [report, null] or [null, the reason] */
export function parse(o) {
  if (!o || typeof o !== "object" || Array.isArray(o)) return [null, "not a report"];
  if (o.format !== 1) return [null, "format must be 1"];
  if (!KINDS.includes(o.kind)) return [null, "unknown kind"];
  if (o.signature != null && (typeof o.signature !== "string" || o.signature.length > 300)) return [null, "bad signature"];
  if (typeof o.version !== "string" || o.version.length > 100) return [null, "bad version"];
  if (typeof o.text !== "string" || !o.text.trim()) return [null, "no text"];
  if (new TextEncoder().encode(o.text).length > MAX_TEXT) return [null, "text too long"];
  let picture = null;
  if (o.picture_png != null) {
    if (typeof o.picture_png !== "string" || o.picture_png.length > Math.ceil(MAX_PICTURE / 3) * 4 + 4) return [null, "picture too large"];
    try {
      picture = Uint8Array.from(atob(o.picture_png), (c) => c.charCodeAt(0));
    } catch {
      return [null, "picture is not base64"];
    }
    if (picture.length > MAX_PICTURE) return [null, "picture too large"];
    if (picture.length < 8 || PNG.some((b, i) => picture[i] !== b)) return [null, "picture is not a PNG"];
  }
  const signature = o.signature == null ? null : oneLine(o.signature, 200) || null;
  return [{ kind: o.kind, signature, version: oneLine(o.version, 64) || "unknown", text: o.text, picture }, null];
}

/** The problem's key: the same kind and signature, the same key, on any PC (10 hex digits of their SHA-256) */
export async function keyOf(kind, signature) {
  const bytes = new TextEncoder().encode(`${kind}\n${signature || ""}`);
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes));
  return [...hash.slice(0, 5)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function titleOf(r, key) {
  return `[report ${key}] ${r.kind}${r.signature ? `: ${r.signature}` : ""}`.slice(0, 255);
}

/** The issue's or the comment's body: what the inbox knows, then the player's report as the launcher wrote it */
export function bodyOf(r, key, first) {
  const cell = (s) => s.replace(/[|`]/g, (c) => (c === "|" ? "\\|" : "'"));
  const head = first
    ? `A problem report sent from the Longshot Launcher. Reports of the same problem (key \`${key}\`) are added here as comments.\n\n`
    : "";
  return `${head}| Kind | Signature | Version |\n|---|---|---|\n| ${r.kind} | ${r.signature ? `\`${cell(r.signature)}\`` : "-"} | ${cell(r.version)} |\n\n` +
    `---\n\n${defuse(r.text)}`;
}

// ---- Forgejo ----

function forgejo(env, fetcher) {
  const repo = env.REPORT_REPO || "MagicBeanDev/Longshot";
  const call = async (method, path, body, type = "application/json") => {
    const headers = { Authorization: `token ${env.REPORT_FORGEJO_TOKEN}`, Accept: "application/json", "User-Agent": UA };
    if (body !== undefined && type) headers["Content-Type"] = type;
    const res = await fetcher(`${FORGE}/api/v1/repos/${repo}${path}`,
                              { method, headers, body: body === undefined ? undefined : type === "application/json" ? JSON.stringify(body) : body });
    if (!res.ok) throw new Error(`git.magicbean.dev answered ${res.status} to ${method} ${path.split("?")[0]}`);
    return res.status === 204 ? null : res.json();
  };
  return { repo, call };
}

/** The open issue filed for a key, or null: Forgejo's search, then the exact [report <key>] in the title */
async function findOpen(f, key) {
  const items = await f.call("GET", `/issues?state=open&type=issues&limit=10&q=${encodeURIComponent(`report ${key}`)}`);
  return (Array.isArray(items) ? items : []).find((i) => typeof i.title === "string" && i.title.startsWith(`[report ${key}]`)) || null;
}

async function labelIds(f, names) {
  const want = String(names || "").split(",").map((n) => n.trim()).filter(Boolean);
  if (!want.length) return [];
  const labels = await f.call("GET", "/labels?limit=100");
  return (Array.isArray(labels) ? labels : []).filter((x) => want.includes(x.name)).map((x) => x.id);
}

async function attach(f, path, picture) {
  const form = new FormData();
  form.append("attachment", new Blob([picture], { type: "image/png" }), "frame.png");
  const a = await f.call("POST", path, form, null);
  return a && a.browser_download_url;
}

/** Files the report: a comment on the open issue with its key, or a new issue. { id, url } */
export async function file(r, env, fetcher = fetch) {
  const f = forgejo(env, fetcher);
  const key = await keyOf(r.kind, r.signature);
  const open = await findOpen(f, key);
  if (open) {
    const c = await f.call("POST", `/issues/${open.number}/comments`, { body: bodyOf(r, key, false) });
    if (r.picture) {
      const url = await attach(f, `/issues/comments/${c.id}/assets`, r.picture);
      if (url) await f.call("PATCH", `/issues/comments/${c.id}`, { body: `${bodyOf(r, key, false)}\n\n![frame](${url})` });
    }
    return { id: `#${open.number}`, url: c.html_url || open.html_url };
  }
  const issue = await f.call("POST", "/issues", {
    title: titleOf(r, key), body: bodyOf(r, key, true), labels: await labelIds(f, env.REPORT_LABELS),
  });
  if (r.picture) {
    const url = await attach(f, `/issues/${issue.number}/assets`, r.picture);
    if (url) await f.call("PATCH", `/issues/${issue.number}`, { body: `${bodyOf(r, key, true)}\n\n![frame](${url})` });
  }
  return { id: `#${issue.number}`, url: issue.html_url };
}

// ---- the request ----

async function limited(limiter, key) {
  if (!limiter) return false;
  const { success } = await limiter.limit({ key });
  return !success;
}

export async function handleReport(request, env, fetcher = fetch) {
  if (request.method !== "POST") return json(405, { error: "POST a report" }, { Allow: "POST" });
  if (!(request.headers.get("Content-Type") || "").toLowerCase().startsWith("application/json")) {
    return json(415, { error: "send application/json" });
  }
  const length = Number(request.headers.get("Content-Length") || "0");
  if (length > MAX_BODY) return json(413, { error: "the report is too large" });
  const address = request.headers.get("CF-Connecting-IP") || "unknown";
  if (await limited(env.REPORT_LIMIT, address) || await limited(env.REPORT_LIMIT_ALL, "all")) {
    return json(429, { error: "too many reports: try again later" }, { "Retry-After": "60" });
  }
  const raw = await request.text();
  if (raw.length > MAX_BODY) return json(413, { error: "the report is too large" });
  let o;
  try {
    o = JSON.parse(raw);
  } catch {
    return json(400, { error: "not JSON" });
  }
  const [r, why] = parse(o);
  if (!r) return json(400, { error: why });
  if (new URL(request.url).hostname !== SITE_HOST) {
    return json(200, { id: "dry-run", url: null, key: await keyOf(r.kind, r.signature) });
  }
  if (env.REPORTS_OFF === "1" || !env.REPORT_FORGEJO_TOKEN) return json(503, { error: "the report inbox is not taking reports now" });
  try {
    return json(200, await file(r, env, fetcher));
  } catch (e) {
    return json(502, { error: e.message });
  }
}
