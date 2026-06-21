// test_paged_anchor.mjs — regression guard for the paged-mode page-anchor logic.
//
// Bug (Codex P1, PR #27): buildPagesPaged anchored each page on the LAST segment
// whose start column was <= p, so on page 0 (where several segments share column 0)
// pageSeg[0] pointed near the BOTTOM of the page. turnPage() then saved that segment
// (readingSeg = pageSeg[target]) and resume / read->listen handoff jumped forward
// past most of the page. The anchor must be the FIRST segment starting on the page
// (its top-left), carrying a column-spanning segment forward when a column has no
// segment start.
//
// The fix factored that logic into a pure pagedAnchors(segPage,total) in index.html.
// This test extracts that exact function SOURCE from index.html and imports it (no
// copy, no eval) so a regression of the shipped function fails here. No deps beyond
// Node's built-in test runner. Run:
//   node --test tests/test_paged_anchor.mjs

import { test, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, writeFileSync, rmSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";
import { tmpdir } from "node:os";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(root, "index.html"), "utf8");

// Extract the `function pagedAnchors(...) { ... }` body from index.html so the test
// runs the SHIPPED implementation, not a copy. Balanced-brace scan from the header.
function extractPagedAnchors() {
  const sig = "function pagedAnchors(";
  const i = html.indexOf(sig);
  assert.notEqual(i, -1, "pagedAnchors() not found in index.html (was it renamed?)");
  let depth = 0, started = false, end = -1;
  for (let j = i; j < html.length; j++) {
    const c = html[j];
    if (c === "{") { depth++; started = true; }
    else if (c === "}") { depth--; if (started && depth === 0) { end = j + 1; break; } }
  }
  assert.notEqual(end, -1, "could not find the end of pagedAnchors()");
  return html.slice(i, end);
}

// Materialize the extracted source as an ESM module and import it (real code, no eval).
const modPath = join(tmpdir(), `paged_anchor_${process.pid}.mjs`);
writeFileSync(modPath, `export ${extractPagedAnchors()}\n`);
after(() => { try { rmSync(modPath); } catch {} });
const { pagedAnchors } = await import(pathToFileURL(modPath).href);

const totalCols = (sp) => (sp.length ? sp[sp.length - 1] + 1 : 1);
const anchors = (sp) => pagedAnchors(sp, totalCols(sp));

test("page-0 anchor is the FIRST segment of the column, not the last (the P1 bug)", () => {
  // segs 0,1,2 all start in column 0; seg3 -> col1; seg4 -> col2.
  // Pre-fix returned [2,3,4] (page 0 anchored on the LAST seg -> resume jumps forward).
  const pageSeg = anchors([0, 0, 0, 1, 2]);
  assert.equal(pageSeg[0], 0, "page 0 must anchor on segment 0 (top-left), not 2");
  assert.deepEqual(pageSeg, [0, 3, 4]);
});

test("multiple segments per column: each page anchors on that column's first start", () => {
  // col0: segs 0,1,2 ; col2: segs 3,4 ; col1 has no start (spanned).
  const pageSeg = anchors([0, 0, 0, 2, 2]);
  assert.equal(pageSeg[0], 0);   // col0 -> first of col0
  assert.equal(pageSeg[1], 2);   // col1 -> carried spanning seg (last start before col1)
  assert.equal(pageSeg[2], 3);   // col2 -> first of col2, NOT 4
  assert.deepEqual(pageSeg, [0, 2, 3]);
});

test("column with no segment start carries the spanning segment forward", () => {
  const pageSeg = anchors([0, 0, 2]); // seg1 spans col0->col1; seg2 starts col2.
  assert.deepEqual(pageSeg, [0, 1, 2], "col1 should carry seg1, not point at seg2");
});

test("multi-column gap (a very long segment) anchors empty columns on the spanner", () => {
  const pageSeg = anchors([0, 3]); // seg0 col0; seg1 col3; cols 1,2 spanned by seg0.
  assert.deepEqual(pageSeg, [0, 0, 0, 1]);
});

test("anchors are monotonic non-decreasing (resume never jumps backward page-to-page)", () => {
  const cases = [[0, 0, 0, 1, 2], [0, 0, 0, 2, 2], [0, 0, 2], [0, 3], [0, 1, 2, 3, 4]];
  for (const sp of cases) {
    const pageSeg = anchors(sp);
    for (let i = 1; i < pageSeg.length; i++) {
      assert.ok(
        pageSeg[i] >= pageSeg[i - 1],
        `non-monotonic anchors for ${JSON.stringify(sp)}: ${JSON.stringify(pageSeg)}`
      );
    }
  }
});
