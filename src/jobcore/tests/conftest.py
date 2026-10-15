"""Shared test setup — profile wiring for the whole suite.

Scoring and the honesty gates are now driven by a candidate profile instead of module-level
constants. Tests must therefore state which candidate they are testing, or they would run against
whatever `PROFILE_DIR` happens to point at on the developer's machine — which is exactly the
cross-contamination this platform exists to prevent.

Two things happen here:

1. **`PROFILE_DIR` is neutralised for the whole session.** No test may read a real person's
   profile, even accidentally.
2. **`backend_lead_tlv` is the implicit default.** That fixture reproduces the constants this
   engine used to hardcode, so the pre-existing scoring and gate tests keep asserting the same
   numbers they always did. That is the behaviour-preservation proof: if those tests had needed
   editing, the migration would have silently changed someone's results.

New tests that care about generality should take the ``frontend_profile`` fixture (or the
``every_profile`` parametrisation) rather than relying on the default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.jobcore import profile as profile_mod
from src.jobcore import scoring

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "profiles" / "_fixtures"

#: The two synthetic candidates the suite is proven against. They differ on every axis that used
#: to be hardcoded: seniority band, country, stack, currency, and geo strictness.
FIXTURE_FILES = {
    "backend_lead_tlv": FIXTURE_DIR / "backend_lead_tlv.json",
    "frontend_ic_berlin": FIXTURE_DIR / "frontend_ic_berlin.json",
}


@pytest.fixture(autouse=True, scope="session")
def _isolate_profile_env(tmp_path_factory):
    """Never let a developer's real profile leak into a test run.

    Popping ``PROFILE_DIR`` is NOT enough and was the original bug here: :func:`profile.load`
    calls ``load_dotenv()``, which reads ``PROFILE_DIR`` straight back out of the repo's ``.env``.
    Once a real profile existed on disk, two "empty profile" tests silently began asserting
    against a real person's data. ``load_dotenv(override=False)`` will not clobber a variable
    that is already set, so we *set* it to an empty directory instead of unsetting it.
    """
    import os

    saved = os.environ.get("PROFILE_DIR")
    os.environ["PROFILE_DIR"] = str(tmp_path_factory.mktemp("no-profile"))
    yield
    if saved is None:
        os.environ.pop("PROFILE_DIR", None)
    else:
        os.environ["PROFILE_DIR"] = saved


@pytest.fixture
def empty_profile():
    """A profile with nothing configured — the fresh-clone state."""
    return profile_mod.load(Path("definitely-not-a-profile.json"))


@pytest.fixture(autouse=True)
def _default_profile():
    """Default every test to the backend-lead fixture, and reset the global after each test."""
    scoring.set_default_profile(profile_mod.load(FIXTURE_FILES["backend_lead_tlv"]))
    yield
    scoring.set_default_profile(None)


@pytest.fixture
def backend_profile():
    return profile_mod.load(FIXTURE_FILES["backend_lead_tlv"])


@pytest.fixture
def frontend_profile():
    return profile_mod.load(FIXTURE_FILES["frontend_ic_berlin"])


@pytest.fixture(params=sorted(FIXTURE_FILES))
def every_profile(request):
    """Parametrise a test across both candidates.

    This is the mechanism that makes a shared platform safe for two people: a change that helps
    one profile cannot quietly wreck the other without turning this suite red.
    """
    return profile_mod.load(FIXTURE_FILES[request.param])
