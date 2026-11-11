/*
 * Rasova as an installed app: the service worker, the "Install Rasova POS"
 * banner, the sheet after installing that explains printing, and the hint
 * when the print agent is not running.
 *
 * The service worker caches static files only. Offline orders and cash
 * payments are queued by core/offline.js from the page, because the
 * Background Sync API is unreliable inside the Android app's WebView.
 */

export function registerServiceWorker(nav = globalThis.navigator, win = globalThis) {
  if (!nav || !("serviceWorker" in nav)) return;
  win.addEventListener("load", () => {
    nav.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}

const AGENT_PORT = 8765;

/* One ping to the local print agent's WebSocket. Resolves on pong. */
function pingAgent(host, timeoutMs) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`ws://${host}:${AGENT_PORT}`);
    const timer = setTimeout(() => {
      ws.close();
      reject(new Error("timeout"));
    }, timeoutMs);
    ws.onopen = () => ws.send(JSON.stringify({ type: "ping" }));
    ws.onmessage = (e) => {
      clearTimeout(timer);
      let d = {};
      try {
        d = JSON.parse(e.data);
      } catch (notJson) {
        /* not a pong */
      }
      ws.close();
      d.type === "pong" ? resolve(d) : reject(new Error("no pong"));
    };
    ws.onerror = () => {
      clearTimeout(timer);
      reject(new Error("error"));
    };
  });
}

