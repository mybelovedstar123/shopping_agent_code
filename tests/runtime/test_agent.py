import pytest

from kyo.runtime.agent import Agent, UnknownKind
from kyo.runtime.messages import Message


class Doubler(Agent):
    name = "doubler"

    def on_double(self, payload, context):
        return {"value": payload["value"] * 2}

    def on_nothing(self, payload, context):
        return None


def test_agent_dispatches_by_message_kind():
    reply = Doubler().handle(Message("a", "doubler", "double", {"value": 4}), None)
    assert reply == {"value": 8}


def test_handler_returning_nothing_replies_with_an_empty_payload():
    assert Doubler().handle(Message("a", "doubler", "nothing"), None) == {}


def test_agent_rejects_unknown_kinds():
    with pytest.raises(UnknownKind, match="doubler does not handle 'triple'"):
        Doubler().handle(Message("a", "doubler", "triple"), None)
