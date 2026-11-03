"""Quick registry query probe for debugging matching.

Usage (needs CE_REGISTRY_API_KEY set):
    python -m transcript_ctid_matcher.probe "Las Positas College"
    python -m transcript_ctid_matcher.probe "Las Positas College" --env sandbox
"""

import argparse
import sys

from . import config
from .match import match_organization, org_search_attempts
from .registry import RegistryClient, langstring


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe registry organization search.")
    parser.add_argument("name", help="Organization name to search for")
    parser.add_argument("--env", choices=["production", "sandbox"], default="production")
    args = parser.parse_args()

    key = config.get_ce_key()
    if not key:
        print(f"ERROR: set {config.CE_API_KEY_VAR} first.", file=sys.stderr)
        return 2

    client = RegistryClient(key, environment=args.env)
    print(f"Searching {args.env} registry for: {args.name!r}\n")
    print("Search attempts (in order):")
    for term, mtype in org_search_attempts(args.name):
        print(f"  {mtype or 'full-text':22} {term!r}")
        try:
            records = client.org_search(term, mtype, take=10)
        except Exception as exc:
            print(f"      ERROR: {exc}")
            continue
        for rec in records[:5]:
            print(f"      - {langstring(rec.get('ceterms:name'))!r}"
                  f"  {rec.get('ceterms:ctid')}")
        if len(records) > 5:
            print(f"      ... {len(records) - 5} more")

    result = match_organization(client, args.name)
    print(f"\nBest match: {result.best.name if result.best else None}"
          f"  confidence={result.confidence} score={result.score}")
    if result.best:
        print(f"  {result.best.uri}")
    for ru in result.runners_up:
        print(f"  runner-up: {ru.name!r} score={ru.score}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
