"""Matching logic: transcript institutions/courses/degrees vs. registry records."""

import logging
import re
from difflib import SequenceMatcher
from typing import Optional

from .models import (CourseEntry, DegreeAward, MatchResult, RegistryCandidate,
                     Skill, SkillSummary, TranscriptData)
from .registry import ORG_TYPES, RegistryClient, langstring, langstring_all

log = logging.getLogger(__name__)

# Common abbreviations seen on transcripts, expanded before comparison.
_ABBREV = {
    "univ": "university",
    "coll": "college",
    "cc": "community college",
    "ccd": "community college district",
    "comm": "community",
    "inst": "institute",
    "tech": "technical",
    "cmty": "community",
    "ctr": "center",
    "jr": "junior",
    "uc": "university of california",
    "hs": "high school",
    "intro": "introduction to",
    "amer": "american",
    "engr": "engineering",
    "sci": "science",
    "mgmt": "management",
    "prin": "principles",
    "fund": "fundamentals",
    "elem": "elementary",
    "adv": "advanced",
    "gen": "general",
    "comp": "composition",
    "dev": "development",
    "admin": "administration",
    # degree abbreviations
    "bs": "bachelor of science",
    "ba": "bachelor of arts",
    "bsn": "bachelor of science in nursing",
    "aa": "associate of arts",
    "aas": "associate of applied science",
    "ms": "master of science",
    "ma": "master of arts",
    "mba": "master of business administration",
    "msa": "master of science in administration",
    "phd": "doctor of philosophy",
    "edd": "doctor of education",
}

_STOPWORDS = {"of", "the", "in", "and", "for", "to", "a", "an", "with", "on", "or"}

# Tokens too common in institution names to distinguish one from another.
_GENERIC_ORG_TOKENS = _STOPWORDS | {
    "university", "college", "colleges", "community", "district", "school",
    "schools", "high", "state", "institute", "institution", "technical",
    "technology", "center", "academy", "county", "at", "campus", "junior",
}

_US_STATE_CODES = {
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi", "id",
    "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn", "ms",
    "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh", "ok",
    "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va", "wa", "wv",
    "wi", "wy",
}

# Keywords in a transcript degree name -> expected CTDL credential class.
_DEGREE_TYPE_HINTS = {
    "bachelor": "ceterms:BachelorDegree",
    "associate": "ceterms:AssociateDegree",
    "master": "ceterms:MasterDegree",
    "doctor": "ceterms:DoctoralDegree",
    "doctorate": "ceterms:DoctoralDegree",
    "certificate": "ceterms:Certificate",
    "diploma": "ceterms:Diploma",
}


def normalize_name(name: str) -> str:
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    words = [_ABBREV.get(w, w) for w in s.split()]
    return " ".join(words)


def normalize_code(subject: Optional[str], number: Optional[str]) -> Optional[str]:
    if not subject and not number:
        return None
    code = f"{subject or ''}{number or ''}"
    return re.sub(r"[^A-Z0-9]", "", code.upper()) or None


