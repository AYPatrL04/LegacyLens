from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens import AnalysisRequest, LegacyLensEngine


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        missing_config = Path(self._tmp.name) / "missing.json"
        self._env = patch.dict("os.environ", {"LEGACYLENS_CONFIG": str(missing_config)}, clear=False)
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

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
                "outputLanguage": "zh-CN",
            }
        )
        self.assertEqual(request.output_language, "zh-CN")
        response = LegacyLensEngine().analyze(request)
        self.assertEqual(response.language, "c")
        self.assertEqual(response.output_language, "zh-Hans")
        self.assertLessEqual(len(response.findings), 2)

    def test_engine_maps_excerpt_lines_to_file_lines(self) -> None:
        response = LegacyLensEngine().analyze(
            AnalysisRequest(
                code="int flags = 0;\nflags = flags | 1;\n",
                file_name="sample.c",
                excerpt_start_line=40,
                cursor_line=41,
                context_scope="none",
                output_language="en",
            )
        )
        self.assertEqual(response.findings[0].span.start_line, 41)
        self.assertEqual(response.output_language, "en")
        self.assertIn("Line 41", response.markdown)


if __name__ == "__main__":
    unittest.main()
