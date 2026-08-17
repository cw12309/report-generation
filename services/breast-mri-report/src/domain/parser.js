const rules = [
  ["exam.indication", /检查目的\s*(?:为|：)\s*([^，。；]+)/, (m) => m[1].trim()],
  ["acquisition.quality", /图像质量\s*(?:为|：)?\s*(满意|基本满意|受限)/, (m) => ({ 满意: "satisfactory", 基本满意: "acceptable", 受限: "limited" })[m[1]]],
  ["comparison.status", /(无可供比较的既往资料|无可比较的既往资料|无既往资料)/, () => "none"],
  ["breastTissue.fgt", /FGT\s*(?:为|：)?\s*([ABCD])\s*类?/i, (m) => m[1].toUpperCase()],
  ["breastTissue.bpeLevel", /BPE\s*(极少|轻度|中度|显著)/i, (m) => ({ 极少: "minimal", 轻度: "mild", 中度: "moderate", 显著: "marked" })[m[1]]],
  ["breastTissue.bpeSymmetry", /BPE[^。；，]*(不对称|对称)/i, (m) => m[1] === "对称" ? "symmetric" : "asymmetric"],
  ["lesion.type", /(非肿块强化|NME|肿块)/i, (m) => /非肿块|NME/i.test(m[1]) ? "nme" : "mass"],
  ["lesion.laterality", /(左乳|右乳|左侧乳腺|右侧乳腺)/, (m) => m[1].startsWith("左") ? "left" : "right"],
  ["lesion.clock", /(\d{1,2})\s*点(?:钟)?/, (m) => Number(m[1])],
  ["lesion.distanceCm", /距乳头\s*(?:约)?\s*(\d+(?:\.\d+)?)\s*(?:厘米|cm)/i, (m) => Number(m[1])],
  ["lesion.depth", /(前|中|后)\s*1\s*\/\s*3/, (m) => ({ 前: "anterior", 中: "middle", 后: "posterior" })[m[1]]],
  ["lesion.shape", /(不规则|椭圆|卵圆|分叶|(?<![椭卵])圆形)(?:形)?(?:肿块)?/, (m) => ({
    不规则: "irregular", 椭圆: "oval", 卵圆: "oval", 分叶: "lobulated", 圆形: "round"
  })[m[1]]],
  ["lesion.margin", /(毛刺|边缘模糊|边缘清楚)/, (m) => ({ 毛刺: "spiculated", 边缘模糊: "indistinct", 边缘清楚: "circumscribed" })[m[1]]],
  ["lesion.distribution", /(节段性|线样|区域性|局灶性|弥漫性)(?:分布)?/, (m) => ({ 节段性: "segmental", 线样: "linear", 区域性: "regional", 局灶性: "focal", 弥漫性: "diffuse" })[m[1]]],
  ["lesion.internalEnhancement", /(不均匀强化|均匀强化|厚环形强化|内部无强化分隔|簇集状|簇状环形)/, (m) => ({
    不均匀强化: "heterogeneous", 均匀强化: "homogeneous", 厚环形强化: "rim",
    内部无强化分隔: "darkSeptations", 簇集状: "clumped", 簇状环形: "clusteredRing"
  })[m[1]]],
  ["lesion.t2", /T2\s*(非高信号|不是高信号|高信号)/i, (m) => m[1] === "高信号" ? "high" : "notHigh"],
  ["lesion.kinetics.early", /早期\s*(快速|中速|慢速)/, (m) => ({ 快速: "fast", 中速: "medium", 慢速: "slow" })[m[1]]],
  ["lesion.kinetics.delayed", /延迟(?:期)?\s*(廓清|平台|持续)/, (m) => ({ 廓清: "washout", 平台: "plateau", 持续: "persistent" })[m[1]]],
  ["lesion.dwi.signal", /DWI\s*(高信号|等信号|低信号)/i, (m) => ({ 高信号: "high", 等信号: "equal", 低信号: "low" })[m[1]]],
  ["lesion.dwi.adcMin", /ADC(?:min)?\s*(?:为|=|：)?\s*(\d+(?:\.\d+)?)/i, (m) => Number(m[1])],
  ["lesion.dwi.bValue", /b\s*值\s*(?:为|=|：)?\s*(\d+)/i, (m) => Number(m[1])],
  ["lesion.dwi.roiMethod", /ROI\s*(?:方法)?\s*(?:为|=|：)\s*([^，。；]+)/i, (m) => m[1].trim()],
  ["lesion.assessment.birads", /(?:M\d+|N\d+|该病灶|此病灶|左乳|右乳)[^。；]{0,18}BI-?RADS\s*([0-6])/i, (m) => m[1]],
  ["lesion.management.action", /(?:建议)?(?:行|进行)?(?:超声|MRI|磁共振|影像(?:学)?)?(?:引导下?)?(?:穿刺|活检|组织学诊断)/i, () => "tissueDiagnosis"],
  ["lesion.management.detail", /((?:超声|MRI|磁共振|影像(?:学)?)?(?:引导下?)?(?:穿刺|活检)[^，。；]*)/i, (m) => m[1].trim()],
  ["lesion.pathology.reference", /((?:病理|组织学)(?:结果|依据|证实|诊断|日期)[^，。；]*)/i, (m) => m[1].trim()]
];

