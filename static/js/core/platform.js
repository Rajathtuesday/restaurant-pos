/*
 * Where Rasova is running: which OS, inside the Android app or not, as an
 * installed PWA or in a tab. The printing code decides from this whether to
 * talk to the local print agent, the native Android service, or the browser.
 */
export function detectPlatform({
  userAgent = globalThis.navigator?.userAgent || "",
  standalone = (globalThis.matchMedia?.("(display-mode: standalone)").matches === true)
    || globalThis.navigator?.standalone === true,
  agentHost = "localhost",
} = {}) {
  const ua = userAgent.toLowerCase();
  const android = /android/.test(ua);
  const ios = /iphone|ipad|ipod/.test(ua) && !android;
  const windows = !android && !ios && /windows/.test(ua);
  const mac = !android && !ios && /mac/.test(ua);
  // The Rasova Android app appends "RasovaPOS-Android/<version>" to the user
  // agent. Inside it, printing is the native foreground service's job.
  const nativeAndroid = /rasovapos-android/i.test(userAgent);
  return {
    android,
    ios,
    windows,
    mac,
    nativeAndroid,
    standalone,
    // On Android the agent is always on the phone itself.
    agentHost: android ? "localhost" : agentHost || "localhost",
  };
}

/*
 * Inside the Android app, after login: hand the native print service the
 * server address and the outlet's agent key. The key travels in a header,
 * never in a URL (printing/views.py). A browser, or an app without the
 * bridge, does nothing here.
 */
export function startAndroidPrinting({
  platform,
  serverUrl,
  agentKey,
  bridge = globalThis.Android,
  ui,
  storage = globalThis.sessionStorage,
}) {
  if (!platform.nativeAndroid || !bridge) return false;
  try {
    if (typeof bridge.startPrintingWithKey === "function") {
      bridge.startPrintingWithKey(serverUrl, agentKey);
      return true;
    }
    // An app from before 1.1.0 polled with the key in the URL, which the
    // server no longer accepts: it must be updated to print. From 1.2.0 the
    // app is signed with Rasova's own key, so the old one must be
    // uninstalled first or Android says "App not installed".
    console.warn("Rasova app is out of date: update it to keep printing.");
    if (ui && storage && !storage.getItem("rasova_app_update_shown")) {
      storage.setItem("rasova_app_update_shown", "1");
      ui.toast(
        "Update the Rasova app to keep printing: uninstall it, then install the new one from rasova.net/android/rasova.apk",
        "warning",
      );
    }
  } catch (e) {
    console.warn("Rasova native bridge error:", e);
  }
  return false;
}
