"""查询改写模块。

当用户输入过短或过于模糊时，使用 LLM 将查询扩展为更具体的描述，
提升多路召回的精准度。

对于过于模糊的输入（如仅输入"CT"，没有部位和诊断），返回追问提示，
引导用户补充信息。

改写前会自动标准化术语（如"CT头部"→"CT头颅"），确保与数据库中的
检查类型、部位、检查项目一致。标准术语来自 metadata.json
（由 extract_metadata.py 从 xlsx 模板提取）。

用法：
    from query_rewrite import rewrite_query, needs_rewrite, is_too_vague, get_clarification

    if is_too_vague(user_input):
        reply = get_clarification(user_input)
    elif needs_rewrite(user_input):
        rewritten = rewrite_query(user_input)
    else:
        rewritten = user_input

触发条件：
- is_too_vague: 查询仅有检查类型，缺少部位和诊断（如"CT"、"MR"）
- needs_rewrite: 查询有类型+部位但无诊断（如"CT头颅"、"MR膝关节"）
"""
import json
import logging
import os
import re

from config import (
    create_openai_client,
    get_rewrite_base_url,
    get_rewrite_model,
    get_rewrite_api_key,
    get_metadata_path,
)
from prompt import load_prompt

logger = logging.getLogger(__name__)

METADATA_PATH = get_metadata_path()


def _load_metadata():
    """加载 metadata.json 中的标准术语。

    Returns:
        dict: 包含 检查类型, 部位, 检查项目, 诊断结论 四个列表
    """
    if os.path.exists(METADATA_PATH):
        with open(METADATA_PATH, "r", encoding="utf-8") as f:
            metadata = json.load(f)
            if isinstance(metadata.get("检查类型"), dict):
                return _flatten_metadata_tree(metadata)
            return metadata
    return {}


def _flatten_metadata_tree(metadata):
    """Convert hierarchical metadata into the flat term lists used here."""
    flattened = {
        "检查类型": sorted(metadata.get("检查类型", {}).keys()),
        "部位": [],
        "检查项目": [],
        "诊断结论": [],
    }
    collected = {
        "一级目录": set(),
        "二级目录": set(),
        "三级目录": set(),
        "四级目录": set(),
    }

    def visit(node):
        if isinstance(node, dict):
            for key, child in node.items():
                if key in collected:
                    if isinstance(child, dict):
                        collected[key].update(child.keys())
                    elif isinstance(child, list):
                        collected[key].update(child)
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(metadata)
    flattened["部位"] = sorted(collected["一级目录"])
    flattened["检查项目"] = sorted(
        collected["二级目录"]
        | collected["三级目录"]
        | collected["四级目录"]
    )
    return flattened


_METADATA = _load_metadata()

KNOWN_TYPES = set(_METADATA.get("检查类型", [])) or {"CT", "MR", "DR", "MRI", "X光", "X线"}

KNOWN_PARTS = set(_METADATA.get("部位", [])) or {
    "头颅", "头颈部", "胸部", "腹部", "盆腔", "脊柱",
    "四肢及关节", "血管", "颈椎", "腰椎", "胸椎",
}

_DEFAULT_KNOWN_TYPES = {"CT", "MR", "DR", "MRI", "X光", "X线"}
_DEFAULT_KNOWN_PARTS = {
    "头颅", "头颈部", "胸部", "腹部", "盆腔", "脊柱",
    "四肢及关节", "血管", "颈椎", "腰椎", "胸椎",
}


def reload_metadata():
    """重新加载 metadata.json 并刷新模块级术语集合。

    在上传新的 xlsx 报告模板后调用，确保查询改写模块
    能识别新入库的检查类型、部位、检查项目、诊断结论。
    """
    global _METADATA, KNOWN_TYPES, KNOWN_PARTS
    _METADATA = _load_metadata()
    KNOWN_TYPES = set(_METADATA.get("检查类型", [])) or _DEFAULT_KNOWN_TYPES
    KNOWN_PARTS = set(_METADATA.get("部位", [])) or _DEFAULT_KNOWN_PARTS

PART_ALIASES = {
    "头": "头颅",
    "脑": "头颅",
    "颅脑": "头颅",
    "头部": "头颅",
    "颈": "头颈部",
    "颈部": "头颈部",
    "胸": "胸部",
    "胸廓": "胸部",
    "腹": "腹部",
    "盆腔": "盆腔",
    "脊柱": "脊柱",
    "脊椎": "脊柱",
    "四肢": "四肢及关节",
    "关节": "四肢及关节",
    "血管": "血管",
}

