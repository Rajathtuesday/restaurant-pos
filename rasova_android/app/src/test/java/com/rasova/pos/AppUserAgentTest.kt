package com.rasova.pos

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The app tells the server its real version (B16, 13 Nov 2026). It used to
 * say "1.0" whatever it was, so the server could never ask an old app to
 * update. The server reads the token as core/app_version.py parses it.
 */
class AppUserAgentTest {

    private val webView = "Mozilla/5.0 (Linux; Android 14; Pixel 7 Build/UQ1A; wv) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"

    @Test
    fun theRealVersionGoesOnTheEnd() {
        assertEquals("$webView RasovaPOS-Android/1.2.1", AppUserAgent.append(webView, "1.2.1"))
    }

    @Test
    fun addedOnceEvenIfTheWebViewKeptAnOldOne() {
        assertEquals("$webView RasovaPOS-Android/1.2.1",
            AppUserAgent.append("$webView RasovaPOS-Android/1.0", "1.2.1"))
    }

    @Test
    fun anOddVersionNameCannotBreakTheHeader() {
        assertEquals("$webView RasovaPOS-Android/1.3.0-beta", AppUserAgent.append(webView, " 1.3.0-beta \n"))
        assertEquals("$webView RasovaPOS-Android/0", AppUserAgent.append(webView, ""))
        assertEquals("RasovaPOS-Android/2.0", AppUserAgent.append(null, "2.0"))
    }
}
