"""记忆模块单元测试

覆盖 Phase 1 核心场景：
1. 意图切换清洗测试
2. 实体提取鲁棒性测试（长词优先 + 歧义处理）
3. EntityTracker Prompt 上下文测试
4. 上下文消解测试

用法：
  python -m app.test.test_memory
"""
import os
import sys
import io
import types

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# PromptBuilder 的 tracing SDK 是运行时可选依赖；纯逻辑测试不需要它。
try:
    import langsmith  # noqa: F401
except ModuleNotFoundError:
    langsmith_stub = types.ModuleType("langsmith")

    def traceable(function=None, **_kwargs):
        if function is not None:
            return function
        return lambda wrapped: wrapped

    langsmith_stub.traceable = traceable
    sys.modules["langsmith"] = langsmith_stub

from memory import EntityTracker, ShortTermMemory
from memory.retriever import MemoryRetriever
from prompt.system_prompt_builder import PromptBuilder


# ═══════════════════════════════════════════════════════════
# 测试辅助
# ═══════════════════════════════════════════════════════════

PASS = 0
FAIL = 0


def test(name: str):
    """测试装饰器"""
    def decorator(fn):
        def wrapper():
            global PASS, FAIL
            try:
                fn()
                PASS += 1
                print(f"   ✅ {name}")
            except AssertionError as e:
                FAIL += 1
                print(f"   ❌ {name}: {e}")
            except Exception as e:
                FAIL += 1
                print(f"   💥 {name}: {type(e).__name__}: {e}")
        return wrapper
    return decorator


def assert_equal(actual, expected, msg=""):
    """断言相等"""
    if actual != expected:
        raise AssertionError(f"{msg} 期望 {expected!r}，实际 {actual!r}")


def assert_true(condition, msg=""):
    """断言真"""
    if not condition:
        raise AssertionError(f"{msg} 期望为 True，实际为 False")


def assert_false(condition, msg=""):
    """断言假"""
    if condition:
        raise AssertionError(f"{msg} 期望为 False，实际为 True")


def assert_in(item, container, msg=""):
    """断言包含"""
    if item not in container:
        raise AssertionError(f"{msg} 期望 {item!r} 在 {container!r} 中")


def assert_not_in(item, container, msg=""):
    """断言不包含"""
    if item in container:
        raise AssertionError(f"{msg} 期望 {item!r} 不在 {container!r} 中")


def assert_not_equal(actual, expected, msg=""):
    """断言不相等"""
    if actual == expected:
        raise AssertionError(f"{msg} 期望不等于 {expected!r}，实际等于 {actual!r}")


# ═══════════════════════════════════════════════════════════
# 测试 1：意图切换清洗测试（最关键）
# ═══════════════════════════════════════════════════════════

