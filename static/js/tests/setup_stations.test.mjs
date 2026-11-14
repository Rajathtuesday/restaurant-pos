/* Setup, Kitchen Stations: the print agent's socket (static/js/setup/station_agent.js). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { agentProblem, agentUrl, createAgent, testPageLines } from "../setup/station_agent.js";

/* A WebSocket stand-in: opens (or fails) at once, records what was sent,
   and answers with `reply(message)` (a list of answers to deliver). */
function fakeSocket({ fails = false, reply = () => [] } = {}) {
  const sent = [];
  class FakeWS {
    static OPEN = 1;
    constructor(url) {
      this.url = url;
      this.readyState = 0;
      this.listeners = [];
      queueMicrotask(() => {
        if (fails) { this.onerror && this.onerror(); return; }
        this.readyState = 1;
        this.onopen && this.onopen();
      });
    }
    addEventListener(_, fn) { this.listeners.push(fn); }
    removeEventListener(_, fn) { this.listeners = this.listeners.filter((f) => f !== fn); }
    send(text) {
      const message = JSON.parse(text);
      sent.push(message);
      queueMicrotask(() => reply(message).forEach((a) => this.listeners.slice().forEach((fn) => fn({ data: JSON.stringify(a) }))));
    }
    close() {}
  }
  return { FakeWS, sent };
}

const instant = { setTimeoutFn: () => 0, clearTimeoutFn: () => {} };

test("the agent is on the phone in the Android app, else at the outlet's host", () => {
  assert.equal(agentUrl(true, "10.0.0.5"), "ws://localhost:8765");
  assert.equal(agentUrl(false, "10.0.0.5"), "ws://10.0.0.5:8765");
  assert.equal(agentUrl(false, ""), "ws://localhost:8765");
});

test("Test Print waits for its own job's answer, ignoring others", async () => {
  const { FakeWS, sent } = fakeSocket({
    reply: (m) => [{ job_id: "someone-else", type: "error" }, { job_id: m.job_id, type: "ok" }],
  });
  const agent = createAgent({ WebSocketCtor: FakeWS, url: "ws://x", ...instant });
  const answer = await agent.testPrint(7, "192.168.1.9", 9100, 123);
  assert.equal(answer.type, "ok");
  assert.equal(sent[0].job_id, "test-7-123");
  assert.equal(sent[0].network_host, "192.168.1.9");
  assert.ok(sent[0].lines.includes("IP: 192.168.1.9:9100\n"));
});

test("a printer's refusal is the agent's own words", async () => {
  const { FakeWS } = fakeSocket({ reply: (m) => [{ job_id: m.job_id, type: "error", message: "Paper out" }] });
  const agent = createAgent({ WebSocketCtor: FakeWS, url: "ws://x", ...instant });
  await assert.rejects(agent.testPrint(1, "1.1.1.1", 9100), /Paper out/);
});

test("no agent: said in words", async () => {
  const { FakeWS } = fakeSocket({ fails: true });
  const agent = createAgent({ WebSocketCtor: FakeWS, url: "ws://x", ...instant });
  const err = await agent.discover().catch((e) => e);
  assert.equal(agentProblem(err, "x"), "Rasova Agent not running");
});

test("a scan returns the printers found, skipping other messages", async () => {
  const { FakeWS } = fakeSocket({
    reply: () => [{ type: "status" }, { type: "network_printers", list: [{ ip: "1.1.1.1", responded_ms: 4 }] }],
  });
  const agent = createAgent({ WebSocketCtor: FakeWS, url: "ws://x", ...instant });
  assert.deepEqual(await agent.discover(), [{ ip: "1.1.1.1", responded_ms: 4 }]);
});

test("the test page carries no em dash or arrow the printer cannot print", () => {
  const text = testPageLines("1.1.1.1", 9100).join("");
  assert.ok(!/[\u2014\u2192]/.test(text));
});
