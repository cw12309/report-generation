import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


APP_DIR = Path(__file__).resolve().parents[1]
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _install_dependency_stubs() -> None:
    try:
        __import__("openai")
    except ModuleNotFoundError:
        openai = types.ModuleType("openai")
        class _OpenAIError(Exception):
            pass

        openai.AsyncOpenAI = object
        openai.OpenAI = object
        openai.APIConnectionError = _OpenAIError
        openai.APITimeoutError = _OpenAIError
        openai.APIStatusError = _OpenAIError
        sys.modules["openai"] = openai

    try:
        __import__("langsmith")
    except ModuleNotFoundError:
        langsmith = types.ModuleType("langsmith")
        langsmith.traceable = lambda function=None, **_kwargs: (
            function if function is not None else lambda wrapped: wrapped
        )
        langsmith.wrappers = types.SimpleNamespace(wrap_openai=lambda client: client)
        sys.modules["langsmith"] = langsmith


_install_dependency_stubs()

import config


class _EmbeddingResource:
    def __init__(self, client):
        self._client = client

    def create(self, **_kwargs):
        self._client.request_url = f"{self._client.base_url.rstrip('/')}/embeddings"
        return types.SimpleNamespace(
            data=[types.SimpleNamespace(index=0, embedding=[0.1, 0.2])]
        )


class _CapturingOpenAI:
    def __init__(self, *, base_url, api_key):
        self.base_url = str(base_url)
        self.api_key = api_key
        self.request_url = ""
        self.embeddings = _EmbeddingResource(self)


class ModelBaseUrlTests(unittest.TestCase):
    def test_legacy_resource_urls_are_normalized_to_base_url(self):
        self.assertEqual(
            config.normalize_openai_base_url(
                "http://localhost:8001/v1/chat/completions/"
            ),
            "http://localhost:8001/v1",
        )
        self.assertEqual(
            config.normalize_openai_base_url(
                "http://localhost:8001/v1/embeddings/embeddings"
            ),
            "http://localhost:8001/v1",
        )

    def test_config_sections_are_normalized_without_changing_rerank(self):
        data = {
            "llms": [{"base_url": "http://chat:8000/v1/chat/completions"}],
            "embeddings": [{"base_url": "http://embed:8000/v1/embeddings"}],
            "reranks": [{"base_url": "http://rerank:8000/v1/rerank"}],
        }

        normalized = config.normalize_openai_model_urls(data)

        self.assertEqual(normalized["llms"][0]["base_url"], "http://chat:8000/v1")
        self.assertEqual(
            normalized["embeddings"][0]["base_url"], "http://embed:8000/v1"
        )
        self.assertEqual(
            normalized["reranks"][0]["base_url"],
            "http://rerank:8000/v1/rerank",
        )

    def test_embedding_client_sends_single_embeddings_endpoint(self):
        with (
            patch.object(config, "OpenAI", _CapturingOpenAI),
            patch.object(config.wrappers, "wrap_openai", side_effect=lambda client: client),
        ):
            client = config.create_embed_client(
                "http://localhost:8001/v1/embeddings", "test-key"
            )
            client.embeddings.create(model="test-embedding", input="hello")

        self.assertEqual(client.base_url, "http://localhost:8001/v1")
        self.assertEqual(
            client.request_url, "http://localhost:8001/v1/embeddings"
        )
        self.assertNotIn("/embeddings/embeddings", client.request_url)

    def test_environment_legacy_url_is_normalized_by_getter(self):
        with patch.dict(
            os.environ,
            {"EMBED_URL": "http://localhost:8001/v1/embeddings"},
        ):
            self.assertEqual(
                config.get_embed_base_url(), "http://localhost:8001/v1"
            )

    def test_milvus_uri_sync_updates_env_file_and_current_process(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("CHAT_MODEL=test-chat\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                previous_uri = os.environ.pop("MILVUS_URI", None)
                try:
                    synced = config.sync_milvus_uri(
                        "http://milvus.example:19530/",
                        str(env_path),
                    )
                    self.assertEqual(synced, "http://milvus.example:19530")
                    self.assertEqual(
                        os.environ["MILVUS_URI"],
                        "http://milvus.example:19530",
                    )
                    self.assertIn(
                        "MILVUS_URI=http://milvus.example:19530",
                        env_path.read_text(encoding="utf-8"),
                    )
                finally:
                    if previous_uri is None:
                        os.environ.pop("MILVUS_URI", None)
                    else:
                        os.environ["MILVUS_URI"] = previous_uri

    def test_milvus_uri_rejects_newlines(self):
        with self.assertRaisesRegex(ValueError, "不能包含换行符"):
            config.normalize_milvus_uri("http://milvus:19530\nMALICIOUS=value")


if __name__ == "__main__":
    unittest.main()
