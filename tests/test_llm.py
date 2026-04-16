from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.llm import Explainer, normalize_ollama_host, select_preferred_model
from legacylens.models import AnalysisRequest, Fact, Finding, Severity, SourceSpan


class FakeLineHallucinationClient:
    model = "fake"

    def generate(self, prompt: str) -> str:
        return "第999行会打开文件，但这个行号并不存在。"


class FakeUnsupportedExistingLineClient:
    model = "fake"

    def generate(self, prompt: str) -> str:
        return "第10行会打开文件，但实际打开文件的是第11行。"


class LlmTests(unittest.TestCase):
    def test_select_preferred_model_uses_code_friendly_model(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(select_preferred_model(["llama3:8b", "qwen3.5:9b"]), "qwen3.5:9b")

    def test_select_preferred_model_falls_back_to_first_model(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(select_preferred_model(["custom:latest", "other:latest"]), "custom:latest")

    def test_normalize_ollama_host_accepts_port_only_environment_value(self) -> None:
        self.assertEqual(normalize_ollama_host(":11434"), "http://127.0.0.1:11434")
        self.assertEqual(normalize_ollama_host("127.0.0.1:11434"), "http://127.0.0.1:11434")

    def test_explainer_appends_warning_for_invalid_generated_line_number(self) -> None:
        request = AnalysisRequest(
            code="def load(path):\n    return open(path).read()\n",
            language="python",
            excerpt_start_line=10,
            cursor_line=11,
            use_llm=True,
        )
        finding = Finding(
            rule_id="test.io",
            language="python",
            title="IO",
            severity=Severity.MEDIUM,
            span=SourceSpan(start_line=11, end_line=11, text="    return open(path).read()"),
            rationale="访问文件。",
            historical_context="",
        )
        response = Explainer(client=FakeLineHallucinationClient()).explain(
            request,
            language="python",
            findings=[finding],
            facts=[],
            context=None,
        )
        self.assertIn("行号校验", response.markdown)
        self.assertIn("999", response.markdown)

    def test_explainer_warns_when_existing_line_is_not_evidence_line(self) -> None:
        request = AnalysisRequest(
            code="def load(path):\n    return open(path).read()\n",
            language="python",
            excerpt_start_line=10,
            cursor_line=11,
            use_llm=True,
        )
        finding = Finding(
            rule_id="test.io",
            language="python",
            title="IO",
            severity=Severity.MEDIUM,
            span=SourceSpan(start_line=11, end_line=11, text="    return open(path).read()"),
            rationale="访问文件。",
            historical_context="",
        )
        response = Explainer(client=FakeUnsupportedExistingLineClient()).explain(
            request,
            language="python",
            findings=[finding],
            facts=[],
            context=None,
        )
        self.assertIn("行号校验", response.markdown)
        self.assertIn("10", response.markdown)


if __name__ == "__main__":
    unittest.main()
