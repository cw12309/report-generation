import { createEmptyCase, createLesion, getValue, normalizeLesion, setValue } from "./domain/model.js";
import {
  candidateForPath,
  confirmCandidateIds,
  criticalReviewCandidates,
  directEvidenceCandidates,
  discardCandidates,
  ensureCandidateState,
  pendingCandidateCount,
  pendingCandidates,
  provenanceForPath,
  recordInteractionEvent,
  syncLegacyPendingPaths,
  upsertCandidate
} from "./domain/candidateLedger.js";
import { buildDeterministicPreview } from "./domain/parser.js";
import { validateCase } from "./domain/validator.js";
import { renderReport } from "./domain/reportRenderer.js";

const STORAGE_KEY = "breast-mri-report-mvp-v3-source-aware";
const numberPaths = new Set([
  "lesion.clock", "lesion.distanceCm", "lesion.size.ap", "lesion.size.transverse",
  "lesion.size.cc", "lesion.dwi.adcMin", "lesion.dwi.bValue"
]);

const massSample = `右乳10点方向、距乳头约4 cm、中1/3见12×8×10 mm不规则肿块，边缘毛刺，内部不均匀强化，T2非高信号，早期快速强化，延迟期廓清。DWI高信号，ADCmin=0.92，b值800，ROI为实性强化区。`;
const normalSample = `双侧乳腺未见可疑肿块或非肿块强化，乳头、皮肤、胸肌及胸壁未见明确受累，区域淋巴结未见明显异常。`;

const uiLabels = {
  laterality: { left: "左乳", right: "右乳" },
  lesionType: { mass: "MASS", nme: "NME" },
  depth: { anterior: "前1/3", middle: "中1/3", posterior: "后1/3" },
  shape: { oval: "卵圆形", lobulated: "分叶状", round: "圆形", irregular: "不规则形" },
  margin: { circumscribed: "边缘清楚", indistinct: "边缘模糊", spiculated: "边缘毛刺" },
  distribution: { diffuse: "弥漫性", regional: "区域性", focal: "局灶性", linear: "线样", segmental: "节段性" },
  enhancement: {
    homogeneous: "均匀强化", heterogeneous: "不均匀强化", rim: "厚环形强化",
    darkSeptations: "内部无强化分隔", clumped: "簇集状强化", clusteredRing: "簇状环形强化"
  },
  early: { slow: "慢速", medium: "中速", fast: "快速", limited: "受限" },
  delayed: { persistent: "持续型", plateau: "平台型", washout: "廓清型", limited: "受限" },
  dwi: { high: "DWI高", equal: "DWI等", low: "DWI低", limited: "DWI受限" },
  bpe: { minimal: "极少", mild: "轻度", moderate: "中度", marked: "显著" },
  symmetry: { symmetric: "对称", asymmetric: "不对称" },
  management: {
    routine: "常规随访", shortInterval: "短期随访", tissueDiagnosis: "组织学诊断",
    surgicalOncology: "外科/肿瘤科管理", additionalImaging: "补充影像检查"
  }
};

const lesionQuickOptions = {
  "lesion.type": [
    { value: "mass", label: "MASS 肿块" },
    { value: "nme", label: "NME 非肿块强化" }
  ],
  "lesion.laterality": [
    { value: "left", label: "左乳" },
    { value: "right", label: "右乳" }
  ],
  "lesion.clock": Array.from({ length: 12 }, (_, index) => ({ value: index + 1, label: `${index + 1}点` })),
  "lesion.depth": [
    { value: "anterior", label: "前1/3" },
    { value: "middle", label: "中1/3" },
    { value: "posterior", label: "后1/3" }
  ],
  "lesion.shape": [
    { value: "oval", label: "卵圆形" },
    { value: "round", label: "圆形" },
    { value: "lobulated", label: "分叶状" },
    { value: "irregular", label: "不规则" }
  ],
  "lesion.margin": [
    { value: "circumscribed", label: "边缘清楚" },
    { value: "indistinct", label: "边缘不清" },
    { value: "spiculated", label: "边缘毛刺" }
  ],
  "lesion.distribution": [
    { value: "linear", label: "导管/线样" },
    { value: "segmental", label: "节段样" },
    { value: "focal", label: "局灶" },
    { value: "regional", label: "区域" },
    { value: "diffuse", label: "弥漫" }
  ],
  massEnhancement: [
    { value: "homogeneous", label: "均匀" },
    { value: "heterogeneous", label: "不均匀" },
    { value: "rim", label: "环形" },
    { value: "darkSeptations", label: "无强化分隔" }
  ],
  nmeEnhancement: [
    { value: "heterogeneous", label: "不均匀" },
    { value: "clumped", label: "簇集状" },
    { value: "clusteredRing", label: "簇状环形" }
  ],
  "lesion.t2": [
    { value: "high", label: "T2高信号" },
    { value: "notHigh", label: "T2非高" },
    { value: "limited", label: "评价受限" }
  ],
  "lesion.kinetics.early": [
    { value: "fast", label: "快速 >120%" },
    { value: "medium", label: "中速 90–120%" },
    { value: "slow", label: "慢速 <90%" },
    { value: "limited", label: "测量受限" }
  ],
  "lesion.kinetics.delayed": [
    { value: "washout", label: "III 型·廓清" },
    { value: "plateau", label: "II 型·平台" },
    { value: "persistent", label: "I 型·持续" },
    { value: "limited", label: "测量受限" }
  ],
  "lesion.dwi.signal": [
    { value: "high", label: "DWI高" },
    { value: "equal", label: "DWI等" },
    { value: "low", label: "DWI低" },
    { value: "limited", label: "DWI受限" }
  ],
  "lesion.assessment.birads": Array.from({ length: 7 }, (_, index) => ({
    value: String(index), label: `${index}类`
  })),
  "lesion.management.action": [
    { value: "routine", label: "常规随访" },
    { value: "shortInterval", label: "短期随访" },
    { value: "tissueDiagnosis", label: "组织学诊断" },
    { value: "surgicalOncology", label: "外科/肿瘤科" },
    { value: "additionalImaging", label: "补充影像" }
  ]
};

const DEFAULT_CHOICE_VALUE = "__default__";
const defaultManagementForBirads = (birads) => ({
  "0": "additionalImaging",
  "1": "routine",
  "2": "routine",
  "3": "shortInterval",
  "4": "tissueDiagnosis",
  "5": "tissueDiagnosis",
  "6": "surgicalOncology"
})[String(birads)] || "";

const graphQuickOptions = {
  "breastTissue.fgt": [
    { value: "A", label: "A 几乎全脂肪" },
    { value: "B", label: "B 散在纤维腺体" },
    { value: "C", label: "C 不均匀纤维腺体" },
    { value: "D", label: "D 极多纤维腺体" }
  ],
  "breastTissue.bpeLevel": [
    { value: "minimal", label: "极少" },
    { value: "mild", label: "轻度" },
    { value: "moderate", label: "中度" },
    { value: "marked", label: "显著" }
  ],
  "breastTissue.bpeSymmetry": [
    { value: "symmetric", label: "对称" },
    { value: "asymmetric", label: "不对称" }
  ],
  "associatedStructures.status": [
    { value: "normal", label: "正常基线" },
    { value: "abnormal", label: "异常" },
    { value: "limited", label: "评价受限" }
  ],
  "lymphNodes.status": [
    { value: "normal", label: "未见异常（基线）" },
    { value: "abnormal", label: "异常" },
    { value: "limited", label: "评价受限" }
  ],
  "assessment.leftBirads": Array.from({ length: 7 }, (_, index) => ({ value: String(index), label: `${index}类` })),
  "assessment.rightBirads": Array.from({ length: 7 }, (_, index) => ({ value: String(index), label: `${index}类` })),
  "management.action": [
    { value: "routine", label: "常规随访" },
    { value: "shortInterval", label: "短期随访" },
    { value: "tissueDiagnosis", label: "组织学诊断" },
    { value: "surgicalOncology", label: "外科/肿瘤科" },
    { value: "additionalImaging", label: "补充影像" }
  ]
};

const dom = Object.fromEntries([
  "dictation", "structuredForm", "extractButton", "massSampleButton", "normalSampleButton",
  "extractionNotice", "questionPanel", "gateCount", "generateButton", "reportContent",
  "physicianConfirm", "signButton", "signHint", "resetButton", "workflowSteps",
  "sessionBadge", "saveState", "lesionSection", "lesionIdLabel", "shapeField", "marginField",
  "distributionField", "toast", "modelStatus", "agentConversation", "structureSummary",
  "lesionOverview", "lesionCards", "addLesionButton", "voiceButton", "voiceStatus",
  "reportOutputButton", "reportOutputDialog", "closeReportOutputButton", "reportOutputState",
  "reportOutputPreview", "printGeneratedReportButton", "printStage", "exportTextButton"
].map((id) => [id, document.getElementById(id)]));

let data = loadCase();
let agentBusy = false;
let activeAgentRequest = 0;
let voiceRecognition = null;
let voiceListening = false;
let activeReportFormat = "structured";
let previousPrintTitle = "";
const graphManualOpen = new Set();

function loadCase() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    const empty = createEmptyCase();
    const loaded = {
      ...empty,
      ...saved,
      exam: { ...empty.exam, ...(saved?.exam || {}) },
      acquisition: {
        ...empty.acquisition,
        ...(saved?.acquisition || {}),
        contrast: { ...empty.acquisition.contrast, ...(saved?.acquisition?.contrast || {}) }
      },
      comparison: { ...empty.comparison, ...(saved?.comparison || {}) },
      breastTissue: { ...empty.breastTissue, ...(saved?.breastTissue || {}) },
      associatedStructures: { ...empty.associatedStructures, ...(saved?.associatedStructures || {}) },
      lymphNodes: { ...empty.lymphNodes, ...(saved?.lymphNodes || {}) },
      assessment: { ...empty.assessment, ...(saved?.assessment || {}) },
      management: { ...empty.management, ...(saved?.management || {}) },
      provenance: { ...empty.provenance, ...(saved?.provenance || {}) }
    };
    loaded.lesions = (Array.isArray(saved?.lesions)
      ? saved.lesions
      : saved?.scenario === "lesion" && saved?.lesion ? [saved.lesion] : [])
      .map((lesion, index) => normalizeLesion(lesion, lesion?.id || `L${index + 1}`));

    // V8兼容迁移：只有同侧唯一病灶时，才可把旧的侧别级评估安全迁入病灶级字段。
    for (const side of ["left", "right"]) {
      const sameSideLesions = loaded.lesions.filter((lesion) => lesion.laterality === side);
      const assessmentPath = `assessment.${side}Birads`;
      const overallSource = loaded.provenance?.[assessmentPath];
      if (sameSideLesions.length === 1
        && !sameSideLesions[0].assessment.birads
        && loaded.assessment?.[`${side}Birads`]
        && overallSource?.sourceType !== "institutional_normal_default") {
        sameSideLesions[0].assessment.birads = loaded.assessment[`${side}Birads`];
        if (!sameSideLesions[0].management.action
          && loaded.management?.action
          && loaded.provenance?.["management.action"]?.sourceType !== "institutional_normal_default") {
          sameSideLesions[0].management = { ...loaded.management };
        }
      }
    }
    loaded.activeLesionIndex = Math.min(
      Math.max(Number(saved?.activeLesionIndex) || 0, 0),
      Math.max(loaded.lesions.length - 1, 0)
    );
    loaded.lesion = loaded.lesions[loaded.activeLesionIndex] || createLesion();
    loaded.candidateLedgerVersion = saved?.candidateLedgerVersion || 0;
    loaded.candidateLedger = Array.isArray(saved?.candidateLedger) ? saved.candidateLedger : [];
    loaded.interactionEvents = Array.isArray(saved?.interactionEvents) ? saved.interactionEvents : [];
    loaded.agent = {
      messages: [],
      conflicts: [],
      lastModel: "",
      lastUsage: null,
      lastTiming: null,
      currentTurn: null,
      ...(loaded.agent || {})
    };
    ensureCandidateState(loaded);
    return loaded;
  } catch {
    return createEmptyCase();
  }
}

function lesionUidAt(index = data.activeLesionIndex) {
  return data.lesions?.[index]?.uid || "";
}

function scopedValue(path, lesionUid = lesionUidAt()) {
  if (!path.startsWith("lesion.")) return getValue(data, path);
  const lesion = data.lesions?.find((item) => item.uid === lesionUid);
  return lesion ? getValue(lesion, path.replace(/^lesion\./, "")) : undefined;
}

function setScopedValue(path, value, lesionUid = lesionUidAt()) {
  if (!path.startsWith("lesion.")) return setValue(data, path, value);
  const lesion = data.lesions?.find((item) => item.uid === lesionUid);
  if (!lesion) return;
  setValue(lesion, path.replace(/^lesion\./, ""), value);
  if (lesion.uid === lesionUidAt()) data.lesion = lesion;
}

function sourceForPath(path, lesionUid = lesionUidAt()) {
  return provenanceForPath(data, path, lesionUid) || data.provenance?.[path];
}

function isPathPending(path, lesionUid = lesionUidAt()) {
  return Boolean(candidateForPath(data, path, lesionUid))
    || (!data.candidateLedger?.length && data.pendingPaths.includes(path));
}

function renumberLesions() {
  let mass = 0;
  let nme = 0;
  data.lesions.forEach((lesion, index) => {
    lesion.id = lesion.type === "mass"
      ? `M${++mass}`
      : lesion.type === "nme" ? `N${++nme}` : `L${index + 1}`;
  });
}

function ensureActiveLesion() {
  if (!data.lesions.length) {
    data.lesions.push(data.lesion || createLesion());
    data.activeLesionIndex = 0;
  }
  data.activeLesionIndex = Math.min(data.activeLesionIndex, data.lesions.length - 1);
  data.lesion = data.lesions[data.activeLesionIndex];
  renumberLesions();
  syncLegacyPendingPaths(data);
  return data.lesion;
}

function hasUnconfirmedLesionCandidates() {
  return pendingCandidates(data).some((candidate) => candidate.scope === "lesion" || candidate.path === "scenario")
    || data.pendingPaths.some((path) => path === "scenario" || path.startsWith("lesion."));
}

function selectLesion(index, openEditor = false) {
  data.activeLesionIndex = index;
  data.lesion = data.lesions[index];
  syncLegacyPendingPaths(data);
  persist();
  render();
  if (openEditor) {
    dom.dictation.focus();
    dom.dictation.scrollIntoView({ behavior: "smooth", block: "center" });
    toast(`后续输入将更新 ${data.lesion.id}`);
  }
}

