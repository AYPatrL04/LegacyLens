from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from legacylens.llm import (
    ApiClient,
    Explainer,
    client_from_configuration,
    load_llm_config,
    normalize_ollama_host,
    select_preferred_model,
)
from legacylens.models import AnalysisRequest, Finding, Severity, SourceSpan


class FakeLineHallucinationClient:
    provider = "fake"
    model = "fake"
    host = "fake"

    def generate(self, prompt: str) -> str:
        return "第 999 行会打开文件，但这个行号并不存在。"


class FakeUnsupportedExistingLineClient:
    provider = "fake"
    model = "fake"
    host = "fake"

    def generate(self, prompt: str) -> str:
        return "第 10 行会打开文件，但实际打开文件的是第 11 行。"


class FakeSuccessClient:
    provider = "fake"
    model = "fake-success"
    host = "https://api.example.test/v1/chat/completions?api_key=secret"

    def generate(self, prompt: str) -> str:
        return "ok"


class FakeFailureClient:
    provider = "fake"
    model = "fake-failure"
    host = "fake"

    def generate(self, prompt: str) -> str:
        raise OSError("network down")


class FakeHttpResponse:
    def __init__(self, payload: dict | None = None, lines: list[bytes] | None = None) -> None:
        self.payload = payload or {}
        self.lines = lines or []

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __iter__(self):
        return iter(self.lines)


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

    def test_config_file_can_select_api_provider_without_required_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "legacylens.json"
            config_path.write_text(
                json.dumps(
                    {
                        "llm": {
                            "mode": "api",
                            "api": {
                                "baseUrl": "https://api.example.test/v1",
                                "apiKeyEnv": "LEGACYLENS_TEST_KEY",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                "os.environ",
                {"LEGACYLENS_CONFIG": str(config_path), "LEGACYLENS_TEST_KEY": "secret"},
                clear=True,
            ):
                config = load_llm_config()
                client = client_from_configuration(config)

        self.assertEqual(config.mode, "api")
        self.assertEqual(config.api_key, "secret")
        self.assertEqual(config.api_model, None)
        self.assertIsInstance(client, ApiClient)
        self.assertEqual(client.model, None)

    def test_api_client_omits_model_when_not_configured_and_uses_api_key(self) -> None:
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["headers"] = {key.lower(): value for key, value in request.header_items()}
            return FakeHttpResponse({"choices": [{"message": {"content": "ok"}}]})

        client = ApiClient(url="https://api.example.test/v1/chat/completions", api_key="secret", model=None)
        with patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(client.generate("prompt"), "ok")

        self.assertEqual(captured["url"], "https://api.example.test/v1/chat/completions")
        self.assertNotIn("model", captured["payload"])
        self.assertEqual(captured["headers"]["authorization"], "Bearer secret")

    def test_api_client_streams_openai_compatible_events(self) -> None:
        def fake_urlopen(request, timeout):
            return FakeHttpResponse(
                lines=[
                    b'data: {"choices":[{"delta":{"content":"hello"}}]}\n',
                    b'data: {"choices":[{"delta":{"content":" world"}}]}\n',
                    b"data: [DONE]\n",
                ]
            )

        client = ApiClient(url="https://api.example.test/v1/chat/completions", api_key="secret", model="remote-model")
        with patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(list(client.generate_stream("prompt")), ["hello", " world"])

    def test_explainer_appends_warning_for_invalid_generated_line_number(self) -> None:
        request, finding = _request_and_finding()
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
        request, finding = _request_and_finding()
        response = Explainer(client=FakeUnsupportedExistingLineClient()).explain(
            request,
            language="python",
            findings=[finding],
            facts=[],
            context=None,
        )
        self.assertIn("行号校验", response.markdown)
        self.assertIn("10", response.markdown)

    def test_explainer_logs_model_call_success_without_leaking_api_key(self) -> None:
        request, finding = _request_and_finding()
        with self.assertLogs("legacylens.llm", level="INFO") as captured:
            response = Explainer(client=FakeSuccessClient()).explain(
                request,
                language="python",
                findings=[finding],
                facts=[],
                context=None,
            )

        logs = "\n".join(captured.output)
        self.assertEqual(response.markdown, "ok")
        self.assertIn("llm call start", logs)
        self.assertIn("llm call success", logs)
        self.assertIn("provider=fake", logs)
        self.assertIn("model=fake-success", logs)
        self.assertNotIn("api_key=secret", logs)
        self.assertIn("api_key=<redacted>", logs)

    def test_explainer_logs_failure_and_fallback(self) -> None:
        request, finding = _request_and_finding()
        with self.assertLogs("legacylens.llm", level="WARNING") as captured:
            response = Explainer(client=FakeFailureClient()).explain(
                request,
                language="python",
                findings=[finding],
                facts=[],
                context=None,
            )

        logs = "\n".join(captured.output)
        self.assertIn("fake unavailable", response.fallback_reason)
        self.assertIn("llm call failed", logs)
        self.assertIn("llm fallback", logs)
        self.assertIn("network down", logs)


def _request_and_finding() -> tuple[AnalysisRequest, Finding]:
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
    return request, finding


if __name__ == "__main__":
    unittest.main()
