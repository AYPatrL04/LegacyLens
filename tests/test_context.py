from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.context import build_project_context, prewarm_project_context
from legacylens.models import AnalysisRequest


class ContextTests(unittest.TestCase):
    def test_directory_context_collects_related_files_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "flags.c"
            source.write_text("int main(void) { return FLAG_ACTIVE; }\n", encoding="utf-8")
            sibling = root / "flags.h"
            sibling.write_text("#define FLAG_ACTIVE 1\n", encoding="utf-8")

            context = build_project_context(
                AnalysisRequest(
                    code="return FLAG_ACTIVE;",
                    file_name=str(source),
                    cursor_line=1,
                    context_scope="directory",
                ),
                language="c",
            )

            self.assertIsNotNone(context)
            assert context is not None
            self.assertIn("flags.h", context.files)
            self.assertIn("flags.h", context.related_files)
            self.assertTrue(any(reference["symbol"] == "FLAG_ACTIVE" for reference in context.symbol_references))

    def test_prewarm_project_context_primes_cache_for_later_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "worker.py"
            source.write_text("def load_value():\n    return helper_value()\n", encoding="utf-8")
            helper = root / "helpers.py"
            helper.write_text("def helper_value():\n    return 1\n", encoding="utf-8")

            self.assertTrue(prewarm_project_context(root))

            context = build_project_context(
                AnalysisRequest(
                    code="return helper_value()",
                    file_name=str(source),
                    cursor_line=2,
                    context_scope="project",
                    project_root=str(root),
                ),
                language="python",
            )

            self.assertIsNotNone(context)
            assert context is not None
            self.assertIn("helpers.py", context.files)
            self.assertTrue(any(reference["symbol"] == "helper_value" for reference in context.symbol_references))


if __name__ == "__main__":
    unittest.main()