function addLesion() {
  if (data.signedAt) return;
  data.lesions.push(createLesion());
  data.activeLesionIndex = data.lesions.length - 1;
  data.lesion = data.lesions[data.activeLesionIndex];
  data.scenario = "lesion";
  renumberLesions();
  syncLegacyPendingPaths(data);
  recordInteractionEvent(data, "lesion_created", { lesionUid: data.lesion.uid, displayId: data.lesion.id });
  data.draft = null;
  graphManualOpen.add("findings");
  graphManualOpen.add(`lesion-${data.activeLesionIndex}`);
  persist();
  render();
  window.requestAnimationFrame(() => {
    dom.structureSummary.querySelector('[data-lesion-quick-path="lesion.type"]')?.focus();
  });
  toast(`已新增 ${data.lesion.id} 节点，请选择类型或直接口述`);
}

function removeLesion(index) {
  if (data.signedAt || !window.confirm(`确定删除病灶 ${data.lesions[index]?.id || index + 1} 吗？`)) return;
  const removedUid = data.lesions[index]?.uid;
  discardCandidates(data, (candidate) => candidate.lesionUid === removedUid, "lesion_removed");
  data.lesions.splice(index, 1);
  data.activeLesionIndex = Math.max(0, Math.min(index, data.lesions.length - 1));
  data.lesion = data.lesions[data.activeLesionIndex] || createLesion();
  data.scenario = data.lesions.length ? "lesion" : "normal";
  renumberLesions();
  syncLegacyPendingPaths(data);
  recordInteractionEvent(data, "lesion_removed", { lesionUid: removedUid });
  data.draft = null;
  persist();
  render();
}

function persist() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  dom.saveState.textContent = "刚刚已保存";
  window.clearTimeout(persist.timer);
  persist.timer = window.setTimeout(() => { dom.saveState.textContent = "本地自动保存"; }, 1400);
}

function toast(message) {
  dom.toast.textContent = message;
  dom.toast.classList.add("show");
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => dom.toast.classList.remove("show"), 2400);
}

function setupVoiceInput() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) {
    dom.voiceStatus.textContent = "当前浏览器不支持语音转写";
    dom.voiceButton.title = "请使用支持 Web Speech API 的 Chrome 或 Edge";
    return;
  }
  voiceRecognition = new Recognition();
  voiceRecognition.lang = "zh-CN";
  voiceRecognition.continuous = true;
  voiceRecognition.interimResults = true;
  let committed = "";
  voiceRecognition.onstart = () => {
    voiceListening = true;
    committed = dom.dictation.value.trim();
    dom.voiceButton.textContent = "停止语音";
    dom.voiceButton.classList.add("is-listening");
    dom.voiceButton.setAttribute("aria-pressed", "true");
    dom.voiceStatus.textContent = "正在转写，可随时停止";
  };
  voiceRecognition.onresult = (event) => {
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) committed = `${committed}${committed ? " " : ""}${transcript}`.trim();
      else interim += transcript;
    }
    dom.dictation.value = `${committed}${interim ? `${committed ? " " : ""}${interim}` : ""}`;
  };
  voiceRecognition.onerror = (event) => {
    const label = event.error === "not-allowed" ? "麦克风权限未授权" : `语音转写失败：${event.error}`;
    dom.voiceStatus.textContent = label;
  };
  voiceRecognition.onend = () => {
    voiceListening = false;
    dom.voiceButton.textContent = "语音输入";
    dom.voiceButton.classList.remove("is-listening");
    dom.voiceButton.setAttribute("aria-pressed", "false");
    if (!data.signedAt) dom.voiceStatus.textContent = "转写完成，可修改后发送";
  };
}

function toggleVoiceInput() {
  if (!voiceRecognition || data.signedAt) return;
  if (voiceListening) voiceRecognition.stop();
  else {
    try {
      voiceRecognition.start();
    } catch {
      voiceRecognition.stop();
    }
  }
}

function currentReportSections() {
  return data.signedSnapshot?.sections || renderReport(data).sections;
}

function reportAsText() {
  return currentReportSections().map((section) => `${section.title}\n${section.text}`).join("\n\n");
}

function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function exportTextReport() {
  const state = data.signedAt ? "正式报告" : "草稿";
  const header = `乳腺 MRI 影像诊断报告（${state}）\n模板：${data.templateId} · ${data.templateVersion}\n`;
  downloadFile(`乳腺MRI报告-${state}.txt`, `${header}\n${reportAsText()}\n`, "text/plain;charset=utf-8");
  toast(`已导出${state}文本`);
}

function reportDocumentState(validation) {
  if (data.signedAt) {
    return {
      key: "signed",
      label: "已签发",
      detail: `内容已锁定 · ${new Date(data.signedAt).toLocaleString("zh-CN", { hour12: false })}`
    };
  }
  const pendingCount = pendingCandidateCount(data);
  if (pendingCount) {
    return {
      key: "pending",
      label: `草稿 · ${pendingCount}项待确认`,
      detail: "候选内容尚未由医生确认，不可作为正式报告"
    };
  }
  if (validation.blocking.length) {
    return {
      key: "blocked",
      label: `草稿 · ${validation.blocking.length}项待完善`,
      detail: "可预览和打印草稿，完成阻断项前不可签发"
    };
  }
  if (data.draft) {
    return { key: "review", label: "已记录 · 待签发", detail: "结构校验通过，等待医生最终核对与签发" };
  }
  return { key: "ready", label: "结构完整 · 可生成", detail: "报告内容来自当前已确认的结构化数据" };
}

function formattedReportTime() {
  const value = data.signedAt || data.signedSnapshot?.generatedAt || data.draft?.generatedAt || new Date().toISOString();
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? "未记录" : time.toLocaleString("zh-CN", { hour12: false });
}

function reportHeaderHtml(title, subtitle, validation) {
  const state = reportDocumentState(validation);
  const projectMeta = data.exam?.projectName ? `
    <div><dt>检查项目</dt><dd>${escapeHtml(data.exam.projectName)}</dd></div>
  ` : "";
  return `
    <header class="generated-document-head">
      <div class="generated-document-title">
        <span class="generated-document-mark">MR</span>
        <div>
          <p>乳腺磁共振标准化报告</p>
          <h1>${escapeHtml(title)}</h1>
          <small>${escapeHtml(subtitle)}</small>
        </div>
      </div>
      <span class="generated-document-status state-${state.key}">${escapeHtml(state.label)}</span>
    </header>
    <dl class="generated-document-meta">
      ${projectMeta}
      <div><dt>模板版本</dt><dd>${escapeHtml(`${data.templateId} · ${data.templateVersion}`)}</dd></div>
      <div><dt>${data.signedAt ? "签发时间" : "生成时间"}</dt><dd>${escapeHtml(formattedReportTime())}</dd></div>
      <div><dt>文档状态</dt><dd>${escapeHtml(state.label)}</dd></div>
    </dl>
    ${state.key === "signed" ? "" : `<p class="generated-draft-notice">${escapeHtml(state.detail)}。本页不能替代医生签发。</p>`}
  `;
}

function structuredPrintField(label, value, required = false) {
  const present = value !== "" && value !== null && value !== undefined;
  return `
    <div class="generated-structure-field ${required && !present ? "is-missing" : ""}">
      <dt>${escapeHtml(label)}</dt>
      <dd>${escapeHtml(present ? value : required ? "待确认" : "未描述")}</dd>
    </div>
  `;
}

function structuredLesionHtml(lesion) {
  const size = [lesion.size?.ap, lesion.size?.transverse, lesion.size?.cc]
    .filter((value) => value !== "" && value !== null && value !== undefined)
    .join("×");
  const location = [
    uiLabels.laterality[lesion.laterality],
    lesion.clock ? `${lesion.clock}点钟` : "",
    lesion.distanceCm !== "" ? `距乳头${lesion.distanceCm} cm` : "",
    uiLabels.depth[lesion.depth]
  ].filter(Boolean).join(" · ");
  const morphology = lesion.type === "nme"
    ? uiLabels.distribution[lesion.distribution]
    : [uiLabels.shape[lesion.shape], uiLabels.margin[lesion.margin]].filter(Boolean).join(" · ");
  const kinetics = [uiLabels.early[lesion.kinetics?.early], uiLabels.delayed[lesion.kinetics?.delayed]].filter(Boolean).join(" · ");
  const dwi = [
    uiLabels.dwi[lesion.dwi?.signal],
    lesion.dwi?.adcMin !== "" ? `ADCmin ${lesion.dwi.adcMin}×10⁻³ mm²/s` : "",
    lesion.dwi?.bValue !== "" ? `b=${lesion.dwi.bValue} s/mm²` : ""
  ].filter(Boolean).join(" · ");
  const birads = lesion.assessment?.birads ? `BI-RADS ${lesion.assessment.birads}类` : "";
  const management = uiLabels.management[lesion.management?.action] || "";
  return `
    <section class="generated-lesion-block">
      <div class="generated-lesion-heading">
        <div><span>${escapeHtml(lesion.id)}</span><strong>${escapeHtml(uiLabels.lesionType[lesion.type] || "病灶")}</strong></div>
        <b>${escapeHtml(birads || "分类待确认")}</b>
      </div>
      <div class="generated-lesion-body">
        <figure class="generated-lesion-map">
          ${lesionMarkerSvg(lesion)}
          <figcaption>病灶时钟面与深度定位示意 · 非诊断影像</figcaption>
        </figure>
        <dl class="generated-structure-grid">
          ${structuredPrintField("定位", location, true)}
          ${structuredPrintField("尺寸", size ? `${size} mm` : "", true)}
          ${structuredPrintField(lesion.type === "nme" ? "分布" : "形态/边缘", morphology)}
          ${structuredPrintField("内部强化", uiLabels.enhancement[lesion.internalEnhancement])}
          ${structuredPrintField("T2信号", lesion.t2 === "high" ? "T2高信号" : lesion.t2 === "notHigh" ? "T2非高信号" : "")}
          ${structuredPrintField("强化动力学", kinetics)}
          ${structuredPrintField("DWI / ADC", dwi)}
          ${structuredPrintField("病灶评估", birads, true)}
          ${structuredPrintField("管理建议", management, true)}
          ${["4", "5"].includes(String(lesion.assessment?.birads || "")) ? structuredPrintField("取样路径", lesion.management?.detail, true) : ""}
          ${String(lesion.assessment?.birads || "") === "6" ? structuredPrintField("病理依据和日期", lesion.pathology?.reference, true) : ""}
        </dl>
      </div>
    </section>
  `;
}

function reportSectionText(title) {
  return currentReportSections().find((section) => section.title === title)?.text || "未生成";
}

function generatedConclusionHtml() {
  return `
    <section class="generated-conclusion-grid">
      <div>
        <h2>诊断结论</h2>
        ${String(reportSectionText("诊断结论")).split("\n").map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
      </div>
      <div>
        <h2>管理建议</h2>
        ${String(reportSectionText("管理建议")).split("\n").map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
      </div>
    </section>
  `;
}

function generatedBlockingHtml(validation) {
  if (!validation.blocking.length) return "";
  return `
    <section class="generated-qc-block">
      <h2>签发前待完善</h2>
      <ol>${validation.blocking.map((item) => `<li>${escapeHtml(item.message)}</li>`).join("")}</ol>
    </section>
  `;
}

function buildStructuredPrintHtml(validation) {
  const lesions = Array.isArray(data.lesions) ? data.lesions : [];
  const relatedStatus = { normal: "未见明确异常", abnormal: "异常", limited: "评价受限" };
  const background = `
    <section class="generated-background-block">
      <div class="generated-section-heading"><span>01</span><h2>乳腺背景与相关结构</h2></div>
      <dl class="generated-background-grid">
        ${structuredPrintField("FGT", data.breastTissue?.fgt ? `${data.breastTissue.fgt}类` : "", true)}
        ${structuredPrintField("BPE程度", uiLabels.bpe[data.breastTissue?.bpeLevel], true)}
        ${structuredPrintField("BPE对称性", uiLabels.symmetry[data.breastTissue?.bpeSymmetry], true)}
        ${structuredPrintField("乳头/皮肤/胸壁", relatedStatus[data.associatedStructures?.status] || "", true)}
        ${structuredPrintField("区域淋巴结", relatedStatus[data.lymphNodes?.status] || "", true)}
      </dl>
    </section>
  `;
  const findings = lesions.length
    ? lesions.map(structuredLesionHtml).join("")
    : `<section class="generated-normal-block"><div>${lesionMarkerSvg({})}</div><p>双侧乳腺未建立异常病灶节点，当前采用已确认的正常结构基线。</p></section>`;
  return `
    <article class="generated-sheet generated-sheet-structured">
      ${reportHeaderHtml("乳腺 MRI 结构化图文报告", "解剖定位图 · 逐病灶结构化信息 · 诊断结论", validation)}
      ${generatedBlockingHtml(validation)}
      ${background}
      <section class="generated-findings-block">
        <div class="generated-section-heading"><span>02</span><h2>${lesions.length ? `病灶结构化信息（${lesions.length}）` : "病灶结构化信息"}</h2></div>
        ${findings}
      </section>
      <div class="generated-section-heading generated-section-heading-conclusion"><span>03</span><h2>结论与建议</h2></div>
      ${generatedConclusionHtml()}
      <footer class="generated-document-foot">本报告由结构化数据同源生成；最终内容以医生签发版本为准。</footer>
    </article>
  `;
}

