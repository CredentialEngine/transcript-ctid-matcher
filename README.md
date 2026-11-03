# Transcript CTID Matcher

Reads a single **1EdTech CLR 2.0** transcript (JSON) and matches what it
contains to records in the **Credential Registry**:

- the **issuing institution**, transfer institutions, and prior-education
  institutions (organizations),
- **programs and degrees** (credentials and learning programs),
- **courses**, and
- the **competencies** each matched course or program teaches or assesses.

It runs **after parsing and before signing**: it takes an unsigned CLR
(for example, the output of AACRAO's transcript parser), writes the CTIDs
into it beside the entities they identify, and the result is then signed as
a CLR 2.0 credential. It uses only properties CLR 2.0 / Open Badges 3.0
define. Courses,
programs and degrees get `alignment` entries to their Registry records,
following Credential Engine's alignment guidance. Competencies are
`ceasn:Competency` alignments. Organizations get an `otherIdentifier` CTID on
their Profile. See [docs/output-format.md](docs/output-format.md).

This is the Registry-matching core of
[CredentialEngine/transcript-parser](https://github.com/CredentialEngine/transcript-parser),
without the AI PDF extraction and HTML reporting: transcripts arrive already
structured as CLR, and the output is the annotated CLR itself.

## Before you start: institutions must publish to the Registry

Matching can only find what institutions have published. For a transcript
to get CTIDs, the issuing institution must first:

1. **Set up a Credential Engine Registry account** for the organization
   ([account setup guidance](https://guidance.credentialengine.org/account-setup-and-access/)).
2. **Publish its organization profile and catalog** to the Registry:
   credentials and programs, courses, and ideally the competencies each one
   teaches or assesses. Any publishing method works, including Credential
   Engine's [CTDL xTRA tool](https://credentialengine.org/toolkit/credential-engine-ctdl-xtra-tool/).

This is a one-time onboarding step, followed by keeping the catalog current.
It isn't part of the per-transcript flow. The same applies to transfer and
prior-education institutions: their courses and programs match only if they
have published too. If an institution hasn't published, its transcript is
written back with no CTIDs. That is not an error, and the match report shows
each miss.

This is separate from the **Credential Registry Search API key**
(`CE_REGISTRY_API_KEY`), which whoever runs this matcher needs.

## Pipeline integration

See [docs/architecture.md](docs/architecture.md) for sequence diagrams of
where this sits in the Infuse flow and of the Registry calls it makes.

```text
transcript --> parser --> unsigned CLR 2.0 --> match_clr() --> sign --> CLR 2.0
                                                    |
                                                    +--> match report (optional review)
```

**From Python**, call the library directly. It annotates the dict in place:

```python
import json
from transcript_ctid_matcher import match_clr

with open("transcript.json") as f:        # unsigned CLR 2.0 from the parser
    clr = json.load(f)

outcome = match_clr(clr, api_key="<Credential Registry Search API key>")

# clr now holds the CTIDs; hand it to the next step in your pipeline.
print(outcome.summary)   # counts of matched courses, programs, competencies
print(outcome.report)    # every match and miss with confidence and score
```

Signing the result, and deciding what to do with the match report, are the
calling pipeline's job; this package does neither.

Options: `environment="sandbox"`, `cache_dir=...`, and
`min_confidence="high"` to write only high-confidence matches. It raises
`SignedInputError` if the CLR already has proofs, since adding CTIDs after
signing would invalidate the signatures. It raises `ValueError` if the
input isn't a `ClrCredential`.

**From another language**, use the CLI as a pipe step. The annotated CLR goes
to stdout, and the run summary to stderr:

```bash
parser ... | transcript-ctid-matcher - --report match-report.json | next-step ...
```

Input and output are always UTF-8 on every platform, and a leading
byte-order mark is accepted. It exits non-zero, writing nothing to stdout,
if the input is signed, isn't UTF-8 JSON or a CLR, or the API key is
missing.

On Windows, pipe with **PowerShell 7+** or `cmd.exe`. Windows PowerShell 5.1
re-encodes text piped between programs and can corrupt non-ASCII
characters, so there, pass files instead:
`transcript-ctid-matcher in.json -o out.json`.

### Choosing a confidence threshold

Whatever this step writes is signed into an official record. By default,
**high** and **medium** confidence matches are written (see
[docs/confidence.md](docs/confidence.md)). The thresholds are expert-tuned,
not yet statistically calibrated, so consider `min_confidence="high"` /
`--min-confidence high` until they are. Another option is to route the
`--report` output to a registrar for review before signing.

## Quick start

Requires Python 3.10 or later. Only creating and activating the virtual
environment and setting the API key differ by platform.

**macOS / Linux (bash, zsh)**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

export CE_REGISTRY_API_KEY="<Credential Registry Search API key>"
```

**Windows (PowerShell)**

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .

$env:CE_REGISTRY_API_KEY = "<Credential Registry Search API key>"
```

If `Activate.ps1` is blocked with "running scripts is disabled on this
system", allow local scripts for your account once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. In `cmd.exe`,
activate with `.venv\Scripts\activate.bat` and set the key with
`set CE_REGISTRY_API_KEY=...`.

**Then, on any platform:**

```text
transcript-ctid-matcher Transcript.json
```

This writes `Transcript.matched.json` next to the input, ready to sign.

Options: `-o OUT` (output path, `-` for stdout), `--in-place` (overwrite the
input), `--min-confidence high`, `--strip-proofs` (accept already-signed
sample files by removing their proofs; the output must be re-signed),
`--report FILE` (separate JSON report of match confidence, scores and
misses), `--env sandbox`, `--cache-dir DIR`, `--no-cache` (clear cached
registry responses), `-v` (also list each miss).

Registry responses are cached per user: `%LOCALAPPDATA%\transcript-ctid-matcher`
on Windows, `~/Library/Caches/transcript-ctid-matcher` on macOS, and
`~/.cache/transcript-ctid-matcher` on Linux (or under `$XDG_CACHE_HOME` if
set).

`python -m transcript_ctid_matcher.probe "<institution name>"` tests
organization matching directly.

## How matching works

1. **Organizations.** The issuer name has its registrar-office suffix removed
   ("Forsyth Technical Community College Office of the Registrar" →
   "Forsyth Technical Community College"). The institution name in program
   descriptions ("…, Indiana University, Bloomington") is searched too.
   Transfer institutions come from nested credentials' narratives
   ("Transfer From: …"), and prior-education institutions come from
   `creator` Profiles.
2. **Courses** (`achievementType: Course`) are matched by `humanCode`, with
   note-code suffixes like `**` or `*U` removed, and by `name`. They're
   matched against the full Registry catalog of the institution that taught
   them.
3. **Programs and degrees** (degree/certificate achievement types, or an
   `Achievement` with a `fieldOfStudy`) are matched against the
   institution's credentials and learning programs. Groupings like
   "Academic Coursework" are skipped.
4. **Competencies** linked by `ceterms:teaches` / `ceterms:assesses` on each
   matched record are attached as alignments.

Only **high** and **medium** confidence matches are written. Misses are
silent in the output, and confidence scores are never written into the CLR.
Use `-v` or `--report` to see them. See [docs/confidence.md](docs/confidence.md).

## Signatures

Matching must happen **before** signing: the CTIDs become part of the
signed content. Input that already carries proofs is rejected. To experiment
with already-signed sample files, `--strip-proofs` (or `strip_proofs=True`)
removes their proofs first. That output is unsigned and must be re-signed.

## Privacy

Transcripts contain FERPA-protected information. No transcripts or match
output belong in this repository; `.gitignore` excludes all JSON except the
synthetic test fixture. Only institution names, course codes/titles, and
program names are sent to the Registry Search API, never learner data.

## Tests

```text
python -m unittest discover -s tests
```

The tests run offline against a synthetic CLR and a fake Registry client.
GitHub Actions runs them on Linux and Windows, with Python 3.10 and 3.13, on
every push and pull request (`.github/workflows/tests.yml`).
