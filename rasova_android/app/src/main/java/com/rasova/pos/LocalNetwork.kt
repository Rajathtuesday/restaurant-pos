package com.rasova.pos

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Handler
import android.os.Looper

/**
 * Keeps the app on the restaurant's own network while it talks to a Rasova
 * box (1.3.0).
 *
 * A box's Wi-Fi often has no internet: that is the point of a box. Android
 * then marks the Wi-Fi "no internet" and, when mobile data is on, sends the
 * apps' traffic over mobile data instead. 192.168.1.50 can't be reached
 * from there, so the app would show "can't reach the box" while the phone
 * sits on the right Wi-Fi. Binding the app's process to the Wi-Fi (or a
 * cable) sends everything, the WebView included, to the box's network.
 *
 * The binding must be in place BEFORE the first page loads: binding while a
 * page loads changes the network under the WebView, which aborts the load
 * (seen on an Android 16 emulator, 22 Nov 2026: the box served the login
 * page and the app showed "can't reach the box"). So bind() calls back once
 * the process is bound, or after WAIT_MS when there is no Wi-Fi or cable at
 * all (the load then fails and the offline page says what to check). It
 * binds once, and never moves a working binding to another network.
 *
 * Only for a box: in the cloud the app follows Android's own choice.
 */
class LocalNetwork(context: Context) {

    companion object {
        const val WAIT_MS = 3000L
    }

    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var callback: ConnectivityManager.NetworkCallback? = null
    private var bound: Network? = null
    private val waiting = mutableListOf<() -> Unit>()

    /** Wi-Fi or a cable, with or without the internet. */
    private val request: NetworkRequest = NetworkRequest.Builder()
        .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
        .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
        .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        .build()

    /** Bind to the local network, then run [ready] on the main thread. */
    fun bind(ready: () -> Unit) {
        if (bound != null) {
            ready()
            return
        }
        waiting.add(ready)
        if (callback == null) {
            val cb = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    main.post {
                        if (bound == null) {
                            cm.bindProcessToNetwork(network)
                            bound = network
                        }
                        release()
                    }
                }

                override fun onLost(network: Network) {
                    main.post {
                        if (bound == network) {
                            cm.bindProcessToNetwork(null)
                            bound = null
                        }
                    }
                }
            }
            cm.registerNetworkCallback(request, cb)
            callback = cb
        }
        main.postDelayed({ release() }, WAIT_MS)            // no Wi-Fi or cable: load anyway, fail, explain
    }

    private fun release() {
        val now = waiting.toList()
        waiting.clear()
        now.forEach { it() }
    }

    fun unbind() {
        callback?.let {
            try {
                cm.unregisterNetworkCallback(it)
            } catch (e: IllegalArgumentException) {
                // already gone
            }
        }
        callback = null
        bound = null
        waiting.clear()
        cm.bindProcessToNetwork(null)
    }

    /** Is the phone on any Wi-Fi or cable? A box needs nothing more. */
    fun connected(): Boolean = cm.allNetworks.any { network ->
        val caps = cm.getNetworkCapabilities(network) ?: return@any false
        caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
    }
}