function buildTextPrintHtml(validation) {
  return `
    <article class="generated-sheet generated-sheet-text">
      ${reportHeaderHtml("乳腺 MRI 影像诊断报告", "标准文本版 · 报告所见 · 诊断结论 · 管理建议", validation)}
      ${generatedBlockingHtml(validation)}
      <div class="generated-text-sections">
        ${currentReportSections().map((section, index) => `
          <section>
            <div class="generated-section-heading"><span>${String(index + 1).padStart(2, "0")}</span><h2>${escapeHtml(section.title)}</h2></div>
            ${String(section.text || "").split("\n").map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
          </section>
        `).join("")}
      </div>
      <footer class="generated-document-foot">本报告由结构化数据同源生成；最终内容以医生签发版本为准。</footer>
    </article>
  `;
}

function renderReportOutputPreview(format = activeReportFormat) {
  activeReportFormat = format === "text" ? "text" : "structured";
  const validation = validateCase(data);
  const state = reportDocumentState(validation);
  const html = activeReportFormat === "structured"
    ? buildStructuredPrintHtml(validation)
    : buildTextPrintHtml(validation);
  dom.reportOutputDialog.querySelectorAll("[data-report-format]").forEach((button) => {
    const active = button.dataset.reportFormat === activeReportFormat;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  dom.reportOutputState.className = `report-output-state state-${state.key}`;
  dom.reportOutputState.innerHTML = `<strong>${escapeHtml(state.label)}</strong><span>${escapeHtml(state.detail)}</span>`;
  dom.reportOutputPreview.innerHTML = html;
  dom.printStage.innerHTML = html;
  dom.printGeneratedReportButton.textContent = activeReportFormat === "structured" ? "打印结构化图文版" : "打印文本报告版";
}

function openReportOutputDialog() {
  renderReportOutputPreview(activeReportFormat);
  if (typeof dom.reportOutputDialog.showModal === "function") dom.reportOutputDialog.showModal();
  else dom.reportOutputDialog.setAttribute("open", "");
}

function closeReportOutputDialog() {
  if (typeof dom.reportOutputDialog.close === "function") dom.reportOutputDialog.close();
  else dom.reportOutputDialog.removeAttribute("open");
}

function printGeneratedReport() {
  renderReportOutputPreview(activeReportFormat);
  previousPrintTitle = document.title;
  document.title = activeReportFormat === "structured" ? "乳腺MRI结构化图文报告" : "乳腺MRI文本报告";
  document.body.classList.add("printing-generated-report");
  dom.printStage.setAttribute("aria-hidden", "false");
  window.requestAnimationFrame(() => window.print());
}

function cleanupGeneratedPrint() {
  if (!document.body.classList.contains("printing-generated-report")) return;
  document.body.classList.remove("printing-generated-report");
  dom.printStage.setAttribute("aria-hidden", "true");
  if (previousPrintTitle) document.title = previousPrintTitle;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function hydrateForm() {
  if (data.scenario === "lesion") ensureActiveLesion();
  document.querySelectorAll("[data-path]").forEach((input) => {
    input.value = getValue(data, input.dataset.path) ?? "";
    input.closest("label")?.classList.toggle("field-pending", isPathPending(input.dataset.path));
    input.disabled = Boolean(data.signedAt);
  });
  document.querySelector(`input[name="scenario"][value="${data.scenario}"]`).checked = true;
  document.querySelectorAll('input[name="scenario"]').forEach((radio) => { radio.disabled = Boolean(data.signedAt); });
  dom.physicianConfirm.disabled = Boolean(data.signedAt);
  dom.dictation.disabled = Boolean(data.signedAt);
  dom.extractButton.disabled = Boolean(data.signedAt) || agentBusy;
  dom.voiceButton.disabled = Boolean(data.signedAt) || !voiceRecognition;
  dom.massSampleButton.disabled = Boolean(data.signedAt);
  dom.normalSampleButton.disabled = Boolean(data.signedAt);
  updateConditionalFields();
}

function updateConditionalFields() {
  const normal = data.scenario === "normal";
  dom.lesionSection.hidden = normal;
  if (normal) return;
  ensureActiveLesion();
  const nme = data.lesion.type === "nme";
  dom.shapeField.hidden = nme;
  dom.marginField.hidden = nme;
  dom.distributionField.hidden = !nme;
  renumberLesions();
  dom.lesionIdLabel.textContent = data.lesion.id;
}

async function runAgentTurn() {
  const message = dom.dictation.value.trim();
  if (!message) return toast("请先输入本轮医生观察或回答");
  if (agentBusy) return;

  const requestId = ++activeAgentRequest;
  const turnId = `turn-${Date.now().toString(36)}-${requestId}`;
  const turnStartedAt = performance.now();
  agentBusy = true;
  data.agent.messages.push({ role: "user", content: message, at: new Date().toISOString() });
  data.agent.currentTurn = { id: turnId, startedAt: new Date().toISOString(), phase: "local_preview" };
  dom.extractButton.textContent = "本地提取中…";

  const explicitlyAddsLesion = /(?:新增病灶|另见|另有|另一处|第二处|再见)[^。；，]*(?:肿块|非肿块强化|NME|病灶)/i.test(message);
  const previewUpdates = buildDeterministicPreview(message);
  let createdLesionId = "";
  if (explicitlyAddsLesion && data.lesions.length) {
    data.lesions.push(createLesion());
    data.activeLesionIndex = data.lesions.length - 1;
    data.lesion = data.lesions[data.activeLesionIndex];
    data.scenario = "lesion";
    renumberLesions();
    createdLesionId = data.lesion.id;
    recordInteractionEvent(data, "lesion_created_from_phrase", { lesionUid: data.lesion.uid, turnId });
  } else if (previewUpdates.some((update) => update.path.startsWith("lesion.")) && !data.lesions.length) {
    data.scenario = "lesion";
    ensureActiveLesion();
    createdLesionId = data.lesion.id;
  }
  let targetLesionUid = lesionUidAt();
  const currentCaseForModel = structuredClone(data);

  const normalizeLesionPaths = (updates) => updates.map((item) => {
    const update = { ...item };
    if (["assessment.leftBirads", "assessment.rightBirads"].includes(update.path)) {
      update.path = "lesion.assessment.birads";
    } else if (update.path.startsWith("management.") && targetLesionUid) {
      update.path = `lesion.${update.path}`;
    }
    return update;
  });

  const applyUpdates = (updates, model, defaultSourceType) => {
    const normalizedUpdates = normalizeLesionPaths(updates);
    const localConflicts = [];
    let appliedCount = 0;
    for (const update of normalizedUpdates) {
      if (update.path === "scenario" && update.value === "normal" && data.lesions.length) {
        localConflicts.push({
          path: update.path,
          currentValue: "lesion",
          candidateValue: "normal",
          reason: "当前存在病灶节点，未直接切换为正常场景"
        });
        continue;
      }
      if (update.path.startsWith("lesion.") && !targetLesionUid) {
        data.scenario = "lesion";
        ensureActiveLesion();
        targetLesionUid = lesionUidAt();
      }
      const lesionUid = update.path.startsWith("lesion.") ? targetLesionUid : "";
      const previous = scopedValue(update.path, lesionUid);
      const previousSource = sourceForPath(update.path, lesionUid);
      const replaceableDefault = previousSource?.sourceType === "institutional_normal_default";
      const sameValue = previous !== "" && previous !== null && previous !== undefined
        && String(previous) === String(update.value);
      const existing = candidateForPath(data, update.path, lesionUid);
      if (sameValue && previousSource?.confirmation === "physician_confirmed") continue;
      if (sameValue && existing && existing.sourceType === "deterministic_parser"
        && (update.sourceType || defaultSourceType) === "deterministic_parser") continue;
      if (!replaceableDefault && previous !== "" && previous !== null
        && previous !== undefined && String(previous) !== String(update.value)) {
        localConflicts.push({
          path: update.path,
          currentValue: previous,
          candidateValue: update.value,
          lesionUid,
          reason: "本轮内容与已有字段不一致，已保留原值等待处理"
        });
        continue;
      }
      if (update.path.startsWith("lesion.") && data.scenario !== "lesion") {
        setCandidate("scenario", "lesion", update.sourceText || message, {
          sourceType: "deterministic_parser", confidence: 1, turnId
        });
      }
      setCandidate(update.path, update.value, update.sourceText || message, {
        sourceType: update.sourceType || defaultSourceType,
        model,
        confidence: update.confidence,
        requiresExplicitPhysicianConfirmation: update.requiresExplicitPhysicianConfirmation,
        lesionUid,
        turnId
      });
      appliedCount += 1;
    }
    return { appliedCount, localConflicts, normalizedUpdates };
  };

  const previewStartedAt = performance.now();
  const previewResult = applyUpdates(previewUpdates, "local-deterministic-preview", "deterministic_parser");
  const previewMs = Math.round(performance.now() - previewStartedAt);
  data.agent.conflicts = previewResult.localConflicts;
  data.agent.lastTiming = { previewMs, modelMs: null, totalMs: previewMs, cacheHit: false };
  data.agent.currentTurn.phase = "semantic_enrichment";
  data.draft = null;
  data.status = "CLARIFICATION";
  graphManualOpen.add("findings");
  if (targetLesionUid) graphManualOpen.add(`lesion-${data.lesions.findIndex((lesion) => lesion.uid === targetLesionUid)}`);
  persist();
  dom.extractButton.textContent = "语义补充中…";
  render();

  try {
    const response = await fetch("/api/agent/turn", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, currentCase: currentCaseForModel, turnId })
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    if (requestId !== activeAgentRequest) return;

    const candidateUpdates = [...payload.candidateUpdates];
    if (!candidateUpdates.some((item) => item.path === "lesion.type")) {
      const explicitType = /非肿块强化|NME/i.test(message)
        ? "nme"
        : /肿块|MASS/i.test(message) ? "mass" : "";
      if (explicitType) {
        candidateUpdates.push({
          path: "lesion.type",
          value: explicitType,
          sourceText: explicitType === "nme" ? "非肿块强化" : "肿块",
          confidence: 1,
          requiresExplicitPhysicianConfirmation: false,
          sourceType: "deterministic_parser"
        });
      }
    }

    const lesionTurn = data.scenario === "lesion"
      || explicitlyAddsLesion
      || candidateUpdates.some((item) => item.path.startsWith("lesion."));
    if (lesionTurn) {
      const lesionUpdates = normalizeLesionPaths(candidateUpdates);
      const biradsUpdate = lesionUpdates.find((item) => item.path === "lesion.assessment.birads");
      if (biradsUpdate && !lesionUpdates.some((item) => item.path === "lesion.management.action")) {
        candidateUpdates.push({
          path: "lesion.management.action",
          value: defaultManagementForBirads(biradsUpdate.value),
          sourceText: `确定性规则：BI-RADS ${biradsUpdate.value}类默认处置`,
          confidence: 1,
          requiresExplicitPhysicianConfirmation: true,
          sourceType: "deterministic_rule"
        });
      }
    }

    const modelResult = applyUpdates(candidateUpdates, payload.model, "llm_semantic");
    const appliedCount = previewResult.appliedCount + modelResult.appliedCount;

    if (createdLesionId) {
      renumberLesions();
      createdLesionId = data.lesions.find((lesion) => lesion.uid === targetLesionUid)?.id || createdLesionId;
    }

    const modelConflicts = createdLesionId
      ? (payload.conflicts || []).filter((item) => !String(item.path || "").startsWith("lesion."))
      : (payload.conflicts || []);
    data.agent.conflicts = [...previewResult.localConflicts, ...modelResult.localConflicts, ...modelConflicts].slice(0, 10);
    data.agent.lastModel = payload.model;
    data.agent.lastUsage = payload.usage;
    data.agent.lastTiming = {
      previewMs,
      modelMs: payload.timing?.modelMs ?? payload.timing?.totalMs ?? null,
      totalMs: Math.round(performance.now() - turnStartedAt),
      cacheHit: Boolean(payload.timing?.cacheHit)
    };
    data.agent.currentTurn = null;
    if (payload.degraded) {
      dom.modelStatus.textContent = "LLM离线 · 本地提取可用";
      dom.modelStatus.className = "model-status model-degraded";
    }
    const questionText = createdLesionId ? "" : payload.questions?.[0]?.question || "";
    const conflictText = data.agent.conflicts.length
      ? `\n\n发现 ${data.agent.conflicts.length} 个冲突，未覆盖已有值：\n${data.agent.conflicts.map((item) => `• ${item.path}：${item.reason}`).join("\n")}`
      : "";
    const assistantMessage = createdLesionId
      ? `已新建 ${createdLesionId} 病灶节点并填入本轮候选内容。请核对该节点，并继续补充强化动力学、DWI/ADC 及该病灶 BI-RADS。`
      : payload.assistantMessage;
    const content = `${assistantMessage}${questionText ? `\n\n需要您确认：${questionText}` : ""}${conflictText}`;
    data.agent.messages.push({
      role: "assistant",
      content,
      at: new Date().toISOString(),
      model: payload.model,
      extractedCount: appliedCount,
      timing: data.agent.lastTiming
    });
    recordInteractionEvent(data, "turn_completed", {
      turnId,
      lesionUid: targetLesionUid,
      previewCount: previewResult.appliedCount,
      modelCount: modelResult.appliedCount,
      totalMs: data.agent.lastTiming.totalMs,
      cacheHit: data.agent.lastTiming.cacheHit
    });
    data.dictation = "";
    dom.dictation.value = "";
    data.draft = null;
    data.status = "CLARIFICATION";
    persist();
  } catch (error) {
    if (requestId !== activeAgentRequest) return;
    data.agent.messages.push({
      role: "error",
      content: previewResult.appliedCount
        ? `模型语义补充未完成：${error.message}。本地原话明确事实已保留为待确认候选。`
        : `本轮未写入字段：${error.message}`,
      at: new Date().toISOString()
    });
    data.agent.currentTurn = null;
    persist();
    toast(previewResult.appliedCount ? "模型补充失败，本地明确事实已保留" : "Agent 调用失败，本轮未修改结构");
  } finally {
    if (requestId === activeAgentRequest) {
      agentBusy = false;
      dom.extractButton.textContent = "发送片段";
      render();
    }
  }
}

function setCandidate(path, value, sourceText, metadata = {}) {
  if (path.startsWith("lesion.") && !metadata.lesionUid) ensureActiveLesion();
  const lesionUid = path.startsWith("lesion.") ? metadata.lesionUid || lesionUidAt() : "";
  const previousValue = scopedValue(path, lesionUid);
  setScopedValue(path, value, lesionUid);
  return upsertCandidate(data, {
    path,
    value,
    previousValue,
    lesionUid,
    sourceText,
    sourceType: metadata.sourceType || "llm_semantic",
    confidence: metadata.confidence,
    requiresExplicitPhysicianConfirmation: metadata.requiresExplicitPhysicianConfirmation,
    model: metadata.model,
    turnId: metadata.turnId
  });
}

function confirmAllExtraction() {
  const direct = directEvidenceCandidates(data);
  if (!direct.length) return toast("当前只有关键决策或语义候选，请在对应节点复核");
  const count = confirmCandidateIds(data, direct.map((candidate) => candidate.id), "batch_direct_evidence");
  if (!criticalReviewCandidates(data).length) graphManualOpen.clear();
  data.status = "CLARIFICATION";
  persist();
  render();
  toast(`已一次确认 ${count} 个原话明确事实；关键决策仍需节点复核`);
}

function renderAgentConversation() {
  if (!data.agent.messages.length) {
    dom.agentConversation.innerHTML = `
      <div class="agent-empty"><strong>Agent 已就绪</strong><br>
      当前已加载正常结构基线。输入异常描述后，本地规则会立即更新明确事实，模型再补充语义候选；关键决策仍需逐项复核。</div>
    `;
    return;
  }
  dom.agentConversation.innerHTML = data.agent.messages.map((message) => {
    const role = message.role === "user" ? "医生" : message.role === "error" ? "系统" : "Agent";
    const kind = message.role === "user" ? "user" : "assistant";
    const errorClass = message.role === "error" ? " agent-message-error" : "";
    const timing = message.timing;
    const meta = message.model
      ? `<span class="agent-meta">${escapeHtml(message.model)} · 提取 ${message.extractedCount || 0} 个候选${timing ? ` · ${timing.totalMs} ms${timing.cacheHit ? " · 缓存" : ""}` : ""}</span>`
      : "";
    return `<div class="agent-message agent-message-${kind}${errorClass}"><strong>${role}</strong>${escapeHtml(message.content)}${meta}</div>`;
  }).join("");
  dom.agentConversation.scrollTop = dom.agentConversation.scrollHeight;
}

function renderExtractionNotice() {
  const count = pendingCandidateCount(data);
  dom.extractionNotice.hidden = count === 0;
  if (!count) return;
  const directCount = directEvidenceCandidates(data).length;
  const reviewCount = criticalReviewCandidates(data).length;
  const timing = data.agent?.lastTiming;
  const timingText = timing
    ? `<span class="notice-performance">本地预提取 ${timing.previewMs ?? 0} ms · 模型 ${timing.modelMs ?? timing.totalMs ?? 0} ms${timing.cacheHit ? " · 命中缓存" : ""}</span>`
    : data.agent?.currentTurn ? '<span class="notice-performance">本地事实已即时更新 · 模型正在补充语义</span>' : "";
  dom.extractionNotice.innerHTML = `
    <div><strong>${count}</strong> 项待复核：<span>${directCount} 项原话明确事实</span> · <span>${reviewCount} 项关键/语义候选</span></div>
    ${timingText}
    ${directCount ? '<button id="confirmExtraction" class="text-button" type="button">一次确认明确事实</button>' : ""}`;
  document.getElementById("confirmExtraction")?.addEventListener("click", confirmAllExtraction);
}

function updateFromForm(event) {
  const input = event.target;
  const path = input.dataset.path;
  if (!path) return;
  if (path.startsWith("lesion.")) ensureActiveLesion();
  const value = numberPaths.has(path) && input.value !== "" ? Number(input.value) : input.value;
  const lesionUid = path.startsWith("lesion.") ? lesionUidAt() : "";
  setScopedValue(path, value, lesionUid);
  const pending = candidateForPath(data, path, lesionUid);
  if (pending) confirmCandidateIds(data, [pending.id], "direct_form_edit");
  const targetProvenance = path.startsWith("lesion.") ? data.lesion.provenance : data.provenance;
  const provenancePath = path.replace(/^lesion\./, "");
  targetProvenance[provenancePath] = {
    sourceType: "physician_click",
    sourceText: "医生在结构化表单中填写或修改",
    confirmation: "physician_confirmed",
    confirmedAt: new Date().toISOString()
  };
  recordInteractionEvent(data, "field_confirmed_directly", { path, lesionUid });
  if (path === "lesion.type") {
    renumberLesions();
    updateConditionalFields();
  }
  data.draft = null;
  data.status = "CLARIFICATION";
  dom.physicianConfirm.checked = false;
  persist();
  render();
}

function renderQuestions(validation) {
  const blocking = validation.blocking.slice(0, 1);
  dom.gateCount.textContent = validation.blocking.length;
  dom.gateCount.className = `gate-count ${validation.blocking.length ? "has-blockers" : "clear"}`;
  if (!blocking.length) {
    dom.questionPanel.innerHTML = `
      <div class="gate-clear"><strong>结构校验通过</strong><p>记录当前版本并完成医生核对后即可签发。</p></div>
      ${validation.warnings.map((item) => `<div class="issue issue-warning"><span>提醒</span><p>${item.message}</p></div>`).join("")}
    `;
    return;
  }
  dom.questionPanel.innerHTML = `
    <div class="question-heading"><strong>当前唯一阻断项</strong><small>其他缺失不打断</small></div>
    ${blocking.map((item, index) => `<button class="issue issue-blocking" data-focus="${item.path}" ${item.lesionIndex !== null ? `data-lesion-index="${item.lesionIndex}"` : ""} type="button"><span>${index + 1}</span><p>${item.message}</p></button>`).join("")}
    ${validation.warnings.map((item) => `<div class="issue issue-warning"><span>提醒</span><p>${item.message}</p></div>`).join("")}
  `;
  dom.questionPanel.querySelectorAll("[data-focus]").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.lesionIndex !== undefined) {
      selectLesion(Number(button.dataset.lesionIndex), true);
    }
    const focusPath = button.dataset.focus === "breastTissue"
      ? "breastTissue.fgt"
      : button.dataset.focus;
    const target = document.querySelector(`[data-path="${focusPath}"]`);
    target?.focus();
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }));
}