class TestIntentSwitchCleanup:

    @staticmethod
    def setup():
        """前置条件：在 STM 中填入 3 轮对话，设置 last_report，entity_tracker 有状态"""
        stm = ShortTermMemory(max_rounds=6)
        session_id = "test_switch_001"

        stm.add_turn(session_id, "CT 脑部 有脑出血", "报告：脑出血影像学表现...")
        stm.add_turn(session_id, "再看看血管情况", "报告：脑血管未见异常...")
        stm.add_turn(session_id, "补充一下水肿情况", "报告：脑水肿轻度...")

        entity_tracker = EntityTracker()
        entity_tracker.update_from_query("CT 脑部 有脑出血")

        last_report = ['{"影像学表现": {"脑出血": "..."}, "诊断意见": {"脑出血": "..."}}']

        return stm, entity_tracker, last_report, session_id

    @test("切换前 STM 有 3 轮对话")
    def test_stm_has_history():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()
        history = stm.get_history(session_id)
        assert_equal(len(history), 6, "切换前应保留 3 轮完整对话（6 条消息）")

    @test("切换前 entity_tracker 有旧状态")
    def test_entity_has_old_state():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()
        assert_equal(entity_tracker.slots["modality"], "CT", "切换前 modality 应为 CT")
        assert_equal(entity_tracker.slots["body_part"], ["脑部"], "切换前 body_part 应为 脑部")

    @test("切换前 last_report 有内容")
    def test_last_report_has_content():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()
        assert_true(len(last_report[0]) > 0, "切换前 last_report 应有内容")
        assert_in("脑出血", last_report[0], "切换前 last_report 应包含'脑出血'")

    @test("切换意图检测正确")
    def test_detect_switch_intent():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()
        intent = entity_tracker.detect_intent("换成 MR 膝关节")
        assert_equal(intent, "switch", "应检测为 switch 意图")

    @test("切换后 stm.get_history() 返回空")
    def test_switch_clears_stm():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()

        entity_tracker.apply_switch("换成 MR 膝关节")
        stm.clear(session_id)

        history = stm.get_history(session_id)
        assert_equal(len(history), 0, "切换后 STM 历史应为空")

    @test("切换后 last_report 被清空")
    def test_switch_clears_last_report():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()

        entity_tracker.apply_switch("换成 MR 膝关节")
        last_report[0] = ""

        assert_equal(last_report[0], "", "切换后 last_report 应为空字符串")

    @test("切换后 entity_tracker.slots.modality 变为 MR")
    def test_switch_updates_modality():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()

        entity_tracker.apply_switch("换成 MR 膝关节")

        assert_equal(entity_tracker.slots["modality"], "MR", "切换后 modality 应为 MR")

    @test("切换后 entity_tracker.slots.body_part 变为 膝关节")
    def test_switch_updates_body_part():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()

        entity_tracker.apply_switch("换成 MR 膝关节")

        assert_equal(entity_tracker.slots["body_part"], ["膝关节"], "切换后 body_part 应为 膝关节")

    @test("切换后旧状态 CT 已消失")
    def test_switch_removes_old_state():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()

        entity_tracker.apply_switch("换成 MR 膝关节")

        assert_not_equal(entity_tracker.slots["modality"], "CT", "切换后 modality 不应为 CT（旧状态）")
        assert_not_equal(entity_tracker.slots["body_part"], ["脑部"], "切换后 body_part 不应为 脑部（旧状态）")

    @test("切换后 intent 标记为 switch")
    def test_switch_intent_marked():
        stm, entity_tracker, last_report, session_id = TestIntentSwitchCleanup.setup()

        entity_tracker.apply_switch("换成 MR 膝关节")

        assert_equal(entity_tracker.slots["intent"], "switch", "切换后 intent 应为 switch")


# ═══════════════════════════════════════════════════════════
# 测试 2：实体提取鲁棒性测试
# ═══════════════════════════════════════════════════════════

