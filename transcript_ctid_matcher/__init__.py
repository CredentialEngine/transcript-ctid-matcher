"""Match a CLR 2.0 transcript's institutions, courses, programs and
competencies to the Credential Registry and write the CTIDs back in."""

__version__ = "0.1.0"

from .api import MatchOutcome, SignedInputError, match_clr  # noqa: E402

__all__ = ["match_clr", "MatchOutcome", "SignedInputError"]
