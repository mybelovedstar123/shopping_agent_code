import os

import pytest

from kyo.runtime.agent import Agent, AgentError
from kyo.runtime.bus import Bus
from kyo.runtime.process import ProcessAgent
from kyo.runtime.trace import Trace


class Helper(Agent):
    name = "helper"

    def on_add_one(self, payload, context):
        return {"value": payload["value"] + 1, "pid": os.getpid()}


@pytest.fixture
def team():
    trace = Trace()
    bus = Bus(timeout=60, trace=trace)
    worker = ProcessAgent("worker", "process_agents:Worker", {"factor": 3})
    bus.register(worker)
    bus.register(Helper())
    with bus:
        yield bus, trace
    worker.stop()


def test_agent_runs_in_another_process_with_its_settings(team):
    bus, _ = team
    reply = bus.request("worker", "multiply", {"value": 2})
    assert reply["value"] == 6
    assert reply["pid"] != os.getpid()


def test_agent_keeps_its_state_between_messages(team):
    bus, _ = team
    bus.request("worker", "multiply", {"value": 1})
    assert bus.request("worker", "multiply", {"value": 1})["calls"] == 2


def test_handler_failure_comes_back_as_an_error(team):
    bus, _ = team
    with pytest.raises(AgentError, match="ValueError: cannot do that"):
        bus.request("worker", "fail")
    assert bus.request("worker", "multiply", {"value": 1})["value"] == 3


def test_child_can_ask_other_agents_and_leave_notes(team):
    bus, trace = team
    reply = bus.request("worker", "ask_helper", {"value": 1})
    assert reply["value"] == 2 and reply["pid"] == os.getpid()
    assert any(e.get("text") == "asking the helper" for e in trace.events())


def test_error_from_another_agent_reaches_the_child_as_an_error(team):
    bus, _ = team
    with pytest.raises(AgentError, match="UnknownKind"):
        bus.request("worker", "ask_missing")


def test_dead_process_gives_errors_and_leaves_other_agents_working(team):
    bus, _ = team
    with pytest.raises(AgentError, match="the worker process has stopped"):
        bus.request("worker", "die")
    with pytest.raises(AgentError, match="the worker process has stopped"):
        bus.request("worker", "multiply", {"value": 1})
    assert bus.request("helper", "add_one", {"value": 1})["value"] == 2