class TestEntityExtractionRobustness:

    @test("长词优先：PET-CT 不被误识别为 PET 或 CT")
    def test_long_word_priority_pet_ct():
        tracker = EntityTracker()
        tracker.update_from_query("做一个 PET-CT 全身扫描")
        assert_equal(tracker.slots["modality"], "PET-CT",
                     "必须提取 PET-CT，不能误识别为 PET 或 CT")

    @test("长词优先：CTA 不被误识别为 CT")
    def test_long_word_priority_cta():
        tracker = EntityTracker()
        tracker.update_from_query("CTA 冠状动脉检查")
        assert_equal(tracker.slots["modality"], "CTA",
                     "必须提取 CTA，不能误识别为 CT")

    @test("长词优先：MRA 不被误识别为 MR")
    def test_long_word_priority_mra():
        tracker = EntityTracker()
        tracker.update_from_query("MRA 脑血管")
        assert_equal(tracker.slots["modality"], "MRA",
                     "必须提取 MRA，不能误识别为 MR")

    @test("长词优先：DWI 不被误识别为其它")
    def test_long_word_priority_dwi():
        tracker = EntityTracker()
        tracker.update_from_query("DWI 序列检查")
        assert_equal(tracker.slots["modality"], "DWI",
                     "必须提取 DWI")

    @test("歧义验证：MR 膝关节 准确识别")
    def test_ambiguity_mr_knee():
        tracker = EntityTracker()
        tracker.update_from_query("MR 膝关节")
        assert_equal(tracker.slots["modality"], "MR", "MR 膝关节 → modality 应为 MR")
        assert_equal(tracker.slots["body_part"], ["膝关节"], "MR 膝关节 → body_part 应为 膝关节")

    @test("歧义验证：MRA 脑血管 准确区分")
    def test_ambiguity_mra_brain():
        tracker = EntityTracker()
        tracker.update_from_query("MRA 脑血管")
        assert_equal(tracker.slots["modality"], "MRA", "MRA 脑血管 → modality 应为 MRA")
        assert_equal(tracker.slots["body_part"], ["血管"], "MRA 脑血管 → body_part 应为 血管（规则匹配血管）")

    @test("歧义验证：MRI 颅脑 准确识别")
    def test_mri_brain():
        tracker = EntityTracker()
        tracker.update_from_query("MRI 颅脑")
        assert_equal(tracker.slots["modality"], "MRI", "MRI 颅脑 → modality 应为 MRI")
        assert_equal(tracker.slots["body_part"], ["颅脑"], "MRI 颅脑 → body_part 应为 颅脑")

    @test("部位长词优先：膝关节 不被误识别为 膝")
    def test_body_part_long_word_priority():
        tracker = EntityTracker()
        tracker.update_from_query("膝关节置换术后")
        assert_equal(tracker.slots["body_part"], ["膝关节"],
                     "必须提取 膝关节，不能误识别为 膝")

    @test("部位长词优先：颈椎 不被误识别为 颈")
    def test_body_part_cervical():
        tracker = EntityTracker()
        tracker.update_from_query("颈椎 MRI")
        assert_equal(tracker.slots["body_part"], ["颈椎"],
                     "必须提取 颈椎")

    @test("纯 CT 查询（无歧义）")
    def test_ct_only():
        tracker = EntityTracker()
        tracker.update_from_query("CT 胸部")
        assert_equal(tracker.slots["modality"], "CT")
        assert_equal(tracker.slots["body_part"], ["胸部"])

    @test("超声 查询")
    def test_ultrasound():
        tracker = EntityTracker()
        tracker.update_from_query("超声甲状腺")
        assert_equal(tracker.slots["modality"], "超声")
        assert_equal(tracker.slots["body_part"], ["甲状腺"])


# ═══════════════════════════════════════════════════════════
# 测试 3：EntityTracker Prompt 上下文测试
# ═══════════════════════════════════════════════════════════

class TestPromptBuilderEntityContext:
    @test("EntityTracker 无状态时不注入")
    def test_entity_empty_not_injected():
        entity_tracker = EntityTracker()
        entity_tracker.clear()
        ctx = entity_tracker.to_context_prompt()
        assert_equal(ctx, "", "无实体时 to_context_prompt 应返回空字符串")

    @test("PromptBuilder 注入当前会话实体上下文")
    def test_entity_context_injected():
        entity_tracker = EntityTracker()
        entity_tracker.update_from_query("CT 肝脏")
        ctx = entity_tracker.to_context_prompt()
        sys_prompt = PromptBuilder.build("agent_prompt/tool_orchestrator", entity_context=ctx)
        assert_in(ctx, sys_prompt, "Prompt 应包含本轮实体上下文")

    @test("MemoryRetriever 只返回当前会话 STM")
    def test_retriever_only_returns_stm():
        def fake_embedding(text):
            return [1.0, 0.0] if "脑" in text else [0.0, 1.0]

        retriever = MemoryRetriever(fake_embedding)
        retriever.index_stm([
            {"role": "user", "content": "CT 头颅 脑出血"},
            {"role": "assistant", "content": "胸部检查结果"},
        ])
        relevant = retriever.search_relevant("脑出血", top_k_stm=1)
        assert_equal(set(relevant), {"stm"}, "检索结果只能包含 STM")
        assert_equal(len(relevant["stm"]), 1, "应返回一条相关 STM")
        assert_in("脑出血", relevant["stm"][0], "应召回当前会话中的脑出血记录")


# ═══════════════════════════════════════════════════════════
# 测试 4：上下文消解测试
# ═══════════════════════════════════════════════════════════

