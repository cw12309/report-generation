import { createStableId } from "./candidateLedger.js";

const DEFAULT_PROVENANCE_PATHS = [
  "scenario", "associatedStructures.status", "lymphNodes.status",
  "assessment.leftBirads", "assessment.rightBirads", "management.action"
];

const PENDING_SOURCE_PATHS = [
  "exam.projectName", "exam.indication", "exam.clinicalHistory",
  "acquisition.sequences", "acquisition.contrast.name", "acquisition.contrast.doseMl",
  "acquisition.contrast.rateMlPerSec", "acquisition.quality",
  "comparison.status", "comparison.reference",
  "breastTissue.fgt", "breastTissue.bpeLevel", "breastTissue.bpeSymmetry"
];

export const createLesion = (id = "L1", uid = createStableId("lesion")) => ({
  id,
  uid,
  type: "",
  laterality: "",
  clock: "",
  distanceCm: "",
  depth: "",
  size: { ap: "", transverse: "", cc: "" },
  shape: "",
  margin: "",
  distribution: "",
  internalEnhancement: "",
  t2: "",
  kinetics: { early: "", delayed: "" },
  dwi: { signal: "", adcMin: "", bValue: "", roiMethod: "" },
  assessment: { birads: "" },
  management: { action: "", detail: "" },
  pathology: { reference: "" },
  provenance: {}
});

export const normalizeLesion = (lesion = {}, id = lesion.id || "L1") => {
  const empty = createLesion(id, lesion.uid || createStableId("lesion"));
  return {
    ...empty,
    ...lesion,
    size: { ...empty.size, ...(lesion.size || {}) },
    kinetics: { ...empty.kinetics, ...(lesion.kinetics || {}) },
    dwi: { ...empty.dwi, ...(lesion.dwi || {}) },
    assessment: { ...empty.assessment, ...(lesion.assessment || {}) },
    management: { ...empty.management, ...(lesion.management || {}) },
    pathology: { ...empty.pathology, ...(lesion.pathology || {}) },
    provenance: { ...empty.provenance, ...(lesion.provenance || {}) }
  };
};

export const createEmptyCase = () => {
  const provenance = Object.fromEntries(DEFAULT_PROVENANCE_PATHS.map((path) => [path, {
    sourceType: "institutional_normal_default",
    sourceText: "机构正常报告基线 v1.0",
    confirmation: "default_pending_final_signoff"
  }]));
  for (const path of PENDING_SOURCE_PATHS) {
    provenance[path] = {
      sourceType: "not_provided",
      sourceText: "",
      confirmation: "unconfirmed"
    };
  }
  const lesion = createLesion();
  return ({
  templateId: "breast_mri_birads_2025",
  templateVersion: "1.4.0-accuracy-performance",
  baselineVersion: "institution_normal_v1",
  status: "NORMAL_BASELINE",
  scenario: "normal",
  exam: { projectName: "", indication: "", clinicalHistory: "" },
  acquisition: {
    sequences: [],
    contrast: { name: "", doseMl: "", rateMlPerSec: "" },
    quality: ""
  },
  comparison: { status: "", reference: "" },
  breastTissue: { fgt: "", bpeLevel: "", bpeSymmetry: "" },
  associatedStructures: { status: "normal", detail: "" },
  lymphNodes: { status: "normal", detail: "" },
  lesion,
  lesions: [],
  activeLesionIndex: 0,
  assessment: { leftBirads: "1", rightBirads: "1" },
  management: { action: "routine", detail: "" },
  pendingPaths: [],
  candidateLedgerVersion: 1,
  candidateLedger: [],
  interactionEvents: [],
  provenance,
  agent: {
    messages: [],
    conflicts: [],
    lastModel: "",
    lastUsage: null,
    lastTiming: null,
    currentTurn: null
  },
  draft: null,
  signedSnapshot: null,
  signedAt: null
  });
};

export const getValue = (object, path) =>
  path.split(".").reduce((value, key) => value?.[key], object);

export const setValue = (object, path, value) => {
  const keys = path.split(".");
  let cursor = object;
  for (const key of keys.slice(0, -1)) {
    if (!cursor[key] || typeof cursor[key] !== "object") cursor[key] = {};
    cursor = cursor[key];
  }
  cursor[keys.at(-1)] = value;
};

export const hasValue = (value) =>
  value !== "" && value !== null && value !== undefined;

export const isPending = (data, path) => data.pendingPaths.includes(path);

export const markConfirmed = (data, path) => {
  data.pendingPaths = data.pendingPaths.filter((candidate) => candidate !== path);
  if (data.provenance[path]) {
    data.provenance[path].confirmation = "physician_confirmed";
    data.provenance[path].confirmedAt = new Date().toISOString();
  }
};
