import { pendingCandidates } from "./candidateLedger.js";

const labels = {
  quality: { satisfactory: "满意", acceptable: "基本满意", limited: "受限" },
  comparison: { none: "无可供比较的既往乳腺影像资料", compared: "已与既往乳腺影像比较", limited: "既往资料不完整，比较受限" },
  bpe: { minimal: "极少", mild: "轻度", moderate: "中度", marked: "显著" },
  symmetry: { symmetric: "对称", asymmetric: "不对称" },
  laterality: { left: "左", right: "右" },
  depth: { anterior: "前1/3", middle: "中1/3", posterior: "后1/3" },
  shape: { oval: "卵圆形", lobulated: "分叶状", round: "圆形", irregular: "不规则形" },
  margin: { circumscribed: "清楚", indistinct: "模糊", spiculated: "毛刺" },
  distribution: { diffuse: "弥漫性", regional: "区域性", focal: "局灶性", linear: "线样", segmental: "节段性" },
  enhancement: {
    homogeneous: "均匀", heterogeneous: "不均匀", rim: "厚环形",
    darkSeptations: "内部无强化分隔", clumped: "簇集状", clusteredRing: "簇状环形"
  },
  t2: { high: "高信号", notHigh: "非高信号", limited: "无法可靠评价" },
  early: { slow: "慢速", medium: "中速", fast: "快速", limited: "无法可靠评价" },
  delayed: { persistent: "持续型", plateau: "平台型", washout: "廓清型", limited: "无法可靠评价" },
  dwi: { high: "高信号", equal: "等信号", low: "低信号", limited: "无法可靠评价" },
  normality: { normal: "未见明确异常", abnormal: "存在异常", limited: "评价受限" },
  management: {
    routine: "按相应风险分层及筛查指南常规随访",
    shortInterval: "建议短期随访复查",
    tissueDiagnosis: "建议组织学诊断",
    surgicalOncology: "建议乳腺外科和/或肿瘤科管理",
    additionalImaging: "建议补充影像检查"
  }
};

const has = (value) => value !== "" && value !== null && value !== undefined;
const isSystemSource = (source) =>
  ["ris_project", "dicom_protocol", "pacs_context", "system_record"].includes(source);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
})[character]);

function sourceState(data, paths) {
  if (paths.includes("lesion") && data.scenario === "lesion") {
    return pendingCandidates(data).some((candidate) => candidate.scope === "lesion")
      || data.pendingPaths?.some((path) => path.startsWith("lesion."))
      ? { key: "pending", label: "待确认" }
      : { key: "updated", label: "已更新" };
  }
  const sources = paths.map((path) => data.provenance?.[path]?.sourceType).filter(Boolean);
  if (sources.some((source) => ["physician_click", "agent_extraction", "physician_confirmed"].includes(source))) {
    return { key: "updated", label: "已更新" };
  }
  if (sources.some(isSystemSource)) return { key: "system", label: "系统带入" };
  if (sources.length && sources.every((source) => source === "institutional_normal_default")) {
    return { key: "baseline", label: "正常基线" };
  }
  return { key: "pending", label: "待完善" };
}

function comparisonText(comparison = {}) {
  if (comparison.status === "none") return "无可供比较的既往乳腺影像资料";
  if (comparison.status === "compared") {
    return comparison.reference ? `与${comparison.reference}比较` : "已与既往乳腺影像比较";
  }
  if (comparison.status === "limited") {
    return comparison.reference ? `既往资料不完整，比较受限：${comparison.reference}` : "既往资料不完整，比较受限";
  }
  return "";
}

function contrastText(contrast = {}) {
  const details = [
    contrast.name,
    has(contrast.doseMl) ? `剂量${contrast.doseMl} mL` : "",
    has(contrast.rateMlPerSec) ? `速率${contrast.rateMlPerSec} mL/s` : ""
  ].filter(Boolean);
  return details.join("，");
}

