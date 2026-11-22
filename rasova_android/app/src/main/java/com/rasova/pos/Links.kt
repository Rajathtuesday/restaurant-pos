package com.rasova.pos

import java.net.URI

/**
 * Where a link tapped inside the app goes (MainActivity's WebView).
 * Plain Kotlin, so the unit tests can try every kind of link.
 *
 *   Setup     the offline page's Change address (OfflinePage.SETUP_LINK)
 *   Retry     the offline page's Retry: load Rasova again (OfflinePage.RETRY_LINK)
 *   InApp     a Rasova page of this restaurant (or box): stays in the app
 *   Upgrade   a Rasova page asked for over plain http on the internet: the
 *             same page over https, never in the clear
 *   Outside   anything else (WhatsApp, a map, tel:, upi:): the phone's own
 *             browser or app. Since 1.3.0 an http link to another site went
 *             to https inside the app instead; it is the browser's again.
 */
sealed class Route {
    object Setup : Route()
    object Retry : Route()
    object InApp : Route()
    data class Upgrade(val url: String) : Route()
    object Outside : Route()
}

object Links {

    fun route(link: String, serverUrl: String): Route {
        if (link == OfflinePage.SETUP_LINK) return Route.Setup
        if (link == OfflinePage.RETRY_LINK) return Route.Retry
        // The app's own pages (the offline page) have no web address: they
        // stay in the app, never handed to Chrome.
        if (link.startsWith("about:") || link.startsWith("data:")) return Route.InApp
        val host = host(link)?.lowercase() ?: return Route.Outside
        val ours = host == host(serverUrl)?.lowercase() || host == "localhost" || host == "127.0.0.1"
        if (!ours) return Route.Outside
        return if (ServerUrl.cleartextAllowed(link)) Route.InApp else Route.Upgrade(ServerUrl.secure(link))
    }

    private fun host(url: String): String? = try {
        URI(url.trim()).host
    } catch (e: Exception) {
        null
    }
}
