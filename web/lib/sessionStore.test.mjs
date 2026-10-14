import assert from "node:assert/strict";
import { describe, it } from "node:test";

const SESSION_ID_RE = /^[a-f0-9]{32}$/;

function parseSessionIdFromLocation(search, hash) {
  const hashBody = hash.startsWith("#") ? hash.slice(1) : hash;
  const fromHash = new URLSearchParams(hashBody).get("session");
  if (fromHash && SESSION_ID_RE.test(fromHash)) return fromHash;

  const query = search.startsWith("?") ? search.slice(1) : search;
  const fromQuery = new URLSearchParams(query).get("session");
  if (fromQuery && SESSION_ID_RE.test(fromQuery)) return fromQuery;
  return null;
}

function buildHrefWithSession(pathname, search, sessionId) {
  const params = new URLSearchParams(
    search.startsWith("?") ? search.slice(1) : search,
  );
  params.delete("session");
  const query = params.toString();
  const pathWithQuery = query ? `${pathname}?${query}` : pathname;
  if (sessionId && SESSION_ID_RE.test(sessionId)) {
    return `${pathWithQuery}#session=${sessionId}`;
  }
  return pathWithQuery;
}

function shouldApplyRestoredFocus(userStartedDuringRestore) {
  return !userStartedDuringRestore;
}

function mergeTrackedById(restored, current) {
  const byId = new Map();
  for (const item of restored) byId.set(item.sessionId, item);
  for (const item of current) byId.set(item.sessionId, item);
  return [...byId.values()];
}

const SID_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const SID_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

describe("sessionStore URL helpers", () => {
  it("prefers hash session over query", () => {
    const id = parseSessionIdFromLocation(
      `?session=${SID_A}`,
      `#session=${SID_B}`,
    );
    assert.equal(id, SID_B);
  });

  it("reads legacy query session", () => {
    assert.equal(
      parseSessionIdFromLocation(`?session=${SID_A}`, ""),
      SID_A,
    );
  });

  it("rejects invalid session ids", () => {
    assert.equal(parseSessionIdFromLocation("?session=not-valid", ""), null);
  });

  it("writes session into hash and strips query session", () => {
    const href = buildHrefWithSession("/", `?session=${SID_A}&x=1`, SID_B);
    assert.equal(href, `/?x=1#session=${SID_B}`);
  });

  it("clears session from href", () => {
    assert.equal(buildHrefWithSession("/", "?x=1", null), "/?x=1");
  });
});

describe("restore focus helpers", () => {
  it("skips focus when user started during restore", () => {
    assert.equal(shouldApplyRestoredFocus(true), false);
    assert.equal(shouldApplyRestoredFocus(false), true);
  });

  it("lets current tracked entries win on merge", () => {
    const restored = [
      { sessionId: SID_A, status: "paused" },
      { sessionId: SID_B, status: "done" },
    ];
    const current = [{ sessionId: SID_A, status: "running" }];
    const merged = mergeTrackedById(restored, current);
    const a = merged.find((item) => item.sessionId === SID_A);
    assert.equal(a.status, "running");
    assert.equal(merged.length, 2);
  });
});
