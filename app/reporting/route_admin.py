"""路由配置管理：查看/编辑/启停路由、专科Prompt读写、新路由草稿生成 skill"""

import json
import logging
import re
from pathlib import Path
from typing import Optional

import yaml

from app.chat.llm_client import chat_stream

from .report_validator import ReportValidator
from .schemas import RouteInfo

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"
ROUTES_YML = PROMPTS_DIR / "routes.yml"

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


# ===== routes.yml 读写 =====

_DOC_MAX_CHARS = 20000
_DOC_RELEVANT_CHARS = 14000
_PARSE_MAX_CHARS = 1500000  # 解析原文安全上限，防止超大文件撑爆内存
_PDF_MAX_PAGES = 300
_UNSUPPORTED_HINT = "仅支持 pdf / docx / xlsx / txt / md / csv 格式的参考资料"

# 器官核心单字（专属性强、歧义小），用于扩展检索词命中"肝实质/肝胆/肝血管瘤"等变体
# 注意：不含"椎"（会误命中椎动脉/胸椎等非本部位内容），脊柱类依赖"腰椎/颈椎"等二字词匹配
_ORGAN_SINGLE = set("肝肾脾胰肺脑")
# modality/通用词：几乎所有无关行都会提到，作为检索词会误命中，故从匹配词中剔除
_MODALITY_STOP = {
    "mri", "mr", "ct", "dr", "ct平扫", "磁共振", "x线", "超声", "平扫", "增强",
    "检查", "扫描", "报告", "模板", "模版", "正常", "进一步", "建议",
}


def _is_noise_value(s: str) -> bool:
    """判断 Excel 单元格是否为无意义元数据（纯数字/时间戳/编码/用户名），用于裁剪噪声列"""
    s = s.strip()
    if not s:
        return True
    if re.fullmatch(r"[\d.]+", s):  # 纯数字、小数、id、置信度
        return True
    if re.search(r"\d{4}-\d{2}-\d{2}", s):  # 时间戳（含带引号）
        return True
    if re.fullmatch(r"[A-Z]{4,}", s):  # 全大写编码（remark，如 SFGDPYZCPSZQ / LNZC）
        return True
    if re.fullmatch(r"[a-z]{3,}", s):  # 纯小写英文（用户名/来源标记，如 liqi/ncadmin）
        return True
    if s in {"group-inferred", "row-override", "0", "1"}:
        return True
    return False


def _extract_plain(data: bytes) -> str:
    """txt/md/csv：UTF-8/GBK 自适应解码"""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本文件编码无法识别（仅支持 UTF-8/GBK）")


def _extract_docx(data: bytes) -> str:
    """docx：标准库 zipfile + ElementTree 提取段落（含表格单元格内段落）"""
    import io
    import zipfile
    import xml.etree.ElementTree as ET

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml_bytes = z.read("word/document.xml")
    except Exception:
        raise ValueError("docx 文件损坏或不是有效的 Word 文档（.doc 旧格式请另存为 .docx）")
    root = ET.fromstring(xml_bytes)
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for p in root.iter(f"{ns}p"):
        line = "".join(t.text or "" for t in p.iter(f"{ns}t")).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _extract_xlsx(data: bytes) -> str:
    """xlsx：openpyxl 逐表逐行拼接。

    - 单元格内换行规整为"；"，保证一行=一条记录（避免 focus 行级筛选错位）
    - 按值特征裁掉纯数字/时间戳/编码/用户名等元数据噪声列（源表表头与数据错位，故按值而非列名裁剪）
    """
    import io

    try:
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise ValueError("xlsx 文件损坏或不是有效的 Excel 文档（.xls 旧格式请另存为 .xlsx）")
    lines = []
    for ws in wb.worksheets:
        kept = []
        for row in ws.iter_rows(values_only=True):
            cells = []
            for c in row:
                if c is None:
                    continue
                s = str(c).replace("\n", "；").replace("\r", " ").strip()
                if s and not _is_noise_value(s):
                    cells.append(s)
            if cells:
                kept.append("　".join(cells))
        if kept:
            lines.append(f"【工作表：{ws.title}】")
            lines.extend(kept)
    wb.close()
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> str:
    """pdf：pypdf 优先，PyMuPDF 兜底；扫描件（无文字层）给出明确提示"""
    import io

    text = ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "").strip() for p in reader.pages[:_PDF_MAX_PAGES]]
        text = "\n".join(p for p in pages if p)
    except Exception as e:
        logger.warning(f"pypdf 解析失败，尝试 PyMuPDF: {e}")
    if not text.strip():
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(stream=data, filetype="pdf")
            try:
                text = "\n".join(
                    (doc[i].get_text() or "").strip() for i in range(min(doc.page_count, _PDF_MAX_PAGES))
                )
            finally:
                doc.close()
        except Exception as e:
            logger.warning(f"PyMuPDF 解析失败: {e}")
    if not text.strip():
        raise ValueError("PDF 未提取到文字，可能是扫描件/图片型 PDF，请改用含文字层的 PDF 或 docx/txt")
    return text


