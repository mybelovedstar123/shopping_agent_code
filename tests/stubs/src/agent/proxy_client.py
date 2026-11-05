"""Stand-in for the harness client so agent.py can be imported offline. Tests replace its use."""


class ProxyClient:
    def __init__(self, **settings):
        self.settings = settings

    def post_verbose(self, path, body):
        raise AssertionError(f"unexpected network call to {path}")