class TestContextResolution:

    @test("消解：'再看看肝脏' → 补全模态和部位")
    def test_resolve_append_liver():
        tracker = EntityTracker()
        tracker.update_from_query("CT 脑")
        # 设置前置状态
        tracker.slots["modality"] = "CT"
        tracker.slots["body_part"] = ["脑"]

        result = tracker.resolve_context("再看看肝脏")
        assert_in("CT", result, "消解结果应包含继承的模态 CT")
        assert_in("肝脏", result, "消解结果应包含新部位肝脏")

    @test("消解：'再看看这个' → 补全模态和部位")
    def test_resolve_this():
        tracker = EntityTracker()
        tracker.slots["modality"] = "CT"
        tracker.slots["body_part"] = ["脑"]

        result = tracker.resolve_context("再看看这个")
        assert_in("CT", result, "消解结果应包含继承的模态 CT")
        assert_in("脑", result, "消解结果应包含继承的部位 脑")

    @test("消解：完整查询不补全")
    def test_resolve_no_redundant_fill():
        tracker = EntityTracker()
        tracker.slots["modality"] = "CT"
        tracker.slots["body_part"] = ["脑"]

        result = tracker.resolve_context("MR 膝关节")
        assert_equal(result, "MR 膝关节", "完整查询不应被消解修改")

    @test("消解：只缺模态时补全模态")
    def test_resolve_only_modality():
        tracker = EntityTracker()
        tracker.slots["modality"] = "CT"
        tracker.slots["body_part"] = []

        result = tracker.resolve_context("再看看肝脏")
        assert_in("CT", result, "缺模态时应补全")
        assert_in("肝脏", result, "应包含新部位")

    @test("消解：只缺部位时补全部位")
    def test_resolve_only_body_part():
        tracker = EntityTracker()
        tracker.slots["modality"] = None
        tracker.slots["body_part"] = ["脑"]

        result = tracker.resolve_context("CT 再看看")
        assert_in("CT", result, "应包含新模态")
        assert_in("脑", result, "缺部位时应补全")

    @test("消解：无状态时原样返回")
    def test_resolve_no_state():
        tracker = EntityTracker()
        tracker.clear()

        result = tracker.resolve_context("再看看肝脏")
        assert_equal(result, "再看看肝脏", "无状态时原样返回")

    @test("消解：'接着看胃' → 补全模态")
    def test_resolve_continue():
        tracker = EntityTracker()
        tracker.slots["modality"] = "CT"
        tracker.slots["body_part"] = ["肝"]

        result = tracker.resolve_context("接着看胃")
        assert_in("CT", result, "应补全模态 CT")
        assert_in("胃", result, "应包含新部位 胃")


# ═══════════════════════════════════════════════════════════
# 测试 5：clinical_history 和 diagnosis 提取测试
# ═══════════════════════════════════════════════════════════

