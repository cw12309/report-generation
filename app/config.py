import os
import yaml
import logging
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from dotenv import load_dotenv, set_key
from openai import AsyncOpenAI, OpenAI
from langsmith import wrappers

logger = logging.getLogger(__name__)

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_config_path = os.path.join(_project_root, "config.yml")
_env_path = os.path.join(_project_root, ".env")
load_dotenv(_env_path)

_config = {}

def _load():
    global _config
    if _config:
        return
    if os.path.exists(_config_path):
        with open(_config_path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f) or {}
    else:
        _config = {}

def reload_config():
    global _config
    _config = {}
    _load()

def _resolve(path: str, default=None):
    _load()
    keys = path.split(".")
    node = _config
    for k in keys:
        if isinstance(node, dict):
            node = node.get(k)
        else:
            return default
        if node is None:
            return default
    return node

def _get_active(section_key: str) -> dict:
    """获取模型列表中的激活项，兼容旧格式。
    
    新格式: llms / embeddings / reranks (数组，取 active_models 指定或 active=true 的)
    旧格式: llm / embedding / rerank (单个 dict)
    """
    _load()
    items = _config.get(section_key)
    if items is None:
        return {}
    if isinstance(items, list):
        # 优先使用 active_models 中指定的模型
        active_models = _config.get("active_models", {})
        # 映射: llms -> chat_llm, embeddings -> embedding, reranks -> rerank
        active_name_map = {"llms": "chat_llm", "embeddings": "embedding", "reranks": "rerank"}
        active_name = active_models.get(active_name_map.get(section_key, ""))
        if active_name:
            for item in items:
                if item.get("name") == active_name:
                    return item
        # 其次使用 active: true 的模型
        for item in items:
            if item.get("active"):
                return item
        if items:
            return items[0]
        return {}
    if isinstance(items, dict):
        return items
    return {}


def _get_active_rewrite() -> dict:
    """获取问答改写模型，从统一的 llms 列表中查找。
    
    优先 active_models.rewrite_llm 指定名称，其次 active: true。
    """
    _load()
    items = _config.get("llms")
    if items is None:
        return {}
    if isinstance(items, list):
        active_models = _config.get("active_models", {})
        rewrite_name = active_models.get("rewrite_llm")
        if rewrite_name:
            for item in items:
                if item.get("name") == rewrite_name:
                    return item
        for item in items:
            if item.get("active"):
                return item
        if items:
            return items[0]
        return {}
    if isinstance(items, dict):
        return items
    return {}


# ============================================================
# 便捷访问函数
# ============================================================

def get_llm_base_url() -> str:
    return normalize_openai_base_url(
        os.environ.get("CHAT_URL", _get_active("llms").get("base_url", ""))
    )

def get_llm_model() -> str:
    return os.environ.get("CHAT_MODEL", _get_active("llms").get("model", ""))

def get_llm_api_key() -> str:
    return os.environ.get("CHAT_API_KEY", "")

def get_llm_max_tokens() -> int:
    return _get_active("llms").get("max_tokens", 2048)

def get_llm_temperature() -> float:
    return _get_active("llms").get("temperature", 0.3)

def get_rewrite_base_url() -> str:
    return normalize_openai_base_url(
        os.environ.get(
            "REWRITE_URL", _get_active_rewrite().get("base_url", get_llm_base_url())
        )
    )

def get_rewrite_model() -> str:
    return os.environ.get("REWRITE_MODEL", _get_active_rewrite().get("model", get_llm_model()))

def get_rewrite_api_key() -> str:
    return os.environ.get("REWRITE_API_KEY", "") or get_llm_api_key()

def get_embed_base_url() -> str:
    return normalize_openai_base_url(
        os.environ.get("EMBED_URL", _get_active("embeddings").get("base_url", ""))
    )

def get_embed_model() -> str:
    return os.environ.get("EMBED_MODEL", _get_active("embeddings").get("model", ""))

def get_embed_api_key() -> str:
    return os.environ.get("EMBED_API_KEY", "")

def get_embed_dimension() -> int:
    return _get_active("embeddings").get("dimension", 1024)

def get_rerank_base_url() -> str:
    return os.environ.get("RERANK_URL", _get_active("reranks").get("base_url", ""))

def get_rerank_model() -> str:
    return os.environ.get("RERANK_MODEL", _get_active("reranks").get("model", ""))

def get_rerank_api_key() -> str:
    return os.environ.get("SILICONFLOW_API_KEY", "") or os.environ.get("RERANK_API_KEY", "")

def normalize_milvus_uri(uri: str) -> str:
    value = str(uri or "").strip()
    if not value:
        raise ValueError("Milvus 服务地址不能为空")
    if "\r" in value or "\n" in value:
        raise ValueError("Milvus 服务地址不能包含换行符")
    if value.lower().startswith(("http://", "https://")):
        return value.rstrip("/")
    return value

def sync_milvus_uri(uri: str, env_path: str | None = None) -> str:
    """Persist MILVUS_URI and update the current process immediately."""
    normalized = normalize_milvus_uri(uri)
    target_path = env_path or _env_path
    Path(target_path).touch(exist_ok=True)
    set_key(target_path, "MILVUS_URI", normalized, quote_mode="never")
    os.environ["MILVUS_URI"] = normalized
    return normalized

def get_db_path() -> str:
    milvus_uri = os.environ.get("MILVUS_URI", "") or _resolve("milvus.uri", "")
    if milvus_uri:
        return normalize_milvus_uri(milvus_uri)
    path = _resolve("retrieval.db_path", "./data_pipeline/milvus_lite.db")
    if not os.path.isabs(path):
        path = os.path.join(_project_root, path)
    return path