function lesionText(lesion) {
  const sizes = [lesion.size.ap, lesion.size.transverse, lesion.size.cc].filter(has);
  const missing = [
    !lesion.type ? "病灶类型" : "",
    !lesion.laterality ? "侧别" : "",
    !sizes.length ? "尺寸" : ""
  ].filter(Boolean);
  if (missing.length) return `${lesion.id}：新增病灶节点，${missing.join("、")}待补充。`;
  const type = lesion.type === "nme" ? "非肿块强化" : "肿块";
  const breastAndClock = [
    labels.laterality[lesion.laterality] ? `${labels.laterality[lesion.laterality]}乳` : "",
    has(lesion.clock) ? `${lesion.clock}点方向` : ""
  ].join("");
  const location = [
    breastAndClock,
    has(lesion.distanceCm) ? `距乳头约${lesion.distanceCm} cm` : "",
    labels.depth[lesion.depth] ? `位于${labels.depth[lesion.depth]}` : ""
  ].filter(Boolean).join("，");
  const sizeText = sizes.length === 3
    ? `，大小约${sizes.join("×")} mm`
    : sizes.length ? `，可用径线约${Math.max(...sizes.map(Number))} mm` : "";
  const morphology = [];
  if (lesion.type === "mass" && labels.shape[lesion.shape]) morphology.push(`形状${labels.shape[lesion.shape]}`);
  if (lesion.type === "mass" && labels.margin[lesion.margin]) morphology.push(`边缘${labels.margin[lesion.margin]}`);
  if (lesion.type === "nme" && labels.distribution[lesion.distribution]) morphology.push(`${labels.distribution[lesion.distribution]}分布`);
  if (labels.enhancement[lesion.internalEnhancement]) morphology.push(`内部${labels.enhancement[lesion.internalEnhancement]}强化`);
  if (labels.t2[lesion.t2]) morphology.push(`T2呈${labels.t2[lesion.t2]}`);
  if (labels.early[lesion.kinetics.early]) morphology.push(`早期${labels.early[lesion.kinetics.early]}强化`);
  if (labels.delayed[lesion.kinetics.delayed]) morphology.push(`延迟期${labels.delayed[lesion.kinetics.delayed]}`);
  if (labels.dwi[lesion.dwi.signal]) morphology.push(`DWI呈${labels.dwi[lesion.dwi.signal]}`);
  if (has(lesion.dwi.adcMin)) morphology.push(`ADCmin约${lesion.dwi.adcMin}×10⁻³ mm²/s`);
  const descriptorText = morphology.length ? `。${morphology.join("，")}` : "";
  return `${location || "乳腺内"}见一${type}（${lesion.id}）${sizeText}${descriptorText}。`;
}

function lesionConclusion(lesion) {
  if (!lesion.type || !lesion.laterality
    || ![lesion.size?.ap, lesion.size?.transverse, lesion.size?.cc].some(has)) {
    return `${lesion.id}：病灶结构待补充，暂不形成BI-RADS结论。`;
  }
  const birads = lesion.assessment?.birads;
  const assessment = has(birads) ? `BI-RADS ${birads}类` : "BI-RADS待医生确认";
  const pathology = String(birads) === "6" && lesion.pathology?.reference
    ? `，病理依据：${lesion.pathology.reference.replace(/[。；]+$/, "")}`
    : "";
  return `${lesion.id}：${labels.laterality[lesion.laterality]}乳${has(lesion.clock) ? `${lesion.clock}点方向` : ""}${lesion.type === "nme" ? "非肿块强化" : "肿块"}，${assessment}${pathology}。`;
}

function overallAssessmentText(lesions) {
  return ["left", "right"].map((side) => {
    const sideLesions = lesions.filter((lesion) => lesion.laterality === side);
    const sideName = `${labels.laterality[side]}乳`;
    if (!sideLesions.length) return `${sideName}BI-RADS 1类（正常基线）`;
    if (sideLesions.some((lesion) => !has(lesion.assessment?.birads))) {
      return `${sideName}总体评估待全部病灶确认`;
    }
    const overall = Math.max(...sideLesions.map((lesion) => Number(lesion.assessment.birads)));
    return `${sideName}总体BI-RADS ${overall}类`;
  }).join("；");
}

function lesionManagementText(lesion) {
  const action = labels.management[lesion.management?.action];
  if (!action) return `${lesion.id}：管理建议待医生确认。`;
  const detail = lesion.management?.detail
    ? `：${lesion.management.detail.replace(/[。；]+$/, "")}`
    : "";
  return `${lesion.id}：${action}${detail}。`;
}

