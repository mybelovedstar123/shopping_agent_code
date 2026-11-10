import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from kyo.models.base import ModelError
from kyo.models.http import HttpModel, model_from_env

SECRET = "sk-test-secret-value"
PROMPT = [{"role": "user", "content": "hi"}]
OK = {"choices": [{"message": {"content": "hello"}}], "usage": {"cost": 0.002}}


@pytest.fixture
def endpoint():
    """A local endpoint that replies with the queued (status, body) pairs."""
    queue, received = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            received.append((self.path, dict(self.headers), json.loads(self.rfile.read(size))))
            status, body = queue.pop(0)
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1/", queue, received
    server.shutdown()
    server.server_close()


def make(url, sleeps=None):
    record = [] if sleeps is None else sleeps
    return HttpModel(url, "test-model", SECRET, timeout=5, sleep=record.append)


def test_sends_the_request_and_reads_the_reply(endpoint):
    url, queue, received = endpoint
    queue.append((200, OK))
    reply = make(url).complete(PROMPT, max_tokens=50)
    assert (reply.content, reply.cost) == ("hello", 0.002)
    path, headers, body = received[0]
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert body["model"] == "test-model" and body["max_tokens"] == 50 and body["temperature"] == 0
    assert "tools" not in body


def test_sends_tools_and_reads_tool_calls(endpoint):
    url, queue, received = endpoint
    call = {"id": "c1", "function": {"name": "search", "arguments": "{}"}}
    queue.append((200, {"choices": [{"message": {"tool_calls": [call]}}]}))
    reply = make(url).complete(PROMPT, tools=[{"type": "function"}])
    assert reply.tool_calls == [call] and reply.cost == 0
    assert received[0][2]["tool_choice"] == "auto"


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_retries_passing_failures_with_backoff(endpoint, status):
    url, queue, received = endpoint
    sleeps = []
    queue += [(status, {}), (status, {}), (200, OK)]
    assert make(url, sleeps).complete(PROMPT).content == "hello"
    assert len(received) == 3
    assert 1.6 <= sleeps[0] <= 2.4 and 4.0 <= sleeps[1] <= 6.0


def test_gives_up_after_the_last_retry(endpoint):
    url, queue, received = endpoint
    queue += [(503, {})] * 4
    with pytest.raises(ModelError, match="HTTP 503"):
        make(url).complete(PROMPT)
    assert len(received) == 4


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_does_not_retry_permanent_failures(endpoint, status):
    url, queue, received = endpoint
    queue.append((status, {}))
    with pytest.raises(ModelError, match=f"HTTP {status}"):
        make(url).complete(PROMPT)
    assert len(received) == 1


def test_connection_failure_is_a_model_error():
    with pytest.raises(ModelError, match="could not be used"):
        make("http://127.0.0.1:9/v1").complete(PROMPT)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"error": "x"},
        {"choices": [{"message": None}]},
        {"choices": [{"message": "hello"}]},
    ],
)
def test_unusable_replies_are_model_errors(endpoint, body):
    url, queue, _ = endpoint
    queue.append((200, body))
    with pytest.raises(ModelError):
        make(url).complete(PROMPT)


def test_api_key_never_appears_in_errors_or_repr(endpoint):
    url, queue, _ = endpoint
    queue.append((401, {"error": f"bad key {SECRET}"}))
    model = make(url)
    with pytest.raises(ModelError) as caught:
        model.complete(PROMPT)
    assert SECRET not in str(caught.value) and SECRET not in repr(model)


def test_environment_configures_the_model():
    model = model_from_env(
        {
            "KYO_MODEL_BASE_URL": "https://example.test/v1",
            "KYO_MODEL_NAME": "m",
            "KYO_MODEL_API_KEY": SECRET,
        }
    )
    assert model.url == "https://example.test/v1/chat/completions" and model.model == "m"


@pytest.mark.parametrize("env", [{}, {"KYO_MODEL_BASE_URL": "x"}, {"KYO_MODEL_NAME": "m"}])
def test_runs_offline_without_full_configuration(env):
    assert model_from_env(env) is None
