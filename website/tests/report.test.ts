// The launcher's report inbox (worker/report.ts): what it accepts, and what it files on a stand-in Forgejo. No network.
import assert from "node:assert/strict";
import test from "node:test";
import { bodyOf, defuse, file, handleReport, keyOf, MAX_TEXT, parse, titleOf } from "../worker/report.ts";

const PNG = btoa(String.fromCharCode(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3));
const report = (o = {}) => ({ format: 1, kind: "GameCrashed", signature: "0xc0000005 nvoglv64.dll+0x1234", version: "v2.2.0",
                              text: "### What happened\n\nThe game crashed.", ...o });

/** A stand-in for git.magicbean.dev: the open issues, the labels; every call it got */
function forge({ issues = [], labels = [{ id: 7, name: "kind/bug" }, { id: 8, name: "source/report" }, { id: 9, name: "gpu/amd" }] } = {}) {
  const calls = [];
  let next = 100;
  const fetcher = async (url, init) => {
    const u = new URL(url);
    const path = u.pathname.replace("/api/v1/repos/MagicBeanDev/Longshot", "");
    const body = init.body instanceof FormData ? init.body : init.body ? JSON.parse(init.body) : undefined;
    calls.push({ method: init.method, path, query: u.searchParams, body, auth: init.headers.Authorization });
    const ok = (o, status = 200) => new Response(JSON.stringify(o), { status });
    if (init.method === "GET" && path === "/issues") return ok(issues);
    if (init.method === "GET" && path === "/labels") return ok(labels);
    if (init.method === "POST" && path === "/issues") {
      const n = next++;
      return ok({ number: n, html_url: `https://git.magicbean.dev/MagicBeanDev/Longshot/issues/${n}` }, 201);
    }
    if (init.method === "POST" && /^\/issues\/\d+\/comments$/.test(path)) return ok({ id: 555, html_url: "https://c/555" }, 201);
    if (init.method === "POST" && path.endsWith("/assets")) return ok({ browser_download_url: "https://git.magicbean.dev/attachments/x" }, 201);
    if (init.method === "PATCH") return ok({});
    return ok({ message: "no" }, 404);
  };
  return { calls, fetcher };
}

const env = (o = {}) => ({ REPORT_FORGEJO_TOKEN: "t0ken", REPORT_LABELS: "kind/bug, source/report", ...o });
const post = (o, { host = "longshot.magicbean.dev", type = "application/json", method = "POST", ip = "203.0.113.9" } = {}) =>
  new Request(`https://${host}/report`, {
    method, headers: { "Content-Type": type, "CF-Connecting-IP": ip }, body: method === "POST" ? JSON.stringify(o) : undefined,
  });

test("a report the launcher sends is accepted as it is", () => {
  const [r, why] = parse(report({ picture_png: PNG }));
  assert.equal(why, null);
  assert.equal(r.kind, "GameCrashed");
  assert.equal(r.version, "v2.2.0");
  assert.equal(r.picture.length, 11);
  assert.equal(parse(report({ signature: null, kind: "DiskFull", version: "unknown" }))[0].signature, null);
});

test("anything else is turned away, with the reason", () => {
  for (const [o, why] of [
    [null, "not a report"], [[], "not a report"], [report({ format: 2 }), "format must be 1"],
    [report({ kind: "Pwned" }), "unknown kind"], [report({ signature: 5 }), "bad signature"],
    [report({ version: 3 }), "bad version"], [report({ text: "  " }), "no text"],
    [report({ text: "x".repeat(MAX_TEXT + 1) }), "text too long"], [report({ picture_png: "!!!" }), "picture is not base64"],
    [report({ picture_png: btoa("GIF89a....") }), "picture is not a PNG"],
  ]) assert.equal(parse(o)[1], why, JSON.stringify(o)?.slice(0, 60));
});

test("a report can't ping anyone or carry control characters", () => {
  assert.equal(defuse("ask @joey and @team/devs\u0007"), "ask @\u200bjoey and @\u200bteam/devs");
  assert.equal(defuse("mail a@b.c"), "mail a@\u200bb.c");
  const [r] = parse(report({ signature: "line\none @x", version: "v1 | `x`" }));
  assert.equal(r.signature, "line one @\u200bx");
  const body = bodyOf(r, "abc", true);
  assert(body.includes("| v1 \\| 'x' |"), body);    // (a version can't break the table)
});

test("the same problem gets the same key on any PC, another problem another", async () => {
  const a = await keyOf("GameCrashed", "0xc0000005 nvoglv64.dll+0x1234");
  assert.match(a, /^[0-9a-f]{10}$/);
  assert.equal(await keyOf("GameCrashed", "0xc0000005 nvoglv64.dll+0x1234"), a);
  assert.notEqual(await keyOf("GameCrashed", "0xc0000005 amdvlk64.dll+0x10"), a);
  assert.notEqual(await keyOf("GpuLost", "0xc0000005 nvoglv64.dll+0x1234"), a);
  assert.equal(titleOf(parse(report())[0], a), `[report ${a}] GameCrashed: 0xc0000005 nvoglv64.dll+0x1234`);
});