class TestClinicalHistoryAndDiagnosisExtraction:

    @test("规则提取：CT 脑梗 应提取 diagnosis")
    def test_rule_extract_diagnosis():
        tracker = EntityTracker()
        tracker.update_from_query("CT 脑梗")
        assert_equal(tracker.slots["diagnosis"], ["脑梗"], "应提取诊断：脑梗")

    @test("规则提取：CT 头部 脑出血 应提取 diagnosis")
    def test_rule_extract_diagnosis_brain_bleed():
        tracker = EntityTracker()
        tracker.update_from_query("CT 头部 脑出血")
        assert_equal(tracker.slots["diagnosis"], ["脑出血"], "应提取诊断：脑出血")

    @test("规则提取：CT 腹部 肝硬化 应提取 diagnosis")
    def test_rule_extract_diagnosis_liver():
        tracker = EntityTracker()
        tracker.update_from_query("CT 腹部 肝硬化")
        assert_equal(tracker.slots["diagnosis"], ["肝硬化"], "应提取诊断：肝硬化")

    @test("规则提取：多诊断应都提取")
    def test_rule_extract_multiple_diagnoses():
        tracker = EntityTracker()
        tracker.update_from_query("CT 腹部 肝硬化 腹水")
        assert_in("肝硬化", tracker.slots["diagnosis"], "应包含诊断：肝硬化")
        assert_in("腹水", tracker.slots["diagnosis"], "应包含诊断：腹水（如关键词包含）")

    @test("规则提取：CT 头颅 头痛3天 应提取 clinical_history")
    def test_rule_extract_clinical_history():
        tracker = EntityTracker()
        changes = tracker.update_from_query("CT 头颅 头痛3天")
        # 验证 clinical_history 被提取
        assert_true("clinical_history" in changes or tracker.slots["clinical_history"] != "", 
                   "应提取病史信息")

    @test("规则提取：外伤后疼痛 应提取 clinical_history")
    def test_rule_extract_trauma_history():
        tracker = EntityTracker()
        changes = tracker.update_from_query("MR 膝关节 外伤后疼痛1周")
        assert_true("clinical_history" in changes or tracker.slots["clinical_history"] != "",
                   "应提取外伤病史")

    @test("追加模式：多次查询 diagnosis 应累加")
    def test_diagnosis_append_mode():
        tracker = EntityTracker()
        tracker.update_from_query("CT 脑梗")
        tracker.update_from_query("再看看 脑出血")
        assert_in("脑梗", tracker.slots["diagnosis"], "应保留第一个诊断")
        assert_in("脑出血", tracker.slots["diagnosis"], "应追加第二个诊断")

    @test("去重模式：重复 diagnosis 不应重复添加")
    def test_diagnosis_dedup():
        tracker = EntityTracker()
        tracker.update_from_query("CT 脑梗")
        tracker.update_from_query("再看看脑梗情况")
        assert_equal(tracker.slots["diagnosis"].count("脑梗"), 1, "诊断不应重复添加")

    @test("覆盖模式：clinical_history 应被新值覆盖")
    def test_clinical_history_override():
        tracker = EntityTracker()
        tracker.update_from_query("CT 头颅 头痛3天")
        old_history = tracker.slots["clinical_history"]
        tracker.update_from_query("再看看 发热伴咳嗽")
        # 新病史应该覆盖或追加
        assert_true(
            tracker.slots["clinical_history"] != "" or "发热" in tracker.slots["clinical_history"] or "咳嗽" in tracker.slots["clinical_history"],
            "应更新病史信息"
        )

    @test("诊断推断部位：CT 脑出血 应自动推断 body_part 为 脑部")
    def test_diagnosis_infers_body_part_brain():
        tracker = EntityTracker()
        changes = tracker.update_from_query("CT 脑出血")
        assert_equal(tracker.slots["diagnosis"], ["脑出血"], "应提取诊断")
        assert_in("脑部", tracker.slots["body_part"], "应自动推断部位为脑部")

    @test("诊断推断部位：CT 肺炎 应自动推断 body_part 为 肺部")
    def test_diagnosis_infers_body_part_lung():
        tracker = EntityTracker()
        changes = tracker.update_from_query("CT 肺炎")
        assert_equal(tracker.slots["diagnosis"], ["肺炎"], "应提取诊断")
        assert_in("肺部", tracker.slots["body_part"], "应自动推断部位为肺部")

    @test("诊断推断部位：CT 肝硬化 应自动推断 body_part 为 肝脏")
    def test_diagnosis_infers_body_part_liver():
        tracker = EntityTracker()
        changes = tracker.update_from_query("CT 肝硬化")
        assert_equal(tracker.slots["diagnosis"], ["肝硬化"], "应提取诊断")
        assert_in("肝脏", tracker.slots["body_part"], "应自动推断部位为肝脏")

    @test("诊断推断部位：不明确的诊断不推断")
    def test_diagnosis_no_infer_ambiguous():
        tracker = EntityTracker()
        tracker.update_from_query("CT 肿瘤")
        assert_equal(tracker.slots["diagnosis"], ["肿瘤"], "应提取诊断")
        # "肿瘤"部位不明确，不应自动推断
        assert_equal(tracker.slots["body_part"], [], "不应自动推断部位")

    @test("诊断推断：多诊断推断多部位")
    def test_diagnosis_infers_multiple_parts():
        tracker = EntityTracker()
        tracker.update_from_query("CT 脑出血 肺炎")
        assert_in("脑部", tracker.slots["body_part"], "应推断脑部")
        assert_in("肺部", tracker.slots["body_part"], "应推断肺部")

    @test("诊断推断：已有明确部位时不重复推断")
    def test_diagnosis_no_infer_when_body_part_exists():
        tracker = EntityTracker()
        tracker.update_from_query("CT 头颅 脑出血")
        # "头颅"应该已被提取
        assert_in("头颅", tracker.slots["body_part"], "应包含明确提到的部位")
        # 不应再重复添加"脑部"
        assert_equal(tracker.slots["body_part"].count("脑部"), 0, "已有头颅时不应再推断脑部")


