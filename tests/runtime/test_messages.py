import pytest

from kyo.runtime.messages import Message


def test_message_round_trips_through_json():
    message = Message("a", "b", "search", {"words": ["red", "lamp"], "page": 1})
    assert Message.from_json(message.to_json()) == message


def test_message_payload_must_be_json_values():
    with pytest.raises(TypeError):
        Message("a", "b", "search", {"bad": object()}).to_json()


def test_reply_goes_back_to_the_sender_and_names_the_request():
    request = Message("a", "b", "search")
    reply = request.reply({"rows": []})
    assert (reply.sender, reply.recipient, reply.reply_to) == ("b", "a", request.id)
    assert reply.payload == {"rows": []} and not reply.failed


def test_failure_carries_the_error():
    failure = Message("a", "b", "search").failure("store unavailable")
    assert failure.failed and failure.error == "store unavailable"
