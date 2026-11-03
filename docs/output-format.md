# Output Format

The output is the input CLR 2.0 document with Registry matches written in
beside the entities they identify. It uses only properties that CLR 2.0 and
Open Badges 3.0 define, so no JSON-LD context or schema extension is needed.
Everything in the input is kept. The input is expected to be **unsigned**:
matching runs between parsing and signing, so the CTIDs are covered by the
CLR's signatures. Signed input is rejected unless `--strip-proofs` is
given.

The patterns follow the OB 3.0 specification's Credential Engine examples
(§D.5, §D.7) and Credential Engine's guide,
[How to Align Digital Credentials with Meaningful CTDL Data](https://credentialengine.org/wp-content/uploads/2023/03/How-to-Align-Digital-Credentials-with-Meaningful-CTDL-Data.pdf).

## Matched course, program or degree: `alignment` to its Registry record

```json
"alignment": [
  {
    "type": ["Alignment"],
    "targetType": "CTDL",
    "targetCode": "ce-1a2b3c4d-...",
    "targetName": "Expository Writing",
    "targetUrl": "https://credentialfinder.org/resources/ce-1a2b3c4d-...",
    "targetDescription": "Additional information powered by the Credential Registry.",
    "targetFramework": "Credential Transparency Description Language"
  }
]
```

- `targetType` is `ceterms:Credential` ("an alignment to a CTDL Credential
  published by Credential Engine") when the matched Registry record is a
  credential (degree, certificate, …), and `CTDL` ("an alignment to a
  Credential Engine Item") when it's a course or learning program.
- `targetCode` is the CTID; `targetName` is the Registry record's name.
- `targetUrl` is always the Credential Finder URL built from the CTID, which
  Credential Engine recommends over copying Finder or Registry URLs.

## Competencies: `alignment` with `ceasn:Competency`

Each competency that a matched course or program teaches or assesses
(`ceterms:teaches` / `ceterms:assesses` on its Registry record) is added to
the same `alignment` list:

```json
{
  "type": ["Alignment"],
  "targetType": "ceasn:Competency",
  "targetCode": "ce-9f8e7d6c-...",
  "targetName": "Write a thesis-driven essay",
  "targetUrl": "https://credentialfinder.org/resources/ce-9f8e7d6c-...",
  "targetFramework": "English Composition Outcomes",
  "targetDescription": "..."
}
```

A consumer tells the two kinds apart by `targetType`: `CTDL` /
`ceterms:Credential` is the achievement's own Registry record, and
`ceasn:Competency` is a competency it develops. OB 3.0 alignments can't
express CTDL's teaches vs. assesses distinction, so both appear as
`ceasn:Competency`. Competencies without a CTID on the Registry record are
skipped.

Existing alignments, such as transcript note codes (`ext:CourseAttribute`),
are kept. An alignment with the same `targetType` and `targetCode` as an
existing one isn't added again.

## Organizations: `otherIdentifier` on Profiles

`Profile` has no `alignment` property, so organization CTIDs go in
`otherIdentifier`, using the IdentifierTypeEnum extension mechanism
(extended terms must start with `ext:`):

```json
"otherIdentifier": [
  { "type": "IdentifierEntry", "identifier": "ce-...", "identifierType": "ext:CTID" }
]
```

- **Issuer:** added to every copy of the issuer Profile (the CLR root's and
  each nested credential's).
- **Institution that taught a course** (e.g. transfer credit), or awarded
  a program: OB 3.0's `Achievement.creator` is "the person or organization
  that created the achievement definition". If the Achievement already has a
  `creator`, the CTID is added to it; otherwise a `creator` Profile is added,
  named as on the transcript, with the organization's Registry URI as `id`:

  ```json
  "creator": {
    "id": "https://credentialengineregistry.org/resources/ce-...",
    "type": ["Profile"],
    "name": "University of North Carolina-Greensboro",
    "otherIdentifier": [
      { "type": "IdentifierEntry", "identifier": "ce-...", "identifierType": "ext:CTID" }
    ]
  }
  ```

  This happens whenever the institution matched, even if the course itself
  didn't.
- **Prior education** recorded with a `creator` Profile (such as a high
  school) gets the CTID on that Profile.

`ext:CTID` is a new identifier type, not yet defined by 1EdTech or Credential
Engine. Per OB 3.0 §C.2 it should be documented as an extension; this page
is that documentation for now.

## Misses are silent

Nothing is written for an entity that didn't match (low or no confidence).
Match confidence, scores and misses are never written into the CLR. The
command prints them, `-v` lists each miss, and `--report FILE` writes them
all to a separate JSON report.

Re-running on an earlier output adds nothing new, but it doesn't remove
anything either: a CTID or alignment written by an earlier run stays even if
the entity no longer matches. Always match from the original CLR.

## Match report (`--report FILE`)

A separate JSON file, never part of the CLR:

```json
{
  "matchedAt": "2026-11-03T15:04:05Z",
  "registryEnvironment": "production",
  "organizations": [ { "query": "...", "matched": true, "confidence": "high", "score": 0.97, "ctid": "ce-...", "name": "..." } ],
  "courses": [ { "query": "ENG 111 Expository Writing", "achievementId": "urn:uuid:...", "matched": false, "confidence": "low", "score": 0.61 } ],
  "programsAndDegrees": [ "..." ],
  "summary": { "courses": { "total": 21, "matched": 14 }, "competencies": 57, "proofsRemoved": 0 },
  "input": "...", "output": "..."
}
```

Entries for misses carry the confidence and score but not the near-miss
Registry record.

## Duplicated achievements

A CLR lists each Achievement in `credentialSubject.achievement` and again
inside every nested AchievementCredential that awards it. All copies sharing
an `id` get the same annotations.
