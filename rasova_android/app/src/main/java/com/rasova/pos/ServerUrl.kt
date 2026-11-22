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
 * (10.x, 172.16-31.x, 192.168.x, *.local) are left alone: a Rasova box
 * lives there (1.3.0), on plain http, because a certificate needs the
 * internet to get and renew. That is also how a laptop is tested.
 *
 * Android is told cleartext is allowed (network_security_config.xml),
 * because a WebView only honours that switch app-wide, never per address.
 * cleartextAllowed() is what keeps it to the local network: the WebView's
 * navigation and the print service both ask it before any http request.
 */
object ServerUrl {

    /**
     * What someone typed or scanned on the setup screen, as an address, or
     * null if it can't be one. A bare local address ("192.168.1.50",
     * "rasova.local:8080") is a box: http. Anything else bare is https.
     */
    fun normalise(input: String): String? {
        val t = input.trim()
        val lower = t.lowercase()
        val url = when {
            lower.startsWith("https://") -> "https://" + t.substring("https://".length)
            lower.startsWith("http://") -> secure("http://" + t.substring("http://".length))
            !t.contains(".") || t.contains(" ") -> return null
            else -> {
                val bareHost = host("http://$t") ?: return null
                if (isLocal(bareHost)) "http://$t" else "https://$t"
            }
        }
        val h = host(url) ?: return null
        return if (h.contains(".") || h == "localhost") url else null
    }

    /** True when a request to this address may go over plain http. */
    fun cleartextAllowed(url: String): Boolean {
        val trimmed = url.trim()
        if (!trimmed.startsWith("http://", ignoreCase = true)) return true    // https, data:, about:
        val h = host(trimmed) ?: return false
        return isLocal(h)
    }

    /**
     * A Rasova box: an address on the restaurant's own network, which the
     * phone reaches over Wi-Fi or a cable even when that network has no
     * internet. Not the phone itself, and not the emulator's way to its PC.
     */
    fun isBox(url: String): Boolean {
        val h = host(url)?.lowercase()?.trim('[', ']') ?: return false
        if (h == "localhost" || h == "::1" || h == "10.0.2.2" || h.startsWith("127.")) return false
        return isLocal(h)
    }

    private fun host(url: String): String? = try {
        URI(url.trim()).host
    } catch (e: Exception) {
        null
    }

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