def get_milvus_token() -> str:
    return os.environ.get("MILVUS_TOKEN", "")

def get_collection_name() -> str:
    return _resolve("retrieval.collection_name", "report_slices")

def get_markdown_dir() -> str:
    path = _resolve("data.markdown_dir", "./app/data_pipeline/output_markdown")
    if not os.path.isabs(path):
        path = os.path.join(_project_root, path)
    return path

def get_retrieval_index_config() -> dict:
    return {
        "hnsw_m": _resolve("retrieval.hnsw_m", 16),
        "hnsw_ef_construction": _resolve("retrieval.hnsw_ef_construction", 256),
    }

def get_rag_top_k() -> int:
    return _resolve("retrieval.rag_top_k", 10)

def get_rerank_top_k() -> int:
    return _resolve("retrieval.rerank_top_k", 3)

def get_max_rounds() -> int:
    return _resolve("short_term_memory.max_rounds", 5)

def get_decay_factor() -> float:
    return _resolve("short_term_memory.decay_factor", 0.9)

def get_server_port() -> int:
    return _resolve("server.port", 8000)

def get_server_host() -> str:
    return _resolve("server.host", "0.0.0.0")

def get_metadata_path() -> str:
    path = _resolve("paths.metadata", "./app/data_pipeline/report_template/metadata.json")
    if not os.path.isabs(path):
        path = os.path.join(_project_root, path)
    return path


# ============================================================
# OpenAI SDK Client 工厂函数
# ============================================================

_LEGACY_OPENAI_ENDPOINT_SUFFIXES = ("/chat/completions", "/embeddings")


def normalize_openai_base_url(base_url: str) -> str:
    """Return an OpenAI-compatible base URL without a resource endpoint.

    Older configurations may contain ``/chat/completions`` or ``/embeddings``.
    The OpenAI SDK appends those resource paths itself, so keeping them in the
    configured value would produce duplicated paths such as
    ``/v1/embeddings/embeddings``.
    """
    value = (base_url or "").strip()
    if not value:
        return ""

    parts = urlsplit(value)
    path = parts.path.rstrip("/")
    while True:
        lowered_path = path.lower()
        matched_suffix = next(
            (
                suffix
                for suffix in _LEGACY_OPENAI_ENDPOINT_SUFFIXES
                if lowered_path.endswith(suffix)
            ),
            None,
        )
        if not matched_suffix:
            break
        path = path[: -len(matched_suffix)].rstrip("/")

    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def normalize_openai_model_urls(config_data: dict) -> dict:
    """Normalize Chat and Embedding model URLs in a loaded/saved config object."""
    if not isinstance(config_data, dict):
        return config_data
    for section_key in ("llms", "embeddings"):
        models = config_data.get(section_key)
        if isinstance(models, dict):
            models = [models]
        if not isinstance(models, list):
            continue
        for model in models:
            if isinstance(model, dict) and "base_url" in model:
                model["base_url"] = normalize_openai_base_url(model.get("base_url", ""))
    return config_data


def _normalize_base_url(base_url: str, path_suffix: str) -> str:
    """标准化 base_url，支持前端只填到 /v1，后端自动补全路径（用于 requests 调用）。
    
    注意：OpenAI SDK 不需要调用此函数，SDK 会自动拼接路径。
    此函数仅用于 requests 直接调用的场景（如 rerank）。
    
    规则：
    - 如果 URL 已包含 path_suffix，直接返回
    - 如果 URL 以 /v1 结尾但没有 path_suffix，自动补全
    - 其他情况直接拼接
    """
    base_url = base_url.rstrip("/")
    if path_suffix in base_url:
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}{path_suffix}"
    return f"{base_url}{path_suffix}"


def create_openai_client(base_url: str, api_key: str = "", *, asynchronous: bool = False):
    """Create a traced OpenAI-compatible client with Base URL normalization."""
    client_class = AsyncOpenAI if asynchronous else OpenAI
    client = client_class(
        base_url=normalize_openai_base_url(base_url),
        api_key=api_key or "not-needed",
    )
    return wrappers.wrap_openai(client)


def create_llm_client(base_url: str, api_key: str = "") -> AsyncOpenAI:
    return create_openai_client(base_url, api_key, asynchronous=True)


def create_embed_client(base_url: str, api_key: str = "") -> OpenAI:
    return create_openai_client(base_url, api_key)


def get_llm_client() -> AsyncOpenAI:
    """动态获取 LLM 的 AsyncOpenAI 客户端（每次读取最新配置）。

    注意：OpenAI SDK 会自动在 base_url 后拼接 /chat/completions，
    所以 base_url 只需填到 /v1 即可。
    """
    base_url = get_llm_base_url()
    logger.debug(f"[get_llm_client] base_url={base_url} (SDK 会自动拼接 /chat/completions)")
    return create_llm_client(base_url, get_llm_api_key())


def get_embed_client() -> OpenAI:
    """动态获取 Embedding 的 OpenAI 客户端（同步，因为 Embedding 调用量小且简单）。
    
    注意：OpenAI SDK 会自动在 base_url 后拼接 /embeddings，
    所以 base_url 只需填到 /v1 即可。
    """
    base_url = get_embed_base_url()
    logger.debug(f"[get_embed_client] base_url={base_url} (SDK 会自动拼接 /embeddings)")
    return create_embed_client(base_url, get_embed_api_key())


def get_rewrite_client() -> OpenAI:
    """Create the synchronous client shared by RAG rewrite and planning."""
    return create_openai_client(get_rewrite_base_url(), get_rewrite_api_key())
