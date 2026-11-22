package com.rasova.pos

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest

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
 * Only for a box: in the cloud the app follows Android's own choice.
 */
class LocalNetwork(context: Context) {

    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private var callback: ConnectivityManager.NetworkCallback? = null

    /** Wi-Fi or a cable, with or without the internet. */
    private val request: NetworkRequest = NetworkRequest.Builder()
        .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
        .addTransportType(NetworkCapabilities.TRANSPORT_ETHERNET)
        .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        .build()

    fun bind() {
        if (callback != null) return
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                cm.bindProcessToNetwork(network)
            }

            override fun onLost(network: Network) {
                if (cm.boundNetworkForProcess == network) cm.bindProcessToNetwork(null)
            }
        }
        cm.registerNetworkCallback(request, cb)
        callback = cb
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
        cm.bindProcessToNetwork(null)
    }

    /** Is the phone on any Wi-Fi or cable? A box needs nothing more. */
    fun connected(): Boolean = cm.allNetworks.any { network ->
        val caps = cm.getNetworkCapabilities(network) ?: return@any false
        caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
    }
}