function renderLesionCards() {
  const lesions = data.lesions || [];
  dom.addLesionButton.disabled = Boolean(data.signedAt);
  dom.lesionOverview.hidden = lesions.length === 0;
  if (!lesions.length) {
    dom.lesionCards.innerHTML = `<div class="lesion-empty"><strong>当前为正常基线</strong><br>发现异常时，直接描述或新增病灶即可。</div>`;
    return;
  }
  const label = {
    left: "左乳", right: "右乳", mass: "MASS", nme: "NME",
    irregular: "不规则", oval: "卵圆", round: "圆形", lobulated: "分叶",
    spiculated: "毛刺", indistinct: "模糊", circumscribed: "清楚",
    diffuse: "弥漫", regional: "区域", focal: "局灶", linear: "线样", segmental: "节段",
    homogeneous: "均匀强化", heterogeneous: "不均匀强化", rim: "环形强化",
    clumped: "簇集强化", clusteredRing: "簇状环形",
    fast: "快进", medium: "中速", slow: "慢速",
    washout: "廓清", plateau: "平台", persistent: "持续"
  };
  dom.lesionCards.innerHTML = lesions.map((lesion, index) => {
    const sizes = [lesion.size?.ap, lesion.size?.transverse, lesion.size?.cc]
      .filter((value) => value !== "" && value !== null && value !== undefined);
    const size = sizes.length === 3 ? `${sizes.join("×")} mm`
      : sizes.length ? `最大径 ${Math.max(...sizes.map(Number))} mm` : "尺寸待补";
    const location = [
      label[lesion.laterality] || "侧别待补",
      lesion.clock ? `${lesion.clock}点` : "",
      lesion.distanceCm !== "" ? `距乳头${lesion.distanceCm}cm` : ""
    ].filter(Boolean).join(" · ");
    const descriptors = [
      label[lesion.shape] || label[lesion.distribution],
      label[lesion.margin] || label[lesion.internalEnhancement],
      label[lesion.kinetics?.early], label[lesion.kinetics?.delayed]
    ].filter(Boolean).slice(0, 3);
    const sidePath = lesion.laterality ? `assessment.${lesion.laterality}Birads` : "";
    const birads = sidePath && data.provenance?.[sidePath]?.sourceType !== "institutional_normal_default"
      ? `BI-RADS ${getValue(data, sidePath)}` : "BI-RADS 待确认";
    const isActive = index === data.activeLesionIndex;
    const complete = lesion.type && lesion.laterality && sizes.length;
    return `
      <article class="lesion-summary-card ${isActive ? "active" : ""}">
        <button class="lesion-card-main" type="button" data-select-lesion="${index}">
          <span class="lesion-card-id">${escapeHtml(lesion.id)}</span>
          <span class="lesion-card-type">${label[lesion.type] || "待定类型"}</span>
          <strong>${escapeHtml(location)}</strong>
          <span class="lesion-card-size">${escapeHtml(size)}</span>
          <span class="lesion-card-tags">${descriptors.length ? descriptors.map((item) => `<i>${escapeHtml(item)}</i>`).join("") : "<i>等待异常描述</i>"}</span>
          <span class="lesion-card-footer"><b>${birads}</b><em class="${complete ? "complete" : ""}">${complete ? "结构完整" : "待补关键项"}</em></span>
        </button>
        <div class="lesion-card-actions">
          <button type="button" data-edit-lesion="${index}">编辑</button>
          <button type="button" data-remove-lesion="${index}" ${data.signedAt ? "disabled" : ""}>删除</button>
        </div>
      </article>
    `;
  }).join("");
}

function renderWorkflow(validation) {
  const steps = [
    ["NORMAL_BASELINE", "正常基线", true],
    ["OBSERVATION_CAPTURE", "异常更新", data.agent.messages.length > 0],
    ["ASSESSMENT_CONFIRMATION", "关键确认", validation.blocking.length === 0],
    ["DRAFT_READY", "当前版本", Boolean(data.draft)],
    ["SIGNED", "医生签发", Boolean(data.signedAt)]
  ];
  let reachedIncomplete = false;
  dom.workflowSteps.innerHTML = steps.map(([key, label, done]) => {
    const current = !reachedIncomplete && !done;
    if (!done) reachedIncomplete = true;
    return `<li class="${done ? "done" : current ? "current" : ""}"><span></span>${label}</li>`;
  }).join("");
  data.status = data.signedAt ? "SIGNED"
    : data.draft ? "PHYSICIAN_REVIEW"
      : validation.blocking.length === 0 ? "ASSESSMENT_CONFIRMATION"
        : "CLARIFICATION";
  const statusLabels = {
    CLARIFICATION: "待确认", ASSESSMENT_CONFIRMATION: "报告就绪",
    PHYSICIAN_REVIEW: "医生审核", SIGNED: "已签发"
  };
  dom.sessionBadge.textContent = statusLabels[data.status] || "观察录入";
  dom.sessionBadge.className = `status-badge status-${data.status.toLowerCase()}`;
}

function renderReportContent() {
  const liveReport = renderReport(data);
  dom.reportContent.className = "report-content";
  if (!data.signedSnapshot?.sections) {
    dom.reportContent.innerHTML = liveReport.toHtml();
    return;
  }
  dom.reportContent.innerHTML = data.signedSnapshot.sections.map((section) => `
    <section>
      <div class="report-section-title">
        <h3>${escapeHtml(section.title)}</h3>
        <span class="report-origin origin-updated">已签发</span>
      </div>
      ${String(section.text || "").split("\n").map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
    </section>
  `).join("");
}

function pendingSourceLabel(sourceTypes) {
  if (sourceTypes.includes("physician_direct")) return "医生填写待确认";
  if (sourceTypes.includes("physician_click")) return "医生选择待确认";
  if (sourceTypes.includes("deterministic_parser")) return "原话明确";
  if (sourceTypes.includes("deterministic_rule")) return "规则候选";
  return "语义候选";
}

function fieldState(paths, value, options = {}) {
  const pathList = Array.isArray(paths) ? paths : [paths];
  const provenance = pathList.map((path) => data.provenance?.[path]).filter(Boolean);
  if (pathList.some((path) => isPathPending(path))) {
    const pendingSources = pathList
      .filter((path) => isPathPending(path))
      .map((path) => data.provenance?.[path]?.sourceType);
    return { key: "candidate", label: pendingSourceLabel(pendingSources) };
  }
  if (provenance.some((item) => item.confirmation === "physician_confirmed")) {
    return { key: "confirmed", label: "已确认" };
  }
  if (options.defaultInvalid && provenance.some((item) => item.sourceType === "institutional_normal_default")) {
    return options.required
      ? { key: "missing", label: "待医生确认" }
      : { key: "empty", label: "未确认" };
  }
  if (provenance.length && provenance.every((item) => item.sourceType === "institutional_normal_default")) {
    return { key: "baseline", label: "正常基线" };
  }
  const present = value !== "" && value !== null && value !== undefined;
  if (present) return { key: "confirmed", label: "已记录" };
  return options.required
    ? { key: "missing", label: "必填" }
    : { key: "empty", label: "未描述" };
}

function lesionMarkerSvg(lesion) {
  const hasMarker = Boolean(lesion?.laterality && lesion?.clock);
  const hasLaterality = Boolean(lesion?.laterality);
  const cx = lesion?.laterality === "right" ? 62 : 158;
  const cy = 66;
  const angle = ((Number(lesion?.clock || 12) / 12) * Math.PI * 2) - (Math.PI / 2);
  const radius = { anterior: 39, middle: 29, posterior: 19 }[lesion?.depth] || 34;
  const markerX = cx + Math.cos(angle) * radius;
  const markerY = cy + Math.sin(angle) * radius;
  const marker = lesion?.type === "nme"
    ? `<path d="M ${markerX} ${markerY - 7} L ${markerX + 7} ${markerY} L ${markerX} ${markerY + 7} L ${markerX - 7} ${markerY} Z" fill="var(--red)" stroke="#fff" stroke-width="2"/>`
    : `<circle cx="${markerX}" cy="${markerY}" r="6" fill="var(--red)" stroke="#fff" stroke-width="2"/>`;
  return `
    <svg viewBox="0 0 220 146" role="img" aria-label="双乳病灶时钟面与深度定位示意图，非诊断影像">
      <g fill="none" stroke="currentColor" stroke-width="1.4">
        <circle cx="62" cy="66" r="48" ${lesion?.laterality === "right" ? 'stroke="var(--blue)" stroke-width="2.4"' : ""}/>
        <circle cx="158" cy="66" r="48" ${lesion?.laterality === "left" ? 'stroke="var(--blue)" stroke-width="2.4"' : ""}/>
        <circle cx="62" cy="66" r="32" opacity=".38"/><circle cx="62" cy="66" r="17" opacity=".38"/>
        <circle cx="158" cy="66" r="32" opacity=".38"/><circle cx="158" cy="66" r="17" opacity=".38"/>
        <path d="M62 18v96M14 66h96M28 32l68 68M28 100l68-68M158 18v96M110 66h96M124 32l68 68M124 100l68-68" opacity=".2"/>
      </g>
      <g fill="currentColor" font-size="7" opacity=".72">
        <text x="62" y="12" text-anchor="middle">12</text><text x="116" y="69">3</text><text x="62" y="123" text-anchor="middle">6</text><text x="5" y="69">9</text>
        <text x="158" y="12" text-anchor="middle">12</text><text x="212" y="69">3</text><text x="158" y="123" text-anchor="middle">6</text><text x="101" y="69">9</text>
      </g>
      ${hasMarker ? `<g><line x1="${cx}" y1="${cy}" x2="${markerX}" y2="${markerY}" stroke="var(--red)" stroke-width="2" stroke-dasharray="3 2"/><circle cx="${markerX}" cy="${markerY}" r="11" fill="var(--red-soft)" stroke="none"/>${marker}<text x="${markerX}" y="${markerY - 12}" text-anchor="middle" fill="var(--red)" font-size="9" font-weight="850">${escapeHtml(lesion.id)}</text></g>` : hasLaterality ? `<circle cx="${cx}" cy="${cy}" r="7" fill="var(--blue-soft)" stroke="var(--blue)" stroke-width="1.5" stroke-dasharray="2 2"/>` : ""}
      <text x="62" y="140" text-anchor="middle" fill="currentColor" font-size="9" font-weight="750">右乳</text>
      <text x="158" y="140" text-anchor="middle" fill="currentColor" font-size="9" font-weight="750">左乳</text>
    </svg>
  `;
}

