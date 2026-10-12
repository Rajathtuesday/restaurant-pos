package com.rasova.pos

import java.net.URI

/**
 * Rasova server addresses on the internet are always https.
 *
 * Cloudflare answers http:// with a 301 to https:// ("Always Use HTTPS"),
 * Android refuses cleartext http by default, and OkHttp turns a redirected
 * POST into a GET with no body: a print agent pointed at http:// would print
 * a ticket and never mark it done, so it would print again. Any http://
 * address for a public host is therefore upgraded here, wherever an address
 * enters the app: typed on the setup screen, handed over by the web page,
 * or already saved on the phone by an older version.
 *
 * Addresses on the phone itself, the emulator's host and a local network
 * (10.x, 172.16-31.x, 192.168.x, *.local) are left alone, for testing
 * against a laptop.
 */
object ServerUrl {

    fun secure(url: String): String {
        val trimmed = url.trim()
        if (!trimmed.startsWith("http://", ignoreCase = true)) return trimmed
        val host = try {
            URI(trimmed).host
        } catch (e: Exception) {
            null
        } ?: return trimmed
        return if (isLocal(host)) trimmed else "https://" + trimmed.substring("http://".length)
    }

    fun isLocal(host: String): Boolean {
        val h = host.lowercase().trim('[', ']')
        if (h == "localhost" || h == "::1" || h == "10.0.2.2" || h.endsWith(".local")) return true
        val parts = h.split(".")
        if (parts.size != 4) return false
        val octets = parts.map { it.toIntOrNull() ?: return false }
        return when {
            octets[0] == 127 -> true
            octets[0] == 10 -> true
            octets[0] == 192 && octets[1] == 168 -> true
            octets[0] == 172 && octets[1] in 16..31 -> true
            else -> false
        }
    }
}