const detectSize = (text) => {
  const match = text.match(/(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*(?:毫米|mm)/i);
  if (!match) return [];
  return [
    ["lesion.size.ap", Number(match[1])],
    ["lesion.size.transverse", Number(match[2])],
    ["lesion.size.cc", Number(match[3])]
  ];
};

export function extractFacts(text) {
  const updates = [];
  const clauses = String(text || "").split(/[。；;\n]/).map((item) => item.trim()).filter(Boolean);
  const negativeLesionClause = (clause) => /(?:未见|无)(?:明显|可疑|明确)?[^。；，]{0,12}(?:肿块|病灶|异常强化|非肿块强化|NME)/i.test(clause);
  for (const [path, pattern, transform] of rules) {
    const source = path.startsWith("lesion.")
      ? clauses.find((clause) => !negativeLesionClause(clause) && pattern.test(clause))
      : clauses.find((clause) => pattern.test(clause));
    const match = source?.match(pattern);
    if (match) updates.push({ path, value: transform(match), sourceText: match[0] });
  }
  const sizeSource = clauses.find((clause) => !negativeLesionClause(clause) && detectSize(clause).length);
  if (sizeSource) {
    updates.push(...detectSize(sizeSource).map(([path, value]) => ({
      path,
      value,
      sourceText: sizeSource.match(/(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*(?:毫米|mm)/i)?.[0]
    })));
  }

  const normalFinding = /(?:双乳|双侧乳腺).{0,16}(?:未见可疑|无可疑)(?:肿块|病灶|异常|强化)/.test(text);
  const leftNormal = /左乳.{0,12}(?:未见可疑|无可疑)/.test(text);
  const structuresNormal = /(?:乳头|皮肤|胸肌|胸壁).{0,24}(?:未见|无)(?:明确)?受累/.test(text);
  const nodesNormal = /(?:双侧)?(?:腋窝|淋巴结).{0,18}(?:未见|无)(?:明显)?异常/.test(text);

  const safeUpdates = normalFinding
    ? updates.filter((update) => !update.path.startsWith("lesion."))
    : updates;
  const hasLesionFact = safeUpdates.some((update) => update.path.startsWith("lesion."));
  return {
    scenario: normalFinding ? "normal" : hasLesionFact ? "lesion" : null,
    updates: safeUpdates,
    flags: { leftNormal, structuresNormal, nodesNormal }
  };
}

export function buildDeterministicPreview(text) {
  const extracted = extractFacts(text);
  const updates = extracted.updates.map((update) => ({
    ...update,
    confidence: 1,
    sourceType: "deterministic_parser",
    requiresExplicitPhysicianConfirmation: false
  }));
  if (extracted.scenario) {
    updates.unshift({
      path: "scenario",
      value: extracted.scenario,
      sourceText: String(text || ""),
      confidence: 1,
      sourceType: "deterministic_parser",
      requiresExplicitPhysicianConfirmation: extracted.scenario === "normal"
    });
  }
  return updates;
}