function graphKeyForPath(path, lesionIndex = data.activeLesionIndex) {
  if (path === "scenario") return "findings";
  if (path.startsWith("exam.") || path.startsWith("acquisition.") || path.startsWith("comparison.")) return "context";
  if (path.startsWith("breastTissue.")) return "background";
  if (path.startsWith("associatedStructures.")) return "structures";
  if (path.startsWith("lymphNodes.")) return "lymph";
  if (path.startsWith("assessment.")) return "assessment";
  if (path.startsWith("management.")) return "management";
  if (!path.startsWith("lesion.")) return "findings";
  const base = `lesion-${lesionIndex}`;
  if (path === "lesion.type") return `${base}-identity`;
  if (["lesion.laterality", "lesion.clock", "lesion.distanceCm", "lesion.depth"].includes(path)) return `${base}-location`;
  if (path.startsWith("lesion.size.")) return `${base}-size`;
  if (path.startsWith("lesion.dwi.")) return `${base}-dwi`;
  if (path.startsWith("lesion.kinetics.")) return `${base}-kinetics`;
  if (path.startsWith("lesion.assessment.") || path.startsWith("lesion.management.")
    || path.startsWith("lesion.pathology.")) return `${base}-assessment`;
  return `${base}-morphology`;
}

function pathBelongsToGraphNode(path, key, lesionIndex = data.activeLesionIndex) {
  const mapped = graphKeyForPath(path, lesionIndex);
  if (mapped === key) return true;
  if (path === "scenario" && key === `lesion-${lesionIndex}`) return true;
  if (key === "findings") return mapped === "findings" || mapped.startsWith("lesion-");
  if (key === "related") return mapped === "structures" || mapped === "lymph";
  if (key === "diagnosis") return mapped === "assessment" || mapped === "management";
  if (/^lesion-\d+$/.test(key)) return mapped.startsWith(`${key}-`);
  return false;
}

function aggregateGraphState(states) {
  const usable = states.filter(Boolean);
  if (usable.some((state) => state.key === "conflict")) return { key: "conflict", label: "存在冲突" };
  if (usable.some((state) => state.key === "candidate")) return { key: "candidate", label: "候选待确认" };
  if (usable.some((state) => state.key === "missing")) return { key: "missing", label: "必填" };
  if (usable.length && usable.every((state) => state.key === "baseline")) return { key: "baseline", label: "正常基线" };
  if (usable.some((state) => state.key === "confirmed")) return { key: "confirmed", label: "已确认" };
  if (usable.some((state) => state.key === "system")) return { key: "system", label: "系统来源" };
  return { key: "empty", label: "可选" };
}

function graphStateForNode(key, states) {
  const hasConflict = (data.agent?.conflicts || []).some((item) => pathBelongsToGraphNode(item.path || "", key));
  return aggregateGraphState(hasConflict ? [{ key: "conflict", label: "存在冲突" }, ...states] : states);
}

function graphNodeIsOpen(key, state, forceOpen = false) {
  if (data.signedAt) return graphManualOpen.has(key);
  return forceOpen || graphManualOpen.has(key) || ["candidate", "missing", "conflict"].includes(state.key);
}

function graphFact(label, value, state, options = {}) {
  const empty = value === "" || value === null || value === undefined;
  if (empty && options.omitWhenEmpty && state.key === "empty") return "";
  const fallback = state.key === "empty" ? "未描述（默认）" : state.key === "missing" ? "必填" : "待补充";
  return `
    <div class="graph-fact state-${state.key}">
      <span class="graph-fact-key">${escapeHtml(label)}</span>
      <strong class="graph-fact-value">${escapeHtml(empty ? options.emptyLabel || fallback : value)}</strong>
      <span class="graph-fact-state">${escapeHtml(state.label)}</span>
    </div>
  `;
}

function pendingCandidatesForGraphNode(key) {
  return pendingCandidates(data).filter((candidate) => {
    const lesionIndex = candidate.scope === "lesion"
      ? data.lesions.findIndex((lesion) => lesion.uid === candidate.lesionUid)
      : data.activeLesionIndex;
    return pathBelongsToGraphNode(candidate.path, key, Math.max(lesionIndex, 0));
  });
}

function graphNodeActions(key) {
  const actions = [];
  if (!data.signedAt && pendingCandidatesForGraphNode(key).length) {
    actions.push(`<button type="button" class="graph-action graph-action-confirm" data-confirm-graph-node="${escapeHtml(key)}">确认此节点</button>`);
  }
  return actions.join("");
}

function graphNode({ key, label, summary, state, body, actions = "", forceOpen = false, className = "" }) {
  const open = graphNodeIsOpen(key, state, forceOpen);
  return `
    <details class="graph-node state-${state.key} ${className}" data-graph-node="${escapeHtml(key)}" ${open ? "open" : ""} role="treeitem">
      <summary>
        <span class="graph-node-point" aria-hidden="true"></span>
        <span class="graph-node-copy"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(summary)}</span></span>
        <span class="graph-node-state">${escapeHtml(state.label)}</span>
        <span class="graph-node-chevron" aria-hidden="true"></span>
      </summary>
      <div class="graph-node-body" role="group">
        ${body}
        ${actions ? `<div class="graph-node-actions">${actions}</div>` : ""}
      </div>
    </details>
  `;
}

function confirmGraphNode(key) {
  const candidates = pendingCandidatesForGraphNode(key);
  if (!candidates.length) return toast("该节点没有待确认候选");
  const count = confirmCandidateIds(data, candidates.map((candidate) => candidate.id), "graph_node_confirmation");
  graphManualOpen.delete(key);
  if (key.startsWith("lesion-")) graphManualOpen.delete("findings");
  data.status = "CLARIFICATION";
  persist();
  render();
  toast(`已确认 ${count} 个节点事实`);
}

function lesionGraphState(index, paths, value, options = {}) {
  const values = Array.isArray(value) ? value : [value];
  const present = values.some((item) => item !== "" && item !== null && item !== undefined);
  const pathList = Array.isArray(paths) ? paths : [paths];
  const lesionUid = lesionUidAt(index);
  if (pathList.some((path) => isPathPending(path, lesionUid))) {
    const pendingSources = pathList
      .filter((path) => isPathPending(path, lesionUid))
      .map((path) => sourceForPath(path, lesionUid)?.sourceType);
    return { key: "candidate", label: pendingSourceLabel(pendingSources) };
  }
  if (!present) {
    return options.required
      ? { key: "missing", label: "必填" }
      : { key: "empty", label: "未描述" };
  }
  if (index === data.activeLesionIndex) {
    const sources = pathList.map((path) => sourceForPath(path, lesionUid)).filter(Boolean);
    if (sources.some((item) => item.confirmation === "physician_confirmed")) return { key: "confirmed", label: "已确认" };
  }
  return present
    ? { key: "confirmed", label: "已记录" }
    : options.required ? { key: "missing", label: "必填" } : { key: "empty", label: "未描述" };
}

function graphQuickGroup({ label, path, options, required = false, defaultInvalid = false, defaultLabel = "未描述（默认）" }) {
  const current = getValue(data, path);
  const present = lesionValuePresent(current);
  const source = data.provenance?.[path];
  const isPending = isPathPending(path);
  const baseline = source?.sourceType === "institutional_normal_default";
  const missing = !present || (required && defaultInvalid && baseline);
  const groupState = isPending ? "待确认"
    : source?.confirmation === "physician_confirmed" ? "已确认"
      : baseline && !missing ? "默认"
        : missing ? required ? "必填" : "默认" : "已记录";
  const renderedOptions = !required && !present
    ? [{ value: DEFAULT_CHOICE_VALUE, label: defaultLabel, isDefault: true }, ...options]
    : options;
  return `
    <div class="lesion-choice-group graph-choice-group ${required ? "is-required" : ""} ${missing && required ? "is-missing" : ""}">
      <div class="lesion-choice-label">
        <span>${escapeHtml(label)}${required ? '<b aria-hidden="true">*</b>' : ""}</span>
        <em>${groupState}</em>
      </div>
      <div class="lesion-choice-options" role="group" aria-label="${escapeHtml(label)}">
        ${renderedOptions.map((option) => {
          const selected = option.value === DEFAULT_CHOICE_VALUE ? !present : String(current) === String(option.value);
          const defaultSelected = selected && (option.isDefault || baseline);
          return `<button type="button" class="lesion-choice ${selected ? "is-selected" : ""} ${defaultSelected ? "is-default" : ""} ${selected && isPending ? "is-pending" : ""}"
            data-graph-quick-path="${escapeHtml(path)}"
            data-graph-quick-value="${escapeHtml(option.value)}"
            data-graph-quick-label="${escapeHtml(option.label)}"
            aria-pressed="${selected ? "true" : "false"}"
            ${data.signedAt ? "disabled" : ""}>${escapeHtml(option.label)}</button>`;
        }).join("")}
      </div>
    </div>
  `;
}

function graphDirectInput({ label, path, required = false, defaultLabel = "未记录（默认）", type = "text", unit = "" }) {
  const current = getValue(data, path);
  const present = lesionValuePresent(current);
  const pending = isPathPending(path);
  const missing = required && !present;
  const state = pending ? "待确认" : present ? "已记录" : missing ? "必填" : "默认";
  return `
    <label class="lesion-direct-field graph-direct-field ${missing ? "is-missing" : ""} ${pending ? "is-pending" : ""}">
      <span class="lesion-direct-label"><strong>${escapeHtml(label)}${required ? '<b aria-hidden="true">*</b>' : ""}</strong><em>${state}</em></span>
      <span class="lesion-direct-control">
        <input type="${type}" value="${escapeHtml(present ? current : "")}" placeholder="${escapeHtml(missing ? "必填" : defaultLabel)}"
          data-graph-direct-path="${escapeHtml(path)}" data-graph-direct-label="${escapeHtml(label)}"
          ${type === "number" ? 'min="0" step="any" inputmode="decimal"' : ""}
          aria-invalid="${missing ? "true" : "false"}" ${data.signedAt ? "disabled" : ""}>
        ${unit ? `<small>${escapeHtml(unit)}</small>` : ""}
      </span>
    </label>
  `;
}

function lesionQuickGroup({ lesion, index, label, path, options, required = false, hint = "", defaultLabel = "未描述（默认）" }) {
  const relativePath = path.replace(/^lesion\./, "");
  const current = getValue(lesion, relativePath);
  const present = lesionValuePresent(current);
  const isPending = isPathPending(path, lesion.uid);
  const groupState = isPending ? "待确认" : present ? "已记录" : required ? "必填" : "默认";
  const renderedOptions = required
    ? options
    : [{ value: DEFAULT_CHOICE_VALUE, label: defaultLabel, isDefault: true }, ...options];
  return `
    <div class="lesion-choice-group ${required ? "is-required" : ""} ${required && !present ? "is-missing" : ""}">
      <div class="lesion-choice-label">
        <span>${escapeHtml(label)}${required ? '<b aria-hidden="true">*</b>' : ""}</span>
        ${hint ? `<small>${escapeHtml(hint)}</small>` : ""}
        <em>${groupState}</em>
      </div>
      <div class="lesion-choice-options" role="group" aria-label="${escapeHtml(`${lesion.id} ${label}`)}">
        ${renderedOptions.map((option) => {
          const selected = option.value === DEFAULT_CHOICE_VALUE ? !present : String(current) === String(option.value);
          return `<button type="button" class="lesion-choice ${selected ? "is-selected" : ""} ${selected && option.isDefault ? "is-default" : ""} ${selected && isPending ? "is-pending" : ""}"
            data-lesion-quick-path="${escapeHtml(path)}"
            data-lesion-quick-value="${escapeHtml(option.value)}"
            data-lesion-quick-label="${escapeHtml(option.label)}"
            data-lesion-index="${index}"
            aria-pressed="${selected ? "true" : "false"}"
            ${data.signedAt ? "disabled" : ""}>${escapeHtml(option.label)}</button>`;
        }).join("")}
      </div>
    </div>
  `;
}

function lesionDirectField({ lesion, index, label, path, type = "number", unit = "", defaultLabel = "未测量（默认）", highlightMissing = false }) {
  const relativePath = path.replace(/^lesion\./, "");
  const current = getValue(lesion, relativePath);
  const present = lesionValuePresent(current);
  const pending = isPathPending(path, lesion.uid);
  const state = pending ? "待确认" : present ? "已记录" : highlightMissing ? "必填" : "默认";
  return `
    <label class="lesion-direct-field ${highlightMissing ? "is-missing" : ""} ${pending ? "is-pending" : ""}">
      <span class="lesion-direct-label"><strong>${escapeHtml(label)}</strong><em>${state}</em></span>
      <span class="lesion-direct-control">
        <input type="${type}" value="${escapeHtml(present ? current : "")}" placeholder="${escapeHtml(highlightMissing ? "必填" : defaultLabel)}"
          data-lesion-direct-path="${escapeHtml(path)}" data-lesion-direct-label="${escapeHtml(label)}" data-lesion-index="${index}"
          ${type === "number" ? 'min="0" step="any" inputmode="decimal"' : ""}
          aria-invalid="${highlightMissing ? "true" : "false"}" ${data.signedAt ? "disabled" : ""}>
        ${unit ? `<small>${escapeHtml(unit)}</small>` : ""}
      </span>
    </label>
  `;
}

function lesionValuePresent(value) {
  if (Array.isArray(value)) return value.some(lesionValuePresent);
  return value !== "" && value !== null && value !== undefined;
}

