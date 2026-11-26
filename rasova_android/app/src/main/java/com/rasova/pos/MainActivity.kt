package com.rasova.pos

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.util.Log
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

class MainActivity : AppCompatActivity() {

    private lateinit var webView:   WebView
    private lateinit var splashView: View
    private lateinit var setupView: View
    private lateinit var urlInput:  EditText
    private lateinit var localNetwork: LocalNetwork

    private var serverUrl = ""          // live URL the WebView is pointed at
    private var showingOfflinePage = false
    // A page that failed to load is tried once more, quietly, before the
    // offline page: a restaurant's Wi-Fi drops a connection now and then,
    // and one dropped page is not "can't reach the box" (seen on the
    // emulator, 22 Nov 2026: a page cut off mid-way, the next load fine).
    private var quietRetries = 0

    // ZXing QR scanner: registered before onCreate as required by Activity Result API
    private val qrScanLauncher = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { handleScannedUrl(it) }
    }

    // A page's file button: the phone's picker, its answer back to the page
    // exactly once (PendingAnswer.kt). Registered before onCreate, like the scanner.
    private val filePick = PendingAnswer<Array<Uri>>()
    private val filePickLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        filePick.finish(chosenFiles(result.resultCode, result.data))
    }

    private fun pickFiles(callback: ValueCallback<Array<Uri>>, params: WebChromeClient.FileChooserParams): Boolean {
        filePick.begin { files -> callback.onReceiveValue(files) }
        try {
            filePickLauncher.launch(Intent.createChooser(params.createIntent(), null))
        } catch (e: ActivityNotFoundException) {
            filePick.finish(null)
            Toast.makeText(this, "No app on this phone can choose a file.", Toast.LENGTH_LONG).show()
        }
        return true
    }

    /** The files the picker returned: one, several (a multiple pick), or none. */
    private fun chosenFiles(resultCode: Int, data: Intent?): Array<Uri>? {
        if (resultCode != Activity.RESULT_OK || data == null) return null
        val clip = data.clipData
        if (clip != null && clip.itemCount > 0) return Array(clip.itemCount) { clip.getItemAt(it).uri }
        return data.data?.let { arrayOf(it) }
    }

    companion object {
        private const val PREFS_NAME   = "rasova_prefs"
        private const val KEY_SERVER_URL = "server_url"

        private val SSL_ERROR_HTML = """
            <!DOCTYPE html><html>
            <head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              body{margin:0;background:#0a0a0a;color:#fff;font-family:sans-serif;
                   display:flex;flex-direction:column;align-items:center;
                   justify-content:center;height:100vh;text-align:center;padding:2rem}
              .diamond{width:48px;height:48px;background:#C9A84C;transform:rotate(45deg);margin-bottom:24px}
              h2{color:#C9A84C;font-size:1.1rem;letter-spacing:3px;margin:0 0 8px}
              p{color:rgba(255,255,255,0.5);font-size:0.9rem;margin:0}
            </style></head>
            <body>
              <div class="diamond"></div>
              <h2>RASOVA</h2>
              <p>Security certificate error.<br>Please contact support.</p>
            </body></html>
        """.trimIndent()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Android 13+ needs runtime permission for the print service's foreground
        // notification to show. Without it the service can be blocked from starting.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(
                    arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 1001
                )
            }
        }

        webView   = findViewById(R.id.webView)
        splashView = findViewById(R.id.splashView)
        setupView = findViewById(R.id.setupView)
        urlInput  = findViewById(R.id.urlInput)
        localNetwork = LocalNetwork(this)

        configureWebView()

        val saved = getSavedUrl()
        when {
            // Returning user: URL already saved: go straight to the app
            saved != null -> {
                serverUrl = saved
                if (savedInstanceState != null) {
                    followNetwork()
                    webView.restoreState(savedInstanceState)
                } else {
                    loadOrShowOffline()
                }
            }
            // Debug shortcut: SERVER_URL baked into the debug build (dev machine IP)
            BuildConfig.SERVER_URL.isNotBlank() -> {
                serverUrl = BuildConfig.SERVER_URL
                saveUrl(serverUrl)
                loadOrShowOffline()
            }
            // First launch on a release build: show the setup screen
            else -> {
                splashView.visibility = View.GONE
                setupView.visibility  = View.VISIBLE
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (showingOfflinePage && canReachNetwork() && serverUrl.isNotBlank()) {
            loadOrShowOffline()
        }
    }

    // ── Setup screen: manual URL entry ────────────────────────────────────────

    fun onConnectClicked(view: View) {
        val input = urlInput.text.toString().trim()
        val url   = ServerUrl.normalise(input)
        if (url == null) {
            urlInput.error = "Enter an address like spice-garden.rasova.net or 192.168.1.50"
            return
        }
        hideKeyboard()
        connectTo(url)
    }

    // ── Setup screen: QR code scan ────────────────────────────────────────────

    fun onScanQrClicked(view: View) {
        qrScanLauncher.launch(
            ScanOptions().apply {
                setPrompt("Scan the QR code on Setup › Printers, or on a box's Connect a device page")
                setBeepEnabled(true)
                setOrientationLocked(true)
                setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            }
        )
    }

    private fun handleScannedUrl(raw: String) {
        val url = ServerUrl.normalise(raw)
        if (url == null) {
            Toast.makeText(this, "QR code doesn't contain a valid URL", Toast.LENGTH_LONG).show()
            return
        }
        connectTo(url)
    }

    // ── Shared: save URL and start loading ────────────────────────────────────

    private fun connectTo(url: String) {
        // Pointed somewhere new (another restaurant, or a box): the print
        // service must stop collecting the old one's jobs, and must not come
        // back after a reboot. A box prints by itself, so it never starts
        // again there; in the cloud the new restaurant's page starts it.
        val previous = getSavedUrl()
        if (previous != null && Uri.parse(previous).host != Uri.parse(url).host) {
            stopService(Intent(this, PrintService::class.java))
            AgentConfig.clear(this)
        }
        serverUrl = url
        saveUrl(url)
        setupView.visibility  = View.GONE
        splashView.visibility = View.VISIBLE
        loadOrShowOffline()
    }

    /**
     * A box: bind to the restaurant's network first, then load (LocalNetwork
     * explains why the order matters). The cloud: load straight away.
     */
    private fun loadOrShowOffline() {
        if (ServerUrl.isBox(serverUrl)) {
            localNetwork.bind { loadNow() }
        } else {
            localNetwork.unbind()
            loadNow()
        }
    }

    private fun loadNow() {
        if (canReachNetwork()) {
            showingOfflinePage = false
            webView.loadUrl(serverUrl)
        } else {
            showingOfflinePage = true
            splashView.visibility = View.GONE
            webView.loadDataWithBaseURL(null, OfflinePage.html(serverUrl), "text/html", "UTF-8", null)
        }
    }

    /** A box: stay on the Wi-Fi even when it has no internet (LocalNetwork). */
    private fun followNetwork() {
        if (ServerUrl.isBox(serverUrl)) localNetwork.bind {} else localNetwork.unbind()
    }

    private fun canReachNetwork(): Boolean =
        if (ServerUrl.isBox(serverUrl)) localNetwork.connected() else isNetworkAvailable()

    /** The setup screen again, from the offline page's Change address. */
    private fun showSetup() {
        urlInput.setText(serverUrl)
        splashView.visibility = View.GONE
        setupView.visibility  = View.VISIBLE
    }

    // ── WebView setup ─────────────────────────────────────────────────────────

    private fun configureWebView() {
        // Allow chrome://inspect remote debugging of this WebView so print/login
        // issues can be diagnosed from a PC without rebuilding -- debug builds
        // only. Left on unconditionally in a release build, anyone with USB
        // access to a phone running the app could inspect/debug its WebView
        // content via Chrome DevTools.
        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true)
        }

        val settings: WebSettings = webView.settings
        settings.javaScriptEnabled        = true
        settings.domStorageEnabled        = true
        settings.loadsImagesAutomatically = true
        // An https page never loads anything over plain http. A box's pages
        // are http themselves, so they are not affected.
        settings.mixedContentMode         = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        // The real version, so the server can ask an old app to update (AppUserAgent.kt).
        settings.userAgentString = AppUserAgent.append(settings.userAgentString, BuildConfig.VERSION_NAME)
        webView.addJavascriptInterface(JSBridge(this), "Android")
        // Without this the pages' alert/confirm/prompt never show, and their
        // file buttons do nothing (JsDialogs.kt).
        webView.webChromeClient = JsDialogs(this, ::pickFiles)

        webView.webViewClient = object : WebViewClient() {

            override fun onPageCommitVisible(view: WebView, url: String) {
                // A page really showed: the next failure gets its quiet retry again.
                if (url.startsWith("http")) quietRetries = 0
            }

            override fun onPageFinished(view: WebView, url: String) {
                // The offline page finishing is not Rasova coming back: only a
                // real page clears the flag (onResume retries while it is set).
                if (url.startsWith("http")) showingOfflinePage = false
                splashView.visibility = View.GONE
            }

            override fun onReceivedError(
                view: WebView, request: WebResourceRequest, error: WebResourceError
            ) {
                if (request.isForMainFrame) {
                    Log.w("RasovaWeb", "Couldn't load ${request.url}: ${error.errorCode} ${error.description}")
                    // Only a page load, never a form being sent: that is not repeated.
                    if (request.method == "GET" && quietRetries < 1) {
                        quietRetries++
                        val again = request.url.toString()
                        view.postDelayed({ view.loadUrl(again) }, 1000)
                        return
                    }
                    showingOfflinePage = true
                    view.loadDataWithBaseURL(null, OfflinePage.html(serverUrl), "text/html", "UTF-8", null)
                }
            }

            override fun onReceivedSslError(
                view: WebView, handler: SslErrorHandler, error: SslError
            ) {
                handler.cancel()
                view.loadDataWithBaseURL(null, SSL_ERROR_HTML, "text/html", "UTF-8", null)
            }

            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest
            ): Boolean {
                return when (val route = Links.route(request.url.toString(), serverUrl)) {
                    Route.Setup -> { showSetup(); true }
                    Route.Retry -> { quietRetries = 0; loadOrShowOffline(); true }
                    Route.InApp -> false
                    is Route.Upgrade -> { view.loadUrl(route.url); true }
                    Route.Outside -> { openOutside(request.url); true }
                }
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /**
     * A link for another app: the browser, WhatsApp, a UPI app, the dialler.
     * With nothing on the phone to open it (a upi: link and no UPI app), this
     * used to crash the app; now it says so.
     */
    private fun openOutside(url: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, url))
        } catch (e: android.content.ActivityNotFoundException) {
            Toast.makeText(this, "No app on this phone can open that link.", Toast.LENGTH_LONG).show()
        }
    }

    private fun getSavedUrl(): String? =
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getString(KEY_SERVER_URL, null)
            ?.takeIf { it.isNotBlank() }
            ?.let { ServerUrl.secure(it) }   // an http:// address saved by an older version

    private fun saveUrl(url: String) =
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit().putString(KEY_SERVER_URL, url).apply()

    private fun isNetworkAvailable(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val cap = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
            cap.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } else {
            @Suppress("DEPRECATION")
            cm.activeNetworkInfo?.isConnected == true
        }
    }

    private fun hideKeyboard() {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        imm.hideSoftInputFromWindow(urlInput.windowToken, 0)
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }

    override fun onDestroy() {
        localNetwork.unbind()
        super.onDestroy()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
