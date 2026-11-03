# Architecture

This component is the **Credential Registry Query (Consume)** service in the
Infuse architecture (draft, "Infuse based upon CE comments 10-26"). It takes
the unsigned CLR 2.0 transcript produced by Learner Transcript Parsing,
enriches it with Registry metadata (CTIDs for organizations, courses,
programs/degrees and competencies), and returns it before AI Skills
Inferencing and signing.

**No AI is involved here.** Matching is deterministic code calling the
Credential Registry: the same transcript and the same Registry data always
produce the same output. See [confidence.md](confidence.md) for how matches
are scored and [output-format.md](output-format.md) for what is written.

## Where it sits in the Infuse flow

The Orchestrator calls each service in turn. Services other than this one
are labeled as in the draft architecture; their internals are out of scope.

```mermaid
sequenceDiagram
    autonumber
    actor Issuer as Institutional Issuer
    participant Orch as Orchestrator<br/>(Rules Repository)
    participant Parse as Learner Transcript Parsing<br/>(product TBD)
    participant Query as Credential Registry Query<br/>(this component)
    participant Reg as Credential Registry<br/>(Credential Engine)
    participant AI as AI Skills Inferencing<br/>(product TBD)
    participant Sign as Credential Issuing Service<br/>(swappable)
    participant IdR as Issuer Identity Registry<br/>(DIDs + CTIDs)
    participant Wallet as Learner Wallet<br/>(swappable)

    Issuer->>Orch: Transcript (PDF)
    Orch->>Parse: Parse transcript
    Parse-->>Orch: CLR 2.0 JSON, unsigned

    rect rgb(232, 245, 233)
    Note over Orch,Reg: Enrich transcript with Registry metadata
    Orch->>Query: match_clr(clr)
    Query->>Reg: Search organizations, courses,<br/>credentials, competencies
    Reg-->>Query: CTDL records
    Query-->>Orch: CLR with CTIDs and alignments, still unsigned<br/>+ match report (confidence, misses)
    end

    opt Registrar review of the match report
        Orch->>Issuer: Low-confidence and missed matches
        Issuer-->>Orch: Approve
    end

    Orch->>AI: Enriched transcript as input
    AI-->>Orch: Inferred skills

    rect rgb(255, 243, 224)
    Note over Orch,IdR: Trust and credential
    Orch->>Sign: Sign as CLR 2.0
    Sign->>IdR: Issuer is a verified organization?
    IdR-->>Sign: Issuer DID + CTID
    Sign-->>Orch: Signed CLR 2.0
    end

    Orch->>Wallet: Deliver CLR to learner
```

Notes on the flow:

- **Ordering is the contract.** Everything that writes into the CLR,
  including this step and anything AI Skills Inferencing adds, must happen
  before step 12 (signing). This component rejects input that already
  carries proofs (`SignedInputError`).
- **The match report never enters the CLR.** The Orchestrator decides
  whether to keep it, route it to a registrar (step 8), or discard it.
- How AI Skills Inferencing's output is represented in the CLR, and how the
  signer resolves the issuer against the Issuer Identity Registry, are
  outside this component and shown as the draft describes them.

## Inside the Registry query (one transcript)

```mermaid
sequenceDiagram
    autonumber
    participant Orch as Orchestrator<br/>(or CLI)
    participant M as match_clr
    participant Cache as Disk cache
    participant Search as Registry Search API<br/>(API key)
    participant Res as Registry resources<br/>(public, no key)

    Orch->>M: Unsigned CLR 2.0 (dict or stdin)
    M->>M: Reject if signed (proofs present)
    M->>M: Read CLR: issuer, transfer and prior-education<br/>institutions, courses, programs/degrees

    rect rgb(227, 242, 253)
    Note over M,Search: 1. Organizations (issuer, transfer, prior education)
    loop Each institution name, until a strong match
        M->>Search: Org search: exact, contains, then full-text
        Search-->>M: Candidate organizations
    end
    M->>M: Score names, assign confidence
    end

    rect rgb(232, 245, 233)
    Note over M,Search: 2. Courses, against each matched org's catalog
    M->>Cache: Catalog for this org cached?
    alt Cache miss
        M->>Search: All learning opportunities ownedBy / offeredBy org
        Search-->>M: Courses and learning programs
        M->>Cache: Save catalog
    end
    M->>M: Match each course locally by code + title
    end

    rect rgb(255, 243, 224)
    Note over M,Search: 3. Programs and degrees
    M->>Cache: Credentials for this org cached?
    alt Cache miss
        M->>Search: All credentials ownedBy / offeredBy org
        Search-->>M: Credentials
        M->>Cache: Save credentials
    end
    M->>M: Match against credentials + learning programs
    end

    rect rgb(243, 229, 245)
    Note over M,Res: 4. Competencies on matched records
    M->>M: Read ceterms:teaches / ceterms:assesses
    opt Competency given only as a URI
        M->>Res: GET competency resource
        Res-->>M: Competency text
    end
    end

    M->>M: Drop matches below min_confidence
    M->>M: Write alignments and creator / issuer CTIDs<br/>into every copy of each Achievement
    M-->>Orch: Enriched CLR, unsigned + match report
```

Registry traffic per transcript: a few organization searches per
institution, plus up to four paged queries each for the courses and the
credentials of each matched institution. Catalogs are cached on disk per
organization, so a second transcript from the same institution needs only
the organization searches. The cache has no expiry yet; a production
deployment needs a shared cache that refreshes as the Registry changes.

## Relationship to the Issuer Identity Registry

The draft architecture's Issuer Identity Registry holds verified
organizations with their DIDs and CTIDs. This component currently finds the
issuer's CTID by **name matching**. Once the Issuer Identity Registry is
available, looking the issuer up by its DID would be authoritative and
should replace name matching for the issuer. Transfer and prior-education
institutions would still need name matching, since the CLR carries only
their names.
