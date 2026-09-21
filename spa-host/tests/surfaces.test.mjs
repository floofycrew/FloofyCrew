import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { createBus } from "../src/events.mjs";
import { foldReport } from "../src/reporter.mjs";
import { addChunks, bundleStems, chunksFromDocument, createSurfaces, matchBundle, resolveDom, routeApplies } from "../src/surfaces.mjs";

const REGISTRY = JSON.parse(readFileSync(new URL("../src/surfaces.json", import.meta.url), "utf8"));

/** A document whose querySelectorAll answers from a selector -> nodes table (no CSS engine needed). */
function fakeDocument(table, links = []) {
  return {
    body: {},
    querySelectorAll(selector) {
      if (selector === 'script[type="module"][src], link[rel="modulepreload"][href]') {
        return links.map((url) => ({ getAttribute: (name) => (name === "src" || name === "href" ? url : null) }));
      }
      if (selector === "!bad") throw new Error("not a valid selector");
      return table[selector] ?? [];
    },
  };
}

test("the shipped registry is well-formed: unique names, fromBuild, dom and/or bundle, stems without hashes", () => {
  assert.equal(REGISTRY.schema, 1);
  const names = REGISTRY.surfaces.map((s) => s.name);
  assert.equal(new Set(names).size, names.length);
  for (const surface of REGISTRY.surfaces) {
    assert.match(surface.fromBuild, /^\d+\.\d+\.\d+/, surface.name);
    assert.ok(surface.dom || surface.bundle, `${surface.name} has neither dom nor bundle`);
    if (surface.bundle) {
      const stems = bundleStems(surface.bundle);
      assert.ok(stems.length >= 1, `${surface.name}: at least one chunk stem`);
      for (const stem of stems) assert.match(stem, /^[A-Za-z][A-Za-z0-9_.]*$/, `${surface.name}: a chunk is named by its stem, never by hash`);
      assert.ok(surface.bundle.contains || surface.bundle.regex, surface.name);
      if (surface.bundle.regex) assert.doesNotThrow(() => new RegExp(surface.bundle.regex));
    }
    if (surface.dom) assert.ok(surface.dom.selector, surface.name);
  }
  for (const name of ["shell.root", "topbar", "sidebar.nav", "logo", "favicon", "theme.tokens", "apps.page", "settings.page"]) {
    assert.ok(names.includes(name), `required surface ${name}`);
  }
});

test("chunk discovery reads stems from the shell and from ./stem-hash.js references, first hit wins", () => {
  const doc = fakeDocument({}, ["/assets/main-qB7mk2ul.js", "/assets/useTheme-DYOJyrcw.js", "/vendor/react.mjs"]);
  const chunks = chunksFromDocument(doc);
  assert.deepEqual([...chunks], [["main", "main-qB7mk2ul.js"], ["useTheme", "useTheme-DYOJyrcw.js"]]);
  addChunks(chunks, 'import("./App-B7YcgeIc.js");import("./App-ZZZZZZZZ.js");from"./useIsMobile-CVmE8oC-.js"');
  assert.equal(chunks.get("App"), "App-B7YcgeIc.js");
  assert.equal(chunks.get("useIsMobile"), "useIsMobile-CVmE8oC-.js");
  assert.deepEqual([...chunksFromDocument(null)], []);
});

test("matchBundle: contains/regex, some vs once", () => {
  const text = 'a "data-testid":`dashboard-shell` b path:`/apps` c path:`/apps` d';
  assert.equal(matchBundle({ contains: '"data-testid":`dashboard-shell`', count: "once" }, text).status, "matched");
  assert.equal(matchBundle({ contains: "path:`/apps`", count: "once" }, text).status, "missed");
  assert.equal(matchBundle({ contains: "path:`/apps`" }, text).status, "matched");
  assert.equal(matchBundle({ contains: "nope" }, text).status, "missed");
  assert.equal(matchBundle({ regex: "dashboard-\\w+", count: "once" }, text).status, "matched");
  assert.equal(matchBundle({}, text).status, "missed");
});

