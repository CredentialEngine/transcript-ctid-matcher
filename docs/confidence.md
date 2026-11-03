# How Confidence Ratings Are Determined

Every match the matcher makes — institution, course, or credential
— carries a similarity score from 0 to 1, translated into a confidence band:

| Band | Score | Effect on the output |
|---|---|---|
| **high** | ≥ 0.90 | Matched: CTID written |
| **medium** | 0.75–0.89 | Matched: CTID written |
| **low** | 0.55–0.74 | Miss: nothing written |
| **none** | < 0.55 | Miss: nothing written |

Scores and confidence are never written into the CLR. The command prints
them, `-v` lists each miss, and `--report FILE` writes every match and miss
with its score to a separate JSON file.

**Reading misses.** The most common cause of a miss is that the record was
never published to the Registry, not a weak match. Matching can only be as
complete as each institution's published catalog. That applies to the issuer,
and also to transfer and prior-education institutions. If an institution
itself isn't found, none of its courses or programs can match. See
[Before you start](../README.md#before-you-start-institutions-must-publish-to-the-registry).

Scoring lives in [`transcript_ctid_matcher/match.py`](../transcript_ctid_matcher/match.py)
(`confidence_label`, `similarity`, and the per-entity matchers).

## The common foundation: name similarity

Both strings are normalized — lowercased, punctuation stripped, and roughly
thirty abbreviations expanded ("Univ" → "University", "BS" → "Bachelor of
Science", "UC" → "University of California", "Comm Dev" → "Community
Development"). The score is the better of:

- **character-sequence similarity** (robust to typos and small edits), and
- **word-overlap similarity** (robust to word-order differences).

## Institutions

Candidates are retrieved from the Registry Search API through a ladder of
attempts: exact name match on several cleaned variants (parentheticals split
out, campus/city suffixes trimmed, unicode dashes fixed), then substring
match to catch registry campus qualifiers ("University of Missouri" hits
"University of Missouri-Columbia"), then full-text as a last resort.

Candidates are scored against both `ceterms:name` and
`ceterms:alternateName`, with one important guard: if the two names share
**no distinctive token**, the score is capped at 0.5. Generic words
(university, college, community, high, school, state, district...) cannot
drive a match on their own — "Coppell High School" can never match
"Columbine High School" just because both say "high school." An exact
registry name match scores 1.0.

## Courses

Courses are matched locally against the institution's full published Registry
catalog (courses, learning opportunities, and programs, downloaded once per
organization). The strongest signal is the **course code**: if the
transcript's `ENGL 1A` equals the registry record's `ceterms:codedNotation`,
the score floors at 0.85 with up to +0.10 from title agreement — codes are
how registrars think, and this tolerates abbreviated transcript titles.
Without a code match, title similarity carries the score alone, which is why
title-only matches land in medium/low rather than high.

## Credentials

Registry credential names are often long ("BS Option in Community
Development: Concentrations in..."), so in addition to plain similarity the
matcher scores **token containment** — do all the meaningful words of the
transcript degree appear in the registry name? Containment is dampened when
the degree name is too generic to be distinctive (a bare "Bachelor of
Science" cannot strongly match any specific program). A +0.10 bonus applies
when the degree wording implies the record's CTDL class (a "Bachelor..."
degree matching a `ceterms:BachelorDegree` record).

## Skills: no confidence involved

Skills are **not** fuzzy-matched. Once a course matches, its competencies
come directly from the linked `ceterms:teaches` / `ceterms:assesses` data on
that registry record — deterministic linked data, fully traceable to the
course CTID. Fuzzy matching gets us to the right CTID; everything after that
is authoritative Registry data.

## Deterministic by design

No AI is involved. All matching and scoring is inspectable code, and
the same inputs plus the same Registry data always produce the same output.

## Current status of the thresholds

The band thresholds are expert-tuned and validated against a 39-transcript
sample set, not yet statistically calibrated. Treat ratings as *indicative,
pending calibration*. The roadmap's Phase 2 builds a hand-labeled golden set
so each band gets a measured precision/recall number — the intended basis
for registrar-grade transfer-credit use.
