package com.rasova.pos

import android.content.Context

/**
 * Where to poll for print jobs, and the outlet's agent key.
 *
 * The key is sent in the X-Agent-Key header, never in a URL: the server no
 * longer accepts it in the path (/orders/agent/<key>/jobs/), which wrote the
 * secret into every access log. App versions before 1.1.0 saved that old URL;
 * load() turns it into a server and a key once, so a phone that reboots before
 * anyone logs in again keeps printing.
 */
object AgentConfig {

    data class Target(val serverUrl: String, val agentKey: String) {
        /** https://rasova.net/orders/agent/: every agent endpoint hangs off this. */
        val agentBase: String get() = serverUrl.trimEnd('/') + "/orders/agent/"
    }

    // Not "server_url": MainActivity keeps the WebView's own address under that name.
    const val KEY_SERVER_URL = "agent_server_url"
    const val KEY_AGENT_KEY = "agent_key"
    private const val KEY_OLD_POLL_URL = "poll_url"
    const val HEADER = "X-Agent-Key"

    private val OLD_POLL_URL = Regex("^(https?://[^/]+)/orders/agent/([0-9a-fA-F-]{36})/?$")

    fun fromOldPollUrl(url: String): Target? =
        OLD_POLL_URL.matchEntire(url.trim())?.let { Target(it.groupValues[1], it.groupValues[2]) }

    fun load(context: Context): Target? {
        val prefs = context.getSharedPreferences(JSBridge.PREFS, Context.MODE_PRIVATE)
        val server = prefs.getString(KEY_SERVER_URL, null)
        val key = prefs.getString(KEY_AGENT_KEY, null)
        if (!server.isNullOrBlank() && !key.isNullOrBlank()) return Target(server, key)

        val migrated = prefs.getString(KEY_OLD_POLL_URL, null)?.let { fromOldPollUrl(it) } ?: return null
        save(context, migrated)
        return migrated
    }

    fun save(context: Context, target: Target) {
        context.getSharedPreferences(JSBridge.PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_SERVER_URL, target.serverUrl)
            .putString(KEY_AGENT_KEY, target.agentKey)
            .remove(KEY_OLD_POLL_URL)
            .apply()
    }
}
