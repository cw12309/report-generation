import { hasValue } from "./model.js";
import { pendingCandidateCount } from "./candidateLedger.js";

const issue = (path, message, level = "blocking", lesionIndex = null) =>
  ({ path, message, level, lesionIndex });

export function validateCase(data) {
  const issues = [];

  const pendingCount = pendingCandidateCount(data);
  if (pendingCount) {
    issues.push(issue("pendingPaths", `请由医生确认本轮产生的 ${pendingCount} 个异常候选`));
  }
  if (data.agent?.conflicts?.length) {
    issues.push(issue("conflicts", `存在 ${data.agent.conflicts.length} 个未处理冲突`));
  }

  const missingBackground = [
    data.breastTissue?.fgt,
    data.breastTissue?.bpeLevel,
    data.breastTissue?.bpeSymmetry
  ].some((value) => !hasValue(value));
  if (missingBackground) {
    issues.push(issue(
      "breastTissue",
      "请一次确认乳腺背景（FGT、BPE程度和对称性）"
    ));
  }

  if (["abnormal", "limited"].includes(data.associatedStructures.status)
    && !hasValue(data.associatedStructures.detail)) {
    issues.push(issue("associatedStructures.detail", "请说明相关结构异常或评价受限原因"));
  }
  if (["abnormal", "limited"].includes(data.lymphNodes.status)
    && !hasValue(data.lymphNodes.detail)) {
    issues.push(issue("lymphNodes.detail", "请说明淋巴结异常或评价受限表现"));
  }

  if (data.scenario === "lesion") {
    const lesions = data.lesions?.length ? data.lesions : [data.lesion];
    lesions.forEach((lesion, lesionIndex) => {
      const id = lesion.id || `L${lesionIndex + 1}`;
      if (!hasValue(lesion.type)) {
        issues.push(issue("lesion.type", `${id}：请确认病灶类型`, "blocking", lesionIndex));
      }
      if (!hasValue(lesion.laterality)) {
        issues.push(issue("lesion.laterality", `${id}：请确认病灶侧别`, "blocking", lesionIndex));
      }
      const hasAnySize = [lesion.size?.ap, lesion.size?.transverse, lesion.size?.cc].some(hasValue);
      if (!hasAnySize) {
        issues.push(issue("lesion.size.ap", `${id}：请至少提供一个可用径线`, "blocking", lesionIndex));
      }
      const birads = String(lesion.assessment?.birads || "");
      if (!hasValue(birads)) {
        issues.push(issue("lesion.assessment.birads", `${id}：请医生确认该病灶BI-RADS类别`, "blocking", lesionIndex));
      } else {
        const expectedManagement = ({
          "0": "additionalImaging",
          "1": "routine",
          "2": "routine",
          "3": "shortInterval",
          "4": "tissueDiagnosis",
          "5": "tissueDiagnosis",
          "6": "surgicalOncology"
        })[birads];
        if (lesion.management?.action !== expectedManagement) {
          issues.push(issue(
            "lesion.management.action",
            `${id}：请确认与BI-RADS ${birads}类对应的管理建议`,
            "blocking",
            lesionIndex
          ));
        }
        if (["4", "5"].includes(birads) && !hasValue(lesion.management?.detail)) {
          issues.push(issue(
            "lesion.management.detail",
            `${id}：请明确组织学诊断的取样方式或后续路径`,
            "blocking",
            lesionIndex
          ));
        }
        if (birads === "6" && !hasValue(lesion.pathology?.reference)) {
          issues.push(issue(
            "lesion.pathology.reference",
            `${id}：BI-RADS 6类必须核验病理依据和日期`,
            "blocking",
            lesionIndex
          ));
        }
      }
      if (hasValue(lesion.dwi?.adcMin)) {
        if (!hasValue(lesion.dwi.bValue)) {
          issues.push(issue("lesion.dwi.bValue", `${id}：ADC已记录，b值未提供；不阻断报告`, "warning", lesionIndex));
        }
        if (!hasValue(lesion.dwi.roiMethod)) {
          issues.push(issue("lesion.dwi.roiMethod", `${id}：ADC已记录，ROI方法未提供；不阻断报告`, "warning", lesionIndex));
        }
      }
    });
  }

  if (data.scenario === "normal") {
    const categories = [data.assessment.leftBirads, data.assessment.rightBirads].map(Number);
    const requiresTissue = categories.some((value) => value === 4 || value === 5);
    if (requiresTissue && data.management.action !== "tissueDiagnosis") {
      issues.push(issue("management.action", "BI-RADS 4或5类需要明确组织学诊断建议"));
    }
    if (requiresTissue && data.management.action === "tissueDiagnosis"
      && !hasValue(data.management.detail)) {
      issues.push(issue("management.detail", "请明确组织学诊断的取样方式或后续路径"));
    }
  }
  return {
    blocking: issues.filter((item) => item.level === "blocking"),
    warnings: issues.filter((item) => item.level === "warning"),
    all: issues
  };
}
