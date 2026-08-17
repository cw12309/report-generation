const LEDGER_VERSION = 1;
const MAX_CANDIDATES = 240;
const MAX_EVENTS = 320;

const nowIso = () => new Date().toISOString();

const fallbackId = (prefix) => {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
};

export const createStableId = (prefix = "id") => {
  const uuid = globalThis.crypto?.randomUUID?.();
  return uuid ? `${prefix}-${uuid}` : fallbackId(prefix);
};

export const candidateReviewTier = ({ path = "", value, sourceType = "", requiresExplicitPhysicianConfirmation = false }) => {
  if (requiresExplicitPhysicianConfirmation
    || (path === "scenario" && value === "normal")
    || path.includes("assessment.birads")
    || path.includes("management.")
    || path.includes("pathology.")) return "clinical_decision";
  if (["deterministic_parser", "physician_click", "physician_direct", "deterministic_rule"].includes(sourceType)) {
    return "direct_evidence";
  }
  return "semantic_review";
};

const lesionRelativePath = (path) => path.replace(/^lesion\./, "");

export function recordInteractionEvent(data, type, detail = {}) {
  data.interactionEvents = Array.isArray(data.interactionEvents) ? data.interactionEvents : [];
  data.interactionEvents.push({
    id: createStableId("event"),
    type,
    at: nowIso(),
    ...detail
  });
  if (data.interactionEvents.length > MAX_EVENTS) {
    data.interactionEvents.splice(0, data.interactionEvents.length - MAX_EVENTS);
  }
}

function provenanceTarget(data, candidate) {
  if (candidate.scope !== "lesion") return { object: data.provenance, path: candidate.path };
  const lesion = data.lesions?.find((item) => item.uid === candidate.lesionUid);
  if (!lesion) return { object: null, path: "" };
  lesion.provenance = lesion.provenance || {};
  return { object: lesion.provenance, path: lesionRelativePath(candidate.path) };
}

export function pendingCandidates(data) {
  return Array.isArray(data.candidateLedger)
    ? data.candidateLedger.filter((candidate) => candidate.status === "pending")
    : [];
}

export function pendingCandidateCount(data) {
  const ledgerPending = pendingCandidates(data);
  if (data.candidateLedgerVersion === LEDGER_VERSION && ledgerPending.length) return ledgerPending.length;
  return Array.isArray(data.pendingPaths) ? data.pendingPaths.length : 0;
}

export function candidateForPath(data, path, lesionUid = "") {
  return pendingCandidates(data).find((candidate) => candidate.path === path
    && (path.startsWith("lesion.") ? candidate.lesionUid === lesionUid : candidate.scope === "case"));
}

export function provenanceForPath(data, path, lesionUid = "") {
  if (!path.startsWith("lesion.")) return data.provenance?.[path];
  const lesion = data.lesions?.find((item) => item.uid === lesionUid);
  return lesion?.provenance?.[lesionRelativePath(path)];
}

export function syncLegacyPendingPaths(data) {
  const activeUid = data.lesion?.uid || data.lesions?.[data.activeLesionIndex]?.uid || "";
  data.pendingPaths = pendingCandidates(data)
    .filter((candidate) => candidate.scope === "case" || candidate.lesionUid === activeUid)
    .map((candidate) => candidate.path);
  return data.pendingPaths;
}

export function ensureCandidateState(data) {
  data.candidateLedger = Array.isArray(data.candidateLedger) ? data.candidateLedger : [];
  data.interactionEvents = Array.isArray(data.interactionEvents) ? data.interactionEvents : [];
  if (data.candidateLedgerVersion !== LEDGER_VERSION) {
    const legacyPaths = Array.isArray(data.pendingPaths) ? [...new Set(data.pendingPaths)] : [];
    const activeUid = data.lesion?.uid || data.lesions?.[data.activeLesionIndex]?.uid || "";
    for (const path of legacyPaths) {
      const lesionScoped = path.startsWith("lesion.");
      const source = lesionScoped
        ? provenanceForPath(data, path, activeUid) || data.provenance?.[path]
        : data.provenance?.[path];
      data.candidateLedger.push({
        id: createStableId("candidate"),
        scope: lesionScoped ? "lesion" : "case",
        lesionUid: lesionScoped ? activeUid : "",
        path,
        value: null,
        sourceText: source?.sourceText || "V10候选状态迁移",
        sourceType: source?.sourceType || "legacy_candidate",
        confidence: Number(source?.confidence) || 0,
        reviewTier: candidateReviewTier({ path, value: null, sourceType: source?.sourceType }),
        status: "pending",
        createdAt: nowIso(),
        updatedAt: nowIso(),
        turnId: "migration"
      });
    }
    data.candidateLedgerVersion = LEDGER_VERSION;
  }
  syncLegacyPendingPaths(data);
  return data;
}

