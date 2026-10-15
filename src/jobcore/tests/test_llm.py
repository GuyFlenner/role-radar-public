"""Tests for the provider-agnostic LLM adapter — pure request builders + response
parsers (no network). A live smoke test runs only if a provider is reachable.
"""

import io
import json
import os
import urllib.error
import urllib.request

import pytest

from src.jobcore import llm

# --- request builders --------------------------------------------------------


def test_ollama_request_shape():
    url, headers, data = llm.build_ollama_request("sys", "hi", "llama3.1:8b", "http://localhost:11434")
    assert url == "http://localhost:11434/api/chat"
    body = json.loads(data)
    assert body["model"] == "llama3.1:8b"
    assert body["stream"] is False
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_anthropic_request_shape():
    url, headers, data = llm.build_anthropic_request("sys", "hi", "claude-opus-5", "sk-test", 1024)
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-test"
    assert headers["anthropic-version"] == "2023-06-01"
    body = json.loads(data)
    assert body["system"] == "sys" and body["messages"][0]["content"] == "hi"
    assert body["max_tokens"] == 1024


def test_openai_request_shape():
    url, headers, data = llm.build_openai_request("sys", "hi", "gpt-4o-mini", "https://api.openai.com", "sk-x", 512)
    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-x"
    assert json.loads(data)["model"] == "gpt-4o-mini"


# --- response parsers --------------------------------------------------------


def test_parse_ollama():
    assert llm.parse_ollama({"message": {"role": "assistant", "content": "  hello  "}}) == "hello"


def test_parse_anthropic():
    payload = {"content": [{"type": "text", "text": "answer"}]}
    assert llm.parse_anthropic(payload) == "answer"


def test_parse_openai():
    payload = {"choices": [{"message": {"content": "answer"}}]}
    assert llm.parse_openai(payload) == "answer"


# --- config / error handling -------------------------------------------------


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    with pytest.raises(RuntimeError, match="Unknown LLM_PROVIDER"):
        llm.complete("s", "u")


def test_anthropic_missing_key_raises(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm.complete("s", "u")


def test_openai_missing_key_remote_raises(monkeypatch):
    # J5: a remote OpenAI/OpenRouter base with no key → clear error, not a bare 401.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm.complete("s", "u")


def test_openai_missing_key_local_does_not_trip_guard(monkeypatch):
    # J5: a LOCAL server (Ollama/LM Studio/vLLM) may omit the key — the guard must NOT fire;
    # we should reach the network layer (stubbed to fail) instead of the key error.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:1234")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def _no_server(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _no_server)
    with pytest.raises(RuntimeError, match="request failed"):
        llm.complete("s", "u")


def test_http_error_body_is_surfaced(monkeypatch):
    # J1: provider error reason lives in the response BODY — it must reach the message,
    # not be hidden behind an opaque HTTP 400.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openrouter.ai/api")
    body = io.BytesIO(b'{"error":{"message":"Unsupported parameter: use max_completion_tokens"}}')

    def _http_400(*a, **k):
        raise urllib.error.HTTPError("http://x", 400, "Bad Request", {}, body)

    monkeypatch.setattr(urllib.request, "urlopen", _http_400)
    with pytest.raises(RuntimeError, match="max_completion_tokens"):
        llm.complete("s", "u")


# --- cutover validation: OpenRouter / DeepSeek / GPT-5 shape guards ----------
# These lock in the provider-agnostic contract so a future cutover to a cheap
# model can't surprise us. They are pure (no network) and run in CI.


def test_openrouter_base_builds_correct_endpoint():
    # THE #1 gotcha: base must end at /api — the adapter appends /v1/chat/completions.
    # Using https://openrouter.ai/api/v1 would double the /v1 and 404.
    url, headers, data = llm.build_openai_request(
        "sys", "hi", "deepseek/deepseek-chat", "https://openrouter.ai/api", "sk-or-x", 512
    )
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-or-x"
    assert json.loads(data)["model"] == "deepseek/deepseek-chat"


def test_openrouter_wrong_base_would_double_v1():
    # Documents the failure mode so nobody "fixes" the base to include /v1.
    url, _, _ = llm.build_openai_request("s", "u", "m", "https://openrouter.ai/api/v1", "k", 16)
    assert url == "https://openrouter.ai/api/v1/v1/chat/completions"  # <-- broken on purpose


def test_openai_trailing_slash_base_is_normalized():
    url, _, _ = llm.build_openai_request("s", "u", "m", "https://openrouter.ai/api/", "k", 16)
    assert url == "https://openrouter.ai/api/v1/chat/completions"


def test_openrouter_attribution_headers_when_set():
    # D-CT-3: OpenRouter's optional HTTP-Referer / X-Title, added only for openrouter bases.
    _, headers, _ = llm.build_openai_request(
        "s",
        "u",
        "deepseek/deepseek-chat",
        "https://openrouter.ai/api",
        "k",
        16,
        referer="https://github.com/example-org/job-seeking-platform",
        title="jobcore",
    )
    assert headers["HTTP-Referer"] == "https://github.com/example-org/job-seeking-platform"
    assert headers["X-Title"] == "jobcore"


def test_openrouter_headers_absent_when_unset_or_non_openrouter():
    _, h1, _ = llm.build_openai_request("s", "u", "m", "https://openrouter.ai/api", "k", 16)
    assert "HTTP-Referer" not in h1 and "X-Title" not in h1
    # non-openrouter base never gets them, even if passed
    _, h2, _ = llm.build_openai_request("s", "u", "m", "https://api.openai.com", "k", 16, referer="r", title="t")
    assert "HTTP-Referer" not in h2 and "X-Title" not in h2


def test_openrouter_provider_alias_defaults_base(monkeypatch):
    # D-CT-3: LLM_PROVIDER=openrouter with no key → error names the openrouter base,
    # proving the alias routes the openai path with base defaulting to openrouter.ai.
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="openrouter.ai"):
        llm.complete("s", "u")


