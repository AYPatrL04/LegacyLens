from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens import AnalysisRequest, LegacyLensEngine


class EngineTests(unittest.TestCase):
    def test_engine_detects_language_and_renders_markdown(self) -> None:
        response = LegacyLensEngine().analyze(
            AnalysisRequest(
                code="      COMMON /A/ X, Y\n      GO TO 100\n",
                file_name="sample.f",
                cursor_line=1,
            )
        )
        self.assertEqual(response.language, "fortran")
        self.assertTrue(response.findings)
        self.assertIn("Legacy Lens", response.markdown)

    def test_engine_accepts_mapping_aliases(self) -> None:
        request = AnalysisRequest.from_mapping(
            {
                "code": "int flags = 0; flags = flags | 1;",
                "fileName": "sample.c",
                "cursorLine": "1",
                "maxFindings": "2",
            }
        )
        response = LegacyLensEngine().analyze(request)
        self.assertEqual(response.language, "c")
        self.assertLessEqual(len(response.findings), 2)

    def test_engine_maps_excerpt_lines_to_file_lines(self) -> None:
        response = LegacyLensEngine().analyze(
            AnalysisRequest(
                code="int flags = 0;\nflags = flags | 1;\n",
                file_name="sample.c",
                excerpt_start_line=40,
                cursor_line=41,
                context_scope="none",
            )
        )
        self.assertEqual(response.findings[0].span.start_line, 41)
        self.assertIn("第 41 行", response.markdown)


if __name__ == "__main__":
    unittest.main()
