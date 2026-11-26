package com.rasova.pos

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** A page's file picker is answered exactly once, whatever happens (PendingAnswer.kt). */
class PendingAnswerTest {

    @Test
    fun theChosenFilesReachThePage() {
        val got = mutableListOf<List<String>?>()
        val pick = PendingAnswer<List<String>>()
        pick.begin { got.add(it) }
        assertTrue(pick.isWaiting)
        pick.finish(listOf("content://photo/1"))
        assertEquals(listOf(listOf("content://photo/1")), got)
        assertFalse(pick.isWaiting)
    }

    @Test
    fun nothingChosenIsAnAnswerToo() {
        val got = mutableListOf<List<String>?>()
        val pick = PendingAnswer<List<String>>()
        pick.begin { got.add(it) }
        pick.finish(null)
        assertEquals(listOf<List<String>?>(null), got)
    }

    @Test
    fun aSecondAnswerIsDropped() {
        val got = mutableListOf<List<String>?>()
        val pick = PendingAnswer<List<String>>()
        pick.begin { got.add(it) }
        pick.finish(listOf("a"))
        pick.finish(listOf("b"))
        assertEquals(listOf(listOf("a")), got)
    }

    @Test
    fun aLostPickerIsAnsweredNothingWhenTheNextOneOpens() {
        val first = mutableListOf<List<String>?>()
        val second = mutableListOf<List<String>?>()
        val pick = PendingAnswer<List<String>>()
        pick.begin { first.add(it) }
        pick.begin { second.add(it) }
        pick.finish(listOf("c"))
        assertEquals(listOf<List<String>?>(null), first)
        assertEquals(listOf(listOf("c")), second)
    }

    @Test
    fun anAnswerWithNothingWaitingIsDropped() {
        PendingAnswer<List<String>>().finish(listOf("x"))
    }
}
