import { createHash } from "node:crypto";
import { buildDeterministicPreview } from "../domain/parser.js";

const DEFAULT_BASE_URL = "http://14.22.86.97:11001/v1";
const DEFAULT_MODEL = "qwen36-27b";

export const ALLOWED_FIELDS = {
  "scenario": { enum: ["normal", "lesion"] },
  "exam.indication": { type: "string" },
  "acquisition.quality": { enum: ["satisfactory", "acceptable", "limited"] },
  "comparison.status": { enum: ["none", "compared", "limited"] },
  "breastTissue.fgt": { enum: ["A", "B", "C", "D"] },
  "breastTissue.bpeLevel": { enum: ["minimal", "mild", "moderate", "marked"] },
  "breastTissue.bpeSymmetry": { enum: ["symmetric", "asymmetric"] },
  "associatedStructures.status": { enum: ["normal", "abnormal", "limited"] },
  "associatedStructures.detail": { type: "string" },
  "lymphNodes.status": { enum: ["normal", "abnormal", "limited"] },
  "lymphNodes.detail": { type: "string" },
  "lesion.type": { enum: ["mass", "nme"] },
  "lesion.laterality": { enum: ["left", "right"] },
  "lesion.clock": { type: "number", min: 1, max: 12 },
  "lesion.distanceCm": { type: "number", min: 0 },
  "lesion.depth": { enum: ["anterior", "middle", "posterior"] },
  "lesion.size.ap": { type: "number", min: 0 },
  "lesion.size.transverse": { type: "number", min: 0 },
  "lesion.size.cc": { type: "number", min: 0 },
  "lesion.shape": { enum: ["oval", "lobulated", "round", "irregular"] },
  "lesion.margin": { enum: ["circumscribed", "indistinct", "spiculated"] },
  "lesion.distribution": { enum: ["diffuse", "regional", "focal", "linear", "segmental"] },
  "lesion.internalEnhancement": {
    enum: ["homogeneous", "heterogeneous", "rim", "darkSeptations", "clumped", "clusteredRing"]
  },
  "lesion.t2": { enum: ["high", "notHigh", "limited"] },
  "lesion.kinetics.early": { enum: ["slow", "medium", "fast", "limited"] },
  "lesion.kinetics.delayed": { enum: ["persistent", "plateau", "washout", "limited"] },
  "lesion.dwi.signal": { enum: ["high", "equal", "low", "limited"] },
  "lesion.dwi.adcMin": { type: "number", min: 0 },
  "lesion.dwi.bValue": { type: "number", min: 0 },
  "lesion.dwi.roiMethod": { type: "string" },
  "lesion.assessment.birads": { enum: ["0", "1", "2", "3", "4", "5", "6"], physicianOnly: true },
  "lesion.management.action": {
    enum: ["routine", "shortInterval", "tissueDiagnosis", "surgicalOncology", "additionalImaging"],
    physicianOnly: true
  },
  "lesion.management.detail": { type: "string", physicianOnly: true },
  "lesion.pathology.reference": { type: "string", physicianOnly: true },
  "assessment.leftBirads": { enum: ["0", "1", "2", "3", "4", "5", "6"], physicianOnly: true },
  "assessment.rightBirads": { enum: ["0", "1", "2", "3", "4", "5", "6"], physicianOnly: true },
  "management.action": {
    enum: ["routine", "shortInterval", "tissueDiagnosis", "surgicalOncology", "additionalImaging"],
    physicianOnly: true
  },
  "management.detail": { type: "string", physicianOnly: true }
};

const SYSTEM_PROMPT = `你是乳腺MRI报告会话Agent。系统已经加载一份正常报告基线，医生通常只描述异常。你的任务是把医生明确观察或确认的异常事实提取为结构化候选字段。

安全边界：
1. 只提取医生本轮明确说出的事实，不得依据常识或诊断反推未描述内容。
2. 空值、未知、未观察不得变成“正常”。
3. 只有医生明确说明双侧未见可疑肿块或非肿块强化时，才能把scenario设为normal；描述病灶时设为lesion。
4. 不得根据ADC、强化曲线、毛刺或任何积分自动生成BI-RADS类别。
5. BI-RADS类别和管理建议只有医生明确口述时才能作为候选字段；不得替医生决定。异常场景必须写入当前病灶的lesion.assessment.birads、lesion.management.*，不得把一个病灶的分类复制到同侧其他病灶；assessment.leftBirads/rightBirads仅用于无病灶的整体正常评估。
6. 不生成新病灶，不修改数值，不作病理类型推断。
7. 不要询问正常基线、常规缺省、可选描述、图像质量或比较情况。FGT/BPE若医生主动描述则提取；若缺失，由界面的“乳腺背景”合并确认卡处理，不在对话中逐项追问。
8. 每轮最多提出1个问题。只有侧别/病灶类型冲突、当前病灶BI-RADS、BI-RADS 4/5取样路径或BI-RADS 6病理依据会阻断当前报告时才询问；不得为补充基础表头或乳腺背景发起对话追问。
9. 不要逐字段罗列缺失内容，不要把模板填写变成问卷。
10. 只输出JSON，不要输出Markdown代码围栏。

允许的字段路径与取值：
${JSON.stringify(ALLOWED_FIELDS)}

输出格式：
{
  "summary": "对本轮提取结果的简短中文概括",
  "candidate_updates": [
    {"path":"允许字段路径","value":"符合定义的值","source_text":"医生原话","confidence":0.0}
  ],
  "conflicts": [
    {"path":"字段路径","current_value":"已有值","candidate_value":"本轮值","reason":"冲突说明"}
  ],
  "questions": [
    {"path":"需要确认的字段路径","question":"给医生的简短问题","priority":"blocking|warning","options":["可选标准答案"]}
  ],
  "assistant_message": "面向医生的本轮回复，先总结已提取内容，再分组提出关键问题"
}`;