def similarity(a: str, b: str) -> float:
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0
    base = SequenceMatcher(None, a, b).ratio()
    # Token overlap helps when word order differs ("Composition, English" etc.)
    ta, tb = set(a.split()), set(b.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(base, jaccard)


def _content_tokens(text: str) -> set[str]:
    return {w for w in normalize_name(text).split() if w not in _STOPWORDS}


def containment(query: str, candidate: str) -> float:
    """Fraction of the query's content tokens present in the candidate,
    dampened when the query is too short to be distinctive."""
    q = _content_tokens(query)
    if not q:
        return 0.0
    covered = len(q & _content_tokens(candidate)) / len(q)
    specificity = min(1.0, len(q) / 4)
    return covered * specificity


def confidence_label(score: float) -> str:
    if score >= 0.90:
        return "high"
    if score >= 0.75:
        return "medium"
    if score >= 0.55:
        return "low"
    return "none"


def _candidate_from_record(rec: dict, score: float) -> RegistryCandidate:
    ctdl_type = rec.get("@type")
    if isinstance(ctdl_type, list):
        ctdl_type = ctdl_type[0] if ctdl_type else None
    return RegistryCandidate(
        uri=rec.get("@id", ""),
        ctid=rec.get("ceterms:ctid"),
        ctdl_type=ctdl_type,
        name=langstring(rec.get("ceterms:name")),
        coded_notation=langstring(rec.get("ceterms:codedNotation")),
        score=round(score, 3),
    )


# ---------------------------------------------------------------------------
# Skills (ceterms:teaches / ceterms:assesses alignments)
# ---------------------------------------------------------------------------

def _as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def extract_skills(record: dict) -> list[Skill]:
    """Pull competency alignments off a registry course/credential record.

    Alignments are ceterms:CredentialAlignmentObject entries; the competency
    name is usually inline (targetNodeName) but may be only a targetNode URI,
    which resolve_skills() fills in later.
    """
    skills: list[Skill] = []
    for prop, label in (("ceterms:teaches", "teaches"),
                        ("ceterms:assesses", "assesses")):
        for item in _as_list(record.get(prop)):
            if isinstance(item, str):
                skills.append(Skill(uri=item, source=label))
                continue
            if not isinstance(item, dict):
                continue
            target = item.get("ceterms:targetNode")
            skills.append(Skill(
                name=langstring(item.get("ceterms:targetNodeName")),
                description=langstring(item.get("ceterms:targetNodeDescription")),
                framework=langstring(item.get("ceterms:frameworkName")),
                uri=target if isinstance(target, str) else None,
                source=label,
            ))
    return [s for s in skills if s.name or s.uri]


def resolve_skills(client: RegistryClient, results: list[MatchResult]) -> None:
    """Fill in names for URI-only skills via public resource fetches."""
    for result in results:
        for skill in result.skills:
            if skill.name or not skill.uri:
                continue
            rec = client.fetch_resource(skill.uri)
            if not rec:
                continue
            skill.name = (langstring(rec.get("ceasn:competencyText"))
                          or langstring(rec.get("ceasn:name"))
                          or langstring(rec.get("ceterms:name")))
            skill.description = skill.description or langstring(rec.get("ceasn:comment"))


def build_skills_profile(results: list[MatchResult]) -> list[SkillSummary]:
    """Deduplicate skills across all matched courses/credentials."""
    profile: dict[str, SkillSummary] = {}
    for result in results:
        if not result.matched:
            continue
        for skill in result.skills:
            if not skill.name or not skill.name.strip():
                continue
            key = skill.uri or f"name:{normalize_name(skill.name)}|{skill.framework or ''}"
            entry = profile.setdefault(key, SkillSummary(
                name=skill.name.strip(), framework=skill.framework, uri=skill.uri))
            if result.query_text not in entry.source_courses:
                entry.source_courses.append(result.query_text)
    return sorted(profile.values(), key=lambda e: (e.framework or "", e.name.lower()))


# ---------------------------------------------------------------------------
# Organization matching
# ---------------------------------------------------------------------------

def clean_org_name(name: str) -> str:
    """Fix mojibake/unicode dashes and collapse whitespace."""
    s = name.replace("�", "-").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip(" -,")


def _expand_words(text: str) -> str:
    return " ".join(_ABBREV.get(w.lower(), w) for w in text.split())


def org_search_attempts(name: str,
                        extra_names: Optional[list[str]] = None) -> list[tuple[str, Optional[str]]]:
    """Ordered (term, search:matchType) attempts for one institution name.

    Cheap precise attempts (exactMatch) first, then contains on the head of
    the name (catches registry campus qualifiers like "University of
    Missouri-Columbia"), then full-text as a last resort.
    """
    attempts: list[tuple[str, Optional[str]]] = []
    seen: set[tuple[str, Optional[str]]] = set()

    def add(term: Optional[str], match_type: Optional[str]) -> None:
        term = (term or "").strip(" -,;:")
        key = (term.lower(), match_type)
        if term and len(term) > 3 and key not in seen:
            seen.add(key)
            attempts.append((term, match_type))

    base = clean_org_name(name)

    # Transcript quirks: leading state code ("Ca Moreno Valley College"),
    # trailing ", City" suffix, duplicated trailing city token
    # ("University of Pittsburgh Pittsburgh").
    words = base.split()
    if len(words) > 2 and words[0].lower() in _US_STATE_CODES:
        base = " ".join(words[1:])
    base = re.sub(r",[^,]+$", "", base).strip()
    words = base.split()
    if len(words) > 2 and words[-1].lower() in (w.lower() for w in words[:-1]):
        base = " ".join(words[:-1])

    # Parentheticals often hold the better-known name:
    # "Carnegie Institute of Technology (Carnegie Mellon University)"
    paren = re.search(r"\(([^)]+)\)", base)
    no_paren = re.sub(r"\([^)]*\)", "", base).strip()

    for variant in ([paren.group(1)] if paren else []) + [no_paren]:
        add(variant, "search:exactMatch")
        add(_expand_words(variant), "search:exactMatch")

    # "Colleges of the Dallas County Community College District": strip
    # leading generic words down to the first distinctive token.
    lead_words = no_paren.split()
    while lead_words and lead_words[0].lower() in _GENERIC_ORG_TOKENS:
        lead_words.pop(0)
    stripped = " ".join(lead_words)
    if stripped.lower() != no_paren.lower() and len(lead_words) >= 2:
        add(_expand_words(stripped), "search:exactMatch")
        add(_expand_words(stripped), "search:contains")

    # Substring matches catch registry campus qualifiers the transcript
    # omits: "University of Michigan" hits "University of Michigan-Ann Arbor".
    # Try the head of the name before a campus/location separator
    # ("University of Missouri - Columbia" -> "University of Missouri"),
    # then the whole name.
    head = re.split(r"\s+-\s+|-|,| at ", no_paren)[0].strip()
    if head and len(head.split()) >= 2 and head.lower() != no_paren.lower():
        add(_expand_words(head), "search:contains")
    if len(no_paren.split()) >= 2:
        add(_expand_words(no_paren), "search:contains")

    # Full-text last resort on the expanded name (and parenthetical).
    add(_expand_words(no_paren), None)
    if paren:
        add(_expand_words(paren.group(1)), None)

    for extra in extra_names or []:
        if extra:
            add(clean_org_name(extra), "search:exactMatch")

    return attempts


def org_similarity(query: str, candidate: str) -> float:
    """Name similarity with a guard: if the two names share no distinctive
    (non-generic) token, generic overlap like "high school" can't produce a
    match ("Coppell High School" vs "Columbine High School")."""
    score = similarity(query, candidate)
    dq = _content_tokens(query) - _GENERIC_ORG_TOKENS
    dc = _content_tokens(candidate) - _GENERIC_ORG_TOKENS
    if dq and dc and not dq & dc:
        score = min(score, 0.5)
    return score


def match_organization(client: RegistryClient, name: str,
                       extra_names: Optional[list[str]] = None) -> MatchResult:
    """Find the best registry organization for an institution name.

    extra_names: other names for the same place (e.g. the district name) that
    are also worth searching if the primary name finds nothing.
    """
    result = MatchResult(kind="organization", query_text=name)
    cleaned = _expand_words(clean_org_name(name))

    scored: dict[str, tuple[float, dict]] = {}

    def best_so_far() -> float:
        return max((s for s, _ in scored.values()), default=0.0)

    for term, match_type in org_search_attempts(name, extra_names):
        try:
            records = client.org_search(term, match_type)
        except Exception as exc:
            result.detail = f"Registry search failed: {exc}"
            log.warning("Org search failed for %r (%s): %s", term, match_type, exc)
            continue
        for rec in records:
            rec_types = rec.get("@type")
            rec_types = rec_types if isinstance(rec_types, list) else [rec_types]
            if not any(t in ORG_TYPES for t in rec_types):
                continue
            names = langstring_all(rec.get("ceterms:name")) + \
                    langstring_all(rec.get("ceterms:alternateName"))
            best = max((org_similarity(cleaned, n) for n in names if n), default=0.0)
            rid = rec.get("@id", "")
            if rid and (rid not in scored or best > scored[rid][0]):
                scored[rid] = (best, rec)
        if best_so_far() >= 0.92:
            break  # good enough; skip the broader, noisier attempts

    ranked = sorted(scored.values(), key=lambda t: t[0], reverse=True)
    if ranked:
        best_score, best_rec = ranked[0]
        result.score = round(best_score, 3)
        result.confidence = confidence_label(best_score)
        result.best = _candidate_from_record(best_rec, best_score)
        result.runners_up = [_candidate_from_record(r, s) for s, r in ranked[1:4]]
        result.matched = result.confidence in ("high", "medium")
    if not ranked:
        result.detail = result.detail or "No registry organizations returned for this name."
    return result


# ---------------------------------------------------------------------------
# Course matching (local matching against the org's full course list)
# ---------------------------------------------------------------------------

def _index_registry_courses(records: list[dict]) -> list[dict]:
    indexed = []
    for rec in records:
        code = langstring(rec.get("ceterms:codedNotation"))
        indexed.append({
            "record": rec,
            "name": langstring(rec.get("ceterms:name")) or "",
            "code_norm": re.sub(r"[^A-Z0-9]", "", code.upper()) if code else None,
        })
    return indexed


def match_course(course: CourseEntry, indexed: list[dict]) -> MatchResult:
    code = normalize_code(course.subject_code, course.course_number)
    label = " ".join(x for x in (course.subject_code, course.course_number) if x)
    query_text = f"{label} {course.title}".strip()
    result = MatchResult(kind="course", query_text=query_text)

    best_score, best_rec = 0.0, None
    runners: list[tuple[float, dict]] = []
    for entry in indexed:
        title_sim = similarity(course.title, entry["name"]) if entry["name"] else 0.0
        score = title_sim
        if code and entry["code_norm"]:
            if code == entry["code_norm"]:
                # Exact course-code match: strong signal even if titles are
                # abbreviated differently on the transcript.
                score = max(score, 0.85) + 0.10 * title_sim
            elif entry["code_norm"].endswith(code) or code.endswith(entry["code_norm"]):
                score = max(score, 0.60 + 0.30 * title_sim)
        score = min(score, 1.0)
        if score > best_score:
            if best_rec is not None:
                runners.append((best_score, best_rec))
            best_score, best_rec = score, entry["record"]
        elif score >= 0.55:
            runners.append((score, entry["record"]))

    result.score = round(best_score, 3)
    result.confidence = confidence_label(best_score)
    result.matched = result.confidence in ("high", "medium")
    if best_rec is not None and best_score >= 0.40:
        result.best = _candidate_from_record(best_rec, best_score)
        if result.matched:
            result.skills = extract_skills(best_rec)
    runners.sort(key=lambda t: t[0], reverse=True)
    result.runners_up = [_candidate_from_record(r, s) for s, r in runners[:3]]
    return result


# ---------------------------------------------------------------------------
# Credential (degree) matching
# ---------------------------------------------------------------------------

_PROGRAM_TYPES = {"ceterms:LearningProgram", "ceterms:DegreeProgram"}


def _is_program_record(rec: dict) -> bool:
    types = rec.get("@type")
    types = types if isinstance(types, list) else [types]
    return any(t in _PROGRAM_TYPES for t in types)


def match_credential(degree: DegreeAward, records: list[dict]) -> MatchResult:
    query = degree.name if not degree.major else f"{degree.name} - {degree.major}"
    result = MatchResult(kind="credential", query_text=query)

    # Which CTDL credential class does the transcript degree name imply?
    expected_type = None
    for keyword, ctdl_type in _DEGREE_TYPE_HINTS.items():
        if keyword in normalize_name(degree.name):
            expected_type = ctdl_type
            break

    best_score, best_rec = 0.0, None
    runners: list[tuple[float, dict]] = []
    for rec in records:
        name = langstring(rec.get("ceterms:name")) or ""
        base = max(similarity(query, name), similarity(degree.name, name))
        if degree.major:
            base = max(base, 0.5 * similarity(degree.name, name)
                       + 0.5 * similarity(degree.major, name))
        # Registry credential names are often long ("BS Option in ... :
        # Concentrations in ..."); containment rewards the transcript degree's
        # tokens all appearing in the registry name even when lengths differ.
        cont = containment(query, name)
        score = max(base, 0.5 * cont + 0.5 * base)
        rec_types = rec.get("@type")
        rec_types = rec_types if isinstance(rec_types, list) else [rec_types]
        if expected_type and expected_type in rec_types and cont > 0.3:
            score = min(1.0, score + 0.10)
        if score > best_score:
            if best_rec is not None:
                runners.append((best_score, best_rec))
            best_score, best_rec = score, rec
        elif score >= 0.55:
            runners.append((score, rec))

    result.score = round(best_score, 3)
    result.confidence = confidence_label(best_score)
    result.matched = result.confidence in ("high", "medium")
    if best_rec is not None and best_score >= 0.40:
        result.best = _candidate_from_record(best_rec, best_score)
        if result.matched:
            result.skills = extract_skills(best_rec)
    runners.sort(key=lambda t: t[0], reverse=True)
    result.runners_up = [_candidate_from_record(r, s) for s, r in runners[:3]]
    return result


# ---------------------------------------------------------------------------
# Whole-transcript orchestration
# ---------------------------------------------------------------------------

def match_transcript(client: RegistryClient, data: TranscriptData) -> tuple[
        list[MatchResult], list[MatchResult], list[MatchResult],
        list[SkillSummary], list[str]]:
    """Match one transcript's institutions, courses, and degrees, and build
    the transcript-level skills profile from the matched records."""
    errors: list[str] = []

    # 1. Organizations: issuer first, then each transfer institution.
    org_results: list[MatchResult] = []
    issuer_result = match_organization(
        client, data.issuing_institution,
        extra_names=[data.institution_system] if data.institution_system else None)
    org_results.append(issuer_result)
    for t in data.transfer_institutions:
        org_results.append(match_organization(client, t))

    org_by_name = {r.query_text: r for r in org_results}

    # 2. Courses: pull the matched org's full course catalog from the registry
    #    once, then match all transcript courses locally against it.
    course_results: list[MatchResult] = []
    catalogs: dict[str, list[dict]] = {}   # institution name -> indexed courses

    def catalog_for(inst_name: str) -> Optional[list[dict]]:
        if inst_name in catalogs:
            return catalogs[inst_name]
        org = org_by_name.get(inst_name)
        if org is None or org.best is None or not org.matched:
            catalogs[inst_name] = None
            return None
        try:
            recs = client.courses_for_org(org.best.uri, org.best.ctid or org.best.uri)
            log.info("Registry has %d course/LOP records for %s", len(recs), inst_name)
            catalogs[inst_name] = _index_registry_courses(recs)
        except Exception as exc:
            errors.append(f"Failed to fetch registry courses for {inst_name}: {exc}")
            catalogs[inst_name] = None
        return catalogs[inst_name]

    for course in data.courses:
        inst = course.taught_at or data.issuing_institution
        indexed = catalog_for(inst)
        if indexed is None:
            r = MatchResult(
                kind="course",
                query_text=f"{course.subject_code or ''} {course.course_number or ''} "
                           f"{course.title}".strip(),
                detail=f"No matched registry organization for '{inst}', course search skipped.",
            )
            course_results.append(r)
            continue
        if not indexed:
            r = match_course(course, indexed)
            r.detail = f"Organization matched but has no course records in the registry."
            course_results.append(r)
            continue
        course_results.append(match_course(course, indexed))

    # 3. Degrees/credentials/programs awarded. Programs of study are
    #    published either as credentials or as ceterms:LearningProgram
    #    records, so candidates include both.
    credential_results: list[MatchResult] = []
    cred_records: dict[str, list[dict]] = {}
    for degree in data.degrees:
        inst = degree.institution or data.issuing_institution
        org = org_by_name.get(inst) or issuer_result
        if org.best is None or not org.matched:
            r = MatchResult(kind="credential", query_text=degree.name,
                            detail=f"No matched registry organization for '{inst}'.")
            credential_results.append(r)
            continue
        key = org.best.uri
        if key not in cred_records:
            try:
                cred_records[key] = list(client.credentials_for_org(
                    org.best.uri, org.best.ctid or org.best.uri))
            except Exception as exc:
                errors.append(f"Failed to fetch registry credentials for {inst}: {exc}")
                cred_records[key] = []
            for entry in catalog_for(org.query_text) or []:
                if _is_program_record(entry["record"]):
                    cred_records[key].append(entry["record"])
        credential_results.append(match_credential(degree, cred_records[key]))

    # 4. Skills: resolve URI-only competency references, then aggregate a
    #    deduplicated transcript-level skills profile.
    resolve_skills(client, course_results + credential_results)
    skills_profile = build_skills_profile(course_results + credential_results)

    return org_results, course_results, credential_results, skills_profile, errors
