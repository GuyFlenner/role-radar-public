"""Provider-agnostic LLM adapter — the vendor-lock escape hatch.

The native `job-seeking` skill runs the judgment work (research, tailoring)
inside an agentic runtime (Claude Code). This module is the *insurance*: a tiny,
dependency-free (`urllib` only) way to do a one-shot LLM completion from a plain
Python driver against ANY provider — so the deterministic core (`src/jobcore/`)
plus this adapter can run headless with no Claude Code and no vendor lock.

Provider is chosen by `LLM_PROVIDER` (default `ollama` — free & local):
  - ollama  → POST {OLLAMA_HOST}/api/chat            (OLLAMA_MODEL, e.g. llama3.1:8b)
  - anthropic → POST api.anthropic.com/v1/messages   (ANTHROPIC_API_KEY, ANTHROPIC_MODEL)
  - openai  → POST {OPENAI_BASE_URL}/v1/chat/completions (OPENAI_API_KEY, OPENAI_MODEL)
              (also covers OpenAI-compatible servers: vLLM, LM Studio, Together, etc.)
  - openrouter → alias for `openai` with base defaulting to https://openrouter.ai/api
              (OPENAI_API_KEY, OPENAI_MODEL e.g. `deepseek/deepseek-chat`; optional
              OPENROUTER_REFERER / OPENROUTER_TITLE attribution headers)

GPT-5/o-series get `max_completion_tokens` automatically; provider error bodies are
surfaced; reasoning-model empty `content` falls back to the reasoning field.

Pure builders/parsers are separated from the network call so they're unit-tested
without a live provider.
"""

import json
import os
import urllib.error
import urllib.request

from .net import require_http

DEFAULTS = {
    "ollama": {"host": "http://localhost:11434", "model": "llama3.1:8b"},
    "anthropic": {"model": "claude-opus-5"},
    "openai": {"base": "https://api.openai.com", "model": "gpt-4o-mini"},
}


def _provider() -> str:
    return (os.environ.get("LLM_PROVIDER") or "ollama").strip().lower()