export function renderReport(data) {
  const lesions = data.lesions?.length ? data.lesions : data.scenario === "lesion" ? [data.lesion] : [];
  const sequences = Array.isArray(data.acquisition?.sequences)
    ? data.acquisition.sequences.filter(Boolean).join("、")
    : String(data.acquisition?.sequences || "").trim();
  const basicItems = [
    { label: "检查项目", value: String(data.exam?.projectName || "").trim(), paths: ["exam.projectName"] },
    { label: "扫描序列", value: sequences, paths: ["acquisition.sequences"] },
    {
      label: "对比剂",
      value: contrastText(data.acquisition?.contrast),
      paths: ["acquisition.contrast.name", "acquisition.contrast.doseMl", "acquisition.contrast.rateMlPerSec"]
    },
    {
      label: "临床资料",
      value: String(data.exam?.clinicalHistory || data.exam?.indication || "").trim(),
      paths: ["exam.clinicalHistory", "exam.indication"]
    },
    {
      label: "既往对比",
      value: comparisonText(data.comparison),
      paths: ["comparison.status", "comparison.reference"]
    },
    {
      label: "图像质量",
      value: labels.quality[data.acquisition?.quality] || "",
      paths: ["acquisition.quality"]
    }
  ].filter((item) => has(item.value));
  const basicSection = basicItems.length ? {
    title: "检查摘要",
    text: basicItems.map((item) => `${item.label}：${item.value}`).join(" ｜ "),
    paths: basicItems.flatMap((item) => item.paths)
  } : null;
  const background = `双侧乳腺纤维腺体组织量（FGT）为${data.breastTissue.fgt || "待评价"}类；背景实质强化（BPE）为${labels.bpe[data.breastTissue.bpeLevel] || "待评价"}，呈${labels.symmetry[data.breastTissue.bpeSymmetry] || "待评价"}分布。`;
  const structures = `乳头、皮肤、胸肌及胸壁${labels.normality[data.associatedStructures.status] || "待评价"}${data.associatedStructures.detail ? `（${data.associatedStructures.detail}）` : ""}；区域淋巴结${labels.normality[data.lymphNodes.status] || "待评价"}${data.lymphNodes.detail ? `（${data.lymphNodes.detail}）` : ""}。`;
  const lesionSides = new Set(lesions.map((lesion) => lesion.laterality).filter(Boolean));
  const unaffectedBreasts = [
    !lesionSides.has("left") ? "左乳" : "",
    !lesionSides.has("right") ? "右乳" : ""
  ].filter(Boolean);
  const unaffectedText = unaffectedBreasts.length
    ? `${unaffectedBreasts.join("及")}未见可疑肿块或非肿块强化。`
    : "";
  const finding = data.scenario === "normal"
    ? `双侧乳腺未见可疑肿块或非肿块强化。${structures}`
    : `${lesions.map(lesionText).join("\n")}${unaffectedText}\n${structures}`;

  let conclusion;
  let management;
  if (data.scenario === "normal") {
    conclusion = `双侧乳腺MRI未见可疑异常。左乳BI-RADS ${data.assessment.leftBirads}类；右乳BI-RADS ${data.assessment.rightBirads}类。`;
    management = `${labels.management[data.management.action]}。`;
  } else {
    conclusion = `${lesions.map(lesionConclusion).join("\n")}\n总体评估：${overallAssessmentText(lesions)}。`;
    management = lesions.map(lesionManagementText).join("\n");
  }

  const sections = [
    ...(basicSection ? [basicSection] : []),
    { title: "报告所见", text: `${background}\n${finding}`, paths: ["breastTissue.fgt", "breastTissue.bpeLevel", "scenario", "lesion"] },
    { title: "诊断结论", text: conclusion, paths: data.scenario === "normal" ? ["assessment.leftBirads", "assessment.rightBirads"] : ["lesion"] },
    { title: "管理建议", text: management, paths: data.scenario === "normal" ? ["management.action", "management.detail"] : ["lesion"] }
  ];

  return {
    generatedAt: new Date().toISOString(),
    sections,
    toHtml() {
      return sections.map((section) => {
        const origin = sourceState(data, section.paths);
        return `
          <section>
            <div class="report-section-title">
              <h3>${section.title}</h3>
              <span class="report-origin origin-${origin.key}">${origin.label}</span>
            </div>
            ${section.text.split("\n").map((line) => `<p>${escapeHtml(line)}</p>`).join("")}
          </section>
        `;
      }).join("");
    }
  };
}