function renderLesionChoicePanel(lesion, index) {
  const sizeValues = [lesion.size?.ap, lesion.size?.transverse, lesion.size?.cc];
  const hasSize = sizeValues.some(lesionValuePresent);
  const birads = String(lesion.assessment?.birads || "");
  const tissueRequired = ["4", "5"].includes(birads);
  const pathologyRequired = birads === "6";
  const managementRequired = lesionValuePresent(birads);
  const coreValues = [lesion.type, lesion.laterality, sizeValues, birads];
  const descriptorValues = lesion.type === "nme"
    ? [lesion.clock, lesion.distanceCm, lesion.depth, lesion.distribution, lesion.internalEnhancement,
      lesion.t2, lesion.kinetics?.early, lesion.kinetics?.delayed, lesion.dwi?.signal, lesion.dwi?.adcMin]
    : [lesion.clock, lesion.distanceCm, lesion.depth, lesion.shape, lesion.margin, lesion.internalEnhancement,
      lesion.t2, lesion.kinetics?.early, lesion.kinetics?.delayed, lesion.dwi?.signal, lesion.dwi?.adcMin];
  const coreCount = coreValues.filter(lesionValuePresent).length;
  const descriptorCount = descriptorValues.filter(lesionValuePresent).length;
  const typeSpecific = lesion.type === "nme" ? [
    lesionQuickGroup({ lesion, index, label: "分布", path: "lesion.distribution", options: lesionQuickOptions["lesion.distribution"], defaultLabel: "未描述（默认）" }),
    lesionQuickGroup({ lesion, index, label: "内部强化", path: "lesion.internalEnhancement", options: lesionQuickOptions.nmeEnhancement, defaultLabel: "未描述（默认）" })
  ] : lesion.type === "mass" ? [
    lesionQuickGroup({ lesion, index, label: "形态", path: "lesion.shape", options: lesionQuickOptions["lesion.shape"] }),
    lesionQuickGroup({ lesion, index, label: "边缘", path: "lesion.margin", options: lesionQuickOptions["lesion.margin"] }),
    lesionQuickGroup({ lesion, index, label: "内部强化", path: "lesion.internalEnhancement", options: lesionQuickOptions.massEnhancement })
  ] : ['<p class="lesion-type-gate">先选择 MASS 或 NME，系统再显示对应的形态/分布标准词。</p>'];
  const prompt = `请补充 ${lesion.id}：距乳头距离、三径线（mm）、ADCmin、b值和ROI方法。`;

  return `
    <section class="lesion-choice-panel" aria-label="${escapeHtml(`${lesion.id} 规范字段选择`)}">
      <div class="lesion-choice-heading">
        <span><strong>规范字段选择</strong><small>默认项已就位，只修改本次已观察内容</small></span>
        <em>核心 ${coreCount}/4 · 描述 ${descriptorCount}/${descriptorValues.length}</em>
      </div>
      ${lesionQuickGroup({ lesion, index, label: "类型", path: "lesion.type", options: lesionQuickOptions["lesion.type"], required: true })}
      ${lesionQuickGroup({ lesion, index, label: "侧别", path: "lesion.laterality", options: lesionQuickOptions["lesion.laterality"], required: true })}
      ${lesionQuickGroup({ lesion, index, label: "钟点", path: "lesion.clock", options: lesionQuickOptions["lesion.clock"], hint: "1–12点", defaultLabel: "未定位（默认）" })}
      ${lesionQuickGroup({ lesion, index, label: "深度", path: "lesion.depth", options: lesionQuickOptions["lesion.depth"], defaultLabel: "未定位（默认）" })}
      ${typeSpecific.join("")}
      ${lesionQuickGroup({ lesion, index, label: "T2信号", path: "lesion.t2", options: lesionQuickOptions["lesion.t2"], defaultLabel: "未评价（默认）" })}
      ${lesionQuickGroup({ lesion, index, label: "早期强化", path: "lesion.kinetics.early", options: lesionQuickOptions["lesion.kinetics.early"], hint: "规则阈值", defaultLabel: "未测量（默认）" })}
      ${lesionQuickGroup({ lesion, index, label: "延迟曲线", path: "lesion.kinetics.delayed", options: lesionQuickOptions["lesion.kinetics.delayed"], defaultLabel: "未测量（默认）" })}
      ${lesionQuickGroup({ lesion, index, label: "DWI", path: "lesion.dwi.signal", options: lesionQuickOptions["lesion.dwi.signal"], defaultLabel: "未评价（默认）" })}
      <div class="lesion-direct-section ${hasSize ? "" : "is-required"}">
        <div class="lesion-direct-section-title"><strong>尺寸<b aria-hidden="true">*</b></strong><em>${hasSize ? "已记录" : "至少填写一个径线"}</em></div>
        <div class="lesion-direct-grid size-grid">
          ${lesionDirectField({ lesion, index, label: "前后径", path: "lesion.size.ap", unit: "mm", highlightMissing: !hasSize })}
          ${lesionDirectField({ lesion, index, label: "左右径", path: "lesion.size.transverse", unit: "mm", highlightMissing: !hasSize })}
          ${lesionDirectField({ lesion, index, label: "上下径", path: "lesion.size.cc", unit: "mm", highlightMissing: !hasSize })}
        </div>
      </div>
      <div class="lesion-direct-section">
        <div class="lesion-direct-section-title"><strong>定量补充</strong><em>空值保持安全默认</em></div>
        <div class="lesion-direct-grid measurement-grid">
          ${lesionDirectField({ lesion, index, label: "距乳头", path: "lesion.distanceCm", unit: "cm" })}
          ${lesionDirectField({ lesion, index, label: "ADCmin", path: "lesion.dwi.adcMin", unit: "×10⁻³ mm²/s" })}
          ${lesionDirectField({ lesion, index, label: "b值", path: "lesion.dwi.bValue", unit: "s/mm²", defaultLabel: "未记录（默认）" })}
          ${lesionDirectField({ lesion, index, label: "ROI", path: "lesion.dwi.roiMethod", type: "text", defaultLabel: "未描述（默认）" })}
        </div>
        ${!data.signedAt ? `<button type="button" class="lesion-language-action" data-graph-prompt="${escapeHtml(prompt)}" data-graph-lesion-index="${index}">口述数值</button>` : ""}
      </div>
      <div class="lesion-assessment-section ${!birads ? "is-required" : ""}">
        <div class="lesion-assessment-heading">
          <span><strong>病灶级诊断与处置</strong><small>${lesion.id}独立评估，不复制到同侧其他病灶</small></span>
          <em>${birads ? `BI-RADS ${birads}类` : "必须由医生确认"}</em>
        </div>
        ${lesionQuickGroup({ lesion, index, label: "BI-RADS", path: "lesion.assessment.birads", options: lesionQuickOptions["lesion.assessment.birads"], required: true })}
        ${birads ? lesionQuickGroup({ lesion, index, label: "管理建议", path: "lesion.management.action", options: lesionQuickOptions["lesion.management.action"], required: managementRequired, defaultLabel: "建议待定（默认）" }) : '<p class="lesion-type-gate">确认该病灶 BI-RADS 后，系统按确定性规则带出默认管理建议，医生可直接修改。</p>'}
        ${tissueRequired ? `<div class="lesion-direct-section ${lesion.management?.detail ? "" : "is-required"}">
          <div class="lesion-direct-section-title"><strong>取样/后续路径<b aria-hidden="true">*</b></strong><em>${lesion.management?.detail ? "已记录" : "BI-RADS 4/5必填"}</em></div>
          ${lesionDirectField({ lesion, index, label: "取样方式", path: "lesion.management.detail", type: "text", defaultLabel: "例如：MRI引导下活检", highlightMissing: !lesion.management?.detail })}
        </div>` : ""}
        ${pathologyRequired ? `<div class="lesion-direct-section ${lesion.pathology?.reference ? "" : "is-required"}">
          <div class="lesion-direct-section-title"><strong>病理依据和日期<b aria-hidden="true">*</b></strong><em>${lesion.pathology?.reference ? "已核验" : "BI-RADS 6必填"}</em></div>
          ${lesionDirectField({ lesion, index, label: "病理依据", path: "lesion.pathology.reference", type: "text", defaultLabel: "病理诊断、编号和日期", highlightMissing: !lesion.pathology?.reference })}
        </div>` : ""}
      </div>
      <p class="lesion-rule-note">红色仅表示必填；“未描述/未评价”不写入报告事实。BI-RADS必须由医生确认；管理建议由规则生成候选，确认后才进入最终报告。</p>
    </section>
  `;
}

function clearCandidateValue(path, sourceText = "界面恢复安全默认态", lesionUid = lesionUidAt()) {
  if (path.startsWith("lesion.") && !lesionUid) ensureActiveLesion();
  setScopedValue(path, "", lesionUid);
  discardCandidates(data, (candidate) => candidate.path === path
    && (!path.startsWith("lesion.") || candidate.lesionUid === lesionUid), "field_reset");
  const lesion = path.startsWith("lesion.") ? data.lesions.find((item) => item.uid === lesionUid) : null;
  const targetProvenance = lesion ? lesion.provenance : data.provenance;
  targetProvenance[path.replace(/^lesion\./, "")] = {
    sourceType: "not_provided",
    sourceText,
    confirmation: "unconfirmed"
  };
  data.agent.conflicts = (data.agent.conflicts || []).filter((item) => item.path !== path);
}

function prepareLesionEdit(index) {
  if (!Number.isInteger(index) || !data.lesions[index]) return false;
  if (index !== data.activeLesionIndex) {
    data.activeLesionIndex = index;
    data.lesion = data.lesions[index];
    syncLegacyPendingPaths(data);
  }
  return true;
}

function resetTypeSpecificFields(nextType) {
  const paths = nextType === "nme"
    ? ["lesion.shape", "lesion.margin"]
    : ["lesion.distribution"];
  const allowedEnhancement = nextType === "nme" ? lesionQuickOptions.nmeEnhancement : lesionQuickOptions.massEnhancement;
  if (lesionValuePresent(data.lesion.internalEnhancement)
    && !allowedEnhancement.some((option) => option.value === data.lesion.internalEnhancement)) {
    paths.push("lesion.internalEnhancement");
  }
  paths.forEach((path) => clearCandidateValue(path, `切换为 ${nextType.toUpperCase()} 后恢复不适用字段的默认态`));
}

function setLesionQuickCandidate(button) {
  if (data.signedAt) return;
  const index = Number(button.dataset.lesionIndex);
  const path = button.dataset.lesionQuickPath;
  const label = button.dataset.lesionQuickLabel;
  let value = button.dataset.lesionQuickValue;
  if (!Number.isInteger(index) || !data.lesions[index] || !path?.startsWith("lesion.")) return;
  if (!prepareLesionEdit(index)) return;
  const lesionUid = lesionUidAt(index);
  const previous = scopedValue(path, lesionUid);
  const previousSource = sourceForPath(path, lesionUid);
  if (value === DEFAULT_CHOICE_VALUE) {
    if (!lesionValuePresent(previous)) return toast(`“${label}”已是当前默认态`);
    if (previousSource?.confirmation === "physician_confirmed"
      && !window.confirm(`${data.lesion.id} 的已确认值将恢复为“${label}”，是否继续？`)) return;
    clearCandidateValue(path, "界面恢复安全默认态", lesionUid);
    data.draft = null;
    data.status = "CLARIFICATION";
    graphManualOpen.add("findings");
    graphManualOpen.add(`lesion-${index}`);
    persist();
    render();
    return toast(`已恢复“${label}”，不写入报告事实`);
  }
  if (numberPaths.has(path)) value = Number(value);
  if (String(previous) === String(value)) {
    return toast(isPathPending(path, lesionUid) ? `“${label}”已在待确认候选中` : `当前已是“${label}”`);
  }
  if (lesionValuePresent(previous) && previousSource?.confirmation === "physician_confirmed"
    && !window.confirm(`${data.lesion.id} 的已确认值将更改为“${label}”，是否继续？`)) return;
  if (path === "lesion.type" && previous !== value) resetTypeSpecificFields(value);
  setCandidate(path, value, `节点快捷选择：${data.lesion.id} · ${label}`, {
    sourceType: "physician_click",
    confidence: 1,
    lesionUid
  });
  if (path === "lesion.assessment.birads") {
    const ruleAction = defaultManagementForBirads(value);
    setCandidate(
      "lesion.management.action",
      ruleAction,
      `确定性规则：${data.lesion.id} · BI-RADS ${value}类默认处置`,
      { sourceType: "deterministic_rule", confidence: 1, lesionUid }
    );
    if (!["4", "5"].includes(String(value)) && lesionValuePresent(data.lesion.management?.detail)) {
      clearCandidateValue("lesion.management.detail", `BI-RADS调整为${value}类，原取样路径已清空`, lesionUid);
    }
    if (String(value) !== "6" && lesionValuePresent(data.lesion.pathology?.reference)) {
      clearCandidateValue("lesion.pathology.reference", `BI-RADS调整为${value}类，原6类病理依据已清空`, lesionUid);
    }
  }
  const relatedPaths = path === "lesion.assessment.birads"
    ? new Set([path, "lesion.management.action", "lesion.management.detail", "lesion.pathology.reference"])
    : new Set([path]);
  data.agent.conflicts = (data.agent.conflicts || []).filter((item) => !relatedPaths.has(item.path));
  data.draft = null;
  data.status = "CLARIFICATION";
  renumberLesions();
  graphManualOpen.add("findings");
  graphManualOpen.add(`lesion-${index}`);
  persist();
  render();
  toast(`已选择“${label}”，确认节点后写入报告事实`);
}

function setLesionDirectCandidate(input) {
  if (data.signedAt) return;
  const index = Number(input.dataset.lesionIndex);
  const path = input.dataset.lesionDirectPath;
  const label = input.dataset.lesionDirectLabel;
  if (!path?.startsWith("lesion.") || !prepareLesionEdit(index)) return;
  const lesionUid = lesionUidAt(index);
  const previous = scopedValue(path, lesionUid);
  const previousSource = sourceForPath(path, lesionUid);
  const raw = input.value.trim();
  if (raw === "") {
    if (!lesionValuePresent(previous)) return;
    if (previousSource?.confirmation === "physician_confirmed"
      && !window.confirm(`${data.lesion.id} 的已确认“${label}”将清空并恢复默认态，是否继续？`)) return render();
    clearCandidateValue(path, `表格清空：${data.lesion.id} · ${label}`, lesionUid);
  } else {
    const value = numberPaths.has(path) ? Number(raw) : raw;
    if (numberPaths.has(path) && (!Number.isFinite(value) || value < 0)) {
      input.setCustomValidity("请输入大于或等于0的数值");
      input.reportValidity();
      input.setCustomValidity("");
      return;
    }
    if (String(previous) === String(value) && isPathPending(path, lesionUid)) return;
    if (lesionValuePresent(previous) && String(previous) !== String(value)
      && previousSource?.confirmation === "physician_confirmed"
      && !window.confirm(`${data.lesion.id} 的已确认“${label}”将更改，是否继续？`)) return render();
    setCandidate(path, value, `表格直接填写：${data.lesion.id} · ${label}`, {
      sourceType: "physician_direct",
      confidence: 1,
      lesionUid
    });
  }
  data.agent.conflicts = (data.agent.conflicts || []).filter((item) => item.path !== path);
  data.draft = null;
  data.status = "CLARIFICATION";
  graphManualOpen.add("findings");
  graphManualOpen.add(`lesion-${index}`);
  persist();
  render();
  toast(raw === "" ? `“${label}”已恢复默认态` : `“${label}”已进入待确认候选`);
}

function setGraphQuickCandidate(button) {
  if (data.signedAt) return;
  const path = button.dataset.graphQuickPath;
  const label = button.dataset.graphQuickLabel;
  const value = button.dataset.graphQuickValue;
  if (!path || path.startsWith("lesion.")) return;
  const previous = getValue(data, path);
  const previousSource = data.provenance?.[path];
  if (value === DEFAULT_CHOICE_VALUE) {
    if (!lesionValuePresent(previous)) return;
    clearCandidateValue(path);
  } else {
    const baselineNeedsConfirmation = previousSource?.sourceType === "institutional_normal_default";
    if (String(previous) === String(value) && !baselineNeedsConfirmation) {
      return toast(isPathPending(path) ? `“${label}”已在待确认候选中` : `当前已是“${label}”`);
    }
    if (lesionValuePresent(previous) && String(previous) !== String(value)
      && previousSource?.confirmation === "physician_confirmed"
      && !window.confirm(`已确认值将更改为“${label}”，是否继续？`)) return;
    setCandidate(path, value, `图谱快捷选择：${label}`, { sourceType: "physician_click", confidence: 1 });
  }
  if (path === "associatedStructures.status" && value === "normal") clearCandidateValue("associatedStructures.detail");
  if (path === "lymphNodes.status" && value === "normal") clearCandidateValue("lymphNodes.detail");
  data.draft = null;
  data.status = "CLARIFICATION";
  graphManualOpen.add(graphKeyForPath(path));
  persist();
  render();
  toast(`已选择“${label}”，确认节点后生效`);
}

