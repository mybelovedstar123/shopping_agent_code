import threading

import pytest

from kyo.runtime.agent import Agent, AgentError
from kyo.runtime.bus import Bus
from kyo.runtime.trace import Trace


def test_events_are_numbered_in_order():
    trace = Trace()
    trace.record("note", "a", text="first")
    trace.record("note", "b", text="second")
    assert [(e["seq"], e["agent"], e["text"]) for e in trace.events()] == [
        (1, "a", "first"),
        (2, "b", "second"),
    ]


def test_concurrent_events_keep_unique_sequence_numbers():
    trace = Trace()

    def write():
        for _ in range(100):
            trace.record("note", "a")

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert [e["seq"] for e in trace.events()] == list(range(1, 801))


def test_subscribers_see_events_as_they_happen():
    trace = Trace()
    seen = []
    trace.subscribe(seen.append)
    event = trace.record("note", "a")
    assert seen == [event]


def test_failing_subscriber_cannot_affect_others():
    trace = Trace()
    seen = []

    def broken(event):
        raise RuntimeError("observer failed")

    trace.subscribe(broken)
    trace.subscribe(seen.append)
    trace.record("note", "a")
    assert len(seen) == 1


class Worker(Agent):
    name = "worker"

    def on_work(self, payload, context):
        context.note("working on it", size=payload["size"])
        return {"ok": True}

    def on_fail(self, payload, context):
        raise ValueError("cannot")


@pytest.fixture
def traced():
    trace = Trace()
    bus = Bus(timeout=2, trace=trace)
    bus.register(Worker())
    with bus:
        yield bus, trace


def test_bus_records_each_request_reply_and_note(traced):
    bus, trace = traced
    bus.request("worker", "work", {"size": 2})
    assert [(e["type"], e["agent"]) for e in trace.events()] == [
        ("request", "client"),
        ("note", "worker"),
        ("reply", "worker"),
    ]
    request, note, reply = trace.events()
    assert request["to"] == "worker" and request["kind"] == "work"
    assert request["payload"] == {"size": 2}
    assert note["text"] == "working on it" and note["size"] == 2
    assert reply["payload"] == {"ok": True}


def test_bus_records_failures(traced):
    bus, trace = traced
    with pytest.raises(AgentError):
        bus.request("worker", "fail")
    assert trace.events()[-1]["type"] == "failure"
    assert "ValueError: cannot" in trace.events()[-1]["error"]


def test_bus_works_without_a_trace():
    bus = Bus(timeout=2)
    bus.register(Worker())
    with bus:
        assert bus.request("worker", "work", {"size": 1}) == {"ok": True}
