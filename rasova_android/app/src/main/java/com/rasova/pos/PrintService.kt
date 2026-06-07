package com.rasova.pos

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.IBinder
import android.util.Base64
import android.util.Log
import kotlinx.coroutines.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import java.io.OutputStream
import java.net.Socket
import java.util.concurrent.TimeUnit

class PrintService : Service() {

    // Coroutine scope: lets us write the polling loop in clean sequential code
    // without blocking the main thread (which would freeze the UI)
    private val serviceJob = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.IO + serviceJob)

    // OkHttp: an HTTP client library. Does GET/POST to EC2 efficiently.
    private val http = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.SECONDS)
        .writeTimeout(5, TimeUnit.SECONDS)
        .build()

    companion object {
        const val EXTRA_POLL_URL = "poll_url"
        const val CHANNEL_ID     = "rasova_print_channel"
        const val NOTIF_ID       = 101
        private const val TAG    = "RasovaPrint"

        // Status string readable by JSBridge.getPrintingStatus()
        @Volatile var status = "stopped"
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // Read poll URL: either from the intent (fresh start) or SharedPreferences (reboot)
        val pollUrl = intent?.getStringExtra(EXTRA_POLL_URL)
            ?: getSharedPreferences(JSBridge.PREFS, MODE_PRIVATE)
                .getString(JSBridge.KEY_POLL_URL, null)

        if (pollUrl.isNullOrBlank()) {
            status = "no_url"
            stopSelf()
            return START_NOT_STICKY
        }

        // START_STICKY = if Android kills us (low memory), restart us automatically
        createNotificationChannel()

        // Android 14 (API 34) requires the typed startForeground() for a service
        // that declares foregroundServiceType. The 2-arg form throws
        // MissingForegroundServiceTypeException and the service never starts.
        try {
            val notif = buildNotification("Rasova Printing Active")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {  // API 34
                startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
            } else {
                startForeground(NOTIF_ID, notif)
            }
        } catch (e: Exception) {
            Log.e(TAG, "startForeground failed: ${e.message}", e)
            status = "error"
            stopSelf()
            return START_NOT_STICKY
        }
        status = "active"

        startPolling(pollUrl)
        return START_STICKY
    }

    // ── Core polling loop ──────────────────────────────────────────────────────

    private fun startPolling(pollUrl: String) {
        val base     = pollUrl.trimEnd('/') + "/"
        val jobsUrl  = base + "jobs/"
        val doneBase = base + "done/"
        val failBase = base + "failed/"

        scope.launch {
            var backoffMs = 2_000L  // doubles on error, resets on success, max 30s

            while (isActive) {
                // Skip HTTP entirely when offline — avoids 1,800 exceptions/hour on WiFi drop
                if (!isNetworkAvailable()) {
                    status = "error"
                    delay(5_000)
                    continue
                }

                try {
                    val jobs: JSONArray = fetchJobs(jobsUrl)
                    backoffMs = 2_000L  // reset on any successful response

                    if (jobs.length() > 0) {
                        status = "printing"
                        notify("Printing ${jobs.length()} job(s)…")
                    }

                    for (i in 0 until jobs.length()) {
                        val job      = jobs.getJSONObject(i)
                        val jobId    = job.getInt("id")
                        val host     = job.getString("network_host")
                        val port     = job.getInt("network_port")
                        val dataB64  = job.getString("data_b64")

                        // Base64 decode → raw ESC/POS bytes → send to printer via TCP
                        val ok = sendToPrinter(host, port, dataB64)
                        if (ok) {
                            postJson("$doneBase$jobId/", "{}")
                        } else {
                            postJson("$failBase$jobId/",
                                """{"error":"TCP connection failed to $host:$port"}""")
                        }
                    }

                    if (jobs.length() == 0) {
                        status = "active"
                        notify("Rasova Printing Active")
                    }

                } catch (e: Exception) {
                    // Exponential backoff: 2s → 4s → 8s → 16s → 30s (max)
                    backoffMs = minOf(backoffMs * 2, 30_000L)
                    Log.e(TAG, "Poll error (next retry in ${backoffMs}ms): ${e.message}")
                    status = "error"
                    notify("Print error — retrying…")
                }

                delay(backoffMs)
            }
        }
    }

    // Returns true if there is a usable network connection
    private fun isNetworkAvailable(): Boolean {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val cap = cm.getNetworkCapabilities(cm.activeNetwork) ?: return false
            cap.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
        } else {
            @Suppress("DEPRECATION")
            cm.activeNetworkInfo?.isConnected == true
        }
    }

    // ── Network helpers ────────────────────────────────────────────────────────

    private fun fetchJobs(url: String): JSONArray {
        val req  = Request.Builder().url(url).get().build()
        val body = http.newCall(req).execute().use { it.body?.string() ?: "{}" }
        return JSONObject(body).optJSONArray("jobs") ?: JSONArray()
    }

    private fun sendToPrinter(host: String, port: Int, dataB64: String): Boolean {
        return try {
            // Base64 → raw bytes: this is the ESC/POS receipt data
            val bytes: ByteArray = Base64.decode(dataB64, Base64.DEFAULT)
            // Open a raw TCP socket to the printer (port 9100 is the universal ESC/POS port)
            Socket(host, port).use { socket ->
                socket.soTimeout = 5_000
                val out: OutputStream = socket.getOutputStream()
                out.write(bytes)
                out.flush()
            }
            Log.i(TAG, "Printed ${bytes.size} bytes to $host:$port")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Print failed ($host:$port): ${e.message}")
            false
        }
    }

    private fun postJson(url: String, json: String) {
        try {
            val body = json.toRequestBody("application/json".toMediaType())
            val req  = Request.Builder().url(url).post(body).build()
            http.newCall(req).execute().close()
        } catch (e: Exception) {
            Log.e(TAG, "Post to $url failed: ${e.message}")
        }
    }

    // ── Notification helpers ───────────────────────────────────────────────────

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Rasova Print Service",
                NotificationManager.IMPORTANCE_LOW   // LOW = silent, no sound
            ).apply {
                description = "Keeps printing active in background"
            }
            getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    private fun buildNotification(text: String): Notification {
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setContentTitle("Rasova")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_notification)
            .setOngoing(true)   // ongoing = cannot be swiped away by user
            .build()
    }

    private fun notify(text: String) {
        getSystemService(NotificationManager::class.java)
            .notify(NOTIF_ID, buildNotification(text))
    }

    // ── Lifecycle ──────────────────────────────────────────────────────────────

    override fun onBind(intent: Intent?): IBinder? = null  // not a bound service

    override fun onDestroy() {
        status = "stopped"
        serviceJob.cancel()
        super.onDestroy()
    }
}
