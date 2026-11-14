/*
 * Kitchen Stations: talking to the Rasova print agent over its WebSocket
 * (port 8765; on the Android app it runs on the phone itself). Used for the
 * agent's status, a station's Test Print and the network scan for printers.
 *
 * Everything comes in as an argument so node can test it with a fake socket:
 *   WebSocketCtor  the browser's WebSocket
 *   url            ws://<host>:8765
 */
const ESC = "\x1b";
const GS = "\x1d";

/* The test page, as ESC/POS lines the agent sends to the printer. */
export function testPageLines(ip, port) {
  return [
    ESC + "@", ESC + "a\x01", ESC + "!\x18", "Rasova\n", ESC + "!\x00", ESC + "a\x01",
    "Test Print\n", "--------------------------------\n", ESC + "a\x00",
    "Agent to printer connection OK\n", `IP: ${ip}:${port}\n`,
    "--------------------------------\n\n\n", GS + "V\x41\x03",
  ];
}

export function agentUrl(android, host) {
  return "ws://" + (android ? "localhost" : (host || "localhost")) + ":8765";
}

export function createAgent({ WebSocketCtor, url, setTimeoutFn = setTimeout, clearTimeoutFn = clearTimeout }) {
  let socket = null;

  function connect(timeout = 2000) {
    if (socket && socket.readyState === WebSocketCtor.OPEN) return Promise.resolve(socket);
    return new Promise((resolve, reject) => {
      const ws = new WebSocketCtor(url);
      const timer = setTimeoutFn(() => { ws.close(); reject(new Error("timeout")); }, timeout);
      ws.onopen = () => { clearTimeoutFn(timer); socket = ws; resolve(ws); };
      ws.onerror = () => { clearTimeoutFn(timer); reject(new Error("not running")); };
    });
  }

  /* Sends `message` and resolves with the first answer `pick` accepts
     (pick returns undefined to keep waiting, or throws to refuse). */
  async function ask(message, pick, { connectTimeout = 3000, answerTimeout = 10000, timeoutMessage = "timeout" } = {}) {
    const ws = await connect(connectTimeout);
    return new Promise((resolve, reject) => {
      const timer = setTimeoutFn(() => { ws.removeEventListener("message", onMessage); reject(new Error(timeoutMessage)); }, answerTimeout);
      function onMessage(event) {
        let answer;
        try {
          answer = pick(JSON.parse(event.data));
        } catch (refused) {
          clearTimeoutFn(timer);
          ws.removeEventListener("message", onMessage);
          reject(refused);
          return;
        }
        if (answer === undefined) return;
        clearTimeoutFn(timer);
        ws.removeEventListener("message", onMessage);
        resolve(answer);
      }
      ws.addEventListener("message", onMessage);
      ws.send(JSON.stringify(message));
    });
  }

  return {
    connect,

    async ping() {
      const ws = await connect(2000);
      ws.send(JSON.stringify({ type: "ping" }));
    },

    testPrint(stationId, ip, port, now = Date.now()) {
      const jobId = `test-${stationId}-${now}`;
      return ask(
        { type: "print", job_id: jobId, lines: testPageLines(ip, port), network_host: ip, network_port: port },
        (d) => {
          if (d.job_id !== jobId) return undefined;
          if (d.type === "ok") return d;
          throw new Error(d.message || "Print failed");
        },
        { timeoutMessage: "Print timeout" },
      );
    },

    /* [{ip, responded_ms}] */
    discover() {
      return ask(
        { type: "discover_network_printers", port: 9100, timeout: 0.4 },
        (d) => (d.type === "network_printers" ? (d.list || []) : undefined),
        { answerTimeout: 8000 },
      );
    },
  };
}

/* The agent's error, in words for the owner. */
export function agentProblem(err, fallback) {
  const message = err && err.message;
  if (message === "not running") return "Rasova Agent not running";
  return message || fallback;
}
