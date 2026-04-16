from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens import AnalysisRequest, LegacyLensEngine
from legacylens.language import detect_language


class LanguageSupportTests(unittest.TestCase):
    def test_mainstream_suffixes_are_detected(self) -> None:
        cases = {
            "script.py": "python",
            "Main.java": "java",
            "server.go": "go",
            "Controller.cs": "csharp",
            "lib.rs": "rust",
            "analysis.R": "r",
            "app.tsx": "typescript",
            "query.sql": "sql",
            "Dockerfile": "dockerfile",
        }
        for file_name, expected in cases.items():
            with self.subTest(file_name=file_name):
                self.assertEqual(detect_language("", file_name=file_name), expected)

    def test_mainstream_analyzer_handles_new_language(self) -> None:
        response = LegacyLensEngine().analyze(
            AnalysisRequest(
                code="def load_users(path):\n    with open(path) as handle:\n        return handle.read()\n",
                file_name="users.py",
                cursor_line=2,
                context_scope="none",
            )
        )
        self.assertEqual(response.language, "python")
        self.assertTrue(any(finding.rule_id == "python.file-io" for finding in response.findings))


if __name__ == "__main__":
    unittest.main()