def test_deepseek_via_openai_provider_parses_standard_shape():
    # DeepSeek-V3/chat over OpenRouter returns the standard OpenAI shape.
    payload = {
        "id": "gen-x",
        "model": "deepseek/deepseek-chat",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "  OK  "}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 1},
    }
    assert llm.parse_openai(payload) == "OK"


def test_parse_openai_null_content_and_reasoning_fallback():
    # J4: content:null must NOT crash (.strip() on None) — returns "".
    assert llm.parse_openai({"choices": [{"message": {"content": None}}]}) == ""
    # J3: empty content but reasoning present → fall back to reasoning text, not silent empty.
    assert (
        llm.parse_openai({"choices": [{"message": {"content": "", "reasoning_content": "the answer"}}]}) == "the answer"
    )
    assert llm.parse_openai({"choices": [{"message": {"reasoning": "thinking"}}]}) == "thinking"
    # genuinely empty → "" (caller decides); no choices / no payload → "".
    assert llm.parse_openai({"choices": [{"message": {"content": ""}}]}) == ""
    assert llm.parse_openai({"choices": []}) == ""
    assert llm.parse_openai({}) == ""


def test_gpt5_uses_max_completion_tokens():
    # J2: GPT-5.x / o-series reject `max_tokens` and require `max_completion_tokens`.
    for mdl in ("openai/gpt-5", "gpt-5-mini", "o3-mini", "o1"):
        _, _, data = llm.build_openai_request("s", "u", mdl, "https://openrouter.ai/api", "k", 256)
        body = json.loads(data)
        assert body["max_completion_tokens"] == 256, mdl
        assert "max_tokens" not in body, mdl


def test_deepseek_and_gpt4o_use_max_tokens():
    # The recommended DeepSeek default (and gpt-4o) keep the classic `max_tokens`.
    for mdl in ("deepseek/deepseek-chat", "gpt-4o-mini", "qwen/qwen-2.5-72b-instruct"):
        _, _, data = llm.build_openai_request("s", "u", mdl, "https://api.openai.com", "k", 256)
        body = json.loads(data)
        assert body["max_tokens"] == 256, mdl
        assert "max_completion_tokens" not in body, mdl


# --- fallback chain: parsing (pure) ------------------------------------------


def test_parse_fallback_chain_splits_provider_and_keeps_model_colons():
    hops = llm.parse_fallback_chain(
        "ollama:qwen2.5-coder:3b, openrouter:qwen/qwen3-coder:free, openrouter:deepseek/deepseek-chat"
    )
    assert hops == [
        {"provider": "ollama", "model": "qwen2.5-coder:3b"},
        {"provider": "openrouter", "model": "qwen/qwen3-coder:free"},
        {"provider": "openrouter", "model": "deepseek/deepseek-chat"},
    ]


def test_parse_fallback_chain_handles_newlines_semicolons_and_no_model():
    hops = llm.parse_fallback_chain(" anthropic ;\n openai:gpt-4o-mini ,, ")
    assert hops == [
        {"provider": "anthropic", "model": None},
        {"provider": "openai", "model": "gpt-4o-mini"},
    ]


