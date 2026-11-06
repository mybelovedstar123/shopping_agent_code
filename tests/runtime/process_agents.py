"""Agents imported by name inside child processes during tests."""

import os

from kyo.runtime.agent import Agent


class Worker(Agent):
    name = "worker"

    def __init__(self, factor=1):
        self.factor = factor
        self.calls = 0

    def on_multiply(self, payload, context):
        self.calls += 1
        return {"value": payload["value"] * self.factor, "calls": self.calls, "pid": os.getpid()}

    def on_fail(self, payload, context):
        raise ValueError("cannot do that")

    def on_die(self, payload, context):
        os._exit(1)

    def on_ask_helper(self, payload, context):
        context.note("asking the helper")
        return context.request("helper", "add_one", {"value": payload["value"]})

    def on_ask_missing(self, payload, context):
        return context.request("helper", "nonsense")
