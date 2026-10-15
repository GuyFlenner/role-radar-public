"""End-to-end proof that the cheap/free-tier headless path works — over a REAL socket.

No external network, no API keys: we stand up a local OpenAI-compatible server (the
same wire protocol OpenRouter / Ollama / LM Studio speak) that 429s the "free" model
(simulating an exhausted free tier / quota) and serves the "paid" model. Then we drive
the real code paths through it:

  1. complete_with_fallback → free hop 429s → transparently rolls over to the paid hop.
  2. fit_note (the jobcore headless flow) end-to-end against the endpoint, both with a
     single provider and with a fallback chain.

This validates the PLUMBING that will run against a real provider on the Mac. The
real-provider check is one command (see architecture-workflows/PORTABILITY_AND_VENDOR_INDEPENDENCE.md §8):
    uv run python -m src.jobcore.smoke
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.jobcore import fit_note, llm, smoke


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /v1/chat/completions. Model name 'free' → 429."""

    def log_message(self, *a):  # keep the test output clean
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        model = body.get("model", "")
        if "free" in model:
            payload = {"error": {"message": "Rate limit exceeded: free-tier quota exhausted (429)"}}
            data = json.dumps(payload).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        # JSON, not prose: fit_note's llm_assessment() now goes through
        # complete_structured_with_fallback(), which parses+validates the reply
        # against a schema (score/strongest_match/gap) -- a real LLM asked for JSON
        # would answer in kind. The model name rides inside "gap" so tests can still
        # confirm which hop answered.
        reply_obj = {"score": 8, "strongest_match": "Strong match on the stack", "gap": f"name a metric (via {model})"}
        reply = json.dumps(reply_obj)
        payload = {"choices": [{"message": {"role": "assistant", "content": reply}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def fake_provider():
    """A live local OpenAI-compatible server on an ephemeral port. Yields its base URL."""
    server = HTTPServer(("127.0.0.1", 0), _FakeOpenAIHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_fallback_rolls_over_from_quota_free_to_paid_over_real_socket(fake_provider):
    chain = [
        {"provider": "openai", "model": "qwen/qwen3-coder:free", "base_url": fake_provider, "api_key": "sk-test"},
        {"provider": "openai", "model": "deepseek/deepseek-chat", "base_url": fake_provider, "api_key": "sk-test"},
    ]
    text, meta = llm.complete_with_fallback("You are terse.", "assess", chain=chain)
    assert '"score"' in text and "deepseek/deepseek-chat" in text
    assert meta["hop"] == 2 and meta["model"] == "deepseek/deepseek-chat"
    # hop 1 recorded as a 429 failure, hop 2 as the success
    assert meta["attempts"][0]["ok"] is False and "429" in meta["attempts"][0]["error"]
    assert meta["attempts"][1]["ok"] is True


def test_fit_note_e2e_single_provider(fake_provider, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", fake_provider)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek/deepseek-chat")
    monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)

    result = fit_note.llm_assessment("Senior Python/AWS lead. Kafka a plus.", "10y Python, AWS, Kafka, team lead.")
    assert result["score"] == 8
    # No chain configured -> the single-provider path, same shape complete_with_fallback's
    # own no-chain branch has always returned: model isn't tracked back out of complete()
    # here (pre-existing, unrelated to this change) -- only the provider is guaranteed.
    assert result["_answered_by"] == "hop 1: openai"


def test_fit_note_e2e_with_fallback_chain_over_socket(fake_provider, monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", fake_provider)  # both hops resolve their base from env
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openai:qwen/qwen3-coder:free, openai:deepseek/deepseek-chat")

    result = fit_note.llm_assessment("Backend TL, Python + Postgres.", "Backend lead, Python, Postgres, 8y.")
    assert result["score"] == 8
    assert result["_answered_by"] == "hop 2: openai:deepseek/deepseek-chat"  # free quota'd → paid answered


def test_smoke_probe_reports_ok_and_fail_per_hop(fake_provider):
    ok_hop = {"provider": "openai", "model": "deepseek/deepseek-chat", "base_url": fake_provider, "api_key": "sk-test"}
    fail_hop = {"provider": "openai", "model": "qwen/qwen3-coder:free", "base_url": fake_provider, "api_key": "sk-test"}
    assert smoke.probe_hop(ok_hop)["ok"] is True
    bad = smoke.probe_hop(fail_hop)
    assert bad["ok"] is False and "429" in bad["error"]
