"""
Provider pool: several free-tier LLMs, rate limited, with automatic failover.

WHY THIS EXISTS
---------------
Free tiers are capped on two independent axes and they fail differently:

    RPM  requests per minute -- recoverable, just wait a few seconds
    RPD  requests per day    -- NOT recoverable, this provider is done until
                               tomorrow no matter how long you wait

Gemini's free tier for gemini-3.6-flash is 5 RPM and 20 RPD. A 50-resume batch
cannot finish on it at any speed, which is not a bug you can tune your way out
of. The fix is more than one provider.

HOW IT WORKS
------------
1. RATE LIMIT BEFORE SENDING. Each provider enforces a minimum gap between
   calls, so we stay under RPM instead of discovering it through 429s. Asking
   politely is faster than being refused and retrying.

2. RETRY ON RPM, GIVE UP ON RPD. The 429 body says which quota was hit. An RPM
   breach sleeps for the retryDelay the API itself supplies and tries again.
   An RPD breach marks the provider exhausted for the rest of the run --
   retrying a daily cap just burns time to get refused again.

3. FAIL OVER TO THE NEXT PROVIDER. When one is exhausted, the pool moves to the
   next configured provider and carries on. With Gemini plus Groq you have
   roughly 20 + several hundred requests a day, which is plenty for 50 resumes.

4. THE CACHE MAKES RUNS RESUMABLE. Anything already extracted is served from
   disk and costs no quota, so a partial run followed by another run finishes
   the job rather than starting over.

WHY THE PROVIDER IS AN ORDERED LIST
-----------------------------------
Different models extract slightly differently, so the order matters for
consistency: the best model goes first and the others exist to finish the batch
when it runs dry. Record which model produced each record -- CandidateRecord
already stores it -- so a strange result can be traced to the model that
produced it rather than blamed on the prompt.
"""

from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Provider:
    """One free-tier endpoint, with the limits it advertises."""

    name: str
    kind: str                 # "gemini" | "groq" | "openai" | "anthropic"
    model: str
    api_key_env: str
    base_url: Optional[str] = None
    rpm: int = 5              # requests per minute this tier allows
    rpd: int = 20             # requests per day

    # runtime state
    _client: object = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_call: float = field(default=0.0, repr=False)
    _used_today: int = field(default=0, repr=False)
    exhausted: bool = field(default=False, repr=False)

    @property
    def available(self) -> bool:
        return bool(os.getenv(self.api_key_env)) and not self.exhausted

    @property
    def min_interval(self) -> float:
        # 60/rpm is the theoretical floor; the extra 15% absorbs clock skew
        # between us and the provider, which is what turns a "just under the
        # limit" run into a stream of 429s.
        return (60.0 / self.rpm) * 1.15

    def build_client(self):
        import instructor

        if self._client is not None:
            return self._client

        if self.kind == "anthropic":
            from anthropic import Anthropic
            self._client = instructor.from_anthropic(
                Anthropic(api_key=os.environ[self.api_key_env])
            )
        else:
            # Gemini, Groq and OpenAI all speak the OpenAI protocol, so one
            # branch covers three providers. Only the base_url differs.
            from openai import OpenAI
            self._client = instructor.from_openai(
                OpenAI(
                    api_key=os.environ[self.api_key_env],
                    base_url=self.base_url,
                ),
                mode=instructor.Mode.JSON,
            )
        return self._client

    def wait_turn(self) -> None:
        """Blocks until this provider is allowed another request.

        Holding the lock across the sleep serialises callers, which is the
        point: with several worker threads sharing one provider, they must
        queue rather than all fire at once.
        """
        with self._lock:
            gap = time.monotonic() - self._last_call
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last_call = time.monotonic()
            self._used_today += 1


# ============================================================
# THE POOL
# ============================================================
# Ordered best-first. Add or remove entries here; nothing else needs to change.
#
# Model names move. Gemini renamed twice in one afternoon during development,
# and each time the API error named the replacement. If you get a 404, read the
# message and edit the string here.

