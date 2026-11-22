package com.rasova.pos

import org.junit.Assert.assertEquals
import org.junit.Test

/** Where a tapped link goes: in the app, upgraded to https, or out to another app. */
class LinksTest {

    private val cloud = "https://spice.rasova.net/login/"
    private val box = "http://192.168.1.50/login/"

    @Test
    fun theRestaurantsOwnPagesStayInTheApp() {
        assertEquals(Route.InApp, Links.route("https://spice.rasova.net/billing/", cloud))
        assertEquals(Route.InApp, Links.route("http://192.168.1.50/billing/", box))
        assertEquals(Route.InApp, Links.route("HTTP://192.168.1.50/tables/", box))
    }

    @Test
    fun theRestaurantsPageOverPlainHttpOnTheInternetIsUpgraded() {
        assertEquals(Route.Upgrade("https://spice.rasova.net/bill/7/"), Links.route("http://spice.rasova.net/bill/7/", cloud))
    }

    @Test
    fun otherSitesGoToTheBrowserEvenOverHttp() {
        // 1.3.0 before this fix loaded these inside the app over https.
        assertEquals(Route.Outside, Links.route("http://example.com/", cloud))
        assertEquals(Route.Outside, Links.route("https://wa.me/919999999999", cloud))
        assertEquals(Route.Outside, Links.route("http://192.168.1.51/", box), "another machine on the network")
    }

    @Test
    fun linksForOtherAppsGoOut() {
        assertEquals(Route.Outside, Links.route("upi://pay?pa=cafe@upi&am=120", cloud))
        assertEquals(Route.Outside, Links.route("tel:+919999999999", box))
        assertEquals(Route.Outside, Links.route("not a link", box))
    }

    @Test
    fun changeAddressOpensSetup() {
        assertEquals(Route.Setup, Links.route(OfflinePage.SETUP_LINK, box))
    }

    private fun assertEquals(expected: Route, actual: Route, message: String) =
        org.junit.Assert.assertEquals(message, expected, actual)
}