def _focus_terms(focus: str) -> list[str]:
    """从路由名称/部位/别名中提取检索词。

    - 剔除 modality/通用停用词（MRI/CT/检查等，避免误命中无关部位）
    - 中文词加入二字滑窗（上腹部→上腹/腹部）与器官核心单字（肝脏→肝），提升召回
    """
    terms: list[str] = []
    for tok in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", focus or ""):
        if not tok:
            continue
        low = tok.lower()
        if low not in _MODALITY_STOP:
            terms.append(tok)
        for zh in re.findall(r"[\u4e00-\u9fff]{2,}", tok):
            if len(zh) == 2:
                terms.append(zh)
            else:
                terms.extend(zh[i : i + 2] for i in range(len(zh) - 1))
            if zh[0] in _ORGAN_SINGLE:
                terms.append(zh[0])
        # 单字器官（focus 直接给"肝"这类单字时）
        for ch in re.findall(r"[\u4e00-\u9fff]", tok):
            if ch in _ORGAN_SINGLE:
                terms.append(ch)
    seen, out = set(), []
    for t in terms:
        key = t.lower()
        if key in seen or len(t) < 1 or key in _MODALITY_STOP:
            continue
        seen.add(key)
        out.append(t)
    return out[:32]


def _select_relevant(text: str, focus: str) -> tuple[str, int, int]:
    """按与路由名称的相关度筛选文档内容：命中行优先输出，无关行过滤掉（不堆背景噪声）。

    Returns: (筛选后文本, 命中行数, 过滤行数)
    """
    terms = _focus_terms(focus)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not terms or len(lines) <= 1:
        return text.strip(), 0, 0
    low_terms = [t.lower() for t in terms]
    hit = [ln for ln in lines if any(t in ln.lower() for t in low_terms)]
    filtered = len(lines) - len(hit)
    if not hit:
        # 无任何命中：整份文档可能都与本部位无关，返回原文截断交由上层判断
        return text.strip(), 0, filtered
    relevant = "\n".join(hit)
    if len(relevant) > _DOC_MAX_CHARS:
        relevant = relevant[:_DOC_MAX_CHARS] + "\n…（相关条目过多，已截断）"
    header = (
        f"【与「{focus.strip()}」直接相关的材料，共命中 {len(hit)} 条，"
        f"已过滤 {filtered} 条与本部位无关的记录】"
    )
    return header + "\n" + relevant, len(hit), filtered


def extract_reference_text(filename: str, data: bytes, focus: str = "") -> dict:
    """将上传的参考资料文档解析为纯文本，并按路由名称优先抽取相关材料。

    支持：pdf（pypdf/PyMuPDF）、docx、xlsx、txt/md/csv（UTF-8/GBK 自适应）。
    关键：先对全文按 focus 筛选相关行，再截断——避免大文档中靠后的相关内容被提前截掉。
    不支持的格式抛 ValueError。

    Returns: {"text": str, "chars": int, "matched_lines": int, "filtered_lines": int}
    """
    name = (filename or "").lower()
    if name.endswith((".txt", ".md", ".csv", ".markdown")):
        text = _extract_plain(data)
    elif name.endswith(".docx"):
        text = _extract_docx(data)
    elif name.endswith(".xlsx"):
        text = _extract_xlsx(data)
    elif name.endswith(".pdf"):
        text = _extract_pdf(data)
    elif name.endswith((".xls", ".doc", ".ppt", ".pptx")):
        raise ValueError(f"{Path(name).suffix} 旧版/演示格式暂不支持，请另存为 xlsx / docx / pdf 后上传")
    else:
        raise ValueError(_UNSUPPORTED_HINT)
    text = text.strip()
    if not text:
        raise ValueError("文档内容为空（未提取到任何文字）")
    if len(text) > _PARSE_MAX_CHARS:
        text = text[:_PARSE_MAX_CHARS]
    text, matched, filtered = _select_relevant(text, focus)
    if len(text) > _DOC_MAX_CHARS:
        text = text[:_DOC_MAX_CHARS] + "\n…（内容过长，已截断）"
    return {"text": text, "chars": len(text), "matched_lines": matched, "filtered_lines": filtered}


