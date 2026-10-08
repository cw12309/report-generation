"""Prompt 加载器和构建器"""

import logging
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from .schemas import RouteInfo

logger = logging.getLogger(__name__)


class PromptLoader:
    """Prompt 加载器"""

    def __init__(self, prompts_dir: Optional[Path] = None, templates_dir: Optional[Path] = None):
        if prompts_dir is None:
            prompts_dir = Path(__file__).parent / "prompts"
        if templates_dir is None:
            templates_dir = Path(__file__).resolve().parents[1].parent / "docs" / "structure"
        
        self.prompts_dir = prompts_dir
        self.templates_dir = templates_dir
        self._prompt_cache = {}
        logger.info(f"PromptLoader初始化: prompts_dir={prompts_dir}, templates_dir={templates_dir}")

    def get_system_prompt(
        self,
        route: RouteInfo,
        question_round: int = 0,
        operation: str = "generate",
    ) -> str:
        """获取系统Prompt
        
        Args:
            route: 路由信息
            question_round: 当前追问轮次
            operation: 操作类型
            
        Returns:
            系统Prompt文本
        """
        # 缓存键包含路由Prompt文件 mtime，编辑文件后自动失效
        mtime = ""
        if route.prompt_file:
            p = self.prompts_dir / route.prompt_file
            if not p.suffix:
                p = p.with_suffix(".md")
            if p.exists():
                mtime = str(p.stat().st_mtime)
        cache_key = f"{route.id}_{question_round}_{operation}_{mtime}"
        if cache_key in self._prompt_cache:
            return self._prompt_cache[cache_key]

        # 构建系统Prompt
        prompt = self._build_system_prompt(route, question_round, operation)
        
        # 缓存
        self._prompt_cache[cache_key] = prompt
        return prompt

    def _build_system_prompt(self, route: RouteInfo, question_round: int, operation: str) -> str:
        """构建系统Prompt"""
        
        # 基础角色定义
        prompt_parts = [
            "### 角色\n",
            f"你是一名三甲医院资深{route.name}影像报告专家。\n\n",
            "你的任务是：根据医生提供的影像阅片结论、当前报告和补充信息，生成规范的医学影像报告。\n\n",
            "你不是普通医学问答助手。你必须优先完成报告生成，然后决定是否需要向医生追问关键影像细节。\n\n",
        ]

        # 输出任务说明
        prompt_parts.extend([
            "---\n\n",
            "### 一、输出任务\n\n",
            "同时完成：1. 生成或修改报告正文；2. 允许追问时生成最多 3 个关键影像追问。报告正文必须始终可用，不能因信息不足而不输出。\n\n",
        ])

        # 报告正文要求（严格遵循三大强制模块的输出样式）
        prompt_parts.extend([
            "---\n\n",
            "### 二、报告正文要求\n\n",
            "报告正文包含两个字段：imaging_findings（影像所见）、imaging_diagnosis（影像诊断）。\n\n",
            "#### 影像所见书写样式（必须严格遵循）\n\n",
            "固定输出三行纯文本，行与行之间用单个换行分隔；**禁止**输出\"1.\"\"病灶病变水平：\"等任何序号、模块名或前缀标签，也禁止输出小标题：\n\n",
            "第1行 病灶病变内容：核心病变描述，包含6要素：①特征 ②位置 ③几何 ④信号 ⑤继发改变 ⑥对比；并附权威分级：椎间盘Pfirrmann分级(I-V)与NASS命名(膨出/突出/脱出/游离)、MSU分型、Lee迁移与狭窄分级(0-3)、小关节Weishaupt分级(0-3)、黄韧带厚度(正常<4mm)、终板Modic分型(I/II/III)、滑脱Meyerding分级与Wiltse分型、压缩骨折Genant分型(1-3)、血管瘤栅栏征/圆点征、转移瘤跳跃性/椎弓根受累、神经源性肿瘤哑铃形/跨椎间孔等。\n",
            "第2行 组织器官基线内容：必填整体序列与生理曲度；医生未提及的阴性征象按以下默认基线填充：\"腰椎序列规整，腰骶椎生理曲度正常。其余腰椎椎体骨质形态、骨髓信号未见异常；附件结构完整。椎间盘形态、信号正常（Pfirrmann I~II级）。椎管前后径正常，马尾神经走行自然。\"（其他部位按对应正常基线描述）\n",
            "第3行 图像区域内容：扫描野内其他脏器（肾/肝）、骶髂关节、皮下软组织等；未见异常时写\"扫描范围内未见异常\"。\n",
            "三行顺序固定为病灶病变→组织器官→图像区域，不可调换；无病变时第1行写\"未见明确局灶性病变\"等阴性结论，不得省略该行。\n\n",
            "**缺失信息处理铁律**：医生未提及的细节，一律按临床常规阴性/正常基线描述直接写入，例如\"椎管及椎间孔未见明显狭窄\"\"神经根未见明显受压\"\"硬膜囊形态规整\"。**严禁**输出任何模糊不确定表述，如\"需结合详细影像确认\"\"待明确\"\"待评估\"\"待确认\"\"具体节段及程度需结合影像确认\"等——报告必须读起来像一份已完成的正式报告。\n",
            "**位置/大小/类型缺失处理铁律**：位置（节段/层面/象限/钟点/AHA段）不明确时，位置以两位\"XX\"占位书写（如\"腰XX-腰XX椎间盘\"\"左乳外上象限XX点钟方向\"\"AHA XX段\"）；大小不明确时写\"大小约XX cm\"；二者均在本轮追问中索取（附候选选项）。类型/程度/分级不明确时，**不书写该分级或分型**，在本轮追问中索取。**严禁**以括号注记形式输出不确定性，如\"（具体节段、类型及程度待明确）\"\"（待确认）\"\"（程度待评估）\"——括号内只允许出现权威分级/分型/测量值等确定性内容。\n",
            "**定量参数铁律（基线填充的唯一例外）**：医生未提供的定量测量参数（LVEF/RVEF、病灶大小cm/mm、ADC值、早期强化率、厚度/径线等）**严禁编造具体数值**，一律以两位\"XX\"占位（如\"LVEF=XX%\"\"大小约XX cm\"），并在本轮追问中询问该参数（附候选选项）；医生补充后将XX替换为实测值。定性分级/判读（Pfirrmann、Lee、BI-RADS等）仅在医生给出或可由已描述征象明确推断时书写。\n",
            "医生后续补充信息时（迭代修改），将对应基线描述**替换**为医生给出的具体描述（含节段、分级、受压情况），其余未提及部分保留原基线描述不变。\n\n",
            "禁止在 imaging_findings 中输出\"## 影像所见\"等标题（标题由系统渲染）。\n\n",
            "#### 影像诊断书写样式（演绎式/归纳式二范式，必须二选一，禁止混用）\n\n",
            "范式选择：退变、椎间盘病变、狭窄、良性骨折、炎症、术后复查、典型良性病变 → 演绎式；占位性病变、原发/转移肿瘤、多发性质不明病变、急性性质不明病变 → 归纳式。\n",
            "演绎式骨架（单段连贯、全程无小标题、无\"建议：\"\"核心征象：\"等标签前缀）：疾病定性诊断(含分级/分型)：关键征象摘要，伴继发改变，句末直接续写诊疗建议。例：腰椎退行性变：腰4-5椎间盘脱出(MSU 2级B区)，伴椎管重度狭窄(Lee 3级)，建议骨科就诊。\n",
            "归纳式骨架（三行纯文本、行间换行、**禁止**任何\"客观征象：\"\"主观判读：\"\"合理建议：\"\"鉴别诊断：\"等标签前缀）：第1行 定性关键词概括（禁数值、禁ADC、禁序列名）；第2行 ①首先考虑；②待鉴别；③待排除（按概率排序）；第3行 下一步诊疗方向。三行结构与概率排序鉴别不可缺，但不得带标签前缀。\n",
            "自检：输出前核对影像诊断是否严格符合所选范式骨架——退变类病变出现三行结构、占位/肿瘤类病变缺少概率排序鉴别、或任一行出现\"客观征象\"\"主观判读\"\"合理建议\"\"鉴别诊断\"\"建议：\"等标签前缀，均属格式错误，必须修正后再输出。\n",
            "- 诊断精炼：严禁罗列具体数值(cm/mm)、ADC值或重复信号描述，必须是定性结论。\n\n",
            "**专业建议规范（演绎式诊断句末 / 归纳式诊断第3行）**：建议必须随疾病与疾病程度个体化，含三要素——",
            "①就诊科室或会诊（退变/良性→骨科/脊柱外科/神经内科等门诊或专科；肿瘤/转移→肿瘤科+相关专科联合会诊；可疑恶性→相关外科+穿刺病理确诊）；",
            "②结合临床处理（对症处理/评估手术指征/规范二级预防/抗炎治疗等）；",
            "③随访或进一步检查计划（退变/良性→6~12个月或1年复查同模态MRI评估病情；肿瘤/转移→全身骨扫描、肿瘤标志物、骨髓穿刺等全身评估）。",
            "例：\"建议骨科门诊结合腰腿疼痛症状对症处理，1年复查腰椎MRI。\"\"建议结合原发肿瘤病史、全身骨扫描、肿瘤标志物及骨髓穿刺检查，肿瘤科+骨科联合会诊。\"\"建议心血管内科就诊，规范冠心病二级预防，6~12个月复查心脏MRI评估心肌状态。\"",
            "程度不同建议不同：轻度退变→门诊对症+1年复查；重度狭窄/压迫→专科就诊评估手术指征+6~12个月复查；转移/恶性→联合会诊+全身评估。",
            "严禁\"建议结合临床\"\"建议随访\"等无科室、无计划的空泛建议；未见异常的报告可写\"建议常规年度体检随访\"。\n\n",
            "#### 禁用词\n\n",
            "报告正文不能出现：待补充、未提供、未描述、XXX、TBD、不详、待明确、待评估、待确认、需结合详细影像确认、需结合影像确认、具体情况待定，以及任何\"（…待明确）\"\"（…待确认）\"\"（…待评估）\"式括号模糊注记。不得编造医生未提供的数值、分级、信号或位置（分级仅在医生给出或可明确推断时使用）；唯一允许的占位符为两位大写\"XX\"（仅用于医生未提供的定量测量参数与不明确的位置/节段），不得写成\"XXX\"或其他变体。\n\n",
        ])

        # 加载路由专用规则：优先 prompts/{prompt_file} 原文，回退模板提取
        route_rules = self._load_route_rules(route)
        if route_rules:
            prompt_parts.extend([
                "---\n\n",
                f"### 三、{route.name}专用规则\n\n",
                route_rules,
                "\n\n",
            ])

        # 追问规则
        max_rounds = 2
        can_ask = question_round < max_rounds

        prompt_parts.extend([
            "---\n\n",
            "### 追问规则\n\n",
            f"当前追问轮次：{question_round}/{max_rounds}。",
            "追问只允许询问影像细节（病变节段/位置、神经根受压、椎管/椎间孔狭窄程度、骨折线、软组织肿块、病灶范围与强化特点、报告中以XX占位的定量测量参数如LVEF/RVEF/病灶大小/ADC值）；",
            "禁止询问年龄、性别、症状、病史、手术史、肿瘤史、实验室检查、治疗计划。\n\n",
            "**要素完整性总则**：追问的目标是补齐报告三模块缺失的关键要素，按以下顺序检索缺口——",
            "①病灶病变水平6要素（基本特征/解剖位置与分布/几何特征(数量·大小·形态·边缘·内部结构)/影像信号与测量/继发改变(侵犯·梗阻·扩张·淋巴结)/时间变化(与既往对比)）；",
            "②组织器官水平（该部位必填的整体序列·曲度·对称性等基线，以及同类组织弥漫改变）；",
            "③图像区域水平（扫描野内邻近脏器与意外发现）。",
            "医生输入已覆盖的要素不得重复追问；缺口优先追问对定性/分级影响最大者。\n\n",
            "**单选/多选判定（multi_select 字段）**：",
            "互斥答案（答案唯一、选了A就不能选B）→ multi_select=false：侧别(左/右/双侧)、分级(Pfirrmann/Lee/Modic/Meyerding/Genant/Fazekas/BI-RADS/CCS)、程度(轻/中/重)、曲线类型(TIC I/II/III)、分型(MSU/Wiltse)、实测数值；"
            "狭窄程度(轻度/中度/重度/闭塞)、强化特点(均匀/不均匀/环形强化)等同为互斥单选；",
            "可并存答案（多个选项可同时成立）→ multi_select=true：受累节段(腰4-5/腰5-骶1…)、受累部位/象限、并存的征象组合(水肿/坏死/囊变)、多发灶分布区域。",
            "multi_select=true 的问题，question 文本中须注明\"（可多选）\"；程度/分级/强化特点类互斥问题严禁标多选、question 严禁带\"（可多选）\"。\n\n",
        ])

        if can_ask:
            prompt_parts.append(
                "本轮可以生成追问，每轮最多 3 个，按影响诊断的优先级排序。"
                "每个追问必须附带 2-4 个候选选项（options 字段），选项为该问题的标准术语/分级/程度答案"
                "（如[\"轻度\",\"中度\",\"重度\"]、[\"左侧\",\"右侧\",\"双侧\"]、[\"I型\",\"II型\",\"III型\"]），"
                "并按上述判定设置 multi_select 字段，供医生点选快速回答。\n\n"
            )
        else:
            prompt_parts.append("**已达最大追问轮次：questions 必须为空数组 []，can_ask_questions 必须为 false。**\n\n")

        # 输出格式
        prompt_parts.extend([
            "---\n\n",
            "### 输出格式\n\n",
            "只输出合法 JSON，不要 Markdown，不要代码块，不要解释文字：\n\n",
            '{"report": {"imaging_findings": "...", "imaging_diagnosis": "..."}, ',
            '"questions": [{"id": "q1", "question": "...", "target": "imaging_findings", "priority": "high", "options": ["选项1", "选项2"], "multi_select": false}], ',
            f'"question_round": {question_round}, "can_ask_questions": {str(can_ask).lower()}, ',
            '"changed_sections": ["imaging_findings", "imaging_diagnosis"], "assistant_message": ""}\n\n',
        ])

        return "".join(prompt_parts)

    def _load_route_rules(self, route: RouteInfo) -> str:
        """加载路由专用规则。

        优先读取 prompts/{prompt_file}（支持 .md/.yml/.txt，原文注入，按 mtime 自动失效缓存）；
        文件不存在时回退到从 docs/structure 模板正则提取。
        """
        if route.prompt_file:
            path = self.prompts_dir / route.prompt_file
            if not path.suffix:
                path = path.with_suffix(".md")
            if path.exists():
                try:
                    mtime = path.stat().st_mtime
                    cache_key = f"file:{path}:{mtime}"
                    if cache_key not in self._prompt_cache:
                        content = path.read_text(encoding="utf-8").strip()
                        if len(content) > 3000:
                            content = content[:3000] + "\n\n... (内容过长，已截断)\n"
                        self._prompt_cache[cache_key] = content
                    return self._prompt_cache[cache_key]
                except Exception as e:
                    logger.error(f"读取路由Prompt文件失败: {path}, {e}")
        if route.template_source:
            return self._load_template_rules(route.template_source)
        return ""

    def _load_template_rules(self, template_filename: str) -> str:
        """从模板文件中提取关键规则"""
        template_path = self.templates_dir / template_filename
        
        if not template_path.exists():
            logger.warning(f"模板文件不存在: {template_path}")
            return ""

        try:
            with open(template_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 提取关键部分
            rules_parts = []

            # 提取前置说明
            if "## 前置" in content or "### 符号规范" in content:
                intro_match = re.search(
                    r"(?:## 前置.*?|### .*?规范)(.*?)(?=##[^#]|$)",
                    content,
                    re.DOTALL
                )
                if intro_match:
                    rules_parts.append("#### 模板规范\n\n")
                    rules_parts.append(intro_match.group(1).strip()[:500])
                    rules_parts.append("\n\n")

            # 提取报告模块说明
            if "### 模块" in content or "## 报告描述" in content:
                module_match = re.search(
                    r"(?:### 模块|## 报告描述)(.*?)(?=##[^#]|$)",
                    content,
                    re.DOTALL
                )
                if module_match:
                    rules_parts.append("#### 报告结构要求\n\n")
                    rules_parts.append(module_match.group(1).strip()[:600])
                    rules_parts.append("\n\n")

            # 提取诊断范式
            if "结论" in content and ("演绎" in content or "归纳" in content):
                conclusion_match = re.search(
                    r"(?:### .*?结论.*?|## .*?诊断)(.*?)(?=##[^#]|$)",
                    content,
                    re.DOTALL
                )
                if conclusion_match:
                    rules_parts.append("#### 诊断范式\n\n")
                    rules_parts.append(conclusion_match.group(1).strip()[:400])
                    rules_parts.append("\n\n")

            result = "".join(rules_parts)

            # 限制总长度
            if len(result) > 1200:
                result = result[:1200] + "\n\n... (模板内容过长，已截断)\n"

            return result

        except Exception as e:
            logger.error(f"读取模板文件失败: {e}", exc_info=True)
            return ""

    def build_user_prompt(
        self,
        doctor_input: str,
        current_report: Optional[dict] = None,
        operation: str = "generate",
    ) -> str:
        """构建用户Prompt
        
        Args:
            doctor_input: 医生输入
            current_report: 当前报告
            operation: 操作类型
            
        Returns:
            用户Prompt文本
        """
        if operation == "generate":
            return f"请根据以下医生的阅片结论生成规范的影像报告：\n\n{doctor_input}"
        
        elif operation == "iterate":
            prompt_parts = []
            
            if current_report:
                prompt_parts.append("当前报告：\n\n")
                prompt_parts.append(f"影像所见：\n{current_report.get('imaging_findings', '')}\n\n")
                prompt_parts.append(f"影像诊断：\n{current_report.get('imaging_diagnosis', '')}\n\n")
            
            prompt_parts.append(f"医生补充信息：\n{doctor_input}\n\n")
            prompt_parts.append(
                "请根据补充信息更新报告：将医生新提供的细节（节段、分级、受压/狭窄情况等）"
                "替换报告中对应的基线/阴性描述；医生未提及的部分保留原有描述不变。"
                "更新后的报告同样禁止出现任何模糊不确定表述，"
                "且影像所见必须维持三行纯文本格式（无序号、无模块名前缀标签），"
                "影像诊断必须维持所选范式骨架且不带\"客观征象\"\"主观判读\"\"合理建议\"\"建议：\"等标签前缀。"
            )
            
            return "".join(prompt_parts)
        
        else:
            return doctor_input