test("routeApplies and resolveDom: route scoping, counts, text filter, invalid selectors", () => {
  assert.equal(routeApplies("/apps", "/apps"), true);
  assert.equal(routeApplies("/apps", "/apps/library"), true);
  assert.equal(routeApplies("/apps", "/appsx"), false);
  assert.equal(routeApplies(["/a", "/b"], "/b/c"), true);
  assert.equal(routeApplies(undefined, "/anything"), true);
  const one = { id: "root" };
  const doc = fakeDocument({ "#root": [one], ".many": [{ textContent: "x" }, { textContent: "yes" }], "main#main-content": [{}] });
  assert.deepEqual(resolveDom({ selector: "#root", count: "once" }, doc).status, "matched");
  assert.equal(resolveDom({ selector: "#root", count: "once" }, doc).node, one);
  assert.equal(resolveDom({ selector: ".many", count: "once" }, doc).status, "missed");
  assert.equal(resolveDom({ selector: ".many", text: "yes" }, doc).status, "matched");
  assert.equal(resolveDom({ selector: "#missing" }, doc).status, "missed");
  assert.equal(resolveDom({ selector: "!bad" }, doc).status, "missed");
  assert.equal(resolveDom({ selector: "main#main-content", route: "/apps" }, doc, "/settings").status, "not-applicable");
  assert.equal(resolveDom({ selector: "main#main-content", route: "/apps" }, doc, "/apps/library").status, "matched");
  assert.equal(resolveDom(undefined, doc).status, "not-applicable");
  assert.equal(resolveDom({ selector: "#root" }, null).status, "not-applicable");
});

