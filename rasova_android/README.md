# Rasova POS Printing Agent

A thin native Android shell that does two jobs: hosts the Rasova web dashboard in a full-screen WebView, and runs a background service that polls the Rasova backend for print jobs and sends them straight to a LAN printer over a raw TCP socket. This is the "phone becomes the printer bridge" piece of Rasova's printing architecture, no dedicated hardware needed at a cafe.

## What this actually is

There's almost no native UI here on purpose. `MainActivity` is a single Activity hosting a WebView pointed at a tenant's `https://<slug>.rasova.net` dashboard, the real UI lives server-side in Django. The only genuinely native logic is the print job polling loop and the printer socket connection, both in `PrintService`.

## Architecture

- **`MainActivity.kt`**: full-screen WebView, first-launch setup screen (type a subdomain, or scan a QR code from the Rasova dashboard's Setup > Printer page), splash overlay, custom offline/SSL-error pages styled to match the brand.
- **`PrintService.kt`**: a background `Service` (not bound) that owns the polling loop and printer communication. This is where almost all the real logic lives.
- **`JSBridge.kt`**: the JS-to-native bridge, injected into the WebView as `window.Android`. Three methods the Django frontend's JS calls: `startPrinting(pollUrl)`, `getPrintingStatus()`, `isNativeApp()`.
- **`BootReceiver.kt`**: restarts `PrintService` after a phone reboot, reading the saved poll URL from SharedPreferences, no user interaction needed.

## How polling works

The poll URL is pushed in from Django JS via `JSBridge.startPrinting(pollUrl)` after login, not typed in by the user, and cached in SharedPreferences under `poll_url` (a separate value from the WebView's own `server_url`).

From that base URL, three endpoints get built: `jobs/` (GET, polled repeatedly), `done/{id}/` (POST on success), `failed/{id}/` (POST on failure). The expected `jobs/` response is a JSON object with a `jobs` array, each entry carrying `id`, `network_host`, `network_port`, and `data_b64` (base64-encoded raw ESC/POS bytes).

The poll loop isn't fixed-interval, it's exponential backoff: starts at 2 seconds, doubles on any exception up to a 30-second cap, and resets to 2 seconds on every successful response. Before each poll it checks actual network availability and skips the HTTP call entirely if offline, delaying 5 seconds instead, specifically to avoid hammering the device with failed requests on a flaky WiFi connection.

This polling design, an immediate GET-and-act-on-response with no conditional-request handling, is the client-side reason the Django `jobs/` view needs `never_cache`: any caching layer sitting between this app and that endpoint would serve a stale job list, and a printer would either miss a real job or never see it acknowledged correctly.

## Printer communication

Raw TCP, no printer SDK, no USB, no Bluetooth. `sendToPrinter()` decodes the base64 payload back into raw ESC/POS bytes and writes them directly to a socket at the job's `network_host`/`network_port` (9100 is the conventional ESC/POS port, though the actual port always comes from the job payload, not a hardcoded default). The socket connect has an explicit 10-second timeout, deliberately, since a plain `Socket(host, port)` call has no timeout at all and can hang indefinitely against an unplugged or unreachable printer, stalling the entire job queue behind it. This mirrors the same fix already present in the legacy Python agent (`rasova_agent.py`) elsewhere in this project.

Printer setup and management live entirely server-side, in the Django dashboard. This app has no printer-configuration screen of its own, it just executes whatever host/port a job specifies.

## Android 14 foreground service handling

The service is declared with `foregroundServiceType="dataSync"`, and on API 34+ it must call the typed `startForeground()` overload with `ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC`, the older 2-argument version throws `MissingForegroundServiceTypeException` on Android 14. The code branches on SDK version to call the right one, with a try/catch that sets an error status and stops the service cleanly if it still fails.

Stays alive via `START_STICKY` (Android restarts it after being killed for memory), `BootReceiver` after a reboot, and a persistent, non-swipeable low-importance notification. `JSBridge.startPrinting()` also prompts once for battery-optimization exemption via the standard system dialog, triggered from the JS-driven login flow rather than a settings screen in the app.

## Setup: how a device gets paired to a tenant

There's no hardcoded server URL in release builds, `BuildConfig.SERVER_URL` is deliberately blank, a comment in the build file explains an earlier hardcoded value once short-circuited setup and pointed every install at the marketing landing page.

First launch: if a `server_url` is already saved, load it directly. Otherwise show the setup screen, type a subdomain (`your-restaurant.rasova.net`) or scan a QR code from the dashboard's Setup > Printer page. That URL is the entire tenant identity, one install equals one tenant, persisted in SharedPreferences, no separate pairing token.

## Build

Standard Gradle/Android Studio project, single `:app` module.

```
./gradlew assembleDebug
./gradlew assembleRelease
```

- `compileSdk` / `targetSdk` 34, `minSdk` 26 (Android 8.0+)
- Kotlin only, JVM target 17
- No product flavors
- No signing config is checked in (`.gitignore` excludes `*.jks`/`*.keystore`), release signing is currently a manual step via Android Studio's Generate Signed Bundle flow

## Known gaps, worth knowing about

- **No automated tests at all.** No `app/src/test/` or `app/src/androidTest/` directories exist. The polling backoff logic and the printer socket handling are exactly the kind of thing that would benefit from unit tests, neither has any right now.
- **`WebView.setWebContentsDebuggingEnabled(true)` is gated behind `BuildConfig.DEBUG`**, correctly, but it's worth being deliberate about keeping it that way. If this ever shipped unconditionally in a release build, anyone with USB access to a phone running the app could inspect the WebView's contents via Chrome DevTools.
- **Single tenant per install, by design.** Only one `poll_url` and one `server_url` can be stored at a time, matching the current one-phone-per-cafe deployment model. Not a bug, just a real constraint if multi-printer or multi-location-per-device ever becomes a requirement.

## Status

One cafe (Malenadu) running this live in production, phone-only, no dedicated hardware yet.