def test_parse_fallback_chain_empty_is_empty_list():
    assert llm.parse_fallback_chain("") == []
    assert llm.parse_fallback_chain("   ,  ; \n ") == []


# --- fallback chain: logic (complete() monkeypatched, no network) ------------


def test_fallback_uses_first_hop_that_succeeds(monkeypatch):
    calls = []

    def fake_complete(system, user, *, provider=None, model=None, api_key=None, base_url=None, **kw):
        calls.append((provider, model))
        if provider == "ollama":
            raise RuntimeError("ollama request failed (connection refused)")
        return "OK from " + str(model)

    monkeypatch.setattr(llm, "complete", fake_complete)
    text, meta = llm.complete_with_fallback(
        "s",
        "u",
        chain=[{"provider": "ollama", "model": "x"}, {"provider": "openrouter", "model": "deepseek/deepseek-chat"}],
    )
    assert text == "OK from deepseek/deepseek-chat"
    assert meta["provider"] == "openrouter" and meta["hop"] == 2
    assert calls == [("ollama", "x"), ("openrouter", "deepseek/deepseek-chat")]  # tried in order, stopped at success


def test_fallback_treats_empty_reply_as_failure_and_moves_on(monkeypatch):
    def fake_complete(system, user, *, provider=None, model=None, **kw):
        return "   " if provider == "openrouter" and model == "free" else "real answer"

    monkeypatch.setattr(llm, "complete", fake_complete)
    text, meta = llm.complete_with_fallback(
        "s", "u", chain=[{"provider": "openrouter", "model": "free"}, {"provider": "openrouter", "model": "paid"}]
    )
    assert text == "real answer" and meta["model"] == "paid"


def test_fallback_raises_with_all_reasons_when_every_hop_fails(monkeypatch):
    def fake_complete(system, user, *, provider=None, model=None, **kw):
        raise RuntimeError(f"{provider} HTTP 429: rate limit")

    monkeypatch.setattr(llm, "complete", fake_complete)
    with pytest.raises(RuntimeError, match="all 2 fallback hop") as ei:
        llm.complete_with_fallback("s", "u", chain=[{"provider": "ollama"}, {"provider": "openrouter"}])
    assert "429" in str(ei.value)  # each hop's reason is surfaced