test("a new problem opens an issue, labelled, with the picture in it", async () => {
  const f = forge();
  const [r] = parse(report({ picture_png: PNG }));
  const res = await file(r, env(), f.fetcher);
  assert.deepEqual(res, { id: "#100", url: "https://git.magicbean.dev/MagicBeanDev/Longshot/issues/100" });
  const create = f.calls.find((c) => c.method === "POST" && c.path === "/issues");
  assert.match(create.body.title, /^\[report [0-9a-f]{10}\] GameCrashed: /);
  assert.deepEqual(create.body.labels, [7, 8]);
  assert(create.body.body.includes("The game crashed."));
  assert(f.calls.some((c) => c.path === "/issues/100/assets"));
  assert(f.calls.find((c) => c.method === "PATCH").body.body.endsWith("![frame](https://git.magicbean.dev/attachments/x)"));
  assert(f.calls.every((c) => c.auth === "token t0ken"));
});

test("a known problem is a comment on its open issue, not a new one", async () => {
  const key = await keyOf("GameCrashed", "0xc0000005 nvoglv64.dll+0x1234");
  const f = forge({ issues: [{ number: 12, title: "[report 0000000000] other", html_url: "x" },
                             { number: 42, title: `[report ${key}] GameCrashed: ...`, html_url: "https://i/42" }] });
  const res = await file(parse(report())[0], env(), f.fetcher);
  assert.deepEqual(res, { id: "#42", url: "https://c/555" });
  assert(!f.calls.some((c) => c.method === "POST" && c.path === "/issues"));
  assert(f.calls.some((c) => c.method === "POST" && c.path === "/issues/42/comments"));
  // (search only ever matches the key in the title: a mention of it elsewhere does not count)
  const g = forge({ issues: [{ number: 9, title: `Crash, see report ${key}`, html_url: "x" }] });
  assert.equal((await file(parse(report())[0], env(), g.fetcher)).id, "#100");
});

test("a label the repository doesn't have is left out, and no labels asked for is none", async () => {
  const f = forge({ labels: [{ id: 1, name: "kind/bug" }] });
  await file(parse(report())[0], env(), f.fetcher);
  assert.deepEqual(f.calls.find((c) => c.method === "POST" && c.path === "/issues").body.labels, [1]);
  const g = forge();
  await file(parse(report())[0], env({ REPORT_LABELS: "" }), g.fetcher);
  assert.deepEqual(g.calls.find((c) => c.method === "POST" && c.path === "/issues").body.labels, []);
  assert(!g.calls.some((c) => c.path === "/labels"));
});

test("the request: method, type, size, limits, previews and a missing token", async () => {
  const f = forge();
  const res = (r, e = env()) => handleReport(r, e, f.fetcher);
  assert.equal((await res(post(null, { method: "GET" }))).status, 405);
  assert.equal((await res(post(report(), { type: "text/plain" }))).status, 415);
  assert.equal((await res(post({ format: 9 }))).status, 400);
  const big = new Request("https://longshot.magicbean.dev/report", {
    method: "POST", headers: { "Content-Type": "application/json", "Content-Length": String(7 << 20) }, body: "{}",
  });
  assert.equal((await res(big)).status, 413);

  const no = { limit: async () => ({ success: false }) };
  const r429 = await res(post(report()), env({ REPORT_LIMIT: no }));
  assert.equal(r429.status, 429);
  assert.equal(r429.headers.get("Retry-After"), "60");
  assert.equal((await res(post(report()), env({ REPORT_LIMIT_ALL: no }))).status, 429);

  const preview = await res(post(report(), { host: "pr-7-longshot-website.example.workers.dev" }));
  assert.equal(preview.status, 200);
  assert.equal((await preview.json()).id, "dry-run");
  assert.equal(f.calls.length, 0);                   // (a preview files nothing)

  assert.equal((await res(post(report()), {})).status, 503);
  assert.equal((await res(post(report()), env({ REPORTS_OFF: "1" }))).status, 503);
  assert.equal(f.calls.length, 0);

  const ok = await res(post(report()));
  assert.equal(ok.status, 200);
  assert.deepEqual(await ok.json(), { id: "#100", url: "https://git.magicbean.dev/MagicBeanDev/Longshot/issues/100" });
});

test("Forgejo failing is a 502 the launcher reports as not sent", async () => {
  const down = async () => new Response("", { status: 500 });
  const r = await handleReport(post(report()), env(), down);
  assert.equal(r.status, 502);
  assert.match((await r.json()).error, /answered 500/);
});
