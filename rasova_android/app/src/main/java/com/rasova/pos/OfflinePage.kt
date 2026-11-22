package com.rasova.pos

/**
 * The page the app shows when Rasova can't be reached (1.3.0: it knows a
 * box from the cloud). Plain Kotlin, so the unit tests can read it.
 */
object OfflinePage {

    /** The link the offline page uses to reopen the setup screen. */
    const val SETUP_LINK = "rasova://setup"

    private fun escape(text: String) = text
        .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

    /**
     * Shown when Rasova can't be reached. On a box it names the box and
     * says what to check (the Wi-Fi, the box switched on), not "your
     * internet": a box needs none. Change address reopens the setup
     * screen, for a box that got a new address, or another restaurant.
     */
    fun html(serverUrl: String): String {
        val words = if (ServerUrl.isBox(serverUrl))
            "Can't reach the Rasova box at <b>${escape(serverUrl)}</b>.<br>Check this phone is on the restaurant's Wi-Fi and the box is switched on."
        else
            "Check your internet connection"
        return """
        <!DOCTYPE html><html>
        <head><meta name="viewport" content="width=device-width,initial-scale=1">
        <style>
          body{margin:0;background:#0a0a0a;color:#fff;font-family:sans-serif;
               display:flex;flex-direction:column;align-items:center;
               justify-content:center;min-height:100vh;text-align:center;padding:2rem;box-sizing:border-box}
          .diamond{width:48px;height:48px;background:#C9A84C;transform:rotate(45deg);margin-bottom:24px}
          h2{color:#C9A84C;font-size:1.1rem;letter-spacing:3px;margin:0 0 8px}
          p{color:rgba(255,255,255,0.6);font-size:0.9rem;line-height:1.5;margin:0 0 32px}
          b{color:#fff;font-weight:normal;word-break:break-all}
          button{background:#C9A84C;border:none;color:#0a0a0a;padding:0.8rem 2rem;min-height:44px;
                 font-size:0.9rem;font-weight:700;letter-spacing:1px;cursor:pointer}
          a{display:inline-block;margin-top:20px;color:#C9A84C;font-size:0.85rem;padding:12px;letter-spacing:1px}
        </style></head>
        <body>
          <div class="diamond"></div>
          <h2>RASOVA</h2>
          <p>$words</p>
          <button onclick="location.reload()">RETRY</button>
          <a href="$SETUP_LINK">CHANGE ADDRESS</a>
        </body></html>
        """.trimIndent()
    }
}
