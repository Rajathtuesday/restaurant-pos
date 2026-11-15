"""
A stand-in for the Anthropic client, for tests of core/ai_service.py.

No test talks to the real Claude API: the service only builds a real client
from ANTHROPIC_API_KEY, which the test runner blanks (core/settings.py,
core/tests/test_no_real_services.py), and tests that need answers hand
AIService(client=FakeClaude(...)) instead.

    fake = FakeClaude({"categories": [...]})        # every call answers this
    fake = FakeClaude(stop_reason="refusal")         # Claude declines
    fake.fails_with(Exception("503"))                # the call itself fails
    fake.calls                                       # what was sent, in order
"""
import json
from types import SimpleNamespace


class FakeClaude:

    def __init__(self, answer=None, stop_reason="end_turn", text=None):
        self.calls = []
        self._answer = answer
        self._stop_reason = stop_reason
        self._text = text
        self._error = None
        self.messages = SimpleNamespace(create=lambda **kw: self._reply("messages", kw))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: self._reply("beta", kw)))

    def fails_with(self, error):
        self._error = error
        return self

    def _reply(self, endpoint, kwargs):
        self.calls.append({"endpoint": endpoint, **kwargs})
        if self._error is not None:
            raise self._error
        text = self._text if self._text is not None else json.dumps(self._answer)
        return SimpleNamespace(stop_reason=self._stop_reason,
                               content=[SimpleNamespace(type="text", text=text)])