function stripPrivateState(currentCase = {}) {
  const {
    exam, acquisition, comparison, breastTissue, associatedStructures,
    lymphNodes, lesion, assessment, management, scenario, provenance
  } = currentCase;
  const { uid: _lesionUid, provenance: _lesionProvenance, ...compactLesion } = lesion || {};
  const state = {
    scenario: provenance?.scenario ? scenario : "",
    exam, acquisition, comparison, breastTissue, associatedStructures,
    lymphNodes, lesion: compactLesion, assessment, management
  };
  const clone = structuredClone(state);
  for (const [path, metadata] of Object.entries(provenance || {})) {
    if (metadata?.sourceType !== "institutional_normal_default") continue;
    const keys = path.split(".");
    let cursor = clone;
    for (const key of keys.slice(0, -1)) cursor = cursor?.[key];
    if (cursor) cursor[keys.at(-1)] = "";
  }
  return clone;
}

function parseJsonContent(content) {
  const cleaned = String(content || "")
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();
  return JSON.parse(cleaned);
}

function validValue(rule, value) {
  if (rule.enum) return rule.enum.includes(String(value));
  if (rule.type === "string") return typeof value === "string" && value.trim().length > 0;
  if (rule.type === "number") {
    const number = Number(value);
    return Number.isFinite(number)
      && (rule.min === undefined || number >= rule.min)
      && (rule.max === undefined || number <= rule.max);
  }
  return false;
}

export function normalizeAgentResult(raw) {
  const candidateUpdates = Array.isArray(raw?.candidate_updates)
    ? raw.candidate_updates.flatMap((update) => {
      const rule = ALLOWED_FIELDS[update?.path];
      if (!rule || !validValue(rule, update.value)) return [];
      const value = rule.type === "number" ? Number(update.value) : String(update.value);
      return [{
        path: update.path,
        value,
        sourceText: String(update.source_text || ""),
        confidence: Math.max(0, Math.min(1, Number(update.confidence) || 0)),
        requiresExplicitPhysicianConfirmation: Boolean(rule.physicianOnly),
        sourceType: String(update.source_type || "llm_semantic")
      }];
    })
    : [];

  const conflicts = Array.isArray(raw?.conflicts)
    ? raw.conflicts.filter((item) => ALLOWED_FIELDS[item?.path]).slice(0, 10).map((item) => ({
      path: item.path,
      currentValue: item.current_value,
      candidateValue: item.candidate_value,
      reason: String(item.reason || "与已有字段不一致")
    }))
    : [];

  const questions = Array.isArray(raw?.questions)
    ? raw.questions.filter((item) => ALLOWED_FIELDS[item?.path]).slice(0, 1).map((item) => ({
      path: item.path,
      question: String(item.question || ""),
      priority: item.priority === "warning" ? "warning" : "blocking",
      options: Array.isArray(item.options) ? item.options.map(String).slice(0, 8) : []
    })).filter((item) => item.question)
    : [];

  return {
    summary: String(raw?.summary || "已完成本轮事实提取"),
    candidateUpdates,
    conflicts,
    questions,
    assistantMessage: String(raw?.assistant_message || raw?.summary || "请确认本轮提取结果。")
  };
}

function localFallback(message, error) {
  const candidateUpdates = buildDeterministicPreview(message).map((update) => ({
    path: update.path,
    value: update.value,
    source_text: update.sourceText,
    source_type: "deterministic_parser",
    confidence: 1
  }));
  const normalized = normalizeAgentResult({
    summary: `本地规则提取 ${candidateUpdates.length} 个候选字段`,
    candidate_updates: candidateUpdates,
    conflicts: [],
    questions: [],
    assistant_message: candidateUpdates.length
      ? "远程模型暂不可用，已使用本地保守规则提取明确事实；请逐项确认候选。"
      : "远程模型暂不可用，本地规则未识别出可安全写入的明确事实；本轮未修改结构。"
  });
  return {
    ...normalized,
    model: "local-rule-fallback",
    usage: null,
    degraded: true,
    fallbackReason: String(error?.message || "远程模型不可用").slice(0, 300)
  };
}

