package com.rasova.pos

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The agent key moved from the poll URL to the X-Agent-Key header (1.1.0). */
class AgentConfigTest {

    private val key = "3f2b8a1c-9d4e-4f6a-8b7c-1a2b3c4d5e6f"

    @Test
    fun anOldPollUrlBecomesAServerAndAKey() {
        assertEquals(
            AgentConfig.Target("https://rasova.net", key),
            AgentConfig.fromOldPollUrl("https://rasova.net/orders/agent/$key/"),
        )
        assertEquals(
            AgentConfig.Target("https://spice.rasova.net", key),
            AgentConfig.fromOldPollUrl("  https://spice.rasova.net/orders/agent/$key  "),
        )
    }

    @Test
    fun anythingElseIsNotAnOldPollUrl() {
        assertNull(AgentConfig.fromOldPollUrl("https://rasova.net/orders/agent/"))
        assertNull(AgentConfig.fromOldPollUrl("https://rasova.net/orders/agent/not-a-key/"))
        assertNull(AgentConfig.fromOldPollUrl(""))
    }

    @Test
    fun theKeyIsNeverPartOfTheAgentAddress() {
        val target = AgentConfig.Target("https://rasova.net/", key)
        assertEquals("https://rasova.net/orders/agent/", target.agentBase)
    }
}