function setGraphDirectCandidate(input) {
  if (data.signedAt) return;
  const path = input.dataset.graphDirectPath;
  const label = input.dataset.graphDirectLabel;
  if (!path || path.startsWith("lesion.")) return;
  const previous = getValue(data, path);
  const previousSource = data.provenance?.[path];
  const raw = input.value.trim();
  if (raw === "") {
    if (!lesionValuePresent(previous)) return;
    if (previousSource?.confirmation === "physician_confirmed"
      && !window.confirm(`已确认“${label}”将清空并恢复默认态，是否继续？`)) return render();
    clearCandidateValue(path, `表格清空：${label}`);
  } else {
    const value = numberPaths.has(path) ? Number(raw) : raw;
    if (String(previous) === String(value) && isPathPending(path)) return;
    if (lesionValuePresent(previous) && String(previous) !== String(value)
      && previousSource?.confirmation === "physician_confirmed"
      && !window.confirm(`已确认“${label}”将更改，是否继续？`)) return render();
    setCandidate(path, value, `表格直接填写：${label}`, { sourceType: "physician_direct", confidence: 1 });
  }
  data.draft = null;
  data.status = "CLARIFICATION";
  graphManualOpen.add(graphKeyForPath(path));
  persist();
  render();
  toast(raw === "" ? `“${label}”已恢复默认态` : `“${label}”已进入待确认候选`);
}

function renderLesionGraphNode(lesion, index) {
  const base = `lesion-${index}`;
  const sizeValues = [lesion.size?.ap, lesion.size?.transverse, lesion.size?.cc]
    .filter((value) => value !== "" && value !== null && value !== undefined);
  const size = sizeValues.length ? `${sizeValues.join("×")} mm` : "";
  const location = [
    uiLabels.laterality[lesion.laterality],
    lesion.clock ? `${lesion.clock}点钟` : "",
    lesion.distanceCm !== "" ? `距乳头${lesion.distanceCm}cm` : "",
    uiLabels.depth[lesion.depth]
  ].filter(Boolean).join(" · ");
  const morphology = lesion.type === "nme"
    ? [uiLabels.distribution[lesion.distribution], uiLabels.enhancement[lesion.internalEnhancement]].filter(Boolean).join(" · ")
    : [uiLabels.shape[lesion.shape], uiLabels.margin[lesion.margin], uiLabels.enhancement[lesion.internalEnhancement], lesion.t2 === "notHigh" ? "T2非高" : lesion.t2 === "high" ? "T2高" : ""].filter(Boolean).join(" · ");
  const kinetics = [uiLabels.early[lesion.kinetics?.early], uiLabels.delayed[lesion.kinetics?.delayed]].filter(Boolean).join(" · ");
  const dwi = [uiLabels.dwi[lesion.dwi?.signal], lesion.dwi?.adcMin !== "" ? `ADC ${lesion.dwi.adcMin}` : "", lesion.dwi?.bValue !== "" ? `b=${lesion.dwi.bValue}` : ""].filter(Boolean).join(" · ");
  const birads = String(lesion.assessment?.birads || "");
  const management = uiLabels.management[lesion.management?.action] || "";
  const typeState = lesionGraphState(index, "lesion.type", lesion.type, { required: true });
  const locationState = aggregateGraphState([
    lesionGraphState(index, "lesion.laterality", lesion.laterality, { required: true }),
    lesionGraphState(index, ["lesion.clock", "lesion.distanceCm", "lesion.depth"], [lesion.clock, lesion.distanceCm, lesion.depth])
  ]);
  const sizeState = lesionGraphState(index, ["lesion.size.ap", "lesion.size.transverse", "lesion.size.cc"], sizeValues, { required: true });
  const morphologyState = lesionGraphState(index, lesion.type === "nme" ? ["lesion.distribution", "lesion.internalEnhancement"] : ["lesion.shape", "lesion.margin", "lesion.internalEnhancement", "lesion.t2"], morphology);
  const kineticsState = lesionGraphState(index, ["lesion.kinetics.early", "lesion.kinetics.delayed"], kinetics);
  const dwiState = lesionGraphState(index, ["lesion.dwi.signal", "lesion.dwi.adcMin", "lesion.dwi.bValue", "lesion.dwi.roiMethod"], dwi);
  const biradsState = lesionGraphState(index, "lesion.assessment.birads", birads, { required: true });
  const managementState = lesionGraphState(index, "lesion.management.action", lesion.management?.action, { required: Boolean(birads) });
  const tissueState = ["4", "5"].includes(birads)
    ? lesionGraphState(index, "lesion.management.detail", lesion.management?.detail, { required: true })
    : null;
  const pathologyState = birads === "6"
    ? lesionGraphState(index, "lesion.pathology.reference", lesion.pathology?.reference, { required: true })
    : null;
  const candidateOptionalStates = [morphologyState, kineticsState, dwiState].filter((state) => state.key === "candidate");
  const assessmentStates = [biradsState, managementState, tissueState, pathologyState].filter(Boolean);
  const state = graphStateForNode(base, [typeState, locationState, sizeState, ...assessmentStates, ...candidateOptionalStates]);
  const summary = [uiLabels.lesionType[lesion.type] || "类型待补", location || "位置待补", size || "尺寸待补", birads ? `BI-RADS ${birads}类` : "分类待确认"].join(" · ");
  const facts = [
    graphFact("病灶类型", uiLabels.lesionType[lesion.type] || "", typeState),
    graphFact("定位", location, locationState),
    graphFact("尺寸", size, sizeState),
    graphFact(lesion.type === "nme" ? "分布与强化" : "形态与信号", morphology, morphologyState, { omitWhenEmpty: true }),
    graphFact("强化动力学", kinetics, kineticsState, { omitWhenEmpty: true }),
    graphFact("弥散", dwi, dwiState, { omitWhenEmpty: true }),
    graphFact("病灶评估", birads ? `BI-RADS ${birads}类 · ${management || "建议待确认"}` : "", aggregateGraphState(assessmentStates))
  ].join("");
  const actions = [
    graphNodeActions(base),
    index === data.activeLesionIndex
      ? '<span class="graph-target-current">当前输入目标</span>'
      : `<button type="button" class="graph-action" data-graph-target-lesion="${index}">设为输入目标</button>`,
    !data.signedAt ? `<button type="button" class="graph-action graph-action-danger" data-remove-graph-lesion="${index}">删除节点</button>` : ""
  ].join("");
  const body = `
    <div class="graph-lesion-layout">
      <div class="graph-lesion-map">
        ${lesionMarkerSvg(lesion)}
        <div class="graph-lesion-map-meta">
          <span class="${lesion.laterality ? "" : "is-required"}"><b>侧别</b>${escapeHtml(uiLabels.laterality[lesion.laterality] || "必填")}</span>
          <span><b>钟点</b>${escapeHtml(lesion.clock ? `${lesion.clock}点` : "未定位（默认）")}</span>
          <span><b>深度</b>${escapeHtml(uiLabels.depth[lesion.depth] || "未定位（默认）")}</span>
          <span><b>类型</b>${escapeHtml(uiLabels.lesionType[lesion.type] || "必填")}</span>
        </div>
        <small>时钟面/深度示意 · 非诊断影像</small>
      </div>
      <div class="graph-facts">${facts}</div>
    </div>
    ${renderLesionChoicePanel(lesion, index)}
  `;
  return graphNode({ key: base, label: lesion.id, summary, state, body, actions, className: index === data.activeLesionIndex ? "is-active" : "" });
}

function renderStructureSummary(validation) {
  const lesions = data.scenario === "lesion" ? (data.lesions || []) : [];
  const abnormal = lesions.length > 0;
  const fgt = data.breastTissue.fgt ? `${data.breastTissue.fgt}类` : "待确认";
  const bpe = [uiLabels.bpe[data.breastTissue.bpeLevel], uiLabels.symmetry[data.breastTissue.bpeSymmetry]].filter(Boolean).join("、") || "待确认";
  const structures = data.associatedStructures.status === "normal" ? "正常" : data.associatedStructures.detail || "待说明";
  const nodes = data.lymphNodes.status === "normal" ? "未见异常" : data.lymphNodes.detail || "待说明";
  const overall = data.signedAt ? "已签发并锁定"
    : pendingCandidateCount(data) ? `${pendingCandidateCount(data)}项候选待确认`
      : validation.blocking.length ? `${validation.blocking.length}项需要处理`
        : "结构完整 · 可记录版本";

  const contextState = { key: "system", label: "系统资料" };
  const sequenceText = Array.isArray(data.acquisition?.sequences)
    ? data.acquisition.sequences.filter(Boolean).join("、")
    : String(data.acquisition?.sequences || "").trim();
  const qualityText = ({ satisfactory: "满意", acceptable: "基本满意", limited: "受限" })[data.acquisition?.quality] || "";
  const comparisonText = ({
    none: "无可供比较的既往乳腺影像",
    compared: data.comparison?.reference ? `与${data.comparison.reference}比较` : "已与既往乳腺影像比较",
    limited: data.comparison?.reference ? `比较受限：${data.comparison.reference}` : "既往资料不完整，比较受限"
  })[data.comparison?.status] || "";
  const contextItems = [
    { label: "RIS检查项目", value: String(data.exam?.projectName || "").trim() },
    { label: "DICOM序列", value: sequenceText },
    { label: "图像质量", value: qualityText },
    { label: "既往比较", value: comparisonText }
  ].filter((item) => item.value);
  const contextBody = contextItems.length
    ? `<div class="graph-facts">${contextItems.map((item) => graphFact(item.label, item.value, contextState)).join("")}</div>`
    : `<div class="graph-context-empty">
        <strong>乳腺 MRI 模板已确定</strong>
        <span>RIS检查名称、DICOM序列、既往比较和图像质量接入后在此显示，无需医生重复录入。</span>
      </div>`;
  const contextNode = graphNode({
    key: "context",
    label: "系统资料",
    summary: contextItems.length ? `${contextItems.length}项已接入` : "RIS / DICOM 尚未接入",
    state: { key: "system", label: contextItems.length ? "系统带入" : "不阻断" },
    body: contextBody
  });

  const fgtState = fieldState("breastTissue.fgt", data.breastTissue.fgt, { required: true });
  const bpeLevelState = fieldState("breastTissue.bpeLevel", data.breastTissue.bpeLevel, { required: true });
  const bpeSymmetryState = fieldState("breastTissue.bpeSymmetry", data.breastTissue.bpeSymmetry, { required: true });
  const backgroundState = graphStateForNode("background", [fgtState, bpeLevelState, bpeSymmetryState]);
  const backgroundBody = `<div class="graph-inline-editor">
    ${graphQuickGroup({ label: "FGT", path: "breastTissue.fgt", options: graphQuickOptions["breastTissue.fgt"], required: true })}
    ${graphQuickGroup({ label: "BPE程度", path: "breastTissue.bpeLevel", options: graphQuickOptions["breastTissue.bpeLevel"], required: true })}
    ${graphQuickGroup({ label: "BPE对称性", path: "breastTissue.bpeSymmetry", options: graphQuickOptions["breastTissue.bpeSymmetry"], required: true })}
  </div>`;
  const backgroundNode = graphNode({
    key: "background",
    label: "乳腺背景",
    summary: `${fgt} · BPE ${bpe}`,
    state: backgroundState,
    body: backgroundBody,
    actions: graphNodeActions("background")
  });

  const lesionNodes = abnormal
    ? lesions.map((lesion, index) => renderLesionGraphNode(lesion, index)).join("")
    : graphNode({
      key: "normal-findings",
      label: "未见异常病灶",
      summary: "正常结构基线",
      state: { key: "baseline", label: "正常基线" },
      body: '<p class="graph-node-note">未见可疑肿块或非肿块强化；病灶专属节点保持收缩。</p>'
    });
  const findingsState = graphStateForNode("findings", abnormal
    ? lesions.map((lesion, index) => {
      const birads = String(lesion.assessment?.birads || "");
      const required = [
        lesion.type,
        lesion.laterality,
        lesion.size?.ap || lesion.size?.transverse || lesion.size?.cc,
        birads,
        lesion.management?.action,
        ...(["4", "5"].includes(birads) ? [lesion.management?.detail] : []),
        ...(birads === "6" ? [lesion.pathology?.reference] : [])
      ];
      if (pendingCandidates(data).some((candidate) => candidate.lesionUid === lesion.uid || candidate.path === "scenario")) {
        return { key: "candidate", label: "候选待确认" };
      }
      return required.every((value) => value !== "" && value !== null && value !== undefined)
        ? { key: "confirmed", label: "已记录" } : { key: "missing", label: "必填" };
    })
    : [{ key: "baseline", label: "正常基线" }]);
  const findingsActions = [
    graphNodeActions("findings"),
    !data.signedAt ? '<button type="button" class="graph-action" data-new-lesion>＋ 新增发现节点</button>' : ""
  ].join("");
  const findingsNode = graphNode({
    key: "findings",
    label: "影像所见",
    summary: abnormal ? `${lesions.length}个病灶节点 · ${lesions[data.activeLesionIndex]?.id || ""}为当前目标` : "未见可疑病灶",
    state: findingsState,
    body: `<div class="graph-children" role="group">${lesionNodes}</div>`,
    actions: findingsActions
  });

  const structuresState = fieldState("associatedStructures.status", structures);
  const lymphState = fieldState("lymphNodes.status", nodes);
  const structuresDetailRequired = ["abnormal", "limited"].includes(data.associatedStructures.status);
  const lymphDetailRequired = ["abnormal", "limited"].includes(data.lymphNodes.status);
  const structuresDetailState = fieldState("associatedStructures.detail", data.associatedStructures.detail, { required: structuresDetailRequired });
  const lymphDetailState = fieldState("lymphNodes.detail", data.lymphNodes.detail, { required: lymphDetailRequired });
  const relatedState = graphStateForNode("related", [
    structuresState,
    lymphState,
    ...(structuresDetailRequired ? [structuresDetailState] : []),
    ...(lymphDetailRequired ? [lymphDetailState] : [])
  ]);
  const relatedBody = `<div class="graph-inline-editor">
    ${graphQuickGroup({ label: "乳头/皮肤/胸壁", path: "associatedStructures.status", options: graphQuickOptions["associatedStructures.status"] })}
    ${structuresDetailRequired ? graphDirectInput({ label: "相关结构说明", path: "associatedStructures.detail", required: true }) : ""}
    ${graphQuickGroup({ label: "区域淋巴结", path: "lymphNodes.status", options: graphQuickOptions["lymphNodes.status"] })}
    ${lymphDetailRequired ? graphDirectInput({ label: "淋巴结说明", path: "lymphNodes.detail", required: true }) : ""}
  </div>`;
  const relatedNode = graphNode({
    key: "related",
    label: "相关结构",
    summary: `${structures} · 淋巴结${nodes}`,
    state: relatedState,
    body: relatedBody,
    actions: graphNodeActions("related")
  });

  const leftDefault = data.provenance?.["assessment.leftBirads"]?.sourceType === "institutional_normal_default";
  const rightDefault = data.provenance?.["assessment.rightBirads"]?.sourceType === "institutional_normal_default";
  const leftBirads = `${data.assessment.leftBirads || "待确认"}类${leftDefault ? "（基线）" : ""}`;
  const rightBirads = `${data.assessment.rightBirads || "待确认"}类${rightDefault ? "（基线）" : ""}`;
  const managementDefault = data.provenance?.["management.action"]?.sourceType === "institutional_normal_default";
  let diagnosisState;
  let diagnosisBody;
  let diagnosisSummary;
  let diagnosisActions;
  if (abnormal) {
    const lesionAssessmentStates = lesions.map((lesion, index) => {
      const birads = String(lesion.assessment?.birads || "");
      return aggregateGraphState([
        lesionGraphState(index, "lesion.assessment.birads", birads, { required: true }),
        lesionGraphState(index, "lesion.management.action", lesion.management?.action, { required: Boolean(birads) }),
        ...(["4", "5"].includes(birads) ? [lesionGraphState(index, "lesion.management.detail", lesion.management?.detail, { required: true })] : []),
        ...(birads === "6" ? [lesionGraphState(index, "lesion.pathology.reference", lesion.pathology?.reference, { required: true })] : [])
      ]);
    });
    const sideOverall = (side) => {
      const sideLesions = lesions.filter((lesion) => lesion.laterality === side);
      const sideName = side === "left" ? "左乳" : "右乳";
      if (!sideLesions.length) return `${sideName}1类（基线）`;
      if (sideLesions.some((lesion) => !lesion.assessment?.birads)) return `${sideName}总体待确认`;
      return `${sideName}总体${Math.max(...sideLesions.map((lesion) => Number(lesion.assessment.birads)))}类`;
    };
    diagnosisState = graphStateForNode("diagnosis", lesionAssessmentStates);
    diagnosisSummary = `${lesions.map((lesion) => `${lesion.id} ${lesion.assessment?.birads ? `${lesion.assessment.birads}类` : "待确认"}`).join(" · ")} · ${sideOverall("left")} / ${sideOverall("right")}`;
    diagnosisBody = `<div class="lesion-diagnosis-index">
      ${lesions.map((lesion, index) => {
        const birads = lesion.assessment?.birads;
        const state = lesionAssessmentStates[index];
        const location = [uiLabels.laterality[lesion.laterality], lesion.clock ? `${lesion.clock}点` : ""].filter(Boolean).join(" · ") || "位置待补";
        return `<article class="lesion-diagnosis-row state-${state.key}">
          <strong>${escapeHtml(lesion.id)}</strong>
          <span>${escapeHtml(`${location} · ${birads ? `BI-RADS ${birads}类` : "分类待确认"}`)}</span>
          <small>${escapeHtml(uiLabels.management[lesion.management?.action] || "建议待确认")}</small>
          <em>${escapeHtml(state.label)}</em>
        </article>`;
      }).join("")}
      <p>总体评估由同侧病灶类别汇总；每个病灶的分类和建议仍在各自节点内独立确认。</p>
    </div>`;
    diagnosisActions = "";
  } else {
    const leftState = fieldState("assessment.leftBirads", data.assessment.leftBirads);
    const rightState = fieldState("assessment.rightBirads", data.assessment.rightBirads);
    const managementState = fieldState("management.action", data.management.action);
    diagnosisState = graphStateForNode("diagnosis", [leftState, rightState, managementState]);
    diagnosisSummary = `左乳 ${leftBirads} · 右乳 ${rightBirads} · ${managementDefault ? "常规随访（基线）" : uiLabels.management[data.management.action] || "建议待确认"}`;
    diagnosisBody = `<div class="graph-inline-editor diagnosis-inline-editor">
      ${graphQuickGroup({ label: "左乳 BI-RADS", path: "assessment.leftBirads", options: graphQuickOptions["assessment.leftBirads"] })}
      ${graphQuickGroup({ label: "右乳 BI-RADS", path: "assessment.rightBirads", options: graphQuickOptions["assessment.rightBirads"] })}
      ${graphQuickGroup({ label: "管理建议", path: "management.action", options: graphQuickOptions["management.action"] })}
    </div>`;
    diagnosisActions = graphNodeActions("diagnosis");
  }
  const diagnosisNode = graphNode({
    key: "diagnosis",
    label: "诊断与管理",
    summary: diagnosisSummary,
    state: diagnosisState,
    body: diagnosisBody,
    actions: diagnosisActions
  });

  const rootState = data.signedAt ? { key: "signed", label: "已签发" }
    : data.agent?.conflicts?.length ? { key: "conflict", label: `${data.agent.conflicts.length}项冲突` }
      : pendingCandidateCount(data) ? { key: "candidate", label: `${pendingCandidateCount(data)}项候选` }
        : validation.blocking.length ? { key: "missing", label: `${validation.blocking.length}项待处理` }
          : { key: "confirmed", label: "结构完整" };
  dom.structureSummary.innerHTML = `
    <div class="report-graph" aria-label="乳腺MRI动态报告图谱">
      <div class="graph-root state-${rootState.key}">
        <span class="graph-root-mark" aria-hidden="true">MR</span>
        <span class="graph-root-copy"><strong>双侧乳腺 MRI</strong><span>${escapeHtml(overall)}</span></span>
        <span class="graph-root-state">${escapeHtml(rootState.label)}</span>
      </div>
      <div class="graph-tree" role="tree">
        ${contextNode}
        ${backgroundNode}
        ${findingsNode}
        ${relatedNode}
        ${diagnosisNode}
      </div>
      <p class="graph-help"><span></span>灰色为安全默认，不写入病灶事实；红色仅高亮必填，蓝色待确认，绿色已确认。</p>
    </div>
  `;
}

