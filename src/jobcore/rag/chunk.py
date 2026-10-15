"""Deterministic markdown chunking.

Determinism is the load-bearing property: ``chunk_id`` is the upsert key, so the
same input must always produce the same ids. If chunking were nondeterministic,
re-ingesting an unchanged document would write a fresh set of ids alongside the
old ones and the store would silently accumulate duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit. ``chunk_id`` is stable across runs by construction."""

    chunk_id: str
    source: str
    ordinal: int
    text: str


def chunk_markdown(text: str, *, source: str) -> list[Chunk]:
    """Split ``text`` on blank-line boundaries into ordered chunks.

    Deliberately simple and dependency-free. A smarter structure-aware splitter
    (heading-anchored, code-fence aware) is a later refinement; it must preserve
    the determinism contract above.
    """
    blocks = [block.strip() for block in text.split("\n\n")]
    return [
        Chunk(chunk_id=f"{source}:{ordinal}", source=source, ordinal=ordinal, text=block)
        for ordinal, block in enumerate(b for b in blocks if b)
    ]
