"""Offline tests: a synthetic CLR 2.0 transcript matched against a fake
registry client. No network or API key needed.

Run with:  python -m unittest discover -s tests
"""

import copy
import json
import unittest
from pathlib import Path

from transcript_ctid_matcher import SignedInputError, match_clr
from transcript_ctid_matcher.api import _apply_threshold
from transcript_ctid_matcher.cli import default_output
from transcript_ctid_matcher.clr import (clean_course_code, institution_from_issuer,
                                         read_transcript, split_course_code)

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic.credential.json"
REG = "https://credentialengineregistry.org/resources/"

ORG_CTID = "ce-00000000-0000-4000-8000-000000000001"
TRANSFER_CTID = "ce-00000000-0000-4000-8000-000000000002"
COURSE_CTID = "ce-00000000-0000-4000-8000-000000000010"
PROGRAM_CTID = "ce-00000000-0000-4000-8000-000000000020"
COMP_A = "ce-00000000-0000-4000-8000-0000000000a1"
COMP_B = "ce-00000000-0000-4000-8000-0000000000b2"


def org(ctid, name):
    return {"@id": REG + ctid, "@type": "ceterms:CredentialOrganization",
            "ceterms:ctid": ctid, "ceterms:name": {"en-US": name}}


class FakeRegistry:
    """Just enough of RegistryClient for match_transcript."""

    orgs = [org(ORG_CTID, "Example Technical Community College"),
            org(TRANSFER_CTID, "University of Example-Springfield")]

    def org_search(self, term, match_type=None, take=40):
        t = term.lower()
        return [o for o in self.orgs
                if t in o["ceterms:name"]["en-US"].lower()
                or o["ceterms:name"]["en-US"].lower() in t]

    def courses_for_org(self, org_uri, org_ctid):
        if org_ctid != ORG_CTID:
            return []
        return [
            {"@id": REG + COURSE_CTID, "@type": "ceterms:Course",
             "ceterms:ctid": COURSE_CTID,
             "ceterms:name": {"en-US": "Expository Writing"},
             "ceterms:codedNotation": "ENG-111",
             "ceterms:teaches": [
                 {"ceterms:targetNode": REG + COMP_A,
                  "ceterms:targetNodeName": {"en-US": "Write a thesis-driven essay"},
                  "ceterms:frameworkName": {"en-US": "ENG Outcomes"}},
                 {"ceterms:targetNode": REG + COMP_B},   # URI-only, resolved later
             ]},
            {"@id": REG + PROGRAM_CTID, "@type": "ceterms:LearningProgram",
             "ceterms:ctid": PROGRAM_CTID,
             "ceterms:name": {"en-US": "Accounting and Finance - Accounting"}},
        ]

    def credentials_for_org(self, org_uri, org_ctid):
        return []

    def fetch_resource(self, uri):
        if uri.endswith(COMP_B):
            return {"ceasn:competencyText": {"en-US": "Cite sources correctly"}}
        return None


def run(clr, **kw):
    o = match_clr(clr, client=FakeRegistry(), environment="sandbox", **kw)
    return o.summary, o.results, o.report


def unsigned(doc):
    doc = copy.deepcopy(doc)
    for obj in [doc] + [vc for vc in doc["credentialSubject"]["verifiableCredential"]]:
        obj.pop("proof", None)
    return doc


class HelperTests(unittest.TestCase):
    def test_issuer_name(self):
        self.assertEqual(
            institution_from_issuer("Forsyth Technical Community College Office of the Registrar"),
            "Forsyth Technical Community College")
        self.assertEqual(institution_from_issuer("Indiana University Registrar"),
                         "Indiana University")
        self.assertEqual(institution_from_issuer("Some College"), "Some College")

    def test_course_code(self):
        self.assertEqual(clean_course_code("DRA-111**", {"**"}), "DRA-111")
        self.assertEqual(clean_course_code("ENG-111*U", set()), "ENG-111")
        self.assertEqual(split_course_code("DRA-111"), ("DRA", "111"))
        self.assertEqual(split_course_code("ENG W117"), ("ENG", "W117"))
        self.assertEqual(split_course_code("MATH1030"), ("MATH", "1030"))
        self.assertEqual(split_course_code("1A"), (None, "1A"))

    def test_default_output(self):
        self.assertEqual(default_output(Path("a/x.credential.json")), Path("a/x.matched.json"))
        self.assertEqual(default_output(Path("x.json")), Path("x.matched.json"))


