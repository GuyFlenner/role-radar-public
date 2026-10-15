"""Provider smoke test — prove the cheap/free tier works before you rely on it.

Pings EVERY hop in your fallback chain independently (not stopping at the first
success) so you can see which lanes are actually alive — local Ollama, an
OpenRouter `:free` model, a paid DeepSeek overflow, etc. This is the "verify on
the Mac before a run" step from architecture-workflows/PORTABILITY_AND_VENDOR_INDEPENDENCE.md.

Usage:
    # Probe the chain from the environment (LLM_FALLBACK_CHAIN), or the single
    # configured LLM_PROVIDER if no chain is set:
    uv run python -m src.jobcore.smoke

    # Probe an explicit chain (overrides the env), same syntax as LLM_FALLBACK_CHAIN:
    uv run python -m src.jobcore.smoke "ollama:qwen2.5-coder:3b, openrouter:qwen/qwen3-coder:free"

Exit code: 0 if AT LEAST ONE hop answers (the chain can complete a run), else 1.
Keys/bases come from the same env vars as the adapter (OPENAI_API_KEY, OLLAMA_HOST, …)
plus optional per-hop LLM_FALLBACK_KEY_1/2/… overrides.
"""

import os
import sys
import time

from .console import force_utf8_output
from .llm import complete, parse_fallback_chain

_PING_SYSTEM = "You are a terse assistant. Reply with exactly: OK"
_PING_USER = "Reply with exactly: OK"


def _chain_from_args_or_env(argv: list[str]) -> list[dict]:
    """Explicit CLI spec wins; else LLM_FALLBACK_CHAIN; else the single LLM_PROVIDER."""
    spec = argv[1].strip() if len(argv) > 1 else os.environ.get("LLM_FALLBACK_CHAIN", "").strip()
    if spec:
        chain = parse_fallback_chain(spec)
    else:
        chain = [{"provider": (os.environ.get("LLM_PROVIDER") or "ollama").strip().lower(), "model": None}]
    # Overlay optional per-hop key overrides (1-indexed), same rule as complete_with_fallback.
    for i, hop in enumerate(chain, start=1):
        override = os.environ.get(f"LLM_FALLBACK_KEY_{i}")
        if override:
            hop["api_key"] = override
    return chain


def probe_hop(hop: dict, *, timeout: int = 30) -> dict:
    """Ping one hop. Returns {provider, model, ok, ms, reply|error} — never raises."""
    prov = (hop.get("provider") or "").strip().lower()
    mdl = hop.get("model")
    start = time.monotonic()
    try:
        reply = complete(
            _PING_SYSTEM,
            _PING_USER,
            provider=prov,
            model=mdl,
            api_key=hop.get("api_key"),
            base_url=hop.get("base_url"),
            max_tokens=16,
            timeout=timeout,
        )
        ms = int((time.monotonic() - start) * 1000)
        if reply and reply.strip():
            return {"provider": prov, "model": mdl, "ok": True, "ms": ms, "reply": reply.strip()[:40]}
        return {"provider": prov, "model": mdl, "ok": False, "ms": ms, "error": "empty response"}
    except RuntimeError as e:
        ms = int((time.monotonic() - start) * 1000)
        return {"provider": prov, "model": mdl, "ok": False, "ms": ms, "error": str(e)[:160]}


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    argv = argv if argv is not None else sys.argv
    chain = _chain_from_args_or_env(argv)
    print(f"[smoke] probing {len(chain)} hop(s)\n", file=sys.stderr)

    any_ok = False
    for i, hop in enumerate(chain, start=1):
        label = f"{hop['provider']}:{hop['model']}" if hop.get("model") else hop["provider"]
        res = probe_hop(hop)
        if res["ok"]:
            any_ok = True
            print(f"  [{i}] OK    {label:<45} {res['ms']:>6}ms  -> {res['reply']!r}")
        else:
            print(f"  [{i}] FAIL  {label:<45} {res['ms']:>6}ms  -- {res['error']}")

    print("", file=sys.stderr)
    if any_ok:
        print("[smoke] at least one hop answered - the chain can complete a run.", file=sys.stderr)
        return 0
    print("[smoke] NO hop answered — check keys/models/connectivity above.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