export function setupInstallPrompt({
  platform,
  serverUrl,
  agentKey,
  demoSession = false,
  doc = document,
  win = globalThis,
  storage = globalThis.localStorage,
  session = globalThis.sessionStorage,
}) {
  let deferredPrompt = null;

  // The shared /live-demo/ account never sees "Install Rasova POS": it makes
  // no sense on an account nobody owns, and it is a confusing thing to pop
  // up mid pitch on a prospect's screen.
  win.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferredPrompt = e;
    if (!demoSession && !session.getItem("pwa_dismissed")) showInstallBanner();
  });

  win.addEventListener("appinstalled", () => {
    deferredPrompt = null;
    doc.getElementById("pwa-banner")?.remove();
    showPostInstallSheet();
  });

  function showInstallBanner() {
    if (doc.getElementById("pwa-banner")) return;
    const p = platform;
    const sub = p.nativeAndroid
      ? "Rasova app: printing is already active"
      : p.android
        ? "Add to home screen, tap Install, then set up printing in Termux"
        : p.ios
          ? "Use Share, then Add to Home Screen in Safari"
          : "Add to your desktop for the best experience";
    const banner = doc.createElement("div");
    banner.id = "pwa-banner";
    banner.className = "pwa-banner";
    banner.innerHTML = `
      <div class="pwa-banner__text">
        <div class="pwa-banner__title">Install Rasova POS</div>
        <div class="pwa-banner__sub">${sub}</div>
      </div>
      <button type="button" class="pwa-banner__install" data-action="pwa.install">Install</button>
      <button type="button" class="pwa-banner__dismiss" data-action="pwa.dismiss" aria-label="Dismiss">&#10005;</button>
    `;
    doc.body.appendChild(banner);
    banner.querySelector('[data-action="pwa.install"]').addEventListener("click", async () => {
      if (!deferredPrompt) return;
      deferredPrompt.prompt();
      const { outcome } = await deferredPrompt.userChoice;
      deferredPrompt = null;
      banner.remove();
      if (outcome === "accepted") showPostInstallSheet();
    });
    banner.querySelector('[data-action="pwa.dismiss"]').addEventListener("click", () => {
      session.setItem("pwa_dismissed", "1");
      banner.remove();
    });
  }

  function showPostInstallSheet() {
    if (storage.getItem("rasova_agent_setup_done")) return;
    if (platform.nativeAndroid) return; // the app prints by itself
    const p = platform;

    let title;
    let steps;
    let canTest;
    if (p.android) {
      title = "Enable printing: 3 steps, once only";
      const cmd1 = "pkg install python &amp;&amp; pip install requests";
      const cmd2 = `python rasova_agent.py --server '${serverUrl}' --key '${agentKey}' --install-boot`;
      steps = [
        'Install <strong>Termux</strong> from <a href="https://f-droid.org/packages/com.termux/" target="_blank" rel="noopener">F-Droid</a> &amp; <strong>Termux:Boot</strong>',
        `In Termux: <code>${cmd1}</code>`,
        `Copy <strong>rasova_agent.py</strong> to the phone, then run:<br><code class="pwa-sheet__cmd">${cmd2}</code>`,
        "Android Settings, Apps, Termux, Battery: <strong>Unrestricted</strong>",
      ];
      canTest = false; // polling mode: no local WebSocket to test
    } else if (p.ios) {
      title = "Printing on iOS";
      steps = [
        "Rasova will open the receipt in a new tab when you print",
        "Use <strong>Share, then Print</strong> to send to AirPrint printers",
        "No extra app needed",
      ];
      canTest = false;
    } else {
      title = "Enable printing on this device";
      steps = [
        'Download <a href="/agent/rasova_agent.py" download><strong>rasova_agent.py</strong></a> and place it anywhere',
        "Run: <code>pip install websockets && python rasova_agent.py --install</code>",
        "The agent will start automatically at every login",
      ];
      canTest = true;
    }

    const sheet = doc.createElement("div");
    sheet.id = "post-install-sheet";
    sheet.className = "pwa-sheet";
    sheet.innerHTML = `
      <div class="pwa-sheet__panel">
        <button type="button" class="pwa-sheet__close" data-action="sheet.close" aria-label="Close">&#10005;</button>
        <div class="pwa-sheet__eyebrow">Almost there</div>
        <div class="pwa-sheet__title">${title}</div>
        <ol class="pwa-sheet__steps">${steps.map((s) => `<li>${s}</li>`).join("")}</ol>
        ${canTest ? `
        <div class="pwa-sheet__test">
          <button type="button" class="pwa-sheet__test-btn" data-action="sheet.test">Test Connection</button>
          <span id="sheet-agent-status" class="pwa-sheet__status"></span>
        </div>` : ""}
        <button type="button" class="pwa-sheet__done" data-action="sheet.done">Got it, don't show again</button>
      </div>
    `;
    doc.body.appendChild(sheet);
    sheet.querySelector('[data-action="sheet.close"]').addEventListener("click", () => sheet.remove());
    sheet.querySelector('[data-action="sheet.done"]').addEventListener("click", () => {
      storage.setItem("rasova_agent_setup_done", "1");
      sheet.remove();
    });
    sheet.querySelector('[data-action="sheet.test"]')?.addEventListener("click", (e) => testAgent(e.currentTarget));
  }

  async function testAgent(btn) {
    const status = doc.getElementById("sheet-agent-status");
    btn.disabled = true;
    status.textContent = "Connecting...";
    try {
      await pingAgent(platform.agentHost, 4000);
      status.textContent = "Agent connected: printing is ready";
      status.style.color = "#10b981";
    } catch (notRunning) {
      status.textContent = "Not running: follow the steps above first";
      status.style.color = "#ef4444";
    } finally {
      btn.disabled = false;
    }
  }

  function showAgentOfflineHint() {
    if (doc.getElementById("agent-offline-hint")) return;
    const hint = doc.createElement("div");
    hint.id = "agent-offline-hint";
    hint.className = "agent-hint";
    const msg = platform.nativeAndroid
      ? "Printing unavailable: open the Rasova app and allow battery optimization"
      : platform.android
        ? "Printing unavailable: open Termux and run: python rasova_agent.py"
        : "Printing unavailable: start Rasova Agent on this device";
    hint.innerHTML = `<span><strong>Agent offline.</strong> ${msg}</span>
      <button type="button" class="agent-hint__close" data-action="hint.close" aria-label="Dismiss">&#10005;</button>`;
    doc.body.insertAdjacentElement("afterbegin", hint);
    hint.querySelector('[data-action="hint.close"]').addEventListener("click", () => hint.remove());
  }

  // Installed on a desktop and printing not set up yet: probe the agent once.
  // Android polls instead, so there is nothing to probe from the page.
  if (platform.standalone && !storage.getItem("rasova_agent_setup_done")) {
    win.addEventListener("load", () => {
      if (platform.ios || platform.android) return;
      setTimeout(() => {
        pingAgent(platform.agentHost, 3000).catch(showAgentOfflineHint);
      }, 1500);
    });
  }

  return { testAgent, showPostInstallSheet };
}