def load_routes_raw() -> dict:
    """读取 routes.yml 原始配置"""
    with open(ROUTES_YML, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"routes": []}


def save_routes_raw(config: dict):
    """写回 routes.yml（保留中文）"""
    with open(ROUTES_YML, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def validate_route_payload(payload: dict, existing_ids: list[str], allow_same: bool = False) -> list[str]:
    """校验路由配置字段，返回错误列表"""
    errors = []
    rid = str(payload.get("id") or "").strip()
    if not _ID_RE.match(rid):
        errors.append(f"id 必须为小写字母开头的 snake_case（当前: {rid!r}）")
    elif rid in existing_ids and not allow_same:
        errors.append(f"id 已存在: {rid}")
    if not str(payload.get("name") or "").strip():
        errors.append("name 不能为空")
    # prompt_file 由 apply_draft 按 {id}.md 自动生成，无需前端提供
    for field in ("aliases", "modalities", "body_parts", "quick_tags"):
        v = payload.get(field)
        if v is not None and not isinstance(v, list):
            errors.append(f"{field} 必须为列表")
    return errors


# ===== 专科 Prompt 读写 =====

def prompt_path(route: dict) -> Path:
    p = PROMPTS_DIR / str(route.get("prompt_file") or "")
    return p if p.suffix else p.with_suffix(".md")


def read_prompt(route: dict) -> str:
    p = prompt_path(route)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def write_prompt(route: dict, content: str):
    p = prompt_path(route)
    p.write_text(content, encoding="utf-8")


# ===== 校验器联动规则（只读展示） =====

def validator_linkage() -> dict:
    """当前校验器与路由联动的确定性规则摘要"""
    v = ReportValidator
    return {
        "organ_anchors": list(v._ORGAN_ANCHORS),
        "region_anchors": list(v._REGION_ANCHORS),
        "exclusive_q_keywords": list(v._EXCLUSIVE_Q_KEYWORDS),
        "multi_q_keywords": list(v._MULTI_Q_KEYWORDS),
        "occ_strong": list(v._OCC_STRONG),
        "occ_weak": list(v._OCC_WEAK),
        "degen": list(v._DEGEN),
        "inductive_markers": list(v._INDUCTIVE_MARKERS),
        "checks": [
            "必填字段/禁用词/追问轮次≤2/追问数≤3/长度",
            "诊断信号词（归纳或演绎结论）",
            "括号式模糊注记封杀（待明确/待确认/待评估/待定/待排）",
            "范式-格式一致性（强占位征象→归纳三行；仅退变→禁归纳）",
            "影像所见必须三行纯文本",
            "诊断结尾专业建议信号词（建议/随访/会诊/就诊/复查等）",
        ],
        "normalizers": [
            "sanitize_vague：模糊表述→阴性基线；剥离图像质量评述句",
            "normalize_findings_lines：单段→按行首锚点切三行",
            "normalize_questions_select：互斥问题强制单选并剥离（可多选）",
        ],
    }


# ===== 新路由草稿生成 skill =====

_DRAFT_SKILL_PROMPT = """你是一名医疗影像报告模板设计专家。请为给定的检查部位设计一套完整的路由配置草稿。

必须严格输出如下 JSON（不要输出任何其他内容、不要 markdown 代码块）：
{
  "id": "小写snake_case路由ID，如 liver_mri",
  "name": "中文路由名，如 肝脏 MRI",
  "aliases": ["4~6条触发别名：名称无空格写法、简称/口语/邻近部位变体（如 肝脏MR、肝MRI、上腹部MRI），越多越易触发路由"],
  "modalities": ["MRI"],
  "body_parts": ["部位标准词"],
  "quick_tags": ["诊断结论关键词胶囊8~15条，每条为可独立成立的诊断结论（疾病名+必要分级），末尾含一条'XX MRI未见明显异常'"],
  "prompt_md": "专科Prompt全文"
}

prompt_md 必须包含以下四个章节（标题原样使用），内容针对该部位专业化：
#### {name}专用基线与模块要素
- 模块1 病灶病变水平（6要素）：①特征 ②位置 ③几何 ④信号 ⑤继发改变 ⑥对比（结合该部位解剖与权威分级/分型速查）。
- 模块2 组织器官水平（必填）：该器官整体基线，给出默认填充句；严禁图像质量评述。
- 模块3 图像区域水平：扫描野内邻近脏器。
- 输出格式：影像所见固定三行纯文本（病灶→器官→区域），禁序号/模块名前缀。
- 关键病变速查：列出该部位常见病变与权威分级。

#### 要素完整性与追问设计
- 缺口检索顺序：6要素缺口→器官基线→区域水平。
- 本路由追问参照（括号内为 multi_select）：列5~7条，互斥答案(侧别/分级/程度/强化特点)为false，可并存(多病灶部位/并存征象)为true且question注明"（可多选）"。

#### 两范式影像所见与影像诊断规范（二选一，禁混用）
- 范式选择：良性/退变/典型病变→演绎式；占位/肿瘤/性质不明→归纳式。
- 演绎式：诊断单段连贯=明确诊断(含分级)+关键征象摘要+继发改变，句末续写个体化建议（科室+临床处理+随访计划三要素）。
- 归纳式：诊断三行纯文本：第1行定性关键词；第2行①首先考虑；②待鉴别；③待排除；第3行诊疗方向。
- 定量参数铁律与位置/大小缺失铁律（XX占位+本轮追问，禁括号模糊注记）。

#### 标准范例
范例1（演绎式）与范例2（归纳式）各一组：影像所见三行 + 影像诊断（归纳式三行），贴合该部位真实疾病谱。
"""


def _default_aliases(name: str, body_part: str, modality: str) -> list[str]:
    """确定性触发别名集：名称/无空格/简称/中文模态/单字器官变体，保证至少 4 条便于命中路由"""
    mod = (modality or "MRI").upper()
    mod_short = "MR" if mod in ("MRI", "MR") else mod
    mod_cn = "磁共振" if mod in ("MRI", "MR") else mod
    short = body_part[:1] if len(body_part) >= 2 else body_part
    seen: list[str] = []
    for a in (
        name,
        f"{body_part}{mod}",
        f"{body_part}{mod_short}",
        f"{body_part}{mod_cn}",
        f"{short}{mod}",
        f"{short}{mod_cn}",
    ):
        a = (a or "").strip()
        if a and a not in seen:
            seen.append(a)
    return seen


def _fallback_draft(name: str, body_part: str, modality: str, rid: str) -> dict:
    """LLM 不可用时的最小草稿（基于通用骨架，供人工补全）"""
    prompt_md = f"""#### {name}专用基线与模块要素

- 模块1 病灶病变水平（6要素）：①特征 ②位置 ③几何 ④信号 ⑤继发改变 ⑥对比。
- 模块2 组织器官水平（必填）：{body_part}整体形态与信号基线。默认填充："{body_part}形态规整，信号均匀，未见异常。"
- 模块3 图像区域水平：扫描野内邻近脏器与结构。
- 输出格式：影像所见固定三行纯文本（病灶→器官→区域），禁序号/模块名前缀。
- 关键病变速查：待补充（请编辑本文件补全该部位权威分级/分型）。

#### 要素完整性与追问设计

- 缺口检索顺序：6要素缺口→器官基线→区域水平。
- 本路由追问参照（括号内为 multi_select）：
  1. 病灶定位（true，question 注明"（可多选）"）
  2. 病变程度/分级（false）
  3. 并存征象（true，question 注明"（可多选）"）

#### 两范式影像所见与影像诊断规范（二选一，禁混用）

- 范式选择：良性/退变/典型病变→演绎式；占位/肿瘤/性质不明→归纳式。
- 演绎式：诊断单段连贯=明确诊断(含分级)+关键征象摘要+继发改变，句末续写个体化建议（科室+临床处理+随访计划三要素）。
- 归纳式：诊断三行纯文本：第1行定性关键词；第2行①首先考虑；②待鉴别；③待排除；第3行诊疗方向。
- 定量参数铁律：未提供的定量参数严禁编造，一律XX占位并在本轮追问索取。
- 位置/大小/类型缺失铁律：位置以两位XX占位书写并追问；严禁括号式模糊注记。

#### 标准范例

范例1（演绎式）
影像所见：
{body_part}未见明确局灶性病变。
{body_part}形态规整，信号均匀。
扫描范围内邻近脏器未见异常。
影像诊断：
{name}未见明显异常。建议常规年度体检随访。
"""
    return {
        "id": rid,
        "name": name,
        "aliases": _default_aliases(name, body_part, modality),
        "modalities": [modality],
        "body_parts": [body_part],
        "quick_tags": [f"{body_part}未见明显异常"],
        "prompt_md": prompt_md,
        "fallback": True,
    }


async def generate_route_draft(
    name: str,
    body_part: str,
    modality: str = "MRI",
    reference_text: str = "",
    llm_name: str = "",
) -> dict:
    """新路由草稿生成 skill：依据部位名+可选参考资料，LLM 生成完整路由配置草稿。

    Returns: 草稿 dict（id/name/aliases/modalities/body_parts/quick_tags/prompt_md）
    """
    name = (name or "").strip()
    body_part = (body_part or name).strip()
    modality = (modality or "MRI").strip().upper()
    rid = re.sub(r"[^a-z0-9_]+", "_", re.sub(r"[^A-Za-z0-9]+", "_", body_part.lower())).strip("_") or "new_route"
    rid = f"{rid}_{modality.lower()}" if not rid.endswith(modality.lower()) else rid

    ref_block = ""
    if reference_text.strip():
        ref_block = (
            "\n\n医生提供的参考资料（已按与本路由相关度重排，"
            "请优先采纳「直接相关的材料」段落中的术语、分级、征象描述与诊断结论；"
            "与本部位无关的内容一律忽略）：\n"
            f"{reference_text.strip()[:12000]}\n"
        )

    messages = [
        {"role": "system", "content": _DRAFT_SKILL_PROMPT},
        {"role": "user", "content": f"检查部位：{body_part}；检查类型：{modality}；路由中文名：{name}。{ref_block}"},
    ]
    try:
        raw = await chat_stream(
            messages=messages,
            max_tokens=6000,
            temperature=0.4,
            caller="route_draft",
            model_override=llm_name or "",
            timeout=180.0,
            max_retries=0,
            enable_thinking=False,
        )
        m = re.search(r"\{.*\}", raw or "", re.S)
        draft = json.loads(m.group(0)) if m else None
        if not draft or not draft.get("prompt_md"):
            raise ValueError("草稿缺少 prompt_md")
        draft.setdefault("id", rid)
        draft.setdefault("name", name)
        draft.setdefault("modalities", [modality])
        draft.setdefault("body_parts", [body_part])
        draft.setdefault("aliases", [name])
        # 确定性补足触发别名：LLM 给的别名之外并入标准变体，保证至少 4 条、更易命中
        merged = [str(a) for a in (draft.get("aliases") or []) if str(a).strip()]
        for a in _default_aliases(name, body_part, modality):
            if a not in merged:
                merged.append(a)
        draft["aliases"] = merged
        draft.setdefault("quick_tags", [f"{body_part}未见明显异常"])
        draft["fallback"] = False
        return draft
    except Exception as e:
        logger.warning(f"LLM 草稿生成失败，回退最小草稿: {e}")
        return _fallback_draft(name, body_part, modality, rid)


def apply_draft(config: dict, draft: dict, prompt_content: Optional[str] = None) -> RouteInfo:
    """将草稿/编辑结果写入 routes.yml 与专科 md 文件，返回 RouteInfo"""
    rid = str(draft["id"]).strip()
    prompt_file = f"{rid}.md"
    route_data = {
        "id": rid,
        "name": str(draft.get("name") or rid).strip(),
        "aliases": [str(a) for a in draft.get("aliases") or []],
        "modalities": [str(a) for a in draft.get("modalities") or []],
        "body_parts": [str(a) for a in draft.get("body_parts") or []],
        "prompt_file": prompt_file,
        "template_source": draft.get("template_source"),
        "quick_tags": [str(t) for t in draft.get("quick_tags") or []],
        "enabled": bool(draft.get("enabled", True)),
    }
    routes = config.get("routes") or []
    replaced = False
    for i, r in enumerate(routes):
        if r.get("id") == rid:
            routes[i] = route_data
            replaced = True
            break
    if not replaced:
        routes.append(route_data)
    config["routes"] = routes
    save_routes_raw(config)

    content = prompt_content if prompt_content is not None else draft.get("prompt_md", "")
    write_prompt({"prompt_file": prompt_file}, content)
    return RouteInfo(**route_data)
