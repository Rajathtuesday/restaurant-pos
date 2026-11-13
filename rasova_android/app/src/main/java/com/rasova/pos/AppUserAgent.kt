package com.rasova.pos

/**
 * The name the app gives itself in every request: the WebView's own user
 * agent plus "RasovaPOS-Android/<version>".
 *
 * Until 1.2.1 the version was written as a fixed "1.0", whatever the app
 * really was, so the server could not tell an old app from a new one. It is
 * the real version now (BuildConfig.VERSION_NAME), and the server shows a
 * page-wide "update the app" banner to an app older than the minimum it
 * needs (core/app_version.py). Every app before 1.2.1 still says "1.0".
 */
object AppUserAgent {

    const val TOKEN = "RasovaPOS-Android"

    /** [base] with this app's token added once, carrying [versionName]. */
    fun append(base: String?, versionName: String): String {
        val version = versionName.trim().ifEmpty { "0" }.replace(Regex("[^0-9A-Za-z._-]"), "")
        val without = (base ?: "").replace(Regex("\\s*$TOKEN/\\S*"), "").trim()
        return if (without.isEmpty()) "$TOKEN/$version" else "$without $TOKEN/$version"
    }
}
