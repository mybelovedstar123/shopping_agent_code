import threading
import time

import pytest

from kyo.runtime.agent import Agent, AgentError, UnknownKind
from kyo.runtime.bus import AgentTimeout, Bus, UnknownAgent


class Counter(Agent):
    name = "counter"

    def __init__(self):
        self.value = 0
        self.threads = set()

    def on_add(self, payload, context):
        self.threads.add(threading.current_thread().name)
        current = self.value
        time.sleep(0.001)
        self.value = current + payload["by"]
        return {"value": self.value}

    def on_crash(self, payload, context):
        raise ValueError("bad input")

    def on_keep(self, payload, context):
        self.kept = payload["items"]
        self.kept.append("changed by counter")
        return {"items": self.kept}

    def on_unserialisable(self, payload, context):
        return {"value": object()}

    def on_slow(self, payload, context):
        time.sleep(payload["seconds"])
        return {"done": True}


class Asker(Agent):
    name = "asker"

    def on_ask(self, payload, context):
        return context.request("counter", "add", {"by": payload["by"]})


@pytest.fixture
def bus():
    bus = Bus(timeout=2)
    bus.register(Counter())
    bus.register(Asker())
    bus.start()
    yield bus
    bus.stop()


def test_request_returns_the_reply_payload(bus):
    assert bus.request("counter", "add", {"by": 2}) == {"value": 2}


def test_agents_can_ask_each_other(bus):
    assert bus.request("asker", "ask", {"by": 3}) == {"value": 3}


def test_each_agent_works_on_its_own_named_thread():
    agent = Counter()
    bus = Bus()
    bus.register(agent)
    with bus:
        bus.request("counter", "add", {"by": 1})
    assert agent.threads == {"counter"}


def test_mailbox_handles_one_message_at_a_time_in_order(bus):
    threads = [
        threading.Thread(target=bus.request, args=("counter", "add", {"by": 1})) for _ in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert bus.request("counter", "add", {"by": 0}) == {"value": 20}


def test_handler_failure_becomes_an_error_and_the_agent_keeps_working(bus):
    with pytest.raises(AgentError, match="ValueError: bad input"):
        bus.request("counter", "crash")
    assert bus.request("counter", "add", {"by": 1}) == {"value": 1}


def test_unknown_kind_is_reported_to_the_sender(bus):
    with pytest.raises(AgentError, match=UnknownKind.__name__):
        bus.request("counter", "nonsense")


def test_unknown_agent_is_rejected(bus):
    with pytest.raises(UnknownAgent):
        bus.request("nobody", "add")


def test_request_times_out_without_stopping_the_agent(bus):
    with pytest.raises(AgentTimeout):
        bus.request("counter", "slow", {"seconds": 0.2}, timeout=0.01)
    assert bus.request("counter", "add", {"by": 1}) == {"value": 1}


def test_agents_share_no_memory_through_payloads(bus):
    items = ["mine"]
    reply = bus.request("counter", "keep", {"items": items})
    assert items == ["mine"]
    reply["items"].append("changed by caller")
    assert bus.request("counter", "keep", {"items": []})["items"] == ["changed by counter"]


def test_unserialisable_reply_is_an_error_not_a_hang(bus):
    with pytest.raises(AgentError, match="TypeError"):
        bus.request("counter", "unserialisable")


def test_duplicate_agent_names_are_rejected():
    bus = Bus()
    bus.register(Counter())
    with pytest.raises(ValueError, match="already registered"):
        bus.register(Counter())


def test_agents_cannot_join_a_running_bus(bus):
    with pytest.raises(RuntimeError):
        bus.register(Agent())