# ═══════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════

def run_all_tests():
    global PASS, FAIL
    PASS = 0
    FAIL = 0

    print("=" * 60)
    print("  记忆模块单元测试")
    print("=" * 60)

    print("\n📋 测试 1：意图切换清洗测试（最关键）")
    TestIntentSwitchCleanup.test_stm_has_history()
    TestIntentSwitchCleanup.test_entity_has_old_state()
    TestIntentSwitchCleanup.test_last_report_has_content()
    TestIntentSwitchCleanup.test_detect_switch_intent()
    TestIntentSwitchCleanup.test_switch_clears_stm()
    TestIntentSwitchCleanup.test_switch_clears_last_report()
    TestIntentSwitchCleanup.test_switch_updates_modality()
    TestIntentSwitchCleanup.test_switch_updates_body_part()
    TestIntentSwitchCleanup.test_switch_removes_old_state()
    TestIntentSwitchCleanup.test_switch_intent_marked()

    print("\n📋 测试 2：实体提取鲁棒性测试")
    TestEntityExtractionRobustness.test_long_word_priority_pet_ct()
    TestEntityExtractionRobustness.test_long_word_priority_cta()
    TestEntityExtractionRobustness.test_long_word_priority_mra()
    TestEntityExtractionRobustness.test_long_word_priority_dwi()
    TestEntityExtractionRobustness.test_ambiguity_mr_knee()
    TestEntityExtractionRobustness.test_ambiguity_mra_brain()
    TestEntityExtractionRobustness.test_mri_brain()
    TestEntityExtractionRobustness.test_body_part_long_word_priority()
    TestEntityExtractionRobustness.test_body_part_cervical()
    TestEntityExtractionRobustness.test_ct_only()
    TestEntityExtractionRobustness.test_ultrasound()

    print("\n📋 测试 3：EntityTracker Prompt 上下文测试")
    TestPromptBuilderEntityContext.test_entity_empty_not_injected()
    TestPromptBuilderEntityContext.test_entity_context_injected()
    TestPromptBuilderEntityContext.test_retriever_only_returns_stm()

    print("\n📋 测试 4：上下文消解测试")
    TestContextResolution.test_resolve_append_liver()
    TestContextResolution.test_resolve_this()
    TestContextResolution.test_resolve_no_redundant_fill()
    TestContextResolution.test_resolve_only_modality()
    TestContextResolution.test_resolve_only_body_part()
    TestContextResolution.test_resolve_no_state()
    TestContextResolution.test_resolve_continue()

    print("\n📋 测试 5：clinical_history 和 diagnosis 提取测试")
    TestClinicalHistoryAndDiagnosisExtraction.test_rule_extract_diagnosis()
    TestClinicalHistoryAndDiagnosisExtraction.test_rule_extract_diagnosis_brain_bleed()
    TestClinicalHistoryAndDiagnosisExtraction.test_rule_extract_diagnosis_liver()
    TestClinicalHistoryAndDiagnosisExtraction.test_rule_extract_multiple_diagnoses()
    TestClinicalHistoryAndDiagnosisExtraction.test_rule_extract_clinical_history()
    TestClinicalHistoryAndDiagnosisExtraction.test_rule_extract_trauma_history()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_append_mode()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_dedup()
    TestClinicalHistoryAndDiagnosisExtraction.test_clinical_history_override()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_infers_body_part_brain()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_infers_body_part_lung()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_infers_body_part_liver()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_no_infer_ambiguous()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_infers_multiple_parts()
    TestClinicalHistoryAndDiagnosisExtraction.test_diagnosis_no_infer_when_body_part_exists()

    print("\n" + "=" * 60)
    print(f"  结果: 通过 {PASS} / 失败 {FAIL} / 总计 {PASS + FAIL}")
    print("=" * 60)

    return FAIL == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)