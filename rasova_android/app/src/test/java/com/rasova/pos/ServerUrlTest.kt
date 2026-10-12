package com.rasova.pos

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cloudflare redirects http:// to https:// (12 Oct 2026). A redirected POST
 * becomes a GET with no body, so an agent on http:// would never mark a job
 * printed. Internet addresses are upgraded; local ones are left for testing.
 */
class ServerUrlTest {

    @Test
    fun internetAddressesMoveToHttps() {
        assertEquals("https://rasova.net", ServerUrl.secure("http://rasova.net"))
        assertEquals("https://spice.rasova.net/", ServerUrl.secure("  http://spice.rasova.net/ "))
        assertEquals("https://Rasova.net/x", ServerUrl.secure("HTTP://Rasova.net/x"))
        assertEquals("https://8.8.8.8:8000", ServerUrl.secure("http://8.8.8.8:8000"))
    }

    @Test
    fun httpsIsLeftAlone() {
        assertEquals("https://rasova.net", ServerUrl.secure("https://rasova.net"))
    }

    @Test
    fun localAddressesStayAsTyped() {
        for (url in listOf(
            "http://localhost:8000", "http://127.0.0.1:8000", "http://10.0.2.2:8000",
            "http://192.168.1.20:8000", "http://172.20.0.5", "http://10.1.2.3", "http://pos.local",
        )) {
            assertEquals(url, ServerUrl.secure(url))
        }
    }

    @Test
    fun whatCountsAsLocal() {
        assertTrue(ServerUrl.isLocal("192.168.0.1"))
        assertTrue(ServerUrl.isLocal("172.31.255.255"))
        assertFalse(ServerUrl.isLocal("172.32.0.1"))
        assertFalse(ServerUrl.isLocal("11.0.0.1"))
        assertFalse(ServerUrl.isLocal("rasova.net"))
        assertFalse(ServerUrl.isLocal("192.168.1"))
    }

    @Test
    fun brokenInputIsReturnedUnchanged() {
        assertEquals("http://", ServerUrl.secure("http://"))
        assertEquals("not a url", ServerUrl.secure("not a url"))
    }
}
