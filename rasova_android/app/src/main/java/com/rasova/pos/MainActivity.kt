package com.rasova.pos

import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions

class MainActivity : AppCompatActivity() {

    private lateinit var webView:   WebView
    private lateinit var splashView: View
    private lateinit var setupView: View
    private lateinit var urlInput:  EditText

    private var serverUrl = ""          // live URL the WebView is pointed at
    private var showingOfflinePage = false

    // ZXing QR scanner — registered before onCreate as required by Activity Result API
    private val qrScanLauncher = registerForActivityResult(ScanContract()) { result ->
        result.contents?.let { handleScannedUrl(it) }
    }

    companion object {
        private const val PREFS_NAME   = "rasova_prefs"
        private const val KEY_SERVER_URL = "server_url"

        private val OFFLINE_HTML = """
            <!DOCTYPE html><html>
            <head><meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              body{margin:0;background:#0a0a0a;color:#fff;font-family:sans-serif;
                   display:flex;flex-direction:column;align-items:center;
                   justify-content:center;height:100vh;text-align:center;padding:2rem}
              .diamond{width:48px;height:48px;background:#C9A84C;transform:rotate(45deg);margin-bottom:24px}
              h2{color:#C9A84C;font-size:1.1rem;letter-spacing:3px;margin:0 0 8px}
              p{color:rgba(255,255,255,0.5);font-size:0.9rem;margin:0 0 32px}
              button{background:#C9A84C;border:none;color:#0a0a0a;padding:0.8rem 2rem;
                     font-size:0.9rem;font-weight:700;letter-spacing:1px;cursor:pointer}
            </style></head>
            <body>
              <div class="diamond"></div>
              <h2>RASOVA</h2>
              <p>Check your internet connection</p>
              <button onclick="location.reload()">RETRY</button>
            </body></html>
        """.trimIndent()

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

        configureWebView()

        val saved = getSavedUrl()
        when {
            // Returning user: URL already saved — go straight to the app
            saved != null -> {
                serverUrl = saved
                if (savedInstanceState != null) {
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
        if (showingOfflinePage && isNetworkAvailable() && serverUrl.isNotBlank()) {
            loadOrShowOffline()
        }
    }

    // ── Setup screen: manual URL entry ────────────────────────────────────────

    fun onConnectClicked(view: View) {
        val input = urlInput.text.toString().trim()
        val url   = normaliseUrl(input)
        if (url == null) {
            urlInput.error = "Enter a URL like spice-garden.rasova.net"
            return
        }
        hideKeyboard()
        connectTo(url)
    }

    // ── Setup screen: QR code scan ────────────────────────────────────────────

    fun onScanQrClicked(view: View) {
        qrScanLauncher.launch(
            ScanOptions().apply {
                setPrompt("Scan the QR code from Setup → Printer in your Rasova dashboard")
                setBeepEnabled(true)
                setOrientationLocked(true)
                setDesiredBarcodeFormats(ScanOptions.QR_CODE)
            }
        )
    }

    private fun handleScannedUrl(raw: String) {
        val url = normaliseUrl(raw)
        if (url == null) {
            Toast.makeText(this, "QR code doesn't contain a valid URL", Toast.LENGTH_LONG).show()
            return
        }
        connectTo(url)
    }

    // ── Shared: save URL and start loading ────────────────────────────────────

    private fun connectTo(url: String) {
        serverUrl = url
        saveUrl(url)
        setupView.visibility  = View.GONE
        splashView.visibility = View.VISIBLE
        loadOrShowOffline()
    }

    private fun loadOrShowOffline() {
        if (isNetworkAvailable()) {
            showingOfflinePage = false
            webView.loadUrl(serverUrl)
        } else {
            showingOfflinePage = true
            splashView.visibility = View.GONE
            webView.loadDataWithBaseURL(null, OFFLINE_HTML, "text/html", "UTF-8", null)
        }
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
        settings.mixedContentMode         = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        settings.userAgentString = settings.userAgentString + " RasovaPOS-Android/1.0"
        webView.addJavascriptInterface(JSBridge(this), "Android")

        webView.webViewClient = object : WebViewClient() {

            override fun onPageFinished(view: WebView, url: String) {
                showingOfflinePage = false
                splashView.visibility = View.GONE
            }

            override fun onReceivedError(
                view: WebView, request: WebResourceRequest, error: WebResourceError
            ) {
                if (request.isForMainFrame) {
                    showingOfflinePage = true
                    view.loadData(OFFLINE_HTML, "text/html", "UTF-8")
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
                val host = request.url.host ?: ""
                val serverHost = Uri.parse(serverUrl).host ?: ""
                // Keep rasova pages inside the app; open external links in the browser
                return if (host == serverHost ||
                           host == "localhost" ||
                           host == "127.0.0.1") {
                    false
                } else {
                    startActivity(android.content.Intent(
                        android.content.Intent.ACTION_VIEW, request.url))
                    true
                }
            }
        }
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Add https:// if missing; return null if the input looks invalid. */
    private fun normaliseUrl(input: String): String? {
        val t = input.trim().lowercase()
        val url = when {
            t.startsWith("https://") -> t
            t.startsWith("http://")  -> t
            t.contains(".")          -> "https://$t"
            else                     -> return null
        }
        // Must have a host with at least one dot (e.g. rasova.net)
        val host = Uri.parse(url).host ?: return null
        return if (host.contains(".")) url else null
    }

    private fun getSavedUrl(): String? =
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .getString(KEY_SERVER_URL, null)
            ?.takeIf { it.isNotBlank() }

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

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
