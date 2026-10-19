package com.rasova.pos

import android.app.Activity
import android.app.AlertDialog
import android.text.InputType
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.widget.EditText
import android.widget.FrameLayout

/**
 * The web pages' alert(), confirm() and prompt(), shown as Android dialogs.
 *
 * A WebView with no WebChromeClient shows none of them: alert() is skipped,
 * confirm() answers "cancel" and prompt() answers nothing, without a word.
 * Rasova's pages use about 90 of them (are-you-sure checks, the bill's
 * discount and split prompts, stock edits), so in the app those buttons
 * quietly did nothing (found 19 Oct 2026). Every dialog ends in exactly one
 * of result.confirm() or result.cancel(), so the page never hangs.
 */
class JsDialogs(private val activity: Activity) : WebChromeClient() {

    override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean {
        if (activity.isFinishing) return false
        AlertDialog.Builder(activity)
            .setMessage(message)
            .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
            .setOnCancelListener { result.confirm() }
            .show()
        return true
    }

    override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean {
        if (activity.isFinishing) return false
        AlertDialog.Builder(activity)
            .setMessage(message)
            .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm() }
            .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
            .setOnCancelListener { result.cancel() }
            .show()
        return true
    }

    override fun onJsPrompt(
        view: WebView, url: String, message: String, defaultValue: String?, result: JsPromptResult
    ): Boolean {
        if (activity.isFinishing) return false
        val input = EditText(activity).apply {
            setText(defaultValue ?: "")
            setSelectAllOnFocus(true)
            // A number when the page suggests one (discount %, split count, cash float).
            if (defaultValue?.trim()?.toDoubleOrNull() != null) {
                inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            }
        }
        val padding = (20 * activity.resources.displayMetrics.density).toInt()
        val frame = FrameLayout(activity).apply {
            setPadding(padding, padding / 2, padding, 0)
            addView(input)
        }
        AlertDialog.Builder(activity)
            .setMessage(message)
            .setView(frame)
            .setPositiveButton(android.R.string.ok) { _, _ -> result.confirm(input.text.toString()) }
            .setNegativeButton(android.R.string.cancel) { _, _ -> result.cancel() }
            .setOnCancelListener { result.cancel() }
            .show()
        return true
    }
}
