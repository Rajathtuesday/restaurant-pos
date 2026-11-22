package com.rasova.pos

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The offline page knows a box from the cloud (1.3.0). */
class OfflinePageTest {

    @Test
    fun aBoxIsNamedAndTheWifiIsWhatToCheck() {
        val page = OfflinePage.html("http://192.168.1.50/login/")
        assertTrue(page.contains("Can't reach the Rasova box at <b>http://192.168.1.50/login/</b>"))
        assertTrue(page.contains("restaurant's Wi-Fi"))
        assertFalse(page.contains("internet connection"))
    }

    @Test
    fun theCloudStillSaysTheInternet() {
        assertTrue(OfflinePage.html("https://spice.rasova.net").contains("Check your internet connection"))
    }

    @Test
    fun retryIsTheAppsOwnLinkNotAReloadOfThisPage() {
        val page = OfflinePage.html("http://192.168.1.50")
        assertTrue(page.contains("href=\"${OfflinePage.RETRY_LINK}\""))
        assertFalse(page.contains("location.reload"))
    }

    @Test
    fun changeAddressIsAlwaysThere() {
        assertTrue(OfflinePage.html("https://spice.rasova.net").contains("href=\"${OfflinePage.SETUP_LINK}\""))
        assertTrue(OfflinePage.html("http://192.168.1.50").contains("CHANGE ADDRESS"))
    }

    @Test
    fun aScannedAddressIsShownAsTextNotMarkup() {
        val page = OfflinePage.html("http://192.168.1.50/login/?next=/a&b=1")
        assertTrue(page.contains("/login/?next=/a&amp;b=1"))
    }
}
