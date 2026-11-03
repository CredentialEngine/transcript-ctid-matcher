"""Read a 1EdTech CLR 2.0 transcript into the normalized TranscriptData model,
and write registry matches back into the CLR using only fields that
CLR 2.0 / Open Badges 3.0 define (see docs/output-format.md):

- A matched course, program or degree gets an ``alignment`` to its own
  Registry record, following Credential Engine's alignment guidance:
  ``targetType`` "ceterms:Credential" for credentials, "CTDL" for other
  Registry resources (courses, learning programs), CTID in ``targetCode``.
- Competencies a matched course or program teaches or assesses are added as
  ``alignment`` entries with ``targetType: "ceasn:Competency"``.
- Organizations: the issuer Profile and ``creator`` Profiles get an
  ``otherIdentifier`` IdentifierEntry with ``identifierType: "ext:CTID"``
  (Profile has no ``alignment``). A course taught at another institution,
  such as transfer credit, gets a ``creator`` Profile for that institution
  when it has none.
- Misses are silent: nothing is written for an entity that didn't match.

Match confidence is not written into the CLR; the CLI reports it, and can
write it to a separate report file.

The same Achievement appears both in ``credentialSubject.achievement`` and
inside each nested AchievementCredential; every copy with the same ``id`` is
annotated. The CLR is expected to be unsigned and signed afterwards (see
api.match_clr).
"""

import re
from collections import Counter
from typing import Any, Iterator, Optional

from .models import CourseEntry, DegreeAward, MatchResult, TranscriptData

CTID_IDENTIFIER_TYPE = "ext:CTID"
COMPETENCY_TARGET_TYPE = "ceasn:Competency"
CREDENTIAL_TARGET_TYPE = "ceterms:Credential"
CTDL_TARGET_TYPE = "CTDL"
SELF_TARGET_TYPES = {CREDENTIAL_TARGET_TYPE, CTDL_TARGET_TYPE}
CTDL_FRAMEWORK = "Credential Transparency Description Language"
CTDL_DESCRIPTION = "Additional information powered by the Credential Registry."
FINDER_URL = "https://credentialfinder.org/resources/{ctid}"
REGISTRY_URL = "https://credentialengineregistry.org/resources/{ctid}"

# Registry classes that are learning opportunities, not credentials.
LEARNING_OPPORTUNITY_CLASSES = {
    "ceterms:Course", "ceterms:LearningProgram",
    "ceterms:LearningOpportunityProfile", "ceterms:DegreeProgram",
}


# OB3 achievementType values that name an awarded credential or program.
CREDENTIAL_TYPES = {
    "AssociateDegree", "BachelorDegree", "MasterDegree", "DoctoralDegree",
    "ProfessionalDoctorate", "ResearchDoctorate", "Certificate",
    "CertificateOfCompletion", "Certification", "Diploma",
    "GeneralEducationDevelopment", "JourneymanCertificate", "License",
    "MasterCertificate", "Degree", "ApprenticeshipCertificate",
}
# Generic types that are a program only when they name a field of study
# (e.g. a major); otherwise they are groupings like "Academic Coursework".
PROGRAM_IF_FIELD_TYPES = {"Achievement", "LearningProgram"}

_REGISTRAR_SUFFIX = re.compile(
    r"[\s,\-]*(?:the\s+)?(?:office\s+of\s+(?:the\s+)?)?registrar(?:'s\s+office)?\s*$",
    re.IGNORECASE)
