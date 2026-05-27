package com.rasova.pos

import android.os.Bundle
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        webView = findViewById(R.id.webView)
        configureWebView()

        // Restore page after screen rotation — avoids full reload
        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState)
        } else {
            webView.loadUrl(BuildConfig.SERVER_URL)
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

        // Stay inside the app when following links (don't open Chrome)
        webView.webViewClient = object : WebViewClient() {
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