function applyDeterministicAnchors(message, normalizedResult) {
  const rawAnchors = buildDeterministicPreview(message).map((update) => ({
    path: update.path,
    value: update.value,
    source_text: update.sourceText,
    source_type: "deterministic_parser",
    confidence: 1
  }));
  const anchors = normalizeAgentResult({ candidate_updates: rawAnchors }).candidateUpdates;
  if (!anchors.length) return { ...normalizedResult, deterministicAnchors: [] };
  const merged = new Map(normalizedResult.candidateUpdates.map((item) => [item.path, item]));
  for (const anchor of anchors) merged.set(anchor.path, anchor);
  return {
    ...normalizedResult,
    candidateUpdates: [...merged.values()],
    deterministicAnchors: anchors.map((item) => item.path)
  };
}

export function createAgentService(options = {}) {
  const baseUrl = String(options.baseUrl || process.env.VLLM_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
  const model = options.model || process.env.VLLM_MODEL || DEFAULT_MODEL;
  const fetchImpl = options.fetchImpl || fetch;
  const cacheTtlMs = Number(options.cacheTtlMs ?? 30_000);
  const resultCache = new Map();
  const inFlight = new Map();

  const requestKey = (message, currentCase) => createHash("sha256")
    .update(JSON.stringify({ message: message.trim(), state: stripPrivateState(currentCase) }))
    .digest("hex");

  const rememberResult = (key, value) => {
    resultCache.set(key, { value: structuredClone(value), expiresAt: Date.now() + cacheTtlMs });
    if (resultCache.size > 64) resultCache.delete(resultCache.keys().next().value);
  };

  async function modelStatus() {
    const response = await fetchImpl(`${baseUrl}/models`, {
      signal: AbortSignal.timeout(10_000)
    });
    if (!response.ok) throw new Error(`模型列表请求失败：HTTP ${response.status}`);
    const payload = await response.json();
    return {
      online: payload.data?.some((item) => item.id === model) || false,
      model,
      baseUrl
    };
  }

  async function runTurn({ message, currentCase }) {
    if (!message || typeof message !== "string" || message.trim().length === 0) {
      throw new Error("医生本轮输入不能为空");
    }
    if (message.length > 12_000) throw new Error("本轮输入超过12000字符限制");

    const startedAt = performance.now();
    const key = requestKey(message, currentCase);
    const cached = resultCache.get(key);
    if (cached && cached.expiresAt > Date.now()) {
      return {
        ...structuredClone(cached.value),
        timing: { ...cached.value.timing, totalMs: Math.round(performance.now() - startedAt), cacheHit: true }
      };
    }
    if (inFlight.has(key)) {
      const shared = await inFlight.get(key);
      return {
        ...structuredClone(shared),
        timing: { ...shared.timing, totalMs: Math.round(performance.now() - startedAt), cacheHit: true, deduplicated: true }
      };
    }

    const execute = async () => {
      const modelStartedAt = performance.now();
      try {
      const response = await fetchImpl(`${baseUrl}/chat/completions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: AbortSignal.timeout(60_000),
        body: JSON.stringify({
          model,
          temperature: 0.1,
          max_tokens: 1800,
          response_format: { type: "json_object" },
          messages: [
            { role: "system", content: SYSTEM_PROMPT },
            {
              role: "user",
              content: JSON.stringify({
                current_confirmed_state: stripPrivateState(currentCase),
                physician_message: message,
                instruction: "只提取本轮明确事实。机构正常基线字段已从current_confirmed_state中清空，可被异常描述直接覆盖，不算冲突。只有已由医生确认的非空值与新值不同时列入conflicts。若必须追问，只提出1个会阻断诊断或管理的关键问题。"
              })
            }
          ]
        })
      });
      if (!response.ok) {
        const detail = await response.text();
        throw new Error(`模型调用失败：HTTP ${response.status} ${detail.slice(0, 300)}`);
      }
      const payload = await response.json();
      const content = payload.choices?.[0]?.message?.content;
      if (!content) throw new Error("模型未返回有效内容");
      const result = {
        ...applyDeterministicAnchors(message, normalizeAgentResult(parseJsonContent(content))),
        model,
        usage: payload.usage || null,
        degraded: false,
        timing: {
          modelMs: Math.round(performance.now() - modelStartedAt),
          totalMs: Math.round(performance.now() - startedAt),
          cacheHit: false
        }
      };
      return result;
      } catch (error) {
        if (options.disableFallback) throw error;
        return {
          ...localFallback(message, error),
          timing: {
            modelMs: Math.round(performance.now() - modelStartedAt),
            totalMs: Math.round(performance.now() - startedAt),
            cacheHit: false
          }
        };
      }
    };

    const promise = execute();
    inFlight.set(key, promise);
    try {
      const result = await promise;
      rememberResult(key, result);
      return result;
    } finally {
      inFlight.delete(key);
    }
  }

  return { modelStatus, runTurn, config: { baseUrl, model } };
}
