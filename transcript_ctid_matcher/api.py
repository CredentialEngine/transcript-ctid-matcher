"""Library entry point for embedding in a transcript pipeline.

Intended flow (e.g. AACRAO's transcript parser):

    parse transcript -> CLR 2.0 JSON (unsigned)
                     -> match_clr(clr, ...)      # adds CTIDs in place
                     -> sign as CLR 2.0

    from transcript_ctid_matcher import match_clr

    outcome = match_clr(clr, api_key=os.environ["CE_REGISTRY_API_KEY"])
    # clr now holds the CTIDs; signing it is the calling pipeline's job.
    # outcome.report has confidence and misses, for optional review.

The CLR must be unsigned: CTIDs added after signing would invalidate the
proofs, so signed input is rejected with SignedInputError.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config
from .clr import annotate, match_report, prior_education_orgs, read_transcript, walk
from .match import match_organization, match_transcript
from .models import MatchResult
from .registry import RegistryClient

log = logging.getLogger("transcript_ctid_matcher")

CONFIDENCE_LEVELS = ("high", "medium")


class SignedInputError(ValueError):
    """The CLR already carries proofs; matching must happen before signing."""


@dataclass
class MatchOutcome:
    summary: dict          # counts: courses, programs/degrees, competencies
    report: dict           # every match and miss with confidence and score
    results: dict = field(default_factory=dict)   # raw MatchResults, by kind


def count_proofs(clr: dict) -> int:
    return sum(1 for obj in walk(clr) if "proof" in obj)


def _apply_threshold(results: list[MatchResult], min_confidence: str) -> None:
    allowed = CONFIDENCE_LEVELS[: CONFIDENCE_LEVELS.index(min_confidence) + 1]
    for r in results:
        if r.matched and r.confidence not in allowed:
            r.matched = False


def match_clr(clr: dict, *, client: Optional[RegistryClient] = None,
              api_key: Optional[str] = None, environment: str = "production",
              cache_dir: Optional[Path] = None, min_confidence: str = "medium",
              strip_proofs: bool = False) -> MatchOutcome:
    """Match an unsigned CLR 2.0 document against the Credential Registry and
    write the CTIDs into it **in place**. Pass a copy to keep the original.

    client: a RegistryClient (or test double); otherwise one is built from
        api_key (default: the CE_REGISTRY_API_KEY environment variable).
    min_confidence: "medium" (default) writes high and medium matches;
        "high" writes only high-confidence matches.
    strip_proofs: remove existing proofs instead of raising SignedInputError.
        Only for working with already-signed sample files; the result must
        be re-signed.

    Raises ValueError if the document isn't a CLR 2.0 ClrCredential, and
    SignedInputError if it's signed and strip_proofs is False.
    """
    if min_confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"min_confidence must be one of {CONFIDENCE_LEVELS}")
    proofs = count_proofs(clr)
    if proofs and not strip_proofs:
        raise SignedInputError(
            f"The CLR is already signed ({proofs} proof(s)). Match CTIDs before "
            "signing; adding them afterwards would invalidate the signatures.")
    data = read_transcript(clr)

    if client is None:
        key = api_key or config.get_ce_key()
        if not key:
            raise ValueError(f"No Registry API key: pass api_key or set {config.CE_API_KEY_VAR}.")
        client = RegistryClient(key, environment=environment,
                                cache_dir=cache_dir or config.default_cache_dir())

    log.info("Issuer: %s; %d course(s), %d program/degree(s), transfer: %s",
             data.issuing_institution, len(data.courses), len(data.degrees),
             ", ".join(data.transfer_institutions) or "none")
    orgs, courses, creds, _skills, errors = match_transcript(client, data)
    prior = {aid: match_organization(client, name)
             for aid, name in prior_education_orgs(clr)}
    _apply_threshold(orgs + courses + creds + list(prior.values()), min_confidence)

    if proofs:
        for obj in list(walk(clr)):
            obj.pop("proof", None)
    summary = annotate(clr, data, orgs, courses, creds, prior)
    summary["proofsRemoved"] = proofs
    if errors:
        summary["errors"] = errors
    report = {
        "matchedAt": datetime.now(timezone.utc).replace(microsecond=0)
                             .isoformat().replace("+00:00", "Z"),
        "registryEnvironment": environment,
        "minConfidence": min_confidence,
        **match_report(data, orgs, courses, creds, prior),
        "summary": summary,
    }
    results = {"organizations": orgs + list(prior.values()),
               "courses": courses, "programs/degrees": creds}
    return MatchOutcome(summary=summary, report=report, results=results)