export function upsertCandidate(data, candidate) {
  ensureCandidateState(data);
  const lesionScoped = candidate.path.startsWith("lesion.");
  const lesionUid = lesionScoped ? candidate.lesionUid || data.lesion?.uid || "" : "";
  const existing = candidateForPath(data, candidate.path, lesionUid);
  const timestamp = nowIso();
  const sourceType = candidate.sourceType || "llm_semantic";
  const reviewTier = candidate.reviewTier || candidateReviewTier({
    path: candidate.path,
    value: candidate.value,
    sourceType,
    requiresExplicitPhysicianConfirmation: candidate.requiresExplicitPhysicianConfirmation
  });
  const next = {
    id: existing?.id || createStableId("candidate"),
    scope: lesionScoped ? "lesion" : "case",
    lesionUid,
    path: candidate.path,
    value: candidate.value,
    previousValue: candidate.previousValue,
    sourceText: String(candidate.sourceText || ""),
    sourceType,
    confidence: Math.max(0, Math.min(1, Number(candidate.confidence) || 0)),
    reviewTier,
    evidenceStrength: reviewTier === "direct_evidence" ? "原话直接证据"
      : reviewTier === "clinical_decision" ? "关键临床决策" : "语义提取待复核",
    status: "pending",
    createdAt: existing?.createdAt || timestamp,
    updatedAt: timestamp,
    turnId: candidate.turnId || ""
  };
  if (existing) Object.assign(existing, next);
  else data.candidateLedger.push(next);

  const target = provenanceTarget(data, next);
  if (target.object) {
    target.object[target.path] = {
      sourceType,
      sourceText: next.sourceText,
      confirmation: "pending",
      confidence: next.confidence,
      reviewTier,
      candidateId: next.id,
      model: candidate.model || ""
    };
  }
  recordInteractionEvent(data, existing ? "candidate_revised" : "candidate_created", {
    candidateId: next.id,
    path: next.path,
    lesionUid: next.lesionUid,
    turnId: next.turnId,
    sourceType,
    reviewTier
  });
  if (data.candidateLedger.length > MAX_CANDIDATES) {
    const removable = data.candidateLedger.findIndex((item) => item.status !== "pending");
    if (removable >= 0) data.candidateLedger.splice(removable, 1);
  }
  syncLegacyPendingPaths(data);
  return next;
}

export function confirmCandidateIds(data, ids, reason = "node_confirmation") {
  ensureCandidateState(data);
  const idSet = new Set(ids);
  let count = 0;
  for (const candidate of data.candidateLedger) {
    if (candidate.status !== "pending" || !idSet.has(candidate.id)) continue;
    candidate.status = "confirmed";
    candidate.confirmedAt = nowIso();
    const target = provenanceTarget(data, candidate);
    if (target.object?.[target.path]) {
      target.object[target.path].confirmation = "physician_confirmed";
      target.object[target.path].confirmedAt = candidate.confirmedAt;
    }
    recordInteractionEvent(data, "candidate_confirmed", {
      candidateId: candidate.id,
      path: candidate.path,
      lesionUid: candidate.lesionUid,
      reason
    });
    count += 1;
  }
  syncLegacyPendingPaths(data);
  return count;
}

export function discardCandidates(data, predicate, reason = "candidate_cleared") {
  ensureCandidateState(data);
  let count = 0;
  for (const candidate of data.candidateLedger) {
    if (candidate.status !== "pending" || !predicate(candidate)) continue;
    candidate.status = "discarded";
    candidate.discardedAt = nowIso();
    recordInteractionEvent(data, "candidate_discarded", {
      candidateId: candidate.id,
      path: candidate.path,
      lesionUid: candidate.lesionUid,
      reason
    });
    count += 1;
  }
  syncLegacyPendingPaths(data);
  return count;
}

export function directEvidenceCandidates(data) {
  return pendingCandidates(data).filter((candidate) => candidate.reviewTier === "direct_evidence");
}

export function criticalReviewCandidates(data) {
  return pendingCandidates(data).filter((candidate) => candidate.reviewTier !== "direct_evidence");
}