_TRANSFER_FROM = re.compile(r"Transfer(?:red)?\s+From:\s*([^;\n]+)", re.IGNORECASE)
_CODE_SPLIT = re.compile(r"^([A-Za-z&]+)[\s\-_.]+([A-Za-z]?[0-9][0-9A-Za-z.]*)$|^([A-Za-z&]+)([0-9][0-9A-Za-z.]*)$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _types(obj: dict) -> list:
    t = obj.get("type")
    return t if isinstance(t, list) else [t]


def walk(node: Any) -> Iterator[dict]:
    """Every dict in a JSON tree, depth first."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def institution_from_issuer(name: str) -> str:
    """"Forsyth Technical Community College Office of the Registrar" ->
    "Forsyth Technical Community College"."""
    stripped = _REGISTRAR_SUFFIX.sub("", name).strip(" ,-")
    return stripped or name.strip()


def clean_course_code(human_code: str, note_codes: set[str]) -> str:
    """Drop transcript note-code suffixes: "DRA-111**" -> "DRA-111",
    "ENG-111*U" -> "ENG-111"."""
    code = human_code.strip()
    for note in sorted(note_codes, key=len, reverse=True):
        if note and code.endswith(note):
            code = code[: -len(note)]
            break
    return re.sub(r"\*+[A-Za-z]?$", "", code).strip(" -")


def split_course_code(code: str) -> tuple[Optional[str], Optional[str]]:
    m = _CODE_SPLIT.match(code.strip())
    if m:
        return (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return None, code or None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _course_sources(clr: dict) -> dict[str, str]:
    """Achievement id -> transfer institution, from nested credentials'
    narratives ("Transfer From: University of X; transfer")."""
    sources: dict[str, str] = {}
    subject = clr.get("credentialSubject") or {}
    for vc in subject.get("verifiableCredential") or []:
        cs = vc.get("credentialSubject") or {}
        ach = cs.get("achievement") or {}
        m = _TRANSFER_FROM.search(cs.get("narrative") or "")
        if m and ach.get("id"):
            sources.setdefault(ach["id"], m.group(1).strip())
    return sources


def _description_institution(ach: dict) -> Optional[str]:
    """"Bachelor of Arts in Mathematics (Undergraduate), Indiana University,
    Bloomington" -> "Indiana University, Bloomington"."""
    desc, name = ach.get("description") or "", ach.get("name") or ""
    if not desc.startswith(name):
        return None
    rest = re.sub(r"^\s*\([^)]*\)", "", desc[len(name):])
    if not rest.startswith(","):
        return None
    return rest.lstrip(", ").strip() or None


def read_transcript(clr: dict) -> TranscriptData:
    if "ClrCredential" not in _types(clr):
        raise ValueError("Input is not a CLR 2.0 ClrCredential "
                         f"(type={clr.get('type')!r}).")
    subject = clr.get("credentialSubject") or {}
    achievements = subject.get("achievement") or []

    issuer = clr.get("issuer") or {}
    issuer_name = issuer.get("name") if isinstance(issuer, dict) else None
    institution = institution_from_issuer(issuer_name or "")

    # Program/degree descriptions often carry the institution name proper
    # ("..., Indiana University, Bloomington"), a useful second search term.
    desc_names = Counter(
        n for a in achievements
        if a.get("achievementType") != "Course"
        and (n := _description_institution(a)))
    alt_name = desc_names.most_common(1)[0][0] if desc_names else None
    if not institution and alt_name:
        institution, alt_name = alt_name, None
    if not institution:
        raise ValueError("Could not determine the issuing institution from the CLR issuer.")
    if alt_name and alt_name.lower() == institution.lower():
        alt_name = None

    transfer_from = _course_sources(clr)
    data = TranscriptData(issuing_institution=institution,
                          institution_system=alt_name)

    for ach in achievements:
        kind = ach.get("achievementType")
        creator = ach.get("creator") if isinstance(ach.get("creator"), dict) else None
        creator_name = (creator or {}).get("name")
        if kind == "Course":
            note_codes = {al.get("targetCode") for al in ach.get("alignment") or []
                          if al.get("targetType") == "ext:CourseAttribute"}
            code = clean_course_code(ach.get("humanCode") or "", note_codes - {None})
            subj, num = split_course_code(code)
            taught_at = transfer_from.get(ach.get("id")) or creator_name
            data.courses.append(CourseEntry(
                source_id=ach.get("id"), subject_code=subj, course_number=num,
                title=ach.get("name") or "", is_transfer=ach.get("id") in transfer_from,
                taught_at=taught_at))
        elif kind in CREDENTIAL_TYPES or (
                kind in PROGRAM_IF_FIELD_TYPES and ach.get("fieldOfStudy")):
            name = ach.get("name") or ach.get("fieldOfStudy")
            field = ach.get("fieldOfStudy")
            major = field if field and field.lower() not in name.lower() else None
            data.degrees.append(DegreeAward(
                source_id=ach.get("id"), name=name, major=major,
                institution=creator_name))

    seen = {institution.lower()}
    for name in [c.taught_at for c in data.courses] + [d.institution for d in data.degrees]:
        if name and name.lower() not in seen:
            seen.add(name.lower())
            data.transfer_institutions.append(name)
    return data


def prior_education_orgs(clr: dict) -> list[tuple[str, str]]:
    """(achievement id, creator name) for non-course, non-program
    Achievements carrying a creator Profile, e.g. a high school recorded as
    prior education. Matched as organizations only."""
    out = []
    for ach in (clr.get("credentialSubject") or {}).get("achievement") or []:
        creator = ach.get("creator")
        kind = ach.get("achievementType")
        if not isinstance(creator, dict) or not creator.get("name"):
            continue
        if kind == "Course" or kind in CREDENTIAL_TYPES or ach.get("fieldOfStudy"):
            continue
        out.append((ach.get("id"), creator["name"]))
    return out




# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def is_match(result: Optional[MatchResult]) -> bool:
    return bool(result and result.matched and result.best and result.best.ctid)


def finder_url(ctid: str) -> str:
    """Stable Credential Finder URL built from the CTID, per Credential
    Engine's guidance (rather than whichever URI style the Registry returned)."""
    return FINDER_URL.format(ctid=ctid)


def self_alignment(result: MatchResult) -> dict:
    """Alignment from a matched Achievement to its own Registry record."""
    best = result.best
    target_type = (CTDL_TARGET_TYPE if best.ctdl_type in LEARNING_OPPORTUNITY_CLASSES
                   or result.kind == "course" else CREDENTIAL_TARGET_TYPE)
    return {"type": ["Alignment"], "targetType": target_type,
            "targetCode": best.ctid, "targetName": best.name or best.ctid,
            "targetUrl": finder_url(best.ctid),
            "targetDescription": CTDL_DESCRIPTION,
            "targetFramework": CTDL_FRAMEWORK}


def competency_alignments(result: MatchResult) -> list[dict]:
    out, seen = [], set()
    if not is_match(result):
        return out
    for skill in result.skills:
        ctid = skill.ctid
        if not ctid or ctid in seen:
            continue
        seen.add(ctid)
        al = {"type": ["Alignment"], "targetType": COMPETENCY_TARGET_TYPE,
              "targetCode": ctid, "targetName": skill.name or ctid,
              "targetUrl": finder_url(ctid)}
        if skill.description:
            al["targetDescription"] = skill.description
        if skill.framework:
            al["targetFramework"] = skill.framework
        out.append(al)
    return out


def _add_alignments(obj: dict, new: list[dict]) -> None:
    """Append alignments, skipping any whose (targetType, targetCode) is
    already present, so existing ones are kept and re-runs don't duplicate."""
    existing = obj.get("alignment") or []
    have = {(a.get("targetType"), a.get("targetCode"))
            for a in existing if isinstance(a, dict)}
    added = [a for a in new if (a["targetType"], a["targetCode"]) not in have]
    if added:
        obj["alignment"] = existing + added


def _add_ctid(profile: dict, ctid: str) -> None:
    ids = [e for e in profile.get("otherIdentifier") or []
           if not (isinstance(e, dict) and e.get("identifierType") == CTID_IDENTIFIER_TYPE
                   and e.get("identifier") == ctid)]
    ids.append({"type": "IdentifierEntry", "identifier": ctid,
                "identifierType": CTID_IDENTIFIER_TYPE})
    profile["otherIdentifier"] = ids


def creator_profile(name: str, org: MatchResult) -> dict:
    """Profile for an institution that has none in the CLR (e.g. transfer
    credit). Its id is the organization's Registry URI."""
    ctid = org.best.ctid
    return {"id": REGISTRY_URL.format(ctid=ctid), "type": ["Profile"], "name": name,
            "otherIdentifier": [{"type": "IdentifierEntry", "identifier": ctid,
                                 "identifierType": CTID_IDENTIFIER_TYPE}]}


def annotate(clr: dict, data: TranscriptData, org_results: list[MatchResult],
             course_results: list[MatchResult], credential_results: list[MatchResult],
             prior_orgs: dict[str, MatchResult]) -> dict:
    """Write matches into the CLR in place. Returns counts for reporting
    (nothing about the run itself is written into the CLR)."""
    issuer_result = org_results[0]
    org_by_name = {r.query_text: r for r in org_results}

    # Achievement id -> (its match, the institution that created it when
    # that isn't the issuer, and that institution's name on the transcript).
    by_achievement: dict[str, tuple[MatchResult, Optional[MatchResult], Optional[str]]] = {}
    for course, res in zip(data.courses, course_results):
        if course.source_id:
            org = org_by_name.get(course.taught_at) if course.taught_at else None
            by_achievement[course.source_id] = (res, org, course.taught_at)
    for degree, res in zip(data.degrees, credential_results):
        if degree.source_id:
            org = org_by_name.get(degree.institution) if degree.institution else None
            by_achievement[degree.source_id] = (res, org, degree.institution)
    for aid, org in prior_orgs.items():
        if aid:
            by_achievement.setdefault(aid, (None, org, None))

    issuer = clr.get("issuer") if isinstance(clr.get("issuer"), dict) else {}
    issuer_ids = {issuer.get("id")} - {None}

    copies = 0
    for obj in list(walk(clr)):
        oid = obj.get("id")
        types = _types(obj)
        if "Achievement" in types and oid in by_achievement:
            res, org, org_name = by_achievement[oid]
            if is_match(res):
                _add_alignments(obj, [self_alignment(res)] + competency_alignments(res))
                copies += 1
            if is_match(org):
                creator = obj.get("creator")
                if isinstance(creator, dict):
                    _add_ctid(creator, org.best.ctid)
                elif org_name:
                    obj["creator"] = creator_profile(org_name, org)
        elif "Profile" in types and oid in issuer_ids and is_match(issuer_result):
            _add_ctid(obj, issuer_result.best.ctid)

    def counts(results: list[MatchResult]) -> dict:
        return {"total": len(results), "matched": sum(is_match(r) for r in results)}

    return {
        "courses": counts(course_results),
        "programsAndDegrees": counts(credential_results),
        "competencies": len({al["targetCode"]
                             for r in course_results + credential_results
                             for al in competency_alignments(r)}),
        "annotatedAchievementCopies": copies,
    }


# ---------------------------------------------------------------------------
# Separate match report (confidence and misses; never written into the CLR)
# ---------------------------------------------------------------------------

def _report_entry(result: MatchResult, source_id: Optional[str] = None) -> dict:
    entry: dict[str, Any] = {"query": result.query_text, "matched": is_match(result),
                             "confidence": result.confidence, "score": result.score}
    if source_id:
        entry["achievementId"] = source_id
    if is_match(result):
        entry.update({k: v for k, v in {
            "ctid": result.best.ctid, "name": result.best.name,
            "type": result.best.ctdl_type, "codedNotation": result.best.coded_notation,
        }.items() if v})
    return entry


def match_report(data: TranscriptData, org_results: list[MatchResult],
                 course_results: list[MatchResult], credential_results: list[MatchResult],
                 prior_orgs: dict[str, MatchResult]) -> dict:
    return {
        "organizations": [_report_entry(r) for r in org_results]
                         + [_report_entry(r, aid) for aid, r in prior_orgs.items()],
        "courses": [_report_entry(r, c.source_id)
                    for c, r in zip(data.courses, course_results)],
        "programsAndDegrees": [_report_entry(r, d.source_id)
                               for d, r in zip(data.degrees, credential_results)],
    }