PROVIDERS: list[Provider] = [
    Provider(
        name="gemini",
        kind="gemini",
        model="gemini-3.6-flash",
        api_key_env="GOOGLE_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        rpm=5, rpd=20,
    ),
    # A second Google project has its own independent quota. Optional -- if
    # GOOGLE_API_KEY_2 is unset this entry is simply skipped.
    Provider(
        name="gemini-2",
        kind="gemini",
        model="gemini-3.6-flash",
        api_key_env="GOOGLE_API_KEY_2",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        rpm=5, rpd=20,
    ),
      Provider(
        name="groq-120b",
        kind="groq",
        model="openai/gpt-oss-120b",
        api_key_env="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        rpm=25, rpd=900,
    ),
    # Same key, smaller model. Groq's daily caps are per-model, so listing a
    # second model on the same account is extra headroom for free.
    Provider(
        name="groq-20b",
        kind="groq",
        model="openai/gpt-oss-20b",
        api_key_env="GROQ_API_KEY",
        base_url="https://api.groq.com/openai/v1",
        rpm=25, rpd=900,
    ),
    Provider(
        name="openai",
        kind="openai",
        model="gpt-4o-mini",
        api_key_env="OPENAI_API_KEY",
        rpm=50, rpd=10_000,
    ),
    Provider(
        name="anthropic",
        kind="anthropic",
        model="claude-haiku-4-5-20251001",
        api_key_env="ANTHROPIC_API_KEY",
        rpm=50, rpd=10_000,
    ),
]


def active_providers() -> list[Provider]:
    return [p for p in PROVIDERS if p.available]


# ============================================================
# ERROR CLASSIFICATION
# ============================================================
# The whole failover strategy depends on telling these two apart, and the only
# place that information exists is the error text.

def is_rate_limit(exc: Exception) -> bool:
    return "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)


def is_daily_limit(exc: Exception) -> bool:
    """True when the quota that was hit is the per-DAY one.

    Google names the quota in the error body: 'GenerateRequestsPerDay...' vs
    'GenerateRequestsPerMinute...'. Waiting out a daily cap is pointless, so
    this distinction decides between retry and failover.
    """
    text = str(exc)
    return "PerDay" in text or "per day" in text.lower()


def retry_delay_from(exc: Exception, default: float = 20.0) -> float:
    """Providers tell you exactly how long to wait. Use their number.

    Guessing a backoff means either waiting too long or getting refused again.
    """
    match = re.search(r"[Rr]etry in ([\d.]+)s", str(exc))
    if match:
        return min(float(match.group(1)) + 1.0, 65.0)
    return default


# ============================================================
# CALL WITH FAILOVER
# ============================================================

def call_with_failover(
    make_request,
    max_attempts_per_provider: int = 3,
    verbose: bool = True,
):
    """Runs make_request(client, provider) against providers until one works.

    make_request is a callable so this module knows nothing about resumes or
    schemas -- it handles quota and failover only. That separation is why the
    same pool can later serve JD parsing and ranking explanations.
    """
    providers = active_providers()
    if not providers:
        raise RuntimeError(
            "No usable providers. Set at least one of: "
            + ", ".join(p.api_key_env for p in PROVIDERS)
        )

    last_error: Optional[Exception] = None

    for provider in providers:
        for attempt in range(max_attempts_per_provider):
            provider.wait_turn()
            try:
                return make_request(provider.build_client(), provider)
            except Exception as exc:
                last_error = exc

                if not is_rate_limit(exc):
                    # Not a quota problem -- a bad model name, a malformed
                    # request, a network failure. Another provider will not fix
                    # it, and retrying hides the real cause.
                    raise

                if is_daily_limit(exc):
                    provider.exhausted = True
                    if verbose:
                        print(f"    {provider.name}: daily quota gone, "
                              f"switching provider")
                    break

                delay = retry_delay_from(exc)
                if verbose:
                    print(f"    {provider.name}: rate limited, "
                          f"waiting {delay:.0f}s "
                          f"(attempt {attempt + 1}/{max_attempts_per_provider})")
                time.sleep(delay)

    raise RuntimeError(f"all providers exhausted; last error: {last_error}")


def describe_pool() -> str:
    lines = []
    for p in PROVIDERS:
        if os.getenv(p.api_key_env):
            state = "exhausted" if p.exhausted else f"{p.rpm}/min, {p.rpd}/day"
            lines.append(f"  {p.name:10} {p.model:28} {state}")
        else:
            lines.append(f"  {p.name:10} {'(no ' + p.api_key_env + ')':28} skipped")
    return "\n".join(lines)