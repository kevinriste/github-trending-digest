

def test_api_key_for_routes_luna_to_noshare(monkeypatch):
    import hn_comment_camps as m
    monkeypatch.setenv("OPENAI_API_KEY", "sk-share")
    monkeypatch.setenv("OPENAI_API_KEY_NOSHARE", "sk-noshare")
    assert m._api_key_for("gpt-6-luna") == "sk-noshare"
    assert m._api_key_for("gpt-6-sol") == "sk-share"
    monkeypatch.delenv("OPENAI_API_KEY_NOSHARE")
    assert m._api_key_for("gpt-6-luna") == "sk-share"


def _flex_429():
    import httpx2
    from openai import RateLimitError
    req = httpx2.Request("POST", "https://api.openai.com/v1/responses")
    return RateLimitError("Resource Unavailable", response=httpx2.Response(429, request=req), body=None)


class _FakeResponses:
    def __init__(self, fail_flex):
        self.fail_flex = fail_flex
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("service_tier") == "flex" and self.fail_flex:
            raise _flex_429()
        return "resp"


class _FakeClient:
    def __init__(self, fail_flex=False):
        self.responses = _FakeResponses(fail_flex)
        self.retries = []

    def with_options(self, max_retries):
        self.retries.append(max_retries)
        return self


def test_create_uses_flex_then_falls_back(monkeypatch):
    import hn_comment_camps as m
    monkeypatch.setenv("OPENAI_API_KEY_NOSHARE", "sk-noshare")
    monkeypatch.delenv("OPENAI_FLEX", raising=False)
    client = _FakeClient(fail_flex=True)
    assert m._create(client, model="gpt-6-luna", timeout=300, input="x") == "resp"
    flex, standard = client.responses.calls
    assert flex["service_tier"] == "flex" and flex["timeout"] == m.FLEX_TIMEOUT and client.retries == [0]
    assert "service_tier" not in standard and standard["timeout"] == 300


def test_create_standard_tier_without_noshare_or_when_disabled(monkeypatch):
    import hn_comment_camps as m
    monkeypatch.delenv("OPENAI_API_KEY_NOSHARE", raising=False)
    client = _FakeClient()
    m._create(client, model="gpt-6-luna", timeout=300, input="x")
    monkeypatch.setenv("OPENAI_API_KEY_NOSHARE", "sk-noshare")
    monkeypatch.setenv("OPENAI_FLEX", "0")
    m._create(client, model="gpt-6-luna", timeout=300, input="x")
    assert [c.get("service_tier") for c in client.responses.calls] == [None, None]