test("createSurfaces: get/refresh emit surfaces.changed on change, check() folds dom and bundle rows", async () => {
  const rootNode = { id: "root" };
  const doc = fakeDocument({ "#root": [rootNode], "main#main-content": [] }, ["/assets/main-AAAAAAAA.js"]);
  const win = { location: { pathname: "/apps" } };
  const bus = createBus({ log: () => {} });
  const changes = [];
  bus.on("surfaces.changed", (_n, p) => changes.push(p));
  const chunkTexts = {
    "/assets/main-AAAAAAAA.js": 'import("./App-BBBBBBBB.js");import"./terminal-CCCCCCCC.js"; const x = "in-main";',
    "/assets/App-BBBBBBBB.js": 'const y = "in-app";',
    "/assets/terminal-CCCCCCCC.js": "r.dataset.theme=ne(e,t)",
  };
  const fetchImpl = async (url) => ({ ok: url in chunkTexts, text: async () => chunkTexts[url] });
  const surfaces = createSurfaces({
    document: doc,
    window: win,
    bus,
    fetch: fetchImpl,
    definitions: [
      { name: "shell.root", fromBuild: "0.6.0", dom: { selector: "#root", count: "once" }, bundle: { chunk: "main", contains: "in-main" } },
      { name: "apps.page", fromBuild: "0.7.0.5", dom: { selector: "main#main-content", route: "/apps" }, bundle: { chunk: "App", contains: "in-app" } },
      { name: "elsewhere", fromBuild: "0.7.0.5", dom: { selector: "main#main-content", route: "/settings" } },
      { name: "gone", fromBuild: "0.7.0.5", bundle: { chunk: "Nowhere", contains: "x" } },
      { name: "stale", fromBuild: "0.7.0.5", bundle: { chunk: "App", contains: "removed-in-this-build", count: "once" } },
      { name: "theme.tokens", fromBuild: "0.6.0", bundle: { chunk: ["useTheme", "terminal"], contains: ".dataset.theme=", count: "once" } },
      { name: "moved-away", fromBuild: "0.6.0", bundle: { chunk: ["useTheme", "Nowhere"], contains: ".dataset.theme=" } },
      { name: "wrong-in-both", fromBuild: "0.6.0", bundle: { chunk: ["main", "App"], contains: "absent" } },
    ],
  });
  assert.deepEqual(surfaces.names(), ["shell.root", "apps.page", "elsewhere", "gone", "stale", "theme.tokens", "moved-away", "wrong-in-both"]);
  assert.equal(surfaces.get("shell.root"), rootNode);
  assert.equal(surfaces.get("apps.page"), null);
  const first = surfaces.refresh();
  assert.deepEqual(first.matched, ["shell.root"]);
  assert.deepEqual(first.missed, ["apps.page"]);
  assert.equal(changes.length, 0, "the first resolve already happened in get(); nothing changed since");
  doc.querySelectorAll = ((original) => (selector) => (selector === "main#main-content" ? [{ id: "main" }] : original(selector)))(doc.querySelectorAll.bind(doc));
  const second = surfaces.refresh("test");
  assert.equal(second.changed, true);
  assert.deepEqual(second.matched, ["shell.root", "apps.page"]);
  assert.equal(changes.length, 1);
  assert.equal(changes[0].how, "test");

  const rows = await surfaces.check();
  const byKey = Object.fromEntries(rows.map((r) => [`${r.name}#${r.kind}`, r]));
  assert.equal(byKey["shell.root#dom"].status, "matched");
  assert.equal(byKey["shell.root#bundle"].status, "matched");
  assert.equal(byKey["shell.root#bundle"].chunk, "main-AAAAAAAA.js");
  assert.equal(byKey["apps.page#bundle"].status, "matched", "a lazy chunk is discovered through main's ./App-<hash>.js reference");
  assert.equal(byKey["apps.page#bundle"].chunk, "App-BBBBBBBB.js");
  assert.equal(byKey["elsewhere#dom"].status, "not-applicable");
  assert.equal(byKey["gone#bundle"].status, "missed");
  assert.match(byKey["gone#bundle"].reason, /no chunk with stem Nowhere/);
  assert.equal(byKey["stale#bundle"].status, "missed");
  assert.equal(byKey["theme.tokens#bundle"].status, "matched", "a fallback stem is tried when the first one is not referenced (useTheme folded into terminal on public 0.6.0)");
  assert.equal(byKey["theme.tokens#bundle"].chunk, "terminal-CCCCCCCC.js");
  assert.equal(byKey["moved-away#bundle"].status, "missed");
  assert.match(byKey["moved-away#bundle"].reason, /no chunk with stem useTheme\|Nowhere/);
  assert.equal(byKey["wrong-in-both#bundle"].status, "missed");
  assert.equal(byKey["wrong-in-both#bundle"].chunk, "App-BBBBBBBB.js", "a miss names the last stem that was found");
  assert.match(byKey["wrong-in-both#bundle"].reason, /no occurrence/);

  const report = foldReport(rows, { matched: [], missed: [] }, { hostVersion: "0.7.0.5", route: "/apps", chunks: Object.fromEntries(surfaces.chunks()) });
  assert.deepEqual(report.matched, ["shell.root#dom", "shell.root#bundle", "apps.page#dom", "apps.page#bundle", "theme.tokens#bundle"]);
  assert.deepEqual(report.missed.map((m) => m.name), ["gone#bundle", "stale#bundle", "moved-away#bundle", "wrong-in-both#bundle"]);
  assert.deepEqual(report.notApplicable.map((m) => m.name), ["elsewhere#dom"]);
  assert.deepEqual(report.spaFingerprints, { matched: 5, total: 9, missed: ["gone#bundle", "stale#bundle", "moved-away#bundle", "wrong-in-both#bundle"] });
  assert.equal(report.hostVersion, "0.7.0.5");
  assert.equal(report.chunks.main, "main-AAAAAAAA.js");
});

test("foldReport merges patch rows in both string and object forms", () => {
  const report = foldReport([], { matched: ["patch:a#ops/0", { name: "b" }], missed: ["c", { name: "d", reason: "ambiguous (2)" }] });
  assert.deepEqual(report.matched, ["patch:a#ops/0", "patch:b"]);
  assert.deepEqual(report.missed, [{ name: "c", kind: "patch", reason: "fingerprint miss" }, { kind: "patch", name: "d", reason: "ambiguous (2)" }]);
  assert.equal(report.total, 4);
});