class ClrTests(unittest.TestCase):
    def setUp(self):
        # The fixture is signed; the pipeline's input (parser output) isn't.
        self.signed = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.clr = unsigned(self.signed)

    def test_read(self):
        data = read_transcript(self.clr)
        self.assertEqual(data.issuing_institution, "Example Technical Community College")
        self.assertEqual(data.transfer_institutions, ["University of Example-Springfield"])
        by_title = {c.title: c for c in data.courses}
        self.assertEqual(by_title["Expository Writing"].subject_code, "ENG")
        self.assertEqual(by_title["Expository Writing"].course_number, "111")
        self.assertTrue(by_title["Theatre Appreciation"].is_transfer)
        # Only the major counts as a program; "Academic Coursework" is a grouping.
        self.assertEqual([d.name for d in data.degrees], ["Accounting and Finance-Accounting"])

    def test_rejects_non_clr(self):
        with self.assertRaises(ValueError):
            read_transcript({"type": ["VerifiableCredential"]})

    def test_annotate(self):
        clr = copy.deepcopy(self.clr)
        summary, _, report = run(clr)
        text = json.dumps(clr)

        self.assertNotIn("ext:ctidMatch", text)   # no non-spec properties
        self.assertEqual(summary["proofsRemoved"], 0)
        self.assertEqual(summary["courses"], {"total": 2, "matched": 1})
        self.assertEqual(summary["programsAndDegrees"]["matched"], 1)
        self.assertEqual(summary["competencies"], 2)

        ach = {a["name"]: a for a in clr["credentialSubject"]["achievement"]}
        eng = ach["Expository Writing"]
        self.assertNotIn("otherIdentifier", eng)
        self_al = [a for a in eng["alignment"] if a["targetCode"] == COURSE_CTID]
        self.assertEqual(self_al, [{
            "type": ["Alignment"], "targetType": "CTDL", "targetCode": COURSE_CTID,
            "targetName": "Expository Writing",
            "targetUrl": "https://credentialfinder.org/resources/" + COURSE_CTID,
            "targetDescription": "Additional information powered by the Credential Registry.",
            "targetFramework": "Credential Transparency Description Language"}])
        comps = [a for a in eng["alignment"] if a["targetType"] == "ceasn:Competency"]
        self.assertEqual({a["targetCode"] for a in comps}, {COMP_A, COMP_B})
        self.assertIn("Cite sources correctly", {a["targetName"] for a in comps})
        self.assertEqual({a["targetUrl"] for a in comps},
                         {"https://credentialfinder.org/resources/" + c for c in (COMP_A, COMP_B)})
        # Pre-existing note-code alignment is kept.
        self.assertTrue(any(a["targetType"] == "ext:CourseAttribute" for a in eng["alignment"]))

        # Program matched to a LearningProgram record -> CTDL target type.
        prog = ach["Accounting and Finance-Accounting"]
        self.assertEqual([(a["targetType"], a["targetCode"]) for a in prog["alignment"]],
                         [("CTDL", PROGRAM_CTID)])

        # Transfer course: unmatched (no catalog), so nothing about the course
        # is written, but it gets a creator Profile for the transfer school.
        thea = ach["Theatre Appreciation"]
        self.assertNotIn("alignment", thea)
        self.assertEqual(thea["creator"], {
            "id": REG + TRANSFER_CTID, "type": ["Profile"],
            "name": "University of Example-Springfield",
            "otherIdentifier": [{"type": "IdentifierEntry", "identifier": TRANSFER_CTID,
                                 "identifierType": "ext:CTID"}]})

        # Issuer Profile, at the root and inside nested credentials.
        self.assertEqual(clr["issuer"]["otherIdentifier"][0]["identifier"], ORG_CTID)
        for vc in clr["credentialSubject"]["verifiableCredential"]:
            self.assertEqual(vc["issuer"]["otherIdentifier"][0]["identifier"], ORG_CTID)
        # Nested copies are annotated too.
        nested = {vc["credentialSubject"]["achievement"]["name"]: vc["credentialSubject"]["achievement"]
                  for vc in clr["credentialSubject"]["verifiableCredential"]}
        self.assertEqual(nested["Expository Writing"]["alignment"], eng["alignment"])
        self.assertEqual(nested["Theatre Appreciation"]["creator"], thea["creator"])
        self.assertIsNot(nested["Theatre Appreciation"]["creator"], thea["creator"])

        # Confidence lives only in the separate report, misses included.
        by_q = {c["query"]: c for c in report["courses"]}
        self.assertEqual(by_q["ENG 111 Expository Writing"]["confidence"], "high")
        self.assertFalse(by_q["DRA 111 Theatre Appreciation"]["matched"])
        self.assertNotIn("ctid", by_q["DRA 111 Theatre Appreciation"])

    def test_credential_target_type(self):
        from transcript_ctid_matcher.clr import self_alignment
        from transcript_ctid_matcher.models import MatchResult, RegistryCandidate
        r = MatchResult(kind="credential", query_text="BA Math", matched=True,
                        confidence="high", best=RegistryCandidate(
                            uri=REG + PROGRAM_CTID, ctid=PROGRAM_CTID,
                            ctdl_type="ceterms:BachelorDegree", name="BA in Mathematics"))
        self.assertEqual(self_alignment(r)["targetType"], "ceterms:Credential")

    def test_misses_are_silent(self):
        clr = copy.deepcopy(self.clr)
        clr["issuer"]["name"] = "Nowhere College Office of the Registrar"
        before = copy.deepcopy(clr)
        summary, results, _ = run(clr)
        self.assertEqual(summary["courses"]["matched"], 0)
        self.assertFalse(results["organizations"][0].matched)
        self.assertNotIn("otherIdentifier", clr["issuer"])
        for a in clr["credentialSubject"]["achievement"]:
            orig = next(b for b in before["credentialSubject"]["achievement"] if b["id"] == a["id"])
            a.pop("creator", None)   # the transfer school still matches
            orig.pop("creator", None)
            self.assertEqual(a, orig, a["name"])

    def test_signed_input_rejected(self):
        clr = copy.deepcopy(self.signed)
        with self.assertRaises(SignedInputError):
            run(clr)
        self.assertEqual(clr, self.signed)   # untouched

    def test_strip_proofs(self):
        clr = copy.deepcopy(self.signed)
        summary, _, _ = run(clr, strip_proofs=True)
        self.assertEqual(summary["proofsRemoved"], 3)
        self.assertNotIn('"proof"', json.dumps(clr))

    def test_min_confidence(self):
        from transcript_ctid_matcher.models import MatchResult
        hi = MatchResult(kind="course", query_text="a", matched=True, confidence="high")
        med = MatchResult(kind="course", query_text="b", matched=True, confidence="medium")
        _apply_threshold([hi, med], "high")
        self.assertTrue(hi.matched)
        self.assertFalse(med.matched)
        with self.assertRaises(ValueError):
            run(copy.deepcopy(self.clr), min_confidence="low")

    def test_rerun_is_idempotent(self):
        clr = copy.deepcopy(self.clr)
        run(clr)
        once = copy.deepcopy(clr)
        run(clr)
        self.assertEqual(once, clr)


if __name__ == "__main__":
    unittest.main()
