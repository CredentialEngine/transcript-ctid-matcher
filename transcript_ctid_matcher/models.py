"""Data models: the normalized view of a CLR transcript that matching works
on, and the registry match results produced for it."""

from typing import List, Optional

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Normalized transcript content (read from the CLR by clr.read_transcript)
# ---------------------------------------------------------------------------

class CourseEntry(BaseModel):
    source_id: Optional[str] = None         # CLR Achievement id this came from
    subject_code: Optional[str] = None      # e.g. "ENGL"
    course_number: Optional[str] = None     # e.g. "1A", "1030"
    title: str                              # e.g. "College Composition and Reading"
    is_transfer: bool = False               # taken at another institution and transferred in
    taught_at: Optional[str] = None         # institution that actually taught it,
                                            # if different from the issuing institution


class DegreeAward(BaseModel):
    source_id: Optional[str] = None         # CLR Achievement id this came from
    name: str                               # e.g. "Associate of Science"
    major: Optional[str] = None
    institution: Optional[str] = None       # awarding institution if not the issuer


class TranscriptData(BaseModel):
    issuing_institution: str                # the school that produced the transcript
    institution_system: Optional[str] = None     # alternate name worth searching too
    transfer_institutions: List[str] = []
    degrees: List[DegreeAward] = []
    courses: List[CourseEntry] = []


# ---------------------------------------------------------------------------
# Match result models (transcript entity vs. Credential Registry record)
# ---------------------------------------------------------------------------

class Skill(BaseModel):
    """A competency linked to a matched registry record (ceterms:teaches /
    ceterms:assesses alignment)."""
    name: Optional[str] = None              # None until resolved, if URI-only
    description: Optional[str] = None
    framework: Optional[str] = None         # competency framework name
    uri: Optional[str] = None               # target competency resource URI
    source: Optional[str] = None            # "teaches" | "assesses"

    @property
    def ctid(self) -> Optional[str]:
        if not self.uri:
            return None
        tail = self.uri.rstrip("/").rsplit("/", 1)[-1]
        return tail if tail.startswith("ce-") else None


class SkillSummary(BaseModel):
    """One deduplicated skill across all matched records."""
    name: str
    framework: Optional[str] = None
    uri: Optional[str] = None
    source_courses: List[str] = []          # which matched courses teach it


class RegistryCandidate(BaseModel):
    uri: str                                # @id of the registry resource
    ctid: Optional[str] = None
    ctdl_type: Optional[str] = None
    name: Optional[str] = None
    coded_notation: Optional[str] = None
    score: float = 0.0                      # 0..1 similarity score


class MatchResult(BaseModel):
    kind: str                               # "organization" | "course" | "credential"
    query_text: str                         # what we searched for (name / code + title)
    matched: bool = False
    confidence: str = "none"                # "high" | "medium" | "low" | "none"
    score: float = 0.0
    best: Optional[RegistryCandidate] = None
    runners_up: List[RegistryCandidate] = []
    detail: Optional[str] = None            # human-readable explanation
    skills: List[Skill] = []                # competencies on the matched record