CLARIFICATION_TEMPLATES = {
    "CT": "您想查询哪个部位的CT检查？例如：\n• CT头颅（脑出血、脑梗死等）\n• CT胸部（肺气肿、肺炎等）\n• CT腹部（肝脏、肾脏等）\n• CT血管（CTA相关）",
    "MR": "您想查询哪个部位的MR检查？例如：\n• MR头颅（脑肿瘤、脑梗死等）\n• MR膝关节（半月板、韧带等）\n• MR脊柱（椎间盘突出等）",
    "DR": "您想查询哪个部位的DR检查？例如：\n• DR胸部（肺部感染等）\n• DR四肢及关节（骨折等）\n• DR脊柱（骨质增生等）",
    "MRI": "您想查询哪个部位的MRI检查？例如：\n• MRI头颅（脑肿瘤等）\n• MRI脊柱（椎间盘等）",
    "X光": "您想查询哪个部位的X光检查？例如：\n• X光胸部\n• X光四肢及关节",
    "X线": "您想查询哪个部位的X线检查？例如：\n• X线胸部\n• X线四肢及关节",
}


def parse_query_keywords(query):
    """从用户输入中提取结构化关键词。

    解析检查类型、部位，并将剩余文本作为诊断关键词。
    供多路召回的元数据过滤和关键词检索使用。

    Args:
        query: 用户输入的查询文本（建议先经过 standardize_query 标准化）

    Returns:
        dict: 包含 检查类型, 部位, 诊断关键词 等字段
    """
    check_type = ""
    for t in KNOWN_TYPES:
        if t in query:
            check_type = t
            break
    if "MRI" in query and not check_type:
        check_type = "MR"

    part = ""
    for alias, standard in PART_ALIASES.items():
        if alias in query and _is_standalone_part(query, alias):
            part = standard
            break
    if not part:
        for p in KNOWN_PARTS:
            if p in query:
                part = p
                break

    cleaned = query
    for t in KNOWN_TYPES:
        cleaned = cleaned.replace(t, "")
    if part:
        for alias, standard in PART_ALIASES.items():
            if standard == part and alias in cleaned:
                cleaned = cleaned.replace(alias, "")
        cleaned = cleaned.replace(part, "")
    cleaned = cleaned.strip()

    diag_keywords = [cleaned] if cleaned and len(cleaned) >= 2 else []

    return {"检查类型": check_type, "部位": part, "诊断关键词": diag_keywords}


def is_too_vague(query):
    """判断查询是否过于模糊，需要追问用户。

    当查询仅有检查类型，缺少部位和诊断时返回 True。
    例如："CT"、"MR" → True
         "CT头颅"、"脑出血" → False

    Args:
        query: 用户输入的查询文本

    Returns:
        bool: True 表示查询过于模糊，应追问用户
    """
    components = parse_query_keywords(query)
    has_type = bool(components["检查类型"])
    has_part = bool(components["部位"])
    has_diag = bool(components["诊断关键词"])

    if has_type and not has_part and not has_diag:
        return True

    if not has_type and not has_part and not has_diag:
        return True

    return False


def get_clarification(query):
    """根据模糊查询生成追问提示。

    Args:
        query: 用户输入的查询文本

    Returns:
        str: 追问提示文本，引导用户补充部位或诊断信息
    """
    components = parse_query_keywords(query)
    check_type = components["检查类型"]

    if check_type and check_type in CLARIFICATION_TEMPLATES:
        return CLARIFICATION_TEMPLATES[check_type]

    if not check_type and not components["部位"]:
        return "请提供更具体的检查信息，例如：\n• CT头颅脑出血\n• MR膝关节半月板损伤\n• DR胸部肺部感染"

    return "请补充检查部位或诊断信息，例如：CT头颅、MR膝关节等。"


REWRITE_SYSTEM_PROMPT = load_prompt("rag_prompt/rewrite")


def _build_rewrite_prompt(keywords=None):
    """构建包含数据库元数据的改写提示词。

    Args:
        keywords: parse_query_keywords 返回的关键词字典，
                  用于按检查类型+部位过滤诊断结论，减少 token 消耗。
                  为 None 时注入全量诊断结论。
    """
    check_types = "、".join(_METADATA.get("检查类型", list(KNOWN_TYPES)))
    parts = "、".join(_METADATA.get("部位", list(KNOWN_PARTS)))
    projects = "、".join(_METADATA.get("检查项目", []))

    all_diagnoses = _METADATA.get("诊断结论", [])
    if keywords and all_diagnoses:
        check_type = keywords.get("检查类型", "")
        part = keywords.get("部位", "")
        filtered = []
        for d in all_diagnoses:
            if check_type and check_type in d:
                filtered.append(d)
            elif part and part in d:
                filtered.append(d)
        diagnoses = "、".join(filtered) if filtered else "、".join(all_diagnoses[:200])
    else:
        diagnoses = "、".join(all_diagnoses[:200])

    return REWRITE_SYSTEM_PROMPT.format(
        check_types=check_types,
        parts=parts,
        projects=projects,
        diagnoses=diagnoses,
    )


