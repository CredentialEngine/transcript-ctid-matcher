"""Command-line entry point.

Usage:
    transcript-ctid-matcher transcript.json [-o OUT] [options]
    parser ... | transcript-ctid-matcher - -o - | signer ...

The input CLR must be unsigned (matching happens before signing); use
--strip-proofs only to experiment with already-signed sample files.
The run summary goes to stderr, so stdout can carry the CLR.
"""

import argparse
import json
import logging
import shutil
import sys
from collections import Counter
from pathlib import Path

from . import config
from .api import CONFIDENCE_LEVELS, MatchOutcome, SignedInputError, match_clr
from .clr import is_match
from .registry import RegistryClient

log = logging.getLogger("transcript_ctid_matcher")


def default_output(path: Path) -> Path:
    name = path.name
    for suffix in (".credential.json", ".json"):
        if name.endswith(suffix):
            return path.with_name(name[: -len(suffix)] + ".matched.json")
    return path.with_name(name + ".matched.json")


def print_summary(outcome: MatchOutcome, out: str, verbose: bool) -> None:
    summary, results = outcome.summary, outcome.results
    err = sys.stderr
    print(f"\nWrote {out}", file=err)
    for org in results["organizations"]:
        if is_match(org):
            print(f"  org  {org.query_text!r}: matched ({org.confidence}, {org.score})"
                  f" -> {org.best.name!r} {org.best.ctid}", file=err)
        else:
            print(f"  org  {org.query_text!r}: not matched", file=err)
    for label in ("courses", "programs/degrees"):
        rs = results[label]
        c = Counter(r.confidence for r in rs if not is_match(r))
        misses = ", ".join(f"{k} {v}" for k, v in sorted(c.items()))
        print(f"  {label}: {sum(is_match(r) for r in rs)}/{len(rs)} matched"
              + (f" (misses by confidence: {misses})" if c else ""), file=err)
        if verbose:
            for r in rs:
                if not is_match(r):
                    print(f"      miss: {r.query_text!r} ({r.confidence}, {r.score})", file=err)
    print(f"  competencies linked: {summary['competencies']}", file=err)
    if summary.get("proofsRemoved"):
        print(f"  proofs removed: {summary['proofsRemoved']} (re-sign the output)", file=err)
    for e in summary.get("errors", []):
        print(f"  error: {e}", file=err)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="transcript-ctid-matcher",
        description="Match an unsigned CLR 2.0 transcript's institutions, courses, programs "
                    "and competencies against the Credential Registry, and write the CTIDs "
                    "into the JSON before it is signed.")
    parser.add_argument("input", help="CLR 2.0 transcript JSON file, or - for stdin")
    parser.add_argument("-o", "--output",
                        help="Output file, or - for stdout (default: <input>.matched.json "
                             "next to the input; stdout when reading stdin)")
    parser.add_argument("--in-place", action="store_true",
                        help="Overwrite the input file instead of writing a new one")
    parser.add_argument("--report", metavar="FILE",
                        help="Also write a JSON match report (confidence, scores, misses) "
                             "to FILE; this information is never written into the CLR")
    parser.add_argument("--min-confidence", choices=CONFIDENCE_LEVELS, default="medium",
                        help="Lowest confidence written into the CLR (default: %(default)s)")
    parser.add_argument("--strip-proofs", action="store_true",
                        help="Accept already-signed input by removing its proofs "
                             "(the output must then be re-signed)")
    parser.add_argument("--env", choices=["production", "sandbox"], default="production",
                        help="Credential Registry environment (default: production)")
    parser.add_argument("--cache-dir", default=str(config.default_cache_dir()),
                        help="Registry response cache (default: %(default)s)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Clear the registry response cache before running")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    from_stdin = args.input == "-"
    src = None if from_stdin else Path(args.input)
    if src is not None and not src.is_file():
        parser.error(f"File not found: {src}")
    if args.in_place and (args.output or from_stdin):
        parser.error("--in-place needs a file input and no --output")
    if args.in_place:
        out = str(src)
    elif args.output:
        out = args.output
    else:
        out = "-" if from_stdin else str(default_output(src))

    ce_key = config.get_ce_key()
    if not ce_key:
        print(f"ERROR: set {config.CE_API_KEY_VAR} to run registry matching.", file=sys.stderr)
        return 2

    try:
        text = sys.stdin.read() if from_stdin else src.read_text(encoding="utf-8")
        clr = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"ERROR: input is not valid JSON: {exc}", file=sys.stderr)
        return 2

    cache_dir = Path(args.cache_dir)
    if args.no_cache and cache_dir.exists():
        shutil.rmtree(cache_dir)
    client = RegistryClient(ce_key, environment=args.env, cache_dir=cache_dir)

    try:
        outcome = match_clr(clr, client=client, environment=args.env,
                            min_confidence=args.min_confidence,
                            strip_proofs=args.strip_proofs)
    except SignedInputError as exc:
        print(f"ERROR: {exc} (use --strip-proofs to experiment with signed samples)",
              file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    body = json.dumps(clr, indent=2, ensure_ascii=False) + "\n"
    if out == "-":
        sys.stdout.write(body)
        sys.stdout.flush()
    else:
        out_path = Path(out)
        tmp = out_path.with_name(out_path.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        tmp.replace(out_path)
    if args.report:
        outcome.report["input"] = "<stdin>" if from_stdin else str(src)
        outcome.report["output"] = "<stdout>" if out == "-" else out
        Path(args.report).write_text(
            json.dumps(outcome.report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(outcome, "<stdout>" if out == "-" else out, args.verbose)
    if args.report:
        print(f"  match report: {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