def _is_local(base: str) -> bool:
    """A local OpenAI-compatible server (Ollama/LM Studio/vLLM) may need no API key."""
    b = base.lower()
    return any(h in b for h in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"))


def _openai_token_param(model: str) -> str:
    """Newer OpenAI models (GPT-5.x and o1/o3/o4 reasoning) reject `max_tokens` and require
    `max_completion_tokens`. DeepSeek / gpt-4o / most OpenRouter models accept `max_tokens`."""
    name = model.lower().rsplit("/", 1)[-1]  # strip an "openai/"-style gateway prefix
    if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return "max_completion_tokens"
    return "max_tokens"


# --- pure request builders ---------------------------------------------------


def build_ollama_request(system: str, user: str, model: str, host: str) -> tuple[str, dict, bytes]:
    url = host.rstrip("/") + "/api/chat"
    headers = {"Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
    }
    return url, headers, json.dumps(body).encode("utf-8")


def build_anthropic_request(
    system: str, user: str, model: str, api_key: str, max_tokens: int
) -> tuple[str, dict, bytes]:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    return url, headers, json.dumps(body).encode("utf-8")


def build_openai_request(
    system: str,
    user: str,
    model: str,
    base: str,
    api_key: str,
    max_tokens: int,
    *,
    referer: str | None = None,
    title: str | None = None,
) -> tuple[str, dict, bytes]:
    url = base.rstrip("/") + "/v1/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    # OpenRouter's optional attribution headers — only meaningful (and only sent) for
    # openrouter.ai bases; some free-tier models rank/gate on them.
    if "openrouter.ai" in base:
        if referer:
            headers["HTTP-Referer"] = referer
        if title:
            headers["X-Title"] = title
    body = {
        "model": model,
        _openai_token_param(model): max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
    }
    return url, headers, json.dumps(body).encode("utf-8")


# --- pure response parsers ---------------------------------------------------


def parse_ollama(payload: dict) -> str:
    return (payload.get("message") or {}).get("content", "").strip()


def parse_anthropic(payload: dict) -> str:
    for block in payload.get("content", []):
        if block.get("type") == "text":
            return block.get("text", "").strip()
    return ""


def parse_openai(payload: dict) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    # `content` can be "" or null on reasoning models (DeepSeek-R1 / GPT-5 reasoning); fall back
    # to the reasoning field so we never silently return empty when text actually exists.
    # `or ""` (not `.get(..., "")`) also guards content:null → avoids .strip() on None.
    text = msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""
    return text.strip()


# --- the one-shot call -------------------------------------------------------


def complete(
    system: str,
    user: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> str:
    """One-shot completion against the configured provider. Returns the text.

    `api_key` / `base_url`, when given, OVERRIDE the environment — this is what lets a
    fallback chain drive the same provider at different keys/bases (e.g. a free OpenRouter
    key for one hop and a paid one for the next) without mutating global env. When omitted
    the behaviour is unchanged: keys/bases come from the environment.

    Raises RuntimeError with a clear message on missing config / provider error.
    """
    prov = provider or _provider()
    if prov == "ollama":
        host = base_url or os.environ.get("OLLAMA_HOST", DEFAULTS["ollama"]["host"])
        mdl = model or os.environ.get("OLLAMA_MODEL", DEFAULTS["ollama"]["model"])
        url, headers, data = build_ollama_request(system, user, mdl, host)
        parse = parse_ollama
    elif prov == "anthropic":
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
        mdl = model or os.environ.get("ANTHROPIC_MODEL", DEFAULTS["anthropic"]["model"])
        url, headers, data = build_anthropic_request(system, user, mdl, key, max_tokens)
        parse = parse_anthropic
    elif prov in ("openai", "openrouter"):
        key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        # `openrouter` is an alias for the OpenAI path with the OpenRouter base as default.
        default_base = "https://openrouter.ai/api" if prov == "openrouter" else DEFAULTS["openai"]["base"]
        base = base_url or os.environ.get("OPENAI_BASE_URL", default_base)
        mdl = model or os.environ.get("OPENAI_MODEL", DEFAULTS["openai"]["model"])
        if not key and not _is_local(base):
            raise RuntimeError(
                f"LLM_PROVIDER={prov} but OPENAI_API_KEY is not set (base={base}). "
                "Local servers (localhost/127.0.0.1) may omit it."
            )
        url, headers, data = build_openai_request(
            system,
            user,
            mdl,
            base,
            key,
            max_tokens,
            referer=os.environ.get("OPENROUTER_REFERER"),
            title=os.environ.get("OPENROUTER_TITLE"),
        )
        parse = parse_openai
    else:
        raise RuntimeError(f"Unknown LLM_PROVIDER: {prov!r} (use ollama | anthropic | openai | openrouter)")

    req = urllib.request.Request(require_http(url), data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Providers put the actionable reason (e.g. "Use max_completion_tokens", "insufficient
        # credits", "model not found") in the RESPONSE BODY — surface it instead of a bare code.
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise RuntimeError(f"{prov} HTTP {e.code} ({url}): {body[:500] or e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"{prov} request failed ({url}): {e}") from e
    return parse(payload)


# --- fallback chain: keep going until something answers ----------------------
# The vendor-lock/quota insurance. A chain is an ordered list of provider hops;
# on ANY failure (rate-limit/quota 429, auth, 5xx, timeout, connection, or an
# empty reply) we try the next hop. This is exactly the "when a free tier hits
# its cap, switch to the next" behaviour — e.g. local Ollama → OpenRouter :free
# → OpenRouter paid (DeepSeek), so a run finishes even if every free lane is dry.


def parse_fallback_chain(spec: str) -> list[dict]:
    """Pure parser (no env, no network) for LLM_FALLBACK_CHAIN.

    Format: comma / newline / semicolon separated `provider[:model]` hops. The model
    keeps its own colons, so only the FIRST colon splits provider from model:

        "ollama:qwen2.5-coder:3b, openrouter:qwen/qwen3-coder:free, openrouter:deepseek/deepseek-chat"
        -> [{"provider":"ollama","model":"qwen2.5-coder:3b"},
            {"provider":"openrouter","model":"qwen/qwen3-coder:free"},
            {"provider":"openrouter","model":"deepseek/deepseek-chat"}]

    A hop with no model (e.g. "anthropic") yields model=None → provider defaults apply.
    """
    hops: list[dict] = []
    for raw in spec.replace("\n", ",").replace(";", ",").split(","):
        entry = raw.strip()
        if not entry:
            continue
        provider_part, _, model_part = entry.partition(":")
        provider = provider_part.strip().lower()
        model = model_part.strip() or None
        if provider:
            hops.append({"provider": provider, "model": model})
    return hops


def complete_with_fallback(
    system: str,
    user: str,
    *,
    chain: list[dict] | None = None,
    max_tokens: int = 2048,
    timeout: int = 120,
) -> tuple[str, dict]:
    """Try each hop in `chain` until one returns non-empty text.

    Each hop is a dict: {"provider", optional "model", "api_key", "base_url"}.
    When `chain` is None it is read from LLM_FALLBACK_CHAIN; optional per-hop key
    overrides come from LLM_FALLBACK_KEY_1, LLM_FALLBACK_KEY_2, … (1-indexed) — this
    is the (discouraged but supported) hook for rotating a second account's key.
    An empty/unset chain falls back to the single configured provider (back-compat).

    Returns (text, meta) where meta = {"provider","model","hop","attempts"}.
    Raises RuntimeError listing every hop's failure if none succeed.
    """
    if chain is None:
        chain = parse_fallback_chain(os.environ.get("LLM_FALLBACK_CHAIN", ""))
        for i, hop in enumerate(chain, start=1):
            override = os.environ.get(f"LLM_FALLBACK_KEY_{i}")
            if override:
                hop["api_key"] = override

    # No chain configured → preserve the original single-provider behaviour.
    if not chain:
        prov = _provider()
        text = complete(system, user, max_tokens=max_tokens, timeout=timeout)
        return text, {"provider": prov, "model": None, "hop": 1, "attempts": [{"provider": prov, "ok": True}]}

    attempts: list[dict] = []
    for i, hop in enumerate(chain, start=1):
        prov = (hop.get("provider") or "").strip().lower()
        mdl = hop.get("model")
        try:
            text = complete(
                system,
                user,
                provider=prov,
                model=mdl,
                api_key=hop.get("api_key"),
                base_url=hop.get("base_url"),
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except RuntimeError as e:
            attempts.append({"provider": prov, "model": mdl, "ok": False, "error": str(e)})
            continue
        if text and text.strip():
            attempts.append({"provider": prov, "model": mdl, "ok": True})
            return text, {"provider": prov, "model": mdl, "hop": i, "attempts": attempts}
        attempts.append({"provider": prov, "model": mdl, "ok": False, "error": "empty response"})

    detail = "\n  ".join(f"[{a['provider']}:{a.get('model')}] {a.get('error')}" for a in attempts)
    raise RuntimeError(f"all {len(chain)} fallback hop(s) failed:\n  {detail}")


# --- structured output: schema-constrained completion ------------------------
# The gap the 2026-08-25 prompt-chain assessment found: fit_note.py's LLM call
# returned unparsed free text, no schema, no validation. Provider-agnostic by
# construction (no per-provider response_format/tool-forcing branching) -- the
# schema is embedded in the prompt, and the reply is parsed + validated
# deterministically, with one corrective retry that shows the model its own
# error. Keeps this module's own stated bar: dependency-free (urllib only).


def validate_against_schema(data: object, schema: dict, path: str = "$") -> list[str]:
    """Minimal recursive JSON-Schema validator -- type/required/properties/items/enum
    only (no $ref, no allOf/anyOf, no format). Returns JSON-pointer-style error
    strings; empty = valid. Deliberately small: jobcore's actual schemas are flat,
    not deep or self-referential, so a full jsonschema dependency isn't warranted.
    """
    errors: list[str] = []
    expected_type = schema.get("type")
    type_map: dict[str, type | tuple[type, ...]] = {
        "object": dict,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
    }
    if expected_type in type_map:
        if expected_type == "integer" and isinstance(data, bool):
            errors.append(f"{path}: expected integer, got boolean")
            return errors
        if not isinstance(data, type_map[expected_type]):
            errors.append(f"{path}: expected {expected_type}, got {type(data).__name__}")
            return errors  # further checks assume the right container type

    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: {data!r} not in enum {schema['enum']}")

    if expected_type == "object" and isinstance(data, dict):
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{path}.{req}: required property missing")
        for key, subschema in (schema.get("properties") or {}).items():
            if key in data:
                errors.extend(validate_against_schema(data[key], subschema, f"{path}.{key}"))

    if expected_type == "array" and isinstance(data, list):
        item_schema = schema.get("items")
        if item_schema:
            for i, item in enumerate(data):
                errors.extend(validate_against_schema(item, item_schema, f"{path}[{i}]"))

    return errors


def _schema_prompt_suffix(schema: dict) -> str:
    return (
        "\n\nReply with ONLY a single JSON object matching this JSON Schema -- no "
        "prose, no markdown code fence, just the raw JSON:\n" + json.dumps(schema)
    )


def _parse_json_reply(text: str) -> tuple[dict | None, str | None]:
    """Best-effort JSON extraction -- strips a markdown code fence if the model added
    one despite being asked not to (common enough to guard for cheaply). Returns
    (parsed, error) -- exactly one is None.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned[cleaned.find("{") :] if "{" in cleaned else cleaned
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return None, f"not valid JSON: {e}"
    if not isinstance(parsed, dict):
        return None, f"expected a JSON object, got {type(parsed).__name__}"
    return parsed, None


def complete_structured(
    system: str,
    user: str,
    schema: dict,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 2048,
    timeout: int = 120,
    retries: int = 1,
) -> dict:
    """Like `complete`, but the reply is parsed as JSON and validated against
    `schema` before returning. Works identically across every provider (the schema
    rides in the prompt, not a provider-specific API field) -- a malformed or
    schema-invalid reply gets one corrective retry (`retries`, default 1) that shows
    the model its own error before giving up.

    Raises RuntimeError -- either a provider error (same as `complete`), or after
    retries are exhausted, naming exactly what was wrong with the last reply.
    """
    full_system = system + _schema_prompt_suffix(schema)
    attempt_user = user
    last_error = ""
    for attempt in range(retries + 1):
        text = complete(
            full_system,
            attempt_user,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        parsed, parse_error = _parse_json_reply(text)
        if parse_error:
            last_error = parse_error
        else:
            assert parsed is not None  # parse_error is None, so parsed must be set
            validation_errors = validate_against_schema(parsed, schema)
            if not validation_errors:
                return parsed
            last_error = "; ".join(validation_errors)
        if attempt < retries:
            attempt_user = (
                f"{user}\n\nYour previous reply was invalid: {last_error}\n"
                "Reply again with ONLY corrected JSON matching the schema."
            )
    raise RuntimeError(f"complete_structured: reply did not match schema after {retries + 1} attempt(s): {last_error}")


def complete_structured_with_fallback(
    system: str,
    user: str,
    schema: dict,
    *,
    chain: list[dict] | None = None,
    max_tokens: int = 2048,
    timeout: int = 120,
    retries: int = 1,
) -> tuple[dict, dict]:
    """`complete_structured`'s counterpart to `complete_with_fallback` -- same chain
    format/env vars (`LLM_FALLBACK_CHAIN`, `LLM_FALLBACK_KEY_n`), same "no chain
    configured -> single provider" back-compat behaviour, but each hop's reply must
    parse and validate against `schema` (with its own `retries` corrective attempts)
    before the hop counts as having answered -- a hop that answers with malformed
    JSON is treated the same as a hop that errored, and the chain moves on.

    Returns (parsed, meta) where meta mirrors `complete_with_fallback`'s shape.
    Raises RuntimeError listing every hop's failure if none succeed.
    """
    if chain is None:
        chain = parse_fallback_chain(os.environ.get("LLM_FALLBACK_CHAIN", ""))
        for i, hop in enumerate(chain, start=1):
            override = os.environ.get(f"LLM_FALLBACK_KEY_{i}")
            if override:
                hop["api_key"] = override

    if not chain:
        prov = _provider()
        parsed = complete_structured(system, user, schema, max_tokens=max_tokens, timeout=timeout, retries=retries)
        return parsed, {"provider": prov, "model": None, "hop": 1, "attempts": [{"provider": prov, "ok": True}]}

    attempts: list[dict] = []
    for i, hop in enumerate(chain, start=1):
        prov = (hop.get("provider") or "").strip().lower()
        mdl = hop.get("model")
        try:
            parsed = complete_structured(
                system,
                user,
                schema,
                provider=prov,
                model=mdl,
                api_key=hop.get("api_key"),
                base_url=hop.get("base_url"),
                max_tokens=max_tokens,
                timeout=timeout,
                retries=retries,
            )
        except RuntimeError as e:
            attempts.append({"provider": prov, "model": mdl, "ok": False, "error": str(e)})
            continue
        attempts.append({"provider": prov, "model": mdl, "ok": True})
        return parsed, {"provider": prov, "model": mdl, "hop": i, "attempts": attempts}

    detail = "\n  ".join(f"[{a['provider']}:{a.get('model')}] {a.get('error')}" for a in attempts)
    raise RuntimeError(f"all {len(chain)} fallback hop(s) failed:\n  {detail}")


if __name__ == "__main__":
    import sys

    from .console import force_utf8_output

    # A model reply can contain any script; printing it must not raise on a cp1252 console.
    force_utf8_output()

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Reply with exactly: OK"
    prov = _provider()
    print(f"[llm] provider={prov}", file=sys.stderr)
    try:
        print(complete("You are a terse assistant.", prompt))
    except RuntimeError as e:
        print(f"[llm] {e}", file=sys.stderr)
        sys.exit(1)