def standardize_query(query):
    """将用户输入中的非标准术语替换为数据库标准术语。

    仅替换独立的部位别名词，不会拆分已有的完整术语。
    例如："CT头部" → "CT头颅"（"头部"是独立的部位词）
          "CT脑出血" → "CT脑出血"（"脑出血"是完整术语，不替换"脑"）
          "MR膝关节" → "MR膝关节"（"膝关节"是标准检查项目，不替换"关节"）

    Args:
        query: 用户输入的查询文本

    Returns:
        str: 标准化后的查询文本
    """
    for alias, standard in sorted(PART_ALIASES.items(), key=lambda x: -len(x[0])):
        if alias == standard:
            continue
        if alias not in query:
            continue
        if _is_standalone_part(query, alias):
            query = query.replace(alias, standard)
            break
    # 中英文/数字之间加空格: "CT脑出血" → "CT 脑出血"
    query = re.sub(r"([a-zA-Z0-9]+)([\u4e00-\u9fff])", r"\1 \2", query)
    query = re.sub(r"([\u4e00-\u9fff])([a-zA-Z0-9]+)", r"\1 \2", query)
    return query


def _is_standalone_part(query, alias):
    """判断别名是否作为独立的部位词出现在查询中。

    判断规则：
    1. 别名后面不能紧跟非分隔符字符（如"脑"后面跟"出血"则不是独立的）
    2. 别名不能是某个已知检查项目或部位的子串

    "头部" 在 "CT头部" 中是独立的 → True
    "脑" 在 "CT脑出血" 中不是独立的（后面紧跟"出血"）→ False
    "关节" 在 "MR膝关节" 中不是独立的（"膝关节"是已知检查项目）→ False
    "头" 在 "CT头颅" 中不是独立的（后面紧跟"颅"）→ False

    Args:
        query: 完整查询文本
        alias: 待检测的别名

    Returns:
        bool: True 表示别名是独立的部位词
    """
    idx = query.index(alias)
    after = query[idx + len(alias):]

    if after and after[0] not in (" ", "　", "，", "、", "。", ",", "."):
        return False

    known_terms = _METADATA.get("检查项目", []) + _METADATA.get("部位", [])
    for term in known_terms:
        if alias in term and term in query and alias != term:
            return False

    return True


def needs_rewrite(query):
    """判断查询是否需要改写。

    Args:
        query: 用户输入的查询文本

    Returns:
        bool: True 表示需要改写
    """
    if is_too_vague(query):
        return False

    components = parse_query_keywords(query)
    has_type = bool(components["检查类型"])
    has_part = bool(components["部位"])
    has_diag = bool(components["诊断关键词"])

    if (has_type or has_part) and not has_diag:
        return True

    return False


def rewrite_query(query, chat_url=None, chat_model=None, timeout=10):
    """使用 LLM 改写查询。

    改写前会先标准化术语（如"CT头部"→"CT头颅"），确保与数据库术语一致。

    Args:
        query: 用户输入的查询文本
        chat_url: LLM API 地址（默认从环境变量读取）
        chat_model: LLM 模型名（默认从环境变量读取）
        timeout: 超时时间（秒）

    Returns:
        str: 改写后的查询文本。如果改写失败，返回标准化后的查询。
    """
    standardized = standardize_query(query)
    url = chat_url or get_rewrite_base_url()
    model = chat_model or get_rewrite_model()

    keywords = parse_query_keywords(standardized)
    system_prompt = _build_rewrite_prompt(keywords)

    request_params = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": standardized},
        ],
        "max_tokens": 128,
        "temperature": 0.3,
        "stream": False,
    }
    try:
        client = create_openai_client(url, get_rewrite_api_key())
        response = client.chat.completions.create(**request_params, timeout=timeout)
        rewritten = (response.choices[0].message.content or "").strip()
        if rewritten and len(rewritten) >= len(standardized):
            return rewritten
        return standardized
    except Exception as exc:
        logger.warning(
            "Query Rewrite 请求失败，已降级使用标准化原 Query: url=%s, error=%s",
            url,
            exc,
        )
        return standardized


if __name__ == "__main__":
    test_queries = [
        "CT",
        "头颅",
        "CT脑出血",
        "脑出血",
        "MR膝关节",
        "CT弥漫性肺气肿",
        "动脉瘤（宽颈多发）",
        "腹部CTA正常",
        "CT颅脑平扫脑梗死",
        "MR",
        "DR",
    ]

    print("查询改写测试")
    print("=" * 70)

    for q in test_queries:
        vague = is_too_vague(q)
        need = needs_rewrite(q)
        if vague:
            clarification = get_clarification(q)
            status = f"⚠️ 过于模糊 → 追问: {clarification[:50]}..."
        elif need:
            rewritten = rewrite_query(q)
            status = f"✏️ 改写 → {rewritten}"
        else:
            status = "✅ 无需改写"
        print(f"  {q:20s} | 模糊={str(vague):5s} | 改写={str(need):5s} | {status}")