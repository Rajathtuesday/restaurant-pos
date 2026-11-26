package com.rasova.pos

/**
 * One question to the phone at a time, answered exactly once.
 *
 * A page's <input type="file"> reaches the app as onShowFileChooser with a
 * callback the WebView needs answered exactly once: with the chosen files, or
 * with null when nothing was chosen. One left unanswered and the page's file
 * buttons never open a picker again; one answered twice is an error. Until
 * 1.3.1 the app had no onShowFileChooser at all, so a dish photo, a logo, the
 * menu import and a menu file did nothing in the app (fault plan item 12d,
 * 26 Nov 2026).
 */
class PendingAnswer<T> {
    private var waiting: ((T?) -> Unit)? = null

    val isWaiting: Boolean
        get() = waiting != null

    /** A new question. One still waiting (its picker was lost) is answered "nothing" first. */
    fun begin(answer: (T?) -> Unit) {
        waiting?.invoke(null)
        waiting = answer
    }

    /** The answer (null: nothing chosen). Nothing waiting: it is dropped. */
    fun finish(result: T?) {
        val answer = waiting ?: return
        waiting = null
        answer(result)
    }
}