function renderSignoff(validation) {
  const canGenerate = validation.blocking.length === 0 && !data.signedAt;
  dom.generateButton.disabled = !canGenerate;
  const canSign = canGenerate && Boolean(data.draft) && dom.physicianConfirm.checked;
  dom.signButton.disabled = !canSign;
  dom.generateButton.textContent = data.signedAt ? "版本已锁定" : data.draft ? "重新记录当前版本" : "记录当前版本";
  dom.signButton.textContent = data.signedAt ? "已签发 · 报告锁定" : "医生签发";
  dom.signHint.textContent = data.signedAt
    ? `已于 ${new Date(data.signedAt).toLocaleString("zh-CN")} 签发；内容已锁定。`
    : canSign ? "已满足签发条件。签发后将锁定当前版本。" : "签发需要：无阻断项、已生成草稿、医生最终确认。";
}

function render() {
  hydrateForm();
  renderAgentConversation();
  renderExtractionNotice();
  const validation = validateCase(data);
  renderQuestions(validation);
  renderWorkflow(validation);
  renderStructureSummary(validation);
  renderReportContent();
  renderSignoff(validation);
}

async function checkModelStatus() {
  try {
    const response = await fetch("/api/model-status");
    const payload = await response.json();
    if (payload.fallbackAvailable && !payload.online) {
      dom.modelStatus.textContent = "LLM离线 · 本地提取可用";
      dom.modelStatus.className = "model-status model-degraded";
      return;
    }
    if (!response.ok || !payload.online) throw new Error(payload.error || "模型未加载");
    dom.modelStatus.textContent = `${payload.model} 在线`;
    dom.modelStatus.className = "model-status model-online";
  } catch {
    dom.modelStatus.textContent = "服务不可用";
    dom.modelStatus.className = "model-status model-offline";
  }
}

dom.structuredForm.addEventListener("change", updateFromForm);
dom.structuredForm.addEventListener("input", (event) => {
  if (event.target.tagName === "INPUT" && event.target.dataset.path) updateFromForm(event);
});
document.querySelectorAll('input[name="scenario"]').forEach((radio) => radio.addEventListener("change", () => {
  if (radio.value === "normal" && data.lesions.length) {
    if (!window.confirm("切换为无可疑病灶会清除现有病灶卡片，是否继续？")) {
      document.querySelector(`input[name="scenario"][value="${data.scenario}"]`).checked = true;
      return;
    }
    data.lesions = [];
    data.lesion = createLesion();
    data.activeLesionIndex = 0;
    discardCandidates(data, (candidate) => candidate.scope === "lesion", "scenario_changed_to_normal");
    syncLegacyPendingPaths(data);
  }
  data.scenario = radio.value;
  data.draft = null;
  persist();
  render();
}));
dom.extractButton.addEventListener("click", runAgentTurn);
dom.voiceButton.addEventListener("click", toggleVoiceInput);
dom.dictation.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    runAgentTurn();
  }
});
dom.massSampleButton.addEventListener("click", () => { dom.dictation.value = massSample; toast("已载入单肿块示例"); });
dom.normalSampleButton.addEventListener("click", () => { dom.dictation.value = normalSample; toast("已载入无异常示例"); });
dom.addLesionButton.addEventListener("click", addLesion);
dom.structureSummary.addEventListener("click", (event) => {
  if (event.target.closest("[data-new-lesion]")) return addLesion();
  const graphQuickChoice = event.target.closest("[data-graph-quick-path]");
  if (graphQuickChoice) return setGraphQuickCandidate(graphQuickChoice);
  const quickChoice = event.target.closest("[data-lesion-quick-path]");
  if (quickChoice) return setLesionQuickCandidate(quickChoice);
  const confirmNode = event.target.closest("[data-confirm-graph-node]");
  if (confirmNode) return confirmGraphNode(confirmNode.dataset.confirmGraphNode);
  const targetLesion = event.target.closest("[data-graph-target-lesion]");
  if (targetLesion) return selectLesion(Number(targetLesion.dataset.graphTargetLesion));
  const removeGraphLesion = event.target.closest("[data-remove-graph-lesion]");
  if (removeGraphLesion) return removeLesion(Number(removeGraphLesion.dataset.removeGraphLesion));
  const prompt = event.target.closest("[data-graph-prompt]");
  if (prompt) {
    const lesionIndex = prompt.dataset.graphLesionIndex;
    if (lesionIndex !== undefined && Number(lesionIndex) !== data.activeLesionIndex) {
      if (hasUnconfirmedLesionCandidates()) return toast("请先确认当前病灶候选，再切换输入目标");
      data.activeLesionIndex = Number(lesionIndex);
      data.lesion = data.lesions[data.activeLesionIndex];
    }
    if (!dom.dictation.value.trim()) dom.dictation.value = prompt.dataset.graphPrompt;
    persist();
    dom.dictation.focus();
    dom.dictation.scrollIntoView({ behavior: "smooth", block: "center" });
    toast("请在右侧继续描述，模型将填写该节点");
    return;
  }
  const summary = event.target.closest("summary");
  const node = summary?.parentElement?.closest("[data-graph-node]");
  if (!node) return;
  window.requestAnimationFrame(() => {
    if (node.open) graphManualOpen.add(node.dataset.graphNode);
    else graphManualOpen.delete(node.dataset.graphNode);
  });
});
dom.structureSummary.addEventListener("change", (event) => {
  const lesionDirect = event.target.closest("[data-lesion-direct-path]");
  if (lesionDirect) return setLesionDirectCandidate(lesionDirect);
  const graphDirect = event.target.closest("[data-graph-direct-path]");
  if (graphDirect) return setGraphDirectCandidate(graphDirect);
});
dom.lesionCards.addEventListener("click", (event) => {
  const select = event.target.closest("[data-select-lesion]");
  const edit = event.target.closest("[data-edit-lesion]");
  const remove = event.target.closest("[data-remove-lesion]");
  if (select) selectLesion(Number(select.dataset.selectLesion));
  if (edit) selectLesion(Number(edit.dataset.editLesion), true);
  if (remove) removeLesion(Number(remove.dataset.removeLesion));
});
dom.generateButton.addEventListener("click", () => {
  const validation = validateCase(data);
  if (validation.blocking.length) return toast("仍有阻断项，不能生成可签发草稿");
  const report = renderReport(data);
  data.draft = { generatedAt: report.generatedAt, sections: structuredClone(report.sections) };
  data.status = "PHYSICIAN_REVIEW";
  persist();
  render();
  toast("已记录当前报告版本");
});
dom.physicianConfirm.addEventListener("change", () => renderSignoff(validateCase(data)));
dom.signButton.addEventListener("click", () => {
  if (validateCase(data).blocking.length || !data.draft || !dom.physicianConfirm.checked) return;
  const report = renderReport(data);
  data.signedSnapshot = {
    generatedAt: report.generatedAt,
    sections: structuredClone(report.sections),
    templateId: data.templateId,
    templateVersion: data.templateVersion
  };
  data.signedAt = new Date().toISOString();
  data.status = "SIGNED";
  persist();
  render();
  toast("报告已签发并锁定");
});
dom.reportOutputButton.addEventListener("click", openReportOutputDialog);
dom.closeReportOutputButton.addEventListener("click", closeReportOutputDialog);
dom.reportOutputDialog.addEventListener("click", (event) => {
  if (event.target === dom.reportOutputDialog) closeReportOutputDialog();
});
dom.reportOutputDialog.querySelectorAll("[data-report-format]").forEach((button) => {
  button.addEventListener("click", () => renderReportOutputPreview(button.dataset.reportFormat));
});
dom.printGeneratedReportButton.addEventListener("click", printGeneratedReport);
dom.exportTextButton.addEventListener("click", exportTextReport);
window.addEventListener("afterprint", cleanupGeneratedPrint);
dom.resetButton.addEventListener("click", () => {
  if (!window.confirm("确定清空当前演示会话并新建吗？")) return;
  activeAgentRequest += 1;
  agentBusy = false;
  localStorage.removeItem(STORAGE_KEY);
  data = createEmptyCase();
  graphManualOpen.clear();
  dom.dictation.value = "";
  dom.extractionNotice.hidden = true;
  dom.physicianConfirm.checked = false;
  render();
});

setupVoiceInput();
render();
checkModelStatus();
