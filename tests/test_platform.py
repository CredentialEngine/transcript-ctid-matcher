"""Cross-platform behavior: UTF-8 stdin/stdout under a legacy Windows code
page, byte-order marks, and the per-platform cache directory.

These run on any OS; CI also runs the whole suite on Windows.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import transcript_ctid_matcher.cli as cli
from transcript_ctid_matcher.config import cache_base

from test_clr import FIXTURE, FakeRegistry, unsigned

# Characters outside cp1252, so a strict cp1252 stream fails on them.
CJK_SCHOOL = "東京 University"
CJK_TITLE = "Théâtre Appreciation — 東"


def windows_like_stream(data: bytes = b"") -> io.TextIOWrapper:
    """A text stream that behaves like piped stdio on Windows Python <= 3.14:
    cp1252 with strict errors."""
    return io.TextIOWrapper(io.BytesIO(data), encoding="cp1252", errors="strict")


def non_ascii_clr() -> dict:
    clr = unsigned(json.loads(FIXTURE.read_text(encoding="utf-8")))
    for obj in [clr["credentialSubject"]] + [
            vc["credentialSubject"] for vc in clr["credentialSubject"]["verifiableCredential"]]:
        achs = obj["achievement"] if isinstance(obj["achievement"], list) else [obj["achievement"]]
        for a in achs:
            if a.get("name") == "Theatre Appreciation":
                a["name"] = CJK_TITLE
        if "Transfer From" in (obj.get("narrative") or ""):
            obj["narrative"] = f"Transfer From: {CJK_SCHOOL}; transfer"
    return clr


class CliEncodingTests(unittest.TestCase):
    def run_cli(self, argv, stdin_bytes=b""):
        stdin, stdout, stderr = (windows_like_stream(stdin_bytes),
                                 windows_like_stream(), windows_like_stream())
        with mock.patch.object(cli, "RegistryClient", lambda *a, **k: FakeRegistry()), \
                mock.patch.dict(os.environ, {"CE_REGISTRY_API_KEY": "test"}), \
                mock.patch.object(sys, "stdin", stdin), \
                mock.patch.object(sys, "stdout", stdout), \
                mock.patch.object(sys, "stderr", stderr):
            code = cli.main(argv)
            stdout.flush()
            stderr.flush()
        return code, stdout.buffer.getvalue(), stderr.buffer.getvalue()

    def test_pipe_round_trips_utf8_with_bom(self):
        body = json.dumps(non_ascii_clr(), ensure_ascii=False).encode("utf-8")
        code, out, err = self.run_cli(["-"], b"\xef\xbb\xbf" + body)
        self.assertEqual(code, 0, err.decode("cp1252", "replace"))
        result = json.loads(out.decode("utf-8"))
        names = [a["name"] for a in result["credentialSubject"]["achievement"]]
        self.assertIn(CJK_TITLE, names)
        self.assertIn("Wrote <stdout>", err.decode("cp1252", "replace"))
        # The unencodable school name was escaped on stderr, not a crash.
        self.assertIn(b"\\u6771", err)

    def test_file_with_bom(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "t.json", Path(d) / "out.json"
            src.write_bytes(b"\xef\xbb\xbf" + json.dumps(non_ascii_clr(), ensure_ascii=False)
                            .encode("utf-8"))
            code, _, err = self.run_cli([str(src), "-o", str(dst)])
            self.assertEqual(code, 0, err.decode("cp1252", "replace"))
            self.assertIn(CJK_TITLE, dst.read_text(encoding="utf-8"))

    def test_invalid_utf8_is_a_clean_error(self):
        code, out, err = self.run_cli(["-"], b'{"name": "\xff"}')
        self.assertEqual(code, 2)
        self.assertEqual(out, b"")
        self.assertIn(b"not valid UTF-8 JSON", err)


class CacheDirTests(unittest.TestCase):
    def test_platforms(self):
        home = "/home/u"
        self.assertEqual(cache_base("nt", "win32", {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"},
                                    home), r"C:\Users\u\AppData\Local")
        self.assertEqual(cache_base("posix", "darwin", {}, home),
                         os.path.join(home, "Library", "Caches"))
        self.assertEqual(cache_base("posix", "linux", {}, home), os.path.join(home, ".cache"))
        self.assertEqual(cache_base("nt", "win32", {"XDG_CACHE_HOME": "/x", "LOCALAPPDATA": "C:"},
                                    home), "/x")


if __name__ == "__main__":
    unittest.main()
