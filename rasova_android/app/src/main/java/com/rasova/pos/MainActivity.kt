package com.rasova.pos

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.http.SslError
import android.os.Build
import android.os.Bundle
import android.view.View
import android.webkit.CookieManager
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var showingOfflinePage = false

    companion object {
        // Shown when the device has no network on launch or during browsing.
        // Retry button calls location.reload() — WebView re-attempts the load.
        private val OFFLINE_HTML = """
            <!DOCTYPE html>
            <html>
            <head>
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              body{margin:0;background:#0a0a0a;color:#fff;font-family:sans-serif;
                   display:flex;flex-direction:column;align-items:center;
                   justify-content:center;height:100vh;text-align:center;padding:2rem}
              .diamond{width:48px;height:48px;background:#C9A84C;transform:rotate(45deg);margin-bottom:24px}
              h2{color:#C9A84C;font-size:1.1rem;letter-spacing:3px;margin:0 0 8px}
              p{color:rgba(255,255,255,0.5);font-size:0.9rem;margin:0 0 32px}
              button{background:#C9A84C;border:none;color:#0a0a0a;padding:0.8rem 2rem;
                     font-size:0.9rem;font-weight:700;letter-spacing:1px;cursor:pointer}
            </style>
            </head>
            <body>
              <div class="diamond"></div>
              <h2>RASOVA</h2>
              <p>Check your internet connection</p>
              <button onclick="location.reload()">RETRY</button>
            </body>
            </html>
        """.trimIndent()

        // Shown when the SSL certificate is invalid or expired.
        // No retry — a bad cert can't be retried from the app.
        private val SSL_ERROR_HTML = """
            <!DOCTYPE html>
            <html>
            <head>
            <meta name="viewport" content="width=device-width,initial-scale=1">
            <style>
              body{margin:0;background:#0a0a0a;color:#fff;font-family:sans-serif;
                   display:flex;flex-direction:column;align-items:center;
                   justify-content:center;height:100vh;text-align:center;padding:2rem}
              .diamond{width:48px;height:48px;background:#C9A84C;transform:rotate(45deg);margin-bottom:24px}
              h2{color:#C9A84C;font-size:1.1rem;letter-spacing:3px;margin:0 0 8px}
              p{color:rgba(255,255,255,0.5);font-size:0.9rem;margin:0}
            </style>
            </head>
            <body>
              <div class="diamond"></div>
              <h2>RASOVA</h2>
              <p>Security certificate error.<br>Please contact support.</p>
            </body>
            </html>
        """.trimIndent()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        configureWebView()

        // Restore page after screen rotation — avoids full reload
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
        } else {
            loadOrShowOffline()
        }
    }

    override fun onResume() {
        super.onResume()
        // Auto-recover: if the offline page is showing and network is back, reload
        if (showingOfflinePage && isNetworkAvailable()) {
            loadOrShowOffline()
        }
    }

    private fun loadOrShowOffline() {
        if (isNetworkAvailable()) {
            showingOfflinePage = false
            webView.loadUrl(BuildConfig.SERVER_URL)
        } else {
            showingOfflinePage = true
            findViewById<View>(R.id.splashView).visibility = View.GONE
            webView.loadData(OFFLINE_HTML, "text/html", "UTF-8")
        }
    }

    private fun configureWebView() {
        val settings: WebSettings = webView.settings

        // JavaScript must be on — Rasova's UI is entirely JavaScript-driven
        settings.javaScriptEnabled = true

        // DOM storage: needed for localStorage (Rasova uses it to track setup state)
        settings.domStorageEnabled = true

        // Allow the page to load images/fonts from the server
        settings.loadsImagesAutomatically = true
        settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE

        // Keep session cookies across app restarts (so staff don't re-login every time)
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)

        // ★ THE KEY TRICK: append our custom identifier to the browser string.
        //   Django reads navigator.userAgent and sees "RasovaPOS-Android/1.0"
        //   → knows this is the native app → calls Android.startPrinting() after login
        settings.userAgentString = settings.userAgentString + " RasovaPOS-Android/1.0"

        // Register the JavaScript bridge — Django JS can call Android.startPrinting()
        // and Android.getPrintingStatus() as if they were regular JS functions
        webView.addJavascriptInterface(JSBridge(this), "Android")

        webView.webViewClient = object : WebViewClient() {

            // Hide the splash overlay once the first page finishes loading
            override fun onPageFinished(view: WebView, url: String) {
                showingOfflinePage = false
                findViewById<View>(R.id.splashView).visibility = View.GONE
            }

            // Replace Chrome's ERR_NAME_NOT_RESOLVED dinosaur with branded offline page
            override fun onReceivedError(
                view: WebView, request: WebResourceRequest, error: WebResourceError
            ) {
                if (request.isForMainFrame) {
                    showingOfflinePage = true
                    view.loadData(OFFLINE_HTML, "text/html", "UTF-8")
                }
            }

            // SSL certificate expired/invalid — cancel the load, show error page.
            // Never call handler.proceed() — that would silently accept a bad cert.
            override fun onReceivedSslError(
                view: WebView, handler: SslErrorHandler, error: SslError
            ) {
                handler.cancel()
                view.loadData(SSL_ERROR_HTML, "text/html", "UTF-8")
            }

            // Stay inside the app when following links (don't open Chrome)
            override fun shouldOverrideUrlLoading(
                view: WebView, request: WebResourceRequest
            ): Boolean {
                val url = request.url.toString()
                // Keep EC2/server URLs inside the app; open external links in browser
                return if (url.startsWith(BuildConfig.SERVER_URL) ||
                           url.startsWith("http://localhost") ||
                           url.startsWith("http://127.0.0.1")) {
                    false  // false = handle inside WebView
                } else {
                    // Open truly external URLs (e.g. terms of service) in the system browser
                    startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW,
                        android.net.Uri.parse(url)))
                    true
                }
            }
        }
    }

    // Returns true if there is a usable network connection
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

    // Handle Android back button — go back in the web app, not exit the app
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    // Save WebView state so screen rotation doesn't wipe the current page
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        webView.saveState(outState)
    }
}