def test_fallback_reads_chain_and_key_overrides_from_env(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_CHAIN", "openrouter:free, openrouter:paid")
    monkeypatch.setenv("LLM_FALLBACK_KEY_2", "sk-or-second-account")
    seen = {}

    def fake_complete(system, user, *, provider=None, model=None, api_key=None, **kw):
        seen[model] = api_key
        if model == "free":
            raise RuntimeError("openrouter HTTP 429: free tier exhausted")
        return "OK"

    monkeypatch.setattr(llm, "complete", fake_complete)
    text, meta = llm.complete_with_fallback("s", "u")  # chain from env
    assert text == "OK" and meta["model"] == "paid"
    assert seen["paid"] == "sk-or-second-account"  # per-hop key override applied to hop 2


# --- structured output --------------------------------------------------------

_SCHEMA = {
    "type": "object",
    "required": ["score", "note"],
    "properties": {"score": {"type": "integer"}, "note": {"type": "string"}},
}


class TestValidateAgainstSchema:
    def test_valid_object_has_no_errors(self):
        assert llm.validate_against_schema({"score": 7, "note": "ok"}, _SCHEMA) == []

    def test_missing_required_property(self):
        errors = llm.validate_against_schema({"score": 7}, _SCHEMA)
        assert any("note" in e and "required" in e for e in errors)

    def test_wrong_type_reported(self):
        errors = llm.validate_against_schema({"score": "seven", "note": "ok"}, _SCHEMA)
        assert any("score" in e and "expected integer" in e for e in errors)

    def test_bool_is_not_accepted_as_integer(self):
        # Python's bool is an int subclass -- isinstance(True, int) is True, which
        # would silently accept a boolean where an integer was required.
        errors = llm.validate_against_schema({"score": True, "note": "ok"}, _SCHEMA)
        assert any("boolean" in e for e in errors)

    def test_nested_object_validated_recursively(self):
        schema = {
            "type": "object",
            "required": ["outer"],
            "properties": {
                "outer": {"type": "object", "required": ["inner"], "properties": {"inner": {"type": "string"}}}
            },
        }
        errors = llm.validate_against_schema({"outer": {}}, schema)
        assert any("outer.inner" in e for e in errors)

    def test_array_items_validated(self):
        schema = {"type": "array", "items": {"type": "integer"}}
        errors = llm.validate_against_schema([1, "two", 3], schema)
        assert len(errors) == 1 and "[1]" in errors[0]

    def test_enum_violation_reported(self):
        schema = {"type": "string", "enum": ["a", "b"]}
        assert llm.validate_against_schema("c", schema) != []
        assert llm.validate_against_schema("a", schema) == []


class TestCompleteStructured:
    def test_first_try_valid_json_returns_parsed(self, monkeypatch):
        monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"score": 8, "note": "good fit"}')
        result = llm.complete_structured("s", "u", _SCHEMA)
        assert result == {"score": 8, "note": "good fit"}

    def test_strips_markdown_code_fence(self, monkeypatch):
        monkeypatch.setattr(llm, "complete", lambda *a, **k: '```json\n{"score": 5, "note": "x"}\n```')
        assert llm.complete_structured("s", "u", _SCHEMA) == {"score": 5, "note": "x"}

    def test_invalid_then_valid_retries_and_succeeds(self, monkeypatch):
        replies = iter(["not json at all", '{"score": 9, "note": "fixed"}'])
        monkeypatch.setattr(llm, "complete", lambda *a, **k: next(replies))
        result = llm.complete_structured("s", "u", _SCHEMA, retries=1)
        assert result == {"score": 9, "note": "fixed"}

    def test_retry_prompt_shows_the_model_its_own_error(self, monkeypatch):
        seen_users = []

        def fake_complete(system, user, **kw):
            seen_users.append(user)
            return "garbage" if len(seen_users) == 1 else '{"score": 1, "note": "x"}'

        monkeypatch.setattr(llm, "complete", fake_complete)
        llm.complete_structured("s", "u", _SCHEMA, retries=1)
        assert "invalid" in seen_users[1].lower()

    def test_exhausts_retries_and_raises_with_reason(self, monkeypatch):
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "still not json")
        with pytest.raises(RuntimeError, match="did not match schema after 2 attempt"):
            llm.complete_structured("s", "u", _SCHEMA, retries=1)

    def test_missing_required_field_triggers_retry_not_silent_accept(self, monkeypatch):
        monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"score": 5}')  # missing "note"
        with pytest.raises(RuntimeError, match="note"):
            llm.complete_structured("s", "u", _SCHEMA, retries=0)

    def test_zero_retries_means_one_attempt_only(self, monkeypatch):
        calls = {"n": 0}

        def fake_complete(*a, **k):
            calls["n"] += 1
            return "not json"

        monkeypatch.setattr(llm, "complete", fake_complete)
        with pytest.raises(RuntimeError):
            llm.complete_structured("s", "u", _SCHEMA, retries=0)
        assert calls["n"] == 1


class TestCompleteStructuredWithFallback:
    def test_no_chain_uses_single_provider(self, monkeypatch):
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)
        monkeypatch.setattr(llm, "complete", lambda *a, **k: '{"score": 6, "note": "single"}')
        parsed, meta = llm.complete_structured_with_fallback("s", "u", _SCHEMA)
        assert parsed == {"score": 6, "note": "single"}
        assert meta["hop"] == 1

    def test_first_hop_invalid_json_falls_through_to_second_hop(self, monkeypatch):
        def fake_complete(system, user, *, provider=None, model=None, **kw):
            return "not json" if provider == "ollama" else '{"score": 3, "note": "from hop 2"}'

        monkeypatch.setattr(llm, "complete", fake_complete)
        parsed, meta = llm.complete_structured_with_fallback(
            "s",
            "u",
            _SCHEMA,
            chain=[{"provider": "ollama", "model": "x"}, {"provider": "openrouter", "model": "y"}],
            retries=0,
        )
        assert parsed["note"] == "from hop 2"
        assert meta["hop"] == 2

    def test_all_hops_failing_raises(self, monkeypatch):
        monkeypatch.setattr(llm, "complete", lambda *a, **k: "not json")
        with pytest.raises(RuntimeError, match="all 2 fallback hop"):
            llm.complete_structured_with_fallback(
                "s", "u", _SCHEMA, chain=[{"provider": "ollama"}, {"provider": "openrouter"}], retries=0
            )


# --- optional live smoke (only if a provider is reachable) -------------------


@pytest.mark.skipif(
    (os.environ.get("LLM_PROVIDER", "ollama") == "ollama"),
    reason="live LLM smoke skipped by default (needs a reachable Ollama/API; set LLM_PROVIDER + creds to run)",
)
def test_live_completion_smoke():
    out = llm.complete("You are terse.", "Reply with exactly: OK", max_tokens=16)
    assert isinstance(out, str) and out
