"""ATS provider layer — public, no-auth career-board feeds.

Each provider turns one company's public ATS board into the same raw job-dict
shape ``csv_ingest._extract_job`` produces, so the shared
``csv_ingest.filter_score_positions`` core can score/dedup/rank them identically
to the Referally CSV path. Providers are pure-parse + injectable-fetch so tests
run fully offline.
"""

from . import comeet, greenhouse, linkedin

# ats name (as written in ats_targets.json) → provider fetch(target, http_get_*)
REGISTRY = {
    "greenhouse": greenhouse.fetch,
    "comeet": comeet.fetch,
    "linkedin": linkedin.fetch,
}

# Providers whose board is HTML (scraped) rather than a JSON API — they receive
# ``http_get_text`` instead of ``http_get_json`` from the ingest layer.
TEXT_PROVIDERS = frozenset({"linkedin"})

__all__ = ["REGISTRY", "TEXT_PROVIDERS", "greenhouse", "comeet", "linkedin"]
