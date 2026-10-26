console.log("[APP] app.js v62 已加载, 时间:", new Date().toISOString());

// 会话管理 - 每个对话独立的 session_id
let SESSION_ID = generateSessionId();

function generateSessionId() {
  // crypto.randomUUID() 仅在 HTTPS 或 localhost 下可用
  if (crypto && crypto.randomUUID) {
    return "web_" + crypto.randomUUID();
  }
  // 降级方案：使用 Math.random 生成 UUID
  return (
    "web_" +
    Date.now().toString(36) +
    "_" +
    Math.random().toString(36).substr(2, 9) +
    Math.random().toString(36).substr(2, 9)
  );
}

// DOM 元素
const chatContainer = document.getElementById("chatContainer");
const userInput = document.getElementById("userInput");
const btnSend = document.getElementById("btnSend");
const btnClear = document.getElementById("btnClear");
const btnMemory = document.getElementById("btnMemory");
const btnMemoryClose = document.getElementById("btnMemoryClose");
const memoryPanel = document.getElementById("memoryPanel");
const memoryPanelBody = document.getElementById("memoryPanelBody");
const btnBuildIncremental = document.getElementById("btnBuildIncremental");
const btnBuildRebuild = document.getElementById("btnBuildRebuild");
const btnExtractMeta = document.getElementById("btnExtractMeta");
const btnSidebarUpload = document.getElementById("btnSidebarUpload");
const xlsxInput = document.getElementById("xlsxInput");
const kbLog = document.getElementById("kbLog");
const kbLogContent = document.getElementById("kbLogContent");
const manageKbList = document.getElementById("manageKbList");
const kbDetailArea = document.getElementById("kbDetailArea");
const detailKbName = document.getElementById("detailKbName");
const detailKbCount = document.getElementById("detailKbCount");
const kbChunkList = document.getElementById("kbChunkList");
const agentKbSelectorList = document.getElementById("agentKbSelectorList");
const btnOpenKbSelector = document.getElementById("btnOpenKbSelector");
const kbSelectedCount = document.getElementById("kbSelectedCount");
const agentKbModal = document.getElementById("agentKbModal");
const btnCloseKbSelector = document.getElementById("btnCloseKbSelector");
const btnConfirmKbSelector = document.getElementById("btnConfirmKbSelector");
const agentKbModalOverlay = agentKbModal?.querySelector(".agent-modal-overlay");
const statusText = document.getElementById("statusText");
const btnRunEval = document.getElementById("btnRunEval");
const btnClearEvalDataset = document.getElementById("btn-clear-dataset");
const evalStatus = document.getElementById("evalStatus");
const evalProgressText = document.getElementById("evalProgressText");
const evalProgressPercent = document.getElementById("evalProgressPercent");
const evalProgressTrack = document.getElementById("evalProgressTrack");
const evalProgressFill = document.getElementById("evalProgressFill");
const btnCancelEval = document.getElementById("btn-cancel-eval");
const evalHit1 = document.getElementById("evalHit1");
const evalHit3 = document.getElementById("evalHit3");
const evalHit5 = document.getElementById("evalHit5");
const evalHit1Count = document.getElementById("evalHit1Count");
const evalHit3Count = document.getElementById("evalHit3Count");
const evalHit5Count = document.getElementById("evalHit5Count");
const evalRunMeta = document.getElementById("evalRunMeta");
const evalDetailsBody = document.getElementById("evalDetailsBody");
const btnAddEvalCase = document.getElementById("btnAddEvalCase");
const evalCaseModal = document.getElementById("evalCaseModal");
const evalCaseForm = document.getElementById("evalCaseForm");
const evalCaseQuery = document.getElementById("evalCaseQuery");
const chunkAutocomplete = document.getElementById("chunk-autocomplete");
const inputChunkSearch = document.getElementById("input-chunk-search");
const selectedChunkIdInput = document.getElementById("selected-chunk-id");
const chunkSearchResults = document.getElementById("chunk-search-results");
const chunkFilterControls = [
  {
    param: "kb_name",
    optionsKey: "kb_names",
    element: document.getElementById("filterKbName"),
  },
  {
    param: "exam_type",
    optionsKey: "exam_types",
    element: document.getElementById("filterExamType"),
  },
  {
    param: "level1",
    optionsKey: "level1",
    element: document.getElementById("filterLevel1"),
  },
  {
    param: "level2",
    optionsKey: "level2",
    element: document.getElementById("filterLevel2"),
  },
  {
    param: "level3",
    optionsKey: "level3",
    element: document.getElementById("filterLevel3"),
  },
  {
    param: "level4",
    optionsKey: "level4",
    element: document.getElementById("filterLevel4"),
  },
];
const evalCaseError = document.getElementById("evalCaseError");
const btnCloseEvalCase = document.getElementById("btnCloseEvalCase");
const btnCancelEvalCase = document.getElementById("btnCancelEvalCase");
const btnSaveEvalCase = document.getElementById("btnSaveEvalCase");
const btnAutoGenerateCases = document.getElementById("btnAutoGenerateCases");
const evalAutoGenerateModal = document.getElementById("evalAutoGenerateModal");
const evalAutoGenerateForm = document.getElementById("evalAutoGenerateForm");
const evalAutoGenerateCount = document.getElementById("evalAutoGenerateCount");
const evalAutoGenerateMinLength = document.getElementById(
  "evalAutoGenerateMinLength",
);
const evalAutoProgressText = document.getElementById("evalAutoProgressText");
const evalAutoProgressPercent = document.getElementById(
  "evalAutoProgressPercent",
);
const evalAutoProgressBar = document.getElementById("evalAutoProgressBar");
const evalAutoProgressTrack = evalAutoGenerateModal?.querySelector(
  ".eval-auto-progress-track",
);
const evalAutoGenerateLog = document.getElementById("evalAutoGenerateLog");
const evalAutoGenerateError = document.getElementById("evalAutoGenerateError");
const btnCloseEvalAutoGenerate = document.getElementById(
  "btnCloseEvalAutoGenerate",
);
const btnCancelEvalAutoGenerate = document.getElementById(
  "btnCancelEvalAutoGenerate",
);
const btnStartEvalAutoGenerate = document.getElementById(
  "btnStartEvalAutoGenerate",
);
const appToast = document.getElementById("appToast");

// 状态
let isProcessing = false;
let currentReader = null; // 当前 SSE 读取器
let chatAbortController = null; // 当前请求的 AbortController
let knowledgeBases = [];
const SELECTED_KBS_STORAGE_KEY = "selectedKnowledgeBaseIds";

function loadSelectedKnowledgeBaseIds() {
  try {
    const stored = JSON.parse(
      localStorage.getItem(SELECTED_KBS_STORAGE_KEY) || "[]",
    );
    return new Set(
      Array.isArray(stored)
        ? stored.filter((id) => typeof id === "string" && id)
        : [],
    );
  } catch (error) {
    console.warn("[KB] 恢复知识库选择失败，将使用空选择", error);
    return new Set();
  }
}

function persistSelectedKnowledgeBaseIds() {
  try {
    localStorage.setItem(
      SELECTED_KBS_STORAGE_KEY,
      JSON.stringify([...selectedKnowledgeBaseIds]),
    );
  } catch (error) {
    console.warn("[KB] 保存知识库选择失败", error);
  }
}

let selectedKnowledgeBaseIds = loadSelectedKnowledgeBaseIds();
let selectedManageKbId = null;
let kbPollTimer = null;
let kbChunkAbortController = null;
let isSavingEvalCase = false;
let isAutoGeneratingCases = false;
let evalAutoGenerateAbortController = null;
let toastTimer = null;
let evalProgressHideTimer = null;
let evalCancelResetTimer = null;
let evalRunAbortController = null;
let isEvalCancelRequested = false;
let chunkSearchDebounceTimer = null;
let chunkSearchAbortController = null;
let chunkSearchRequestId = 0;
let isChunkSearchOpen = false;
let chunkFilterAbortController = null;
let chunkFilterRequestId = 0;
let evalStatusPollTimer = null;
let evalStatusRequestId = 0;

// API 基础地址
const API_BASE = "/api";

// 发送按钮
btnSend.addEventListener("click", () => sendMessage());
userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// 清空按钮
btnClear.addEventListener("click", async () => {
  if (isProcessing) {
    // 强制取消当前请求
    cancelCurrentRequest();
    updateStatus("⚠️ 已中断当前请求，请稍后重试清空");
    return;
  }
  await doClearSession();
});

// 强制执行清空
async function doClearSession() {
  try {
    await fetch(`${API_BASE}/clear`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: SESSION_ID }),
    });
  } catch (e) {
    /* ignore */
  }
  chatContainer.innerHTML = "";
  chatContainer.appendChild(createEmptyState());
  updateStatus("✅ 会话已清空");
  addSystemMessage("🗑️ 会话已清空，记忆已重置");
}

// 短期记忆按钮
btnMemory.addEventListener("click", async () => {
  await loadMemoryPanel();
});

// 关闭短期记忆面板
btnMemoryClose.addEventListener("click", () => {
  closeMemoryPanel();
});

function closeMemoryPanel() {
  memoryPanel.style.display = "none";
  const overlay = document.querySelector(".memory-overlay");
  if (overlay) overlay.remove();
}

async function loadMemoryPanel() {
  memoryPanel.style.display = "block";

  let overlay = document.querySelector(".memory-overlay");
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "memory-overlay";
    overlay.addEventListener("click", closeMemoryPanel);
    document.body.appendChild(overlay);
  }

  memoryPanelBody.innerHTML = '<div class="memory-loading">加载中...</div>';

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);

  try {
    const resp = await fetch(
      `${API_BASE}/memory?session_id=${SESSION_ID}&_t=${Date.now()}`,
      {
        method: "GET",
        keepalive: false,
        cache: "no-store",
        signal: controller.signal,
      },
    );
    clearTimeout(timeout);

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const data = await resp.json();
    console.log("[短期记忆] 获取成功:", data);
    renderMemoryPanel(data);
  } catch (e) {
    clearTimeout(timeout);
    console.error("[短期记忆] 获取失败:", e);
    memoryPanelBody.innerHTML = `<div class="memory-error">⚠️ 获取短期记忆失败: ${escapeHtml(e.message)}</div>`;
  }
}

function renderMemoryPanel(data) {
  const turns = data.turns || [];
  const entities = data.entities || {};
  const summaries = data.summaries || [];
  const currentTurns = data.current_turns || 0;
  const totalTurns = data.total_turns || 0;
  const maxRounds = data.max_rounds || 5;

  let html = "";

  html += `<div class="memory-stats">
    <span class="memory-stat">当前轮数: <strong>${currentTurns}</strong> / ${maxRounds}</span>
    <span class="memory-stat">累计轮数: <strong>${totalTurns}</strong></span>
    <span class="memory-stat">实体数: <strong>${Object.keys(entities).length}</strong></span>
    <span class="memory-stat">摘要数: <strong>${summaries.length}</strong></span>
  </div>`;

  if (turns.length > 0) {
    html += '<div class="memory-section"><h4>对话历史</h4>';
    turns.forEach((turn) => {
      html += `
        <div class="memory-turn">
          <div class="memory-turn-header">第 ${turn.round} 轮</div>
          <div class="memory-turn-user"><span class="memory-role-label">👤 用户:</span> ${escapeHtml(turn.user)}</div>
          <div class="memory-turn-assistant"><span class="memory-role-label">🤖 助手:</span> ${escapeHtml(turn.assistant)}</div>
        </div>
      `;
    });
    html += "</div>";
  } else {
    html += '<div class="memory-empty">暂无对话历史</div>';
  }

  if (Object.keys(entities).length > 0) {
    html +=
      '<div class="memory-section"><h4>实体追踪</h4><div class="memory-entities">';
    for (const [key, val] of Object.entries(entities)) {
      const displayVal = Array.isArray(val) ? val.join(", ") : val;
      html += `<div class="memory-entity"><span class="memory-entity-key">${escapeHtml(key)}:</span> ${escapeHtml(String(displayVal))}</div>`;
    }
    html += "</div></div>";
  }

  if (summaries.length > 0) {
    html += '<div class="memory-section"><h4>历史摘要（淘汰轮次压缩）</h4>';
    summaries.forEach((s, i) => {
      html += `<div class="memory-summary">${i + 1}. ${escapeHtml(s)}</div>`;
    });
    html += "</div>";
  }

  memoryPanelBody.innerHTML = html;
}

// ==================== 页面切换 ====================

const sidebarBtns = document.querySelectorAll(".sidebar-btn");
const pages = document.querySelectorAll(".page");

sidebarBtns.forEach((btn) => {
  btn.addEventListener("click", () => {
    // 关闭弹窗
    const panel = document.getElementById("llmConfigPanel");
    if (panel) panel.style.display = "none";

    const page = btn.dataset.page;
    if (page !== "deployments") {
      closeApiDeploymentCreate();
      closeApiDeploymentModal();
      closeApiDeploymentDetail();
      closeDeploymentSecret();
      closeApiDeploymentGuide();
    }
    sidebarBtns.forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    pages.forEach((p) => p.classList.remove("active"));
    const pageMap = {
      workspace: "pageWorkspace",
      kb: "pageKB",
      deployments: "pageDeployments",
      eval: "pageEval",
      config: "pageConfig",
      monitor: "pageMonitor",
    };
    const target = document.getElementById(pageMap[page] || "pageWorkspace");
    if (target) target.classList.add("active");
    if (page === "kb") {
      refreshKnowledgeBases();
    } else if (page === "workspace") {
      if (workspaceActiveTab === "agent") {
        refreshKnowledgeBases();
      }
      switchWorkspaceTab(workspaceActiveTab);
    } else if (page === "eval") {
      restoreEvaluationState();
    } else if (page === "deployments") {
      loadApiDeployments();
    }
    if (page === "config") {
      loadConfig();
    }
    if (page === "monitor") {
      activateMonitorTab(monitorActiveTab);
    } else {
      stopMonitorLogStream();
    }
  });
});

// ==================== 工作台内部 Tab 切换 ====================

let workspaceActiveTab = "report";

function switchWorkspaceTab(tab) {
  workspaceActiveTab = tab;

  const agentView = document.getElementById("workspaceAgent");
  const mriView = document.getElementById("workspaceBreastMRI");
  const reportView = document.getElementById("workspaceReport");

  const tabs = document.querySelectorAll(".workspace-tab");
  tabs.forEach((t) => {
    t.classList.toggle("active", t.dataset.workspaceTab === tab);
  });

  if (tab === "agent") {
    if (agentView) agentView.style.display = "";
    if (mriView) mriView.style.display = "none";
    if (reportView) reportView.style.display = "none";
  } else if (tab === "breast-mri") {
    if (agentView) agentView.style.display = "none";
    if (mriView) mriView.style.display = "";
    if (reportView) reportView.style.display = "none";
    if (!breastMRILoaded) {
      initBreastMRI();
    }
  } else if (tab === "report") {
    if (agentView) agentView.style.display = "none";
    if (mriView) mriView.style.display = "none";
    if (reportView) reportView.style.display = "flex";
    if (!reportWorkbenchLoaded) {
      initReportWorkbench();
    }
  }
}

// 绑定工作台 Tab 点击事件
document.addEventListener("DOMContentLoaded", function () {
  const workspaceTabs = document.querySelectorAll(".workspace-tab");
  workspaceTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      switchWorkspaceTab(tab.dataset.workspaceTab);
    });
  });
});

// ==================== 乳腺 MRI 报告工作台 ====================

let breastMRILoaded = false;

async function initBreastMRI() {
  const loading = document.getElementById("breastMRILoading");
  const error = document.getElementById("breastMRIError");
  const iframe = document.getElementById("breastMRIIframe");

  if (breastMRILoaded) return;

  if (loading) loading.style.display = "flex";
  if (error) error.style.display = "none";
  if (iframe) iframe.style.display = "none";

  try {
    const resp = await fetch("/api/breast-mri/health");
    const data = await resp.json();
    if (data.status === "ok") {
      breastMRILoaded = true;
      if (loading) loading.style.display = "none";
      if (iframe) {
        iframe.src = "/breast-mri/";
        iframe.style.display = "block";
      }
    } else {
      throw new Error(data.message || "服务不可用");
    }
  } catch (_e) {
    if (loading) loading.style.display = "none";
    if (error) error.style.display = "flex";
  }
}

function retryBreastMRI() {
  breastMRILoaded = false;
  initBreastMRI();
}

// 绑定重试按钮（DOM 就绪后执行）
document.addEventListener("DOMContentLoaded", function () {
  const btn = document.getElementById("btnBreastMRIRetry");
  if (btn) btn.addEventListener("click", retryBreastMRI);
});

// ==================== 评测中心 ====================

function setEvalCaseError(message = "") {
  if (!evalCaseError) return;
  evalCaseError.textContent = message;
  evalCaseError.hidden = !message;
}

function showToast(message, type = "success") {
  if (!appToast) return;
  window.clearTimeout(toastTimer);
  appToast.textContent = message;
  appToast.className = `app-toast ${type}`;
  appToast.hidden = false;
  requestAnimationFrame(() => appToast.classList.add("show"));
  toastTimer = window.setTimeout(() => {
    appToast.classList.remove("show");
    window.setTimeout(() => {
      appToast.hidden = true;
    }, 180);
  }, 2600);
}

function hideChunkSearchResults() {
  isChunkSearchOpen = false;
  if (chunkSearchResults) chunkSearchResults.hidden = true;
  inputChunkSearch?.setAttribute("aria-expanded", "false");
}

function renderChunkSearchMessage(message, state = "empty") {
  if (!chunkSearchResults) return;
  const messageEl = document.createElement("div");
  messageEl.className = `autocomplete-message ${state}`;
  messageEl.textContent = message;
  chunkSearchResults.replaceChildren(messageEl);
  chunkSearchResults.hidden = !isChunkSearchOpen;
  inputChunkSearch?.setAttribute("aria-expanded", String(isChunkSearchOpen));
}

function resetChunkSearch() {
  window.clearTimeout(chunkSearchDebounceTimer);
  chunkSearchAbortController?.abort();
  chunkSearchAbortController = null;
  if (inputChunkSearch) {
    inputChunkSearch.value = "";
    inputChunkSearch.disabled = false;
    inputChunkSearch.classList.remove("has-selection");
  }
  if (selectedChunkIdInput) selectedChunkIdInput.value = "";
  chunkSearchResults?.replaceChildren();
  hideChunkSearchResults();
}

function resetChunkFilters() {
  chunkFilterAbortController?.abort();
  chunkFilterAbortController = null;
  chunkFilterControls.forEach(({ element }) => {
    if (element) {
      element.value = "";
      element.disabled = false;
    }
  });
}

function appendSelectedChunkFilters(
  params,
  maxIndex = chunkFilterControls.length - 1,
) {
  chunkFilterControls.forEach(({ param, element }, index) => {
    if (index > maxIndex) return;
    const value = element?.value.trim() || "";
    if (value) params.set(param, value);
  });
}

function updateChunkFilterSelect(control, values) {
  const select = control.element;
  if (!select) return false;
  const previousValue = select.value;
  const normalizedValues = Array.isArray(values)
    ? values.map((value) => String(value || "").trim()).filter(Boolean)
    : [];
  const fragment = document.createDocumentFragment();
  fragment.appendChild(new Option("全部", ""));
  normalizedValues.forEach((value) =>
    fragment.appendChild(new Option(value, value)),
  );
  select.replaceChildren(fragment);
  select.value = normalizedValues.includes(previousValue) ? previousValue : "";
  return Boolean(previousValue && !select.value);
}

async function loadChunkFilterOptions(
  maxKnownIndex = chunkFilterControls.length - 2,
  retryAfterReset = true,
) {
  const requestId = ++chunkFilterRequestId;
  chunkFilterAbortController?.abort();
  const controller = new AbortController();
  chunkFilterAbortController = controller;
  const params = new URLSearchParams({ _t: String(Date.now()) });
  // filter-options only needs the selected parent path through Level 3.
  appendSelectedChunkFilters(params, Math.min(maxKnownIndex, 4));
  document.querySelector(".kb-filters-row")?.classList.add("is-loading");

  try {
    const response = await fetch(
      `${API_BASE}/kb/filter-options?${params.toString()}`,
      {
        method: "GET",
        headers: { Accept: "application/json" },
        cache: "no-store",
        signal: controller.signal,
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    if (requestId !== chunkFilterRequestId) return false;
    let selectionWasReset = false;
    chunkFilterControls.forEach((control) => {
      selectionWasReset =
        updateChunkFilterSelect(control, payload[control.optionsKey]) ||
        selectionWasReset;
    });
    if (selectionWasReset && retryAfterReset) {
      // A stale child value was removed. Fetch once more with the normalized
      // path so its descendants show the correct broader option set.
      return loadChunkFilterOptions(chunkFilterControls.length - 2, false);
    }
    return true;
  } catch (error) {
    if (error.name === "AbortError" || requestId !== chunkFilterRequestId)
      return false;
    console.error("[Eval] 加载切片级联筛选失败", error);
    setEvalCaseError(`筛选条件加载失败：${error.message}`);
    return false;
  } finally {
    if (requestId === chunkFilterRequestId) {
      document.querySelector(".kb-filters-row")?.classList.remove("is-loading");
    }
    if (chunkFilterAbortController === controller) {
      chunkFilterAbortController = null;
    }
  }
}

function scheduleChunkSearch({ openResults = false } = {}) {
  window.clearTimeout(chunkSearchDebounceTimer);
  if (openResults) {
    isChunkSearchOpen = true;
    inputChunkSearch?.setAttribute("aria-expanded", "true");
  }
  chunkSearchDebounceTimer = window.setTimeout(
    () => searchEvalChunks(inputChunkSearch?.value || ""),
    300,
  );
}

function selectEvalChunk(chunk) {
  const chunkId = String(chunk.chunk_id || "")
    .trim()
    .toLowerCase();
  if (!chunkId || !inputChunkSearch || !selectedChunkIdInput) return;
  selectedChunkIdInput.value = chunkId;
  inputChunkSearch.value = String(chunk.content_preview || "（无内容预览）");
  inputChunkSearch.classList.add("has-selection");
  setEvalCaseError();
  hideChunkSearchResults();
}

function renderChunkSearchResults(chunks) {
  if (!chunkSearchResults) return;
  if (!chunks.length) {
    renderChunkSearchMessage("没有找到匹配的 active 知识库切片", "empty");
    return;
  }

  const fragment = document.createDocumentFragment();
  chunks.forEach((chunk) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "autocomplete-item";
    item.setAttribute("role", "option");

    const header = document.createElement("span");
    header.className = "autocomplete-item-header";

    const kbBadge = document.createElement("span");
    kbBadge.className = "autocomplete-kb-badge";
    kbBadge.textContent = String(chunk.kb_name || "知识库");

    const chunkId = document.createElement("span");
    chunkId.className = "autocomplete-item-id";
    chunkId.textContent = String(chunk.chunk_id || "");
    header.append(kbBadge, chunkId);

    const preview = document.createElement("span");
    preview.className = "autocomplete-item-text";
    preview.textContent = String(chunk.content_preview || "（无内容预览）");

    item.append(header, preview);
    item.addEventListener("click", () => selectEvalChunk(chunk));
    fragment.appendChild(item);
  });
  chunkSearchResults.replaceChildren(fragment);
  chunkSearchResults.hidden = !isChunkSearchOpen;
  inputChunkSearch?.setAttribute("aria-expanded", String(isChunkSearchOpen));
}

async function searchEvalChunks(searchTerm = "") {
  if (!chunkSearchResults) return;
  const requestId = ++chunkSearchRequestId;
  chunkSearchAbortController?.abort();
  const controller = new AbortController();
  chunkSearchAbortController = controller;
  renderChunkSearchMessage("正在搜索知识库切片...", "loading");

  const params = new URLSearchParams({
    query: String(searchTerm || "").trim(),
    limit: "20",
    _t: String(Date.now()),
  });
  appendSelectedChunkFilters(params);
  try {
    const response = await fetch(`${API_BASE}/kb/chunks?${params.toString()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => []);
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    if (requestId !== chunkSearchRequestId) return;
    renderChunkSearchResults(
      Array.isArray(payload) ? payload.slice(0, 20) : [],
    );
  } catch (error) {
    if (error.name === "AbortError" || requestId !== chunkSearchRequestId)
      return;
    console.error("[Eval] 搜索知识库切片失败", error);
    renderChunkSearchMessage(`搜索失败：${error.message}`, "error");
  } finally {
    if (chunkSearchAbortController === controller) {
      chunkSearchAbortController = null;
    }
  }
}

function openEvalCaseModal() {
  if (!evalCaseModal) return;
  evalCaseForm?.reset();
  resetChunkSearch();
  resetChunkFilters();
  setEvalCaseError();
  evalCaseModal.hidden = false;
  evalCaseModal.setAttribute("aria-hidden", "false");
  loadChunkFilterOptions(-1).then((loaded) => {
    if (loaded && !evalCaseModal.hidden) scheduleChunkSearch();
  });
  requestAnimationFrame(() => evalCaseQuery?.focus());
}

function closeEvalCaseModal() {
  if (!evalCaseModal || isSavingEvalCase) return;
  evalCaseModal.hidden = true;
  evalCaseModal.setAttribute("aria-hidden", "true");
  window.clearTimeout(chunkSearchDebounceTimer);
  chunkSearchAbortController?.abort();
  chunkFilterAbortController?.abort();
  hideChunkSearchResults();
  setEvalCaseError();
}

function setEvalCaseSaving(saving) {
  isSavingEvalCase = saving;
  if (btnSaveEvalCase) {
    btnSaveEvalCase.disabled = saving;
    btnSaveEvalCase.textContent = saving ? "保存中..." : "保存用例";
  }
  if (btnCloseEvalCase) btnCloseEvalCase.disabled = saving;
  if (btnCancelEvalCase) btnCancelEvalCase.disabled = saving;
  if (inputChunkSearch) inputChunkSearch.disabled = saving;
  chunkFilterControls.forEach(({ element }) => {
    if (element) element.disabled = saving;
  });
}

function markEvalDatasetUpdated(total, message) {
  hideEvalRunProgress();
  setEvalStatusMessage(message, "success");
  if (evalRunMeta) {
    evalRunMeta.textContent = `${total ?? "-"} 条用例 · 数据集已更新，等待重新评测`;
  }
}

async function saveEvalCase(event) {
  event.preventDefault();
  if (isSavingEvalCase) return;

  const query = evalCaseQuery?.value.trim() || "";
  const selectedChunkId =
    selectedChunkIdInput?.value.trim().toLowerCase() || "";
  if (!query) {
    setEvalCaseError("请输入测试 Query。");
    evalCaseQuery?.focus();
    return;
  }
  if (!selectedChunkId) {
    setEvalCaseError("请选择一个预期知识库切片。");
    inputChunkSearch?.focus();
    return;
  }
  if (!/^[0-9a-f]{32}$/.test(selectedChunkId)) {
    setEvalCaseError("所选切片缺少合法的 32 位 Chunk ID，请刷新后重试。");
    inputChunkSearch?.focus();
    return;
  }

  setEvalCaseError();
  setEvalCaseSaving(true);
  try {
    const response = await fetch(`${API_BASE}/eval/add_case`, {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query,
        expected_chunk_ids: [selectedChunkId],
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }

    setEvalCaseSaving(false);
    closeEvalCaseModal();
    await loadEvalDataset();
    showToast("添加成功", "success");
    markEvalDatasetUpdated(
      payload.total,
      `测试用例已添加，当前数据集共 ${payload.total ?? "-"} 条。`,
    );
  } catch (error) {
    console.error("[Eval] 添加测试用例失败", error);
    setEvalCaseError(`保存失败：${error.message}`);
    showToast("添加失败", "error");
  } finally {
    setEvalCaseSaving(false);
  }
}

btnAddEvalCase?.addEventListener("click", openEvalCaseModal);
btnCloseEvalCase?.addEventListener("click", closeEvalCaseModal);
btnCancelEvalCase?.addEventListener("click", closeEvalCaseModal);
evalCaseModal
  ?.querySelector("[data-eval-modal-close]")
  ?.addEventListener("click", closeEvalCaseModal);
evalCaseForm?.addEventListener("submit", saveEvalCase);
inputChunkSearch?.addEventListener("input", () => {
  isChunkSearchOpen = true;
  inputChunkSearch.setAttribute("aria-expanded", "true");
  if (selectedChunkIdInput) selectedChunkIdInput.value = "";
  inputChunkSearch.classList.remove("has-selection");
  setEvalCaseError();
  scheduleChunkSearch({ openResults: true });
});
inputChunkSearch?.addEventListener("focus", () => {
  isChunkSearchOpen = true;
  inputChunkSearch.setAttribute("aria-expanded", "true");
  if (chunkSearchResults?.childElementCount) {
    chunkSearchResults.hidden = false;
  } else {
    searchEvalChunks(inputChunkSearch.value);
  }
});
chunkFilterControls.forEach(({ element }) => {
  element?.addEventListener("change", async () => {
    if (selectedChunkIdInput) selectedChunkIdInput.value = "";
    if (inputChunkSearch?.classList.contains("has-selection")) {
      inputChunkSearch.value = "";
      inputChunkSearch.classList.remove("has-selection");
    }
    setEvalCaseError();
    const loaded = await loadChunkFilterOptions();
    if (loaded) scheduleChunkSearch({ openResults: true });
  });
});
document.addEventListener("click", (event) => {
  if (chunkAutocomplete && !chunkAutocomplete.contains(event.target)) {
    hideChunkSearchResults();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && evalCaseModal && !evalCaseModal.hidden) {
    if (chunkSearchResults && !chunkSearchResults.hidden) {
      hideChunkSearchResults();
    } else {
      closeEvalCaseModal();
    }
  }
});

function setEvalAutoGenerateError(message = "") {
  if (!evalAutoGenerateError) return;
  evalAutoGenerateError.textContent = message;
  evalAutoGenerateError.hidden = !message;
}

function setEvalAutoProgress(percent, text) {
  const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
  if (evalAutoProgressBar) evalAutoProgressBar.style.width = `${safePercent}%`;
  if (evalAutoProgressPercent)
    evalAutoProgressPercent.textContent = `${safePercent}%`;
  if (evalAutoProgressText && text) evalAutoProgressText.textContent = text;
  evalAutoProgressTrack?.setAttribute("aria-valuenow", String(safePercent));
}

function appendEvalAutoLog(message, replace = false) {
  if (!evalAutoGenerateLog || !message) return;
  const current = replace ? "" : evalAutoGenerateLog.textContent.trim();
  const next = current ? `${current}\n${message}` : message;
  evalAutoGenerateLog.textContent = next.slice(-8000);
  evalAutoGenerateLog.scrollTop = evalAutoGenerateLog.scrollHeight;
}

function resetEvalAutoGenerateUI() {
  setEvalAutoGenerateError();
  setEvalAutoProgress(0, "等待开始");
  if (evalAutoGenerateLog) {
    evalAutoGenerateLog.textContent = "设置参数后点击“开始生成”。";
  }
  evalAutoProgressTrack?.classList.remove(
    "is-running",
    "is-error",
    "is-complete",
  );
}

function openEvalAutoGenerateModal() {
  if (!evalAutoGenerateModal) return;
  resetEvalAutoGenerateUI();
  evalAutoGenerateModal.hidden = false;
  evalAutoGenerateModal.setAttribute("aria-hidden", "false");
  requestAnimationFrame(() => evalAutoGenerateCount?.focus());
}

function closeEvalAutoGenerateModal() {
  if (!evalAutoGenerateModal || isAutoGeneratingCases) return;
  evalAutoGenerateModal.hidden = true;
  evalAutoGenerateModal.setAttribute("aria-hidden", "true");
  setEvalAutoGenerateError();
}

function setEvalAutoGenerating(running) {
  isAutoGeneratingCases = running;
  if (btnStartEvalAutoGenerate) {
    btnStartEvalAutoGenerate.disabled = running;
    btnStartEvalAutoGenerate.textContent = running ? "生成中..." : "开始生成";
  }
  if (btnCloseEvalAutoGenerate) btnCloseEvalAutoGenerate.disabled = running;
  if (evalAutoGenerateCount) evalAutoGenerateCount.disabled = running;
  if (evalAutoGenerateMinLength) evalAutoGenerateMinLength.disabled = running;
  if (btnCancelEvalAutoGenerate) {
    btnCancelEvalAutoGenerate.disabled = false;
    btnCancelEvalAutoGenerate.textContent = running ? "停止生成" : "取消";
  }
  evalAutoProgressTrack?.classList.toggle("is-running", running);
}

async function consumeSSEStream(response, onEvent) {
  if (!response.body) throw new Error("浏览器未提供流式响应读取能力");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  const consumeFrame = (frame) => {
    const payload = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    if (payload) onEvent(JSON.parse(payload));
  };

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      const frames = buffer.split(/\r?\n\r?\n/);
      buffer = frames.pop() || "";
      frames.forEach(consumeFrame);
      if (done) break;
    }
    if (buffer.trim()) consumeFrame(buffer);
  } finally {
    reader.releaseLock();
  }
}

function handleEvalAutoGenerateEvent(event, state) {
  if (!event || typeof event !== "object") return;
  if (event.type === "error") {
    throw new Error(event.message || "自动生成失败");
  }
  if (event.type === "start") {
    setEvalAutoProgress(0, `准备生成 0/${event.total}`);
    appendEvalAutoLog(event.message, true);
    return;
  }
  if (event.type === "warning") {
    appendEvalAutoLog(`⚠ ${event.message}`);
    return;
  }
  if (event.type === "progress") {
    setEvalAutoProgress(
      event.progress,
      `正在生成 ${event.current}/${event.total}`,
    );
    appendEvalAutoLog(`[${event.current}/${event.total}] ${event.query}`);
    return;
  }
  if (event.type === "done") {
    state.done = event;
    setEvalAutoProgress(100, `生成完成 ${event.current}/${event.total}`);
    evalAutoProgressTrack?.classList.remove("is-running");
    evalAutoProgressTrack?.classList.add("is-complete");
    appendEvalAutoLog(`✓ ${event.message}`);
  }
}

async function startEvalAutoGenerate(event) {
  event.preventDefault();
  if (isAutoGeneratingCases) return;

  const count = Number(evalAutoGenerateCount?.value);
  const minLength = Number(evalAutoGenerateMinLength?.value);
  if (!Number.isInteger(count) || count < 1 || count > 100) {
    setEvalAutoGenerateError("生成数量必须是 1-100 之间的整数。");
    evalAutoGenerateCount?.focus();
    return;
  }
  if (!Number.isInteger(minLength) || minLength < 0 || minLength > 10000) {
    setEvalAutoGenerateError("最小切片长度必须是 0-10000 之间的整数。");
    evalAutoGenerateMinLength?.focus();
    return;
  }

  resetEvalAutoGenerateUI();
  setEvalAutoGenerating(true);
  setEvalAutoProgress(0, `正在连接生成服务...`);
  appendEvalAutoLog("正在连接服务并抽取知识库切片...", true);
  evalAutoGenerateAbortController = new AbortController();
  const streamState = { done: null };

  try {
    const response = await fetch(`${API_BASE}/eval/auto_generate`, {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ count, min_length: minLength }),
      signal: evalAutoGenerateAbortController.signal,
      cache: "no-store",
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }

    await consumeSSEStream(response, (streamEvent) => {
      handleEvalAutoGenerateEvent(streamEvent, streamState);
    });
    if (!streamState.done) {
      throw new Error("流式连接已结束，但没有收到完成事件");
    }

    const done = streamState.done;
    await loadEvalDataset();
    showToast("生成完毕", "success");
    markEvalDatasetUpdated(
      done.dataset_total,
      `自动生成完成：新增 ${done.generated} 条，当前测试集共 ${done.dataset_total} 条。`,
    );
  } catch (error) {
    if (error.name === "AbortError") {
      setEvalAutoProgress(
        Number(evalAutoProgressPercent?.textContent.replace("%", "")) || 0,
        "已停止生成",
      );
      appendEvalAutoLog("已停止；此前显示成功的用例已经保存。", false);
      showToast("已停止生成", "error");
    } else {
      console.error("[Eval] 自动生成失败", error);
      setEvalAutoGenerateError(`生成失败：${error.message}`);
      evalAutoProgressTrack?.classList.remove("is-running");
      evalAutoProgressTrack?.classList.add("is-error");
      appendEvalAutoLog(`✕ ${error.message}`);
      showToast("生成失败", "error");
    }
  } finally {
    evalAutoGenerateAbortController = null;
    setEvalAutoGenerating(false);
  }
}

function cancelOrCloseEvalAutoGenerate() {
  if (isAutoGeneratingCases) {
    evalAutoGenerateAbortController?.abort();
    if (btnCancelEvalAutoGenerate) btnCancelEvalAutoGenerate.disabled = true;
    return;
  }
  closeEvalAutoGenerateModal();
}

btnAutoGenerateCases?.addEventListener("click", openEvalAutoGenerateModal);
btnCloseEvalAutoGenerate?.addEventListener("click", closeEvalAutoGenerateModal);
btnCancelEvalAutoGenerate?.addEventListener(
  "click",
  cancelOrCloseEvalAutoGenerate,
);
evalAutoGenerateModal
  ?.querySelector("[data-eval-auto-close]")
  ?.addEventListener("click", closeEvalAutoGenerateModal);
evalAutoGenerateForm?.addEventListener("submit", startEvalAutoGenerate);
document.addEventListener("keydown", (event) => {
  if (
    event.key === "Escape" &&
    evalAutoGenerateModal &&
    !evalAutoGenerateModal.hidden
  ) {
    closeEvalAutoGenerateModal();
  }
});

function formatEvalRate(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "--";
}

function evalHitBadge(hit) {
  return hit
    ? '<span class="eval-hit-badge yes">命中</span>'
    : '<span class="eval-hit-badge no">未命中</span>';
}

function setEvalLoading(loading) {
  if (!btnRunEval) return;
  btnRunEval.disabled = loading;
  btnRunEval.classList.toggle("is-loading", loading);
  const text = btnRunEval.querySelector(".eval-run-btn-text");
  const spinner = btnRunEval.querySelector(".eval-run-btn-loading");
  if (text) text.hidden = loading;
  if (spinner) spinner.hidden = !loading;
}

function setEvalStatusMessage(message, state = "") {
  if (evalStatus) {
    evalStatus.className = `eval-status${state ? ` ${state}` : ""}`;
  }
  if (evalProgressText) evalProgressText.textContent = message;
}

function setEvalRunProgress(percent, message) {
  window.clearTimeout(evalProgressHideTimer);
  const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
  if (evalProgressTrack) {
    evalProgressTrack.hidden = false;
    evalProgressTrack.classList.remove("is-cancelled");
    evalProgressTrack.classList.toggle("is-complete", safePercent >= 100);
    evalProgressTrack.classList.toggle("is-running", safePercent < 100);
    evalProgressTrack.setAttribute("aria-valuenow", String(safePercent));
  }
  if (evalProgressFill) evalProgressFill.style.width = `${safePercent}%`;
  if (evalProgressPercent) {
    evalProgressPercent.hidden = false;
    evalProgressPercent.textContent = `${safePercent}%`;
  }
  setEvalStatusMessage(message, "running");
}

function hideEvalRunProgress() {
  window.clearTimeout(evalProgressHideTimer);
  if (evalProgressTrack) {
    evalProgressTrack.hidden = true;
    evalProgressTrack.classList.remove(
      "is-running",
      "is-complete",
      "is-cancelled",
    );
  }
  if (evalProgressPercent) evalProgressPercent.hidden = true;
  if (btnCancelEval) {
    btnCancelEval.hidden = true;
    btnCancelEval.disabled = false;
  }
}

function showEvalCancelledState() {
  const currentPercent =
    Number(evalProgressTrack?.getAttribute("aria-valuenow")) || 0;
  if (evalProgressTrack) {
    evalProgressTrack.hidden = false;
    evalProgressTrack.classList.remove("is-running", "is-complete");
    evalProgressTrack.classList.add("is-cancelled");
  }
  if (evalProgressPercent) {
    evalProgressPercent.hidden = false;
    evalProgressPercent.textContent = `${currentPercent}%`;
  }
  setEvalStatusMessage("评测已取消", "cancelled");
}

function scheduleEvalCancelledReset() {
  window.clearTimeout(evalCancelResetTimer);
  evalCancelResetTimer = window.setTimeout(() => {
    hideEvalRunProgress();
    setEvalStatusMessage("评测已取消，可重新开始评测。", "cancelled");
    isEvalCancelRequested = false;
  }, 750);
}

async function cancelRetrievalEvaluation() {
  if (isEvalCancelRequested) return;
  isEvalCancelRequested = true;
  stopEvalStatusPolling();
  if (btnCancelEval) btnCancelEval.disabled = true;

  evalRunAbortController?.abort();
  showEvalCancelledState();
  scheduleEvalCancelledReset();

  try {
    const response = await fetch(`${API_BASE}/eval/cancel`, {
      method: "POST",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
  } catch (error) {
    console.error("[Eval] 通知后端取消失败", error);
    showToast("取消请求发送失败", "error");
  }
}

btnCancelEval?.addEventListener("click", cancelRetrievalEvaluation);

function resetEvalMetrics() {
  if (evalHit1) evalHit1.textContent = "--";
  if (evalHit3) evalHit3.textContent = "--";
  if (evalHit5) evalHit5.textContent = "--";
  if (evalHit1Count) evalHit1Count.textContent = "尚未运行";
  if (evalHit3Count) evalHit3Count.textContent = "尚未运行";
  if (evalHit5Count) evalHit5Count.textContent = "尚未运行";
}

function evalDeleteButton(chunkId) {
  if (!chunkId) return "-";
  return `
    <button
      type="button"
      class="eval-delete-case-btn"
      data-eval-delete-chunk="${escapeHtml(String(chunkId))}"
      title="删除这条测试用例"
    >❌ 删除</button>
  `;
}

function renderEvalDatasetCases(cases) {
  const items = Array.isArray(cases) ? cases : [];
  resetEvalMetrics();
  if (evalRunMeta) {
    evalRunMeta.textContent = `${items.length} 条测试用例 · 等待评测`;
  }
  if (!items.length) {
    evalDetailsBody.innerHTML = `
      <tr class="eval-empty-row"><td colspan="10">测试集为空，请新增或自动生成用例</td></tr>
    `;
    return;
  }

  evalDetailsBody.innerHTML = items
    .map((item, index) => {
      const expectedIds = Array.isArray(item.expected_chunk_ids)
        ? item.expected_chunk_ids
        : [];
      const primaryChunkId = expectedIds[0] || "";
      const expectedHtml = expectedIds
        .map((id) => `<code>${escapeHtml(String(id))}</code>`)
        .join("<br>");
      const metadata = JSON.stringify(item.metadata || {});
      return `
      <tr data-eval-case-chunk="${escapeHtml(primaryChunkId)}">
        <td class="eval-index-cell">${index + 1}</td>
        <td class="eval-query-cell">${escapeHtml(String(item.query || ""))}<span class="eval-query-meta">${escapeHtml(metadata)}</span></td>
        <td class="eval-id-cell">${expectedHtml || "-"}</td>
        <td>-</td>
        <td>-</td>
        <td>-</td>
        <td>-</td>
        <td>-</td>
        <td>-</td>
        <td class="eval-action-cell">${evalDeleteButton(primaryChunkId)}</td>
      </tr>
    `;
    })
    .join("");
}

async function loadEvalDataset() {
  if (!evalDetailsBody) return;
  try {
    const response = await fetch(`${API_BASE}/eval/dataset?_t=${Date.now()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    renderEvalDatasetCases(payload.items);
  } catch (error) {
    console.error("[Eval] 加载测试集失败", error);
    evalDetailsBody.innerHTML = `
      <tr class="eval-empty-row"><td colspan="10">加载测试集失败：${escapeHtml(error.message)}</td></tr>
    `;
    setEvalStatusMessage(`加载测试集失败：${error.message}`, "error");
  }
}

async function clearEvalDataset() {
  if (!confirm("确定要清空所有测试用例吗？此操作不可恢复！")) return;
  if (btnClearEvalDataset) btnClearEvalDataset.disabled = true;
  try {
    const response = await fetch(`${API_BASE}/eval/dataset`, {
      method: "DELETE",
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    await loadEvalDataset();
    hideEvalRunProgress();
    setEvalStatusMessage("测试集已清空，可以新增或自动生成用例。", "success");
    showToast("清空成功", "success");
  } catch (error) {
    console.error("[Eval] 清空测试集失败", error);
    setEvalStatusMessage(`清空失败：${error.message}`, "error");
    showToast("清空失败", "error");
  } finally {
    if (btnClearEvalDataset) btnClearEvalDataset.disabled = false;
  }
}

async function deleteEvalCase(chunkId, button) {
  if (!chunkId || !confirm("确定删除这条用例吗？")) return;
  if (button) button.disabled = true;
  try {
    const response = await fetch(
      `${API_BASE}/eval/dataset/${encodeURIComponent(chunkId)}`,
      {
        method: "DELETE",
        headers: { Accept: "application/json" },
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    await loadEvalDataset();
    hideEvalRunProgress();
    setEvalStatusMessage(
      `删除成功，当前测试集共 ${payload.total ?? "-"} 条。`,
      "success",
    );
    showToast("删除成功", "success");
  } catch (error) {
    console.error("[Eval] 删除测试用例失败", error);
    if (button) button.disabled = false;
    setEvalStatusMessage(`删除失败：${error.message}`, "error");
    showToast("删除失败", "error");
  }
}

btnClearEvalDataset?.addEventListener("click", clearEvalDataset);
evalDetailsBody?.addEventListener("click", (event) => {
  const button = event.target.closest?.("[data-eval-delete-chunk]");
  if (!button) return;
  deleteEvalCase(button.dataset.evalDeleteChunk, button);
});

function renderEvalResults(result) {
  const metrics = result?.metrics || {};
  const details = Array.isArray(result?.details) ? result.details : [];
  const config = result?.config || {};
  const total = Number(metrics.total ?? details.length);

  evalHit1.textContent = formatEvalRate(metrics.hit1);
  evalHit3.textContent = formatEvalRate(metrics.hit3);
  evalHit5.textContent = formatEvalRate(metrics.hit5);
  evalHit1Count.textContent = `${Number(metrics.hit1_count || 0)} / ${total}`;
  evalHit3Count.textContent = `${Number(metrics.hit3_count || 0)} / ${total}`;
  evalHit5Count.textContent = `${Number(metrics.hit5_count || 0)} / ${total}`;
  evalRunMeta.textContent = [
    `${total} 条用例`,
    `Hybrid Top ${config.candidate_top_k ?? "-"}`,
    `Rerank Top ${config.rerank_top_k ?? "-"}`,
    `${config.knowledge_base_count ?? "-"} 个知识库`,
  ].join(" · ");

  if (!details.length) {
    evalDetailsBody.innerHTML = `
      <tr class="eval-empty-row"><td colspan="10">评测完成，但没有返回明细</td></tr>
    `;
    return;
  }

  evalDetailsBody.innerHTML = details
    .map((detail, index) => {
      const expectedIds = Array.isArray(detail.expected_chunk_ids)
        ? detail.expected_chunk_ids
        : [];
      const expectedHtml = expectedIds.length
        ? expectedIds
            .map((id) => `<code>${escapeHtml(String(id))}</code>`)
            .join("<br>")
        : "-";
      const rank =
        detail.hit_rank == null
          ? '<span class="eval-rank-miss">MISS</span>'
          : `<span class="eval-rank-hit">#${Number(detail.hit_rank)}</span>`;
      const score =
        detail.rerank_score == null
          ? "-"
          : Number(detail.rerank_score).toFixed(6);
      const metadata = JSON.stringify(detail.metadata || {});
      return `
      <tr>
        <td class="eval-index-cell">${index + 1}</td>
        <td class="eval-query-cell">${escapeHtml(String(detail.query || ""))}<span class="eval-query-meta">${escapeHtml(metadata)}</span></td>
        <td class="eval-id-cell">${expectedHtml}</td>
        <td>${Number(detail.candidate_count || 0)}</td>
        <td>${rank}</td>
        <td class="eval-score-cell">${score}</td>
        <td>${evalHitBadge(Boolean(detail.hit1))}</td>
        <td>${evalHitBadge(Boolean(detail.hit3))}</td>
        <td>${evalHitBadge(Boolean(detail.hit5))}</td>
        <td class="eval-action-cell">${evalDeleteButton(expectedIds[0] || "")}</td>
      </tr>
    `;
    })
    .join("");
}

function stopEvalStatusPolling() {
  window.clearTimeout(evalStatusPollTimer);
  evalStatusPollTimer = null;
  evalStatusRequestId += 1;
}

function scheduleEvalStatusPoll() {
  window.clearTimeout(evalStatusPollTimer);
  evalStatusPollTimer = window.setTimeout(() => {
    restoreEvaluationState({ polling: true });
  }, 1500);
}

async function restoreEvaluationState({ polling = false } = {}) {
  const requestId = ++evalStatusRequestId;
  try {
    const response = await fetch(`${API_BASE}/eval/status?_t=${Date.now()}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    if (requestId !== evalStatusRequestId) return;

    if (payload.status === "running") {
      const current = Number(payload.current || 0);
      const total = Number(payload.total || 0);
      const progress = Number(payload.progress || 0);
      const query = String(payload.query || "");
      const cancelling = Boolean(payload.cancel_requested);
      setEvalLoading(true);
      if (btnCancelEval) {
        btnCancelEval.hidden = false;
        btnCancelEval.disabled = cancelling;
      }
      setEvalRunProgress(
        progress,
        cancelling
          ? `正在取消评测，等待当前 Query 完成 (${current}/${total || "-"})...`
          : `正在评测 (${current}/${total || "-"})${query ? `：${query}` : "..."}`,
      );
      // A page restored without the original SSE connection follows the
      // durable background worker through this lightweight status endpoint.
      if (!evalRunAbortController) scheduleEvalStatusPoll();
      return;
    }

    window.clearTimeout(evalStatusPollTimer);
    evalStatusPollTimer = null;
    setEvalLoading(false);
    hideEvalRunProgress();
    if (payload.status === "completed" && payload.result) {
      renderEvalResults(payload.result);
      const total = payload.result?.metrics?.total ?? 0;
      const completedAt = payload.completed_at
        ? `，保存于 ${payload.completed_at}`
        : "";
      setEvalStatusMessage(
        `已恢复最后一次评测：共 ${total} 条用例${completedAt}。`,
        "success",
      );
      return;
    }

    await loadEvalDataset();
    if (payload.status === "failed" || payload.status === "interrupted") {
      setEvalStatusMessage(
        payload.message || "上一次评测未完整结束，请重新运行。",
        "error",
      );
    } else if (payload.status === "cancelled") {
      setEvalStatusMessage("上一次评测已取消，可重新开始。", "cancelled");
    }
  } catch (error) {
    if (requestId !== evalStatusRequestId) return;
    console.error("[Eval] 恢复评测现场失败", error);
    if (polling) {
      scheduleEvalStatusPoll();
    } else {
      await loadEvalDataset();
      setEvalStatusMessage(`恢复评测现场失败：${error.message}`, "error");
    }
  }
}

async function runRetrievalEvaluation() {
  if (!btnRunEval || btnRunEval.disabled) return;
  stopEvalStatusPolling();
  window.clearTimeout(evalCancelResetTimer);
  isEvalCancelRequested = false;
  evalRunAbortController = new AbortController();
  if (btnCancelEval) {
    btnCancelEval.hidden = false;
    btnCancelEval.disabled = false;
  }
  setEvalLoading(true);
  setEvalRunProgress(0, "正在准备 Embedding、Hybrid Search 和 Rerank...");
  const streamState = { done: null };

  try {
    const response = await fetch(`${API_BASE}/eval/run`, {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      cache: "no-store",
      signal: evalRunAbortController.signal,
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }

    await consumeSSEStream(response, (event) => {
      if (event.type === "error") {
        throw new Error(event.message || "评测执行失败");
      }
      if (event.type === "start") {
        setEvalRunProgress(0, `正在评测 (0/${event.total})：准备检索环境...`);
      } else if (event.type === "progress") {
        setEvalRunProgress(
          event.progress,
          `正在评测 (${event.current}/${event.total})：${event.query}`,
        );
      } else if (event.type === "done") {
        streamState.done = event;
        setEvalRunProgress(
          100,
          `评测计算完成 (${event.total}/${event.total})，正在渲染结果...`,
        );
      } else if (event.type === "cancelled") {
        streamState.cancelled = event;
        showEvalCancelledState();
      }
    });

    if (streamState.cancelled) {
      scheduleEvalCancelledReset();
      return;
    }
    if (!streamState.done) {
      throw new Error("流式连接已结束，但没有收到完成事件");
    }
    await new Promise((resolve) => {
      evalProgressHideTimer = window.setTimeout(resolve, 450);
    });
    renderEvalResults(streamState.done);
    stopEvalStatusPolling();
    hideEvalRunProgress();
    setEvalStatusMessage(
      `评测完成：共运行 ${streamState.done?.metrics?.total ?? 0} 条用例。`,
      "success",
    );
  } catch (error) {
    if (error.name === "AbortError" && isEvalCancelRequested) {
      showEvalCancelledState();
    } else {
      console.error("[Eval] 评测失败", error);
      hideEvalRunProgress();
      setEvalStatusMessage(`评测失败：${error.message}`, "error");
    }
  } finally {
    evalRunAbortController = null;
    if (!isEvalCancelRequested && !streamState.cancelled) {
      if (btnCancelEval) btnCancelEval.hidden = true;
    }
    setEvalLoading(false);
  }
}

if (btnRunEval) {
  btnRunEval.addEventListener("click", runRetrievalEvaluation);
}

document.addEventListener("DOMContentLoaded", () => {
  restoreEvaluationState();
});

// ==================== 知识库管理 ====================

let kbAbortController = null;

async function refreshKBStatus() {
  return refreshKnowledgeBases();
}

async function refreshKnowledgeBases() {
  try {
    const resp = await fetch(`${API_BASE}/kbs?_t=${Date.now()}`, {
      cache: "no-store",
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const previousSelectedKb = knowledgeBases.find(
      (kb) => kb.kb_id === selectedManageKbId,
    );
    knowledgeBases = data.knowledge_bases || [];
    renderManageKbList(knowledgeBases);
    renderAgentKbSelector(knowledgeBases);
    updateKBPolling();
    const currentSelectedKb = knowledgeBases.find(
      (kb) => kb.kb_id === selectedManageKbId,
    );
    const chunksBecameAvailable =
      currentSelectedKb &&
      currentSelectedKb.status === "processing" &&
      Number(previousSelectedKb?.progress || 0) < 15 &&
      Number(currentSelectedKb.progress || 0) >= 15;
    const buildFinished =
      previousSelectedKb?.status === "processing" &&
      currentSelectedKb &&
      currentSelectedKb.status !== "processing";
    if (chunksBecameAvailable || buildFinished) {
      loadKnowledgeBaseChunks(selectedManageKbId);
    }
  } catch (error) {
    console.error("[KB] 获取知识库列表失败:", error);
    if (manageKbList) {
      manageKbList.innerHTML =
        '<div class="kb-manage-empty">知识库加载失败</div>';
      delete manageKbList.dataset.renderKey;
    }
    if (agentKbSelectorList)
      agentKbSelectorList.innerHTML =
        '<span class="kb-chip-empty">知识库加载失败</span>';
    updateKbSelectedCount();
  }
}

function kbStatusLabel(status) {
  return (
    {
      uploaded: "待构建",
      processing: "构建中...",
      active: "已激活",
      failed: "构建失败",
    }[status] ||
    status ||
    "未知"
  );
}

function formatKbCreatedAt(value) {
  if (!value) return "—";
  return String(value)
    .replace("T", " ")
    .replace(/\.\d+Z?$/, "")
    .slice(0, 19);
}

function renderKbDetail(kb) {
  if (!kbDetailArea || !detailKbName || !detailKbCount || !kbChunkList) return;
  if (!kb) {
    kbChunkAbortController?.abort();
    detailKbName.textContent = "请在左侧选择知识库";
    detailKbCount.textContent = "";
    delete kbChunkList.dataset.kbId;
    kbChunkList.innerHTML =
      '<div class="kb-empty-tip">点击左侧列表查看具体的切片内容...</div>';
    return;
  }

  detailKbName.textContent = kb.kb_name || kb.kb_id;
  if (kbChunkList.dataset.kbId !== kb.kb_id) {
    detailKbCount.textContent = "";
    kbChunkList.dataset.kbId = kb.kb_id;
    kbChunkList.innerHTML = '<div class="kb-empty-tip">正在加载切片...</div>';
  }
}

function renderChunkCard(item) {
  const hierarchy = [item.level_1, item.level_2, item.level_3, item.level_4]
    .filter(Boolean)
    .map(escapeHtml)
    .join(" / ");
  const findings = item.imaging_findings
    ? `<div class="chunk-field"><span>影像学表现</span><p>${escapeHtml(item.imaging_findings)}</p></div>`
    : "";
  const opinion = item.imaging_opinion
    ? `<div class="chunk-field chunk-opinion"><span>影像学意见</span><p>${escapeHtml(item.imaging_opinion)}</p></div>`
    : "";
  const fallbackText =
    !findings && !opinion && item.combined_text
      ? `<div class="chunk-field"><span>切片原文</span><p>${escapeHtml(item.combined_text)}</p></div>`
      : "";
  const sourceParts = [
    item.source_file,
    item.sheet_name ? `Sheet: ${item.sheet_name}` : "",
    item.source_row ? `第 ${item.source_row} 行` : "",
  ].filter(Boolean);

  return `<article class="chunk-card">
    <div class="chunk-card-header">
      <div class="chunk-card-title">
        <span class="chunk-index">#${Number(item.chunk_index) || 0}</span>
        <strong>${escapeHtml(item.module_name || "未命名切片")}</strong>
      </div>
      ${item.modality ? `<span class="chunk-modality">${escapeHtml(item.modality)}</span>` : ""}
    </div>
    ${hierarchy ? `<div class="chunk-hierarchy">${hierarchy}</div>` : ""}
    ${findings}${opinion}${fallbackText}
    ${sourceParts.length ? `<div class="chunk-source">来源：${sourceParts.map(escapeHtml).join(" · ")}</div>` : ""}
  </article>`;
}

async function loadKnowledgeBaseChunks(kbId, page = 1, size = 50) {
  if (!kbChunkList || !detailKbCount) return;
  kbChunkAbortController?.abort();
  const controller = new AbortController();
  kbChunkAbortController = controller;
  kbChunkList.dataset.kbId = kbId;
  kbChunkList.innerHTML =
    '<div class="kb-empty-tip"><span class="spinner-icon"></span> 正在加载切片...</div>';
  detailKbCount.textContent = "";

  try {
    const resp = await fetch(
      `${API_BASE}/kbs/${encodeURIComponent(kbId)}/chunks?page=${page}&size=${size}`,
      { cache: "no-store", signal: controller.signal },
    );
    const payload = await resp.json();
    if (!resp.ok || payload.code !== 0) {
      throw new Error(payload.detail || "切片加载失败");
    }
    if (selectedManageKbId !== kbId) return;

    const data = payload.data || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const total = Number(data.total) || 0;
    detailKbCount.textContent = `共 ${total} 个切片${total > items.length ? ` · 当前显示 ${items.length} 个` : ""}`;
    kbChunkList.innerHTML = items.length
      ? items.map(renderChunkCard).join("")
      : '<div class="kb-empty-tip">暂无切片数据，请构建或重新构建该知识库。</div>';
  } catch (error) {
    if (error.name === "AbortError" || selectedManageKbId !== kbId) return;
    detailKbCount.textContent = "";
    kbChunkList.innerHTML = `<div class="kb-empty-tip error">切片加载失败：${escapeHtml(error.message)}</div>`;
  } finally {
    if (kbChunkAbortController === controller) kbChunkAbortController = null;
  }
}

function renderManageKbList(data = knowledgeBases) {
  if (!manageKbList) return;

  if (
    selectedManageKbId &&
    !data.some((kb) => kb.kb_id === selectedManageKbId)
  ) {
    selectedManageKbId = null;
  }

  if (!data.length) {
    selectedManageKbId = null;
    if (manageKbList.dataset.renderKey !== "empty") {
      manageKbList.innerHTML =
        '<div class="kb-manage-empty">暂无知识库，请先上传 Excel</div>';
      manageKbList.dataset.renderKey = "empty";
    }
    renderKbDetail(null);
    return;
  }

  const renderKey = JSON.stringify({
    selectedManageKbId,
    items: data.map((kb) => [
      kb.kb_id,
      kb.kb_name,
      kb.status,
      kb.progress,
      kb.created_at,
    ]),
  });
  if (manageKbList.dataset.renderKey === renderKey) {
    renderKbDetail(data.find((kb) => kb.kb_id === selectedManageKbId));
    return;
  }

  const listMarkup = data
    .map((kb) => {
      const status = kb.status || "uploaded";
      const buildLabel = status === "uploaded" ? "构建" : "重新构建";
      const buildButton = `<button class="kb-card-btn primary kb-build-btn" data-kb-id="${escapeHtml(kb.kb_id)}">${buildLabel}</button>`;
      const deleteButton = `<button class="kb-card-btn danger kb-delete-btn" data-kb-id="${escapeHtml(kb.kb_id)}">删除</button>`;
      const parsedProgress = Number.parseFloat(kb.progress);
      const progressText = Number.isFinite(parsedProgress)
        ? `构建中... ${Math.min(100, Math.max(0, Math.round(parsedProgress)))}%`
        : "构建中，请稍候...";

      let actionContent = deleteButton;
      if (status === "uploaded" || status === "active") {
        actionContent = `${buildButton}${deleteButton}`;
      } else if (status === "failed") {
        actionContent = `<span class="kb-action-failed">构建失败</span>${buildButton}${deleteButton}`;
      } else if (status === "processing") {
        actionContent = `<div class="kb-action-processing">
        <span class="spinner-icon" aria-hidden="true"></span>
        <span>${progressText}</span>
      </div>`;
      }

      return `<div class="manage-kb-card ${selectedManageKbId === kb.kb_id ? "is-selected" : ""}"
      data-kb-id="${escapeHtml(kb.kb_id)}" role="button" tabindex="0">
      <div class="manage-kb-main">
        <div class="manage-kb-name" title="${escapeHtml(kb.kb_name || kb.kb_id)}">${escapeHtml(kb.kb_name || kb.kb_id)}</div>
        <div class="manage-kb-meta">
          <span class="kb-status-badge kb-status-${status}">${kbStatusLabel(status)}</span>
          <span title="${escapeHtml(kb.kb_id)}">${escapeHtml(kb.kb_id.slice(0, 8))}...</span>
        </div>
      </div>
      <div class="manage-kb-actions">${actionContent}</div>
    </div>`;
    })
    .join("");

  const previousScrollTop = manageKbList.scrollTop;
  manageKbList.innerHTML = listMarkup;
  manageKbList.dataset.renderKey = renderKey;
  manageKbList.scrollTop = previousScrollTop;

  manageKbList.querySelectorAll(".manage-kb-card").forEach((card) => {
    const selectCard = () => {
      const kbId = card.dataset.kbId;
      const sameKb = selectedManageKbId === kbId;
      selectedManageKbId = kbId;
      renderManageKbList(knowledgeBases);
      // 重复点击同一知识库且已有请求在途时跳过，避免中止旧请求产生 ERR_ABORTED
      if (sameKb && kbChunkAbortController && kbChunkList?.dataset.kbId === kbId) {
        return;
      }
      loadKnowledgeBaseChunks(selectedManageKbId);
    };
    card.addEventListener("click", selectCard);
    card.addEventListener("keydown", (event) => {
      if (
        event.target === card &&
        (event.key === "Enter" || event.key === " ")
      ) {
        event.preventDefault();
        selectCard();
      }
    });
  });
  manageKbList.querySelectorAll(".kb-build-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      startKnowledgeBaseBuild(button.dataset.kbId);
    });
  });
  manageKbList.querySelectorAll(".kb-delete-btn").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteKnowledgeBase(button.dataset.kbId);
    });
  });

  renderKbDetail(data.find((kb) => kb.kb_id === selectedManageKbId));
}

function renderAgentKbSelector(data = knowledgeBases) {
  if (!agentKbSelectorList) return;
  const active = data.filter((kb) => kb.status === "active");
  const activeIds = new Set(active.map((kb) => kb.kb_id));
  const validSelectedIds = new Set(
    [...selectedKnowledgeBaseIds].filter((kbId) => activeIds.has(kbId)),
  );
  if (
    validSelectedIds.size !== selectedKnowledgeBaseIds.size ||
    [...validSelectedIds].some((kbId) => !selectedKnowledgeBaseIds.has(kbId))
  ) {
    selectedKnowledgeBaseIds = validSelectedIds;
    persistSelectedKnowledgeBaseIds();
  }
  updateKbSelectedCount();

  if (!active.length) {
    agentKbSelectorList.innerHTML =
      '<span class="kb-chip-empty">暂无已激活知识库，请先前往知识库页面构建</span>';
    return;
  }

  agentKbSelectorList.innerHTML = active
    .map(
      (kb) => `
    <label class="kb-chip-item" title="${escapeHtml(kb.kb_id)}">
      <input type="checkbox" value="${escapeHtml(kb.kb_id)}"
        ${selectedKnowledgeBaseIds.has(kb.kb_id) ? "checked" : ""}>
      <span>${escapeHtml(kb.kb_name || kb.kb_id)}</span>
    </label>`,
    )
    .join("");

  agentKbSelectorList
    .querySelectorAll('input[type="checkbox"]')
    .forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) selectedKnowledgeBaseIds.add(input.value);
        else selectedKnowledgeBaseIds.delete(input.value);
        persistSelectedKnowledgeBaseIds();
        updateKbSelectedCount();
      });
    });
}

function updateKbSelectedCount() {
  const count = selectedKnowledgeBaseIds.size;
  if (kbSelectedCount) {
    kbSelectedCount.textContent = String(count);
    kbSelectedCount.classList.toggle("is-empty", count === 0);
  }
  if (btnOpenKbSelector) {
    btnOpenKbSelector.classList.toggle("has-no-selection", count === 0);
  }
}

function openAgentKbSelector() {
  if (!agentKbModal) return;
  renderAgentKbSelector(knowledgeBases);
  agentKbModal.hidden = false;
  agentKbModal.setAttribute("aria-hidden", "false");
  btnCloseKbSelector?.focus();
}

function closeAgentKbSelector() {
  if (!agentKbModal) return;
  agentKbModal.hidden = true;
  agentKbModal.setAttribute("aria-hidden", "true");
  btnOpenKbSelector?.focus();
}

btnOpenKbSelector?.addEventListener("click", openAgentKbSelector);
btnCloseKbSelector?.addEventListener("click", closeAgentKbSelector);
btnConfirmKbSelector?.addEventListener("click", closeAgentKbSelector);
agentKbModalOverlay?.addEventListener("click", closeAgentKbSelector);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && agentKbModal && !agentKbModal.hidden) {
    closeAgentKbSelector();
  }
});

function updateKBPolling() {
  const hasProcessing = knowledgeBases.some((kb) => kb.status === "processing");
  if (hasProcessing && !kbPollTimer) {
    kbPollTimer = setInterval(refreshKnowledgeBases, 4000);
  } else if (!hasProcessing && kbPollTimer) {
    clearInterval(kbPollTimer);
    kbPollTimer = null;
  }
}

async function startKnowledgeBaseBuild(kbId) {
  try {
    const resp = await fetch(
      `${API_BASE}/kb/${encodeURIComponent(kbId)}/build`,
      { method: "POST" },
    );
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    if (!kbPollTimer) kbPollTimer = setInterval(refreshKnowledgeBases, 4000);
    appendKBLog(`已提交构建: ${kbId}`, "info");
    await refreshKnowledgeBases();
  } catch (error) {
    appendKBLog(`构建提交失败: ${error.message}`, "error");
  }
}

async function deleteKnowledgeBase(kbId) {
  if (!confirm("确定删除该知识库及其 Milvus 数据吗？")) return;
  try {
    const resp = await fetch(`${API_BASE}/kb/${encodeURIComponent(kbId)}`, {
      method: "DELETE",
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    selectedKnowledgeBaseIds.delete(kbId);
    persistSelectedKnowledgeBaseIds();
    if (selectedManageKbId === kbId) selectedManageKbId = null;
    await refreshKnowledgeBases();
  } catch (error) {
    appendKBLog(`删除失败: ${error.message}`, "error");
  }
}

function setKBBtnsDisabled(disabled) {
  if (btnSidebarUpload) btnSidebarUpload.disabled = disabled;
  if (btnBuildIncremental) btnBuildIncremental.disabled = disabled;
  if (btnBuildRebuild) btnBuildRebuild.disabled = disabled;
  if (btnExtractMeta) btnExtractMeta.disabled = disabled;
  if (disabled) {
    [btnSidebarUpload, btnBuildIncremental, btnBuildRebuild, btnExtractMeta]
      .filter(Boolean)
      .forEach((button) => button.classList.add("btn-disabled"));
  } else {
    [btnSidebarUpload, btnBuildIncremental, btnBuildRebuild, btnExtractMeta]
      .filter(Boolean)
      .forEach((button) => button.classList.remove("btn-disabled"));
  }
}

async function runKBBuild(rebuild) {
  // 全量重建需要确认
  if (rebuild) {
    const confirmed = confirm(
      "全量重建将执行以下操作：\n\n" +
        "1. 清空所有已生成的切片文件\n" +
        "2. 重新对 xlsx 文件进行切片\n" +
        "3. 删除向量数据库中的所有数据\n" +
        "4. 重新向量化并入库\n\n" +
        "此操作不可逆，是否继续？",
    );
    if (!confirmed) {
      return;
    }
  }

  if (kbAbortController) {
    kbAbortController.abort();
  }
  kbAbortController = new AbortController();

  setKBBtnsDisabled(true);
  kbLog.style.display = "block";
  kbLogContent.innerHTML = "";

  const mode = rebuild ? "全量重建" : "增量构建";
  appendKBLog(`[${mode}] 开始执行...`, "info");

  try {
    const resp = await fetch(`${API_BASE}/kb/build`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rebuild, batch_size: 16 }),
      signal: kbAbortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const payload = line.slice(6);
          if (payload === "[DONE]") {
            appendKBLog("✅ 构建完成", "done");
            await refreshKBStatus();
            return;
          }
          try {
            const event = JSON.parse(payload);
            const level = event.level || "info";
            appendKBLog(event.msg || "", level);
          } catch (e) {
            appendKBLog(payload, "info");
          }
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      appendKBLog("⚠️ 操作已取消", "error");
    } else {
      appendKBLog(`❌ 错误: ${e.message}`, "error");
    }
  } finally {
    setKBBtnsDisabled(false);
    kbAbortController = null;
  }
}

async function runExtractMetadata() {
  if (kbAbortController) {
    kbAbortController.abort();
  }
  kbAbortController = new AbortController();

  setKBBtnsDisabled(true);
  kbLog.style.display = "block";
  kbLogContent.innerHTML = "";
  appendKBLog("[提取元数据] 开始执行...", "info");

  try {
    const resp = await fetch(`${API_BASE}/kb/extract-metadata`, {
      method: "POST",
      signal: kbAbortController.signal,
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const payload = line.slice(6);
          if (payload === "[DONE]") {
            appendKBLog("✅ 提取完成", "done");
            await refreshKBStatus();
            return;
          }
          try {
            const event = JSON.parse(payload);
            const level = event.level || "info";
            appendKBLog(event.msg || "", level);
          } catch (e) {
            appendKBLog(payload, "info");
          }
        }
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      appendKBLog("⚠️ 操作已取消", "error");
    } else {
      appendKBLog(`❌ 错误: ${e.message}`, "error");
    }
  } finally {
    setKBBtnsDisabled(false);
    kbAbortController = null;
  }
}

function appendKBLog(msg, level) {
  // 进度类消息：覆盖上一条进度，而不是追加
  if (msg.includes("向量化进度")) {
    const lastProgress = kbLogContent.querySelector(".kb-log-progress");
    if (lastProgress) {
      lastProgress.textContent = msg;
      lastProgress.className = `kb-log-line kb-log-${level} kb-log-progress`;
      kbLogContent.scrollTop = kbLogContent.scrollHeight;
      return;
    }
  }

  const div = document.createElement("div");
  div.className = `kb-log-line kb-log-${level}`;
  if (msg.includes("向量化进度")) {
    div.classList.add("kb-log-progress");
  }
  div.textContent = msg;
  kbLogContent.appendChild(div);
  kbLogContent.scrollTop = kbLogContent.scrollHeight;
}

if (btnBuildIncremental)
  btnBuildIncremental.addEventListener("click", () => runKBBuild(false));
if (btnBuildRebuild)
  btnBuildRebuild.addEventListener("click", () => runKBBuild(true));
if (btnExtractMeta)
  btnExtractMeta.addEventListener("click", () => runExtractMetadata());

// 上传 xlsx，上传完成后保持待构建状态
btnSidebarUpload.addEventListener("click", () => xlsxInput.click());
xlsxInput.addEventListener("change", () => {
  if (xlsxInput.files.length > 0) {
    runUploadXlsx(Array.from(xlsxInput.files));
  }
});

// 解耦上传：普通 JSON 响应，上传后不自动构建
async function runUploadXlsx(files) {
  setKBBtnsDisabled(true);
  kbLog.style.display = "block";
  kbLogContent.innerHTML = "";
  appendKBLog(
    `[上传] 已选择 ${files.length} 个 Excel 文件，正在保存...`,
    "info",
  );
  try {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    const resp = await fetch(`${API_BASE}/kb/upload`, {
      method: "POST",
      body: formData,
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    (data.knowledge_bases || []).forEach((kb) => {
      appendKBLog(
        `✅ ${kb.kb_name} 已上传，状态：待构建，kb_id=${kb.kb_id}`,
        "done",
      );
    });
    await refreshKnowledgeBases();
  } catch (error) {
    appendKBLog(`❌ 上传失败: ${error.message}`, "error");
  } finally {
    xlsxInput.value = "";
    setKBBtnsDisabled(false);
  }
}

// 取消当前 SSE 请求
function cancelCurrentRequest() {
  if (currentReader) {
    try {
      currentReader.cancel();
    } catch (e) {
      /* ignore */
    }
    currentReader = null;
  }
  if (chatAbortController) {
    try {
      chatAbortController.abort();
    } catch (e) {
      /* ignore */
    }
    chatAbortController = null;
  }
  setProcessing(false);
}

function createEmptyState() {
  const div = document.createElement("div");
  div.className = "empty-state";
  div.innerHTML = `
    <div class="empty-icon">📋</div>
    <p>输入CT/MRI等检查关键词，生成结构化影像报告</p>
    <p class="hint">例如：CT脑出血、MRI膝关节、脑梗</p>
  `;
  return div;
}

async function sendMessage(selectedDiagnosis) {
  const query = userInput.value.trim();
  if (!query || isProcessing) return;

  const selected_kbs = [...selectedKnowledgeBaseIds];
  if (!selected_kbs.length) {
    addSystemMessage("请至少选择一个知识库");
    updateStatus("⚠️ 请先选择知识库");
    return;
  }

  console.log("[DEBUG] sendMessage 开始, query:", query);

  // 先取消上一个未清理的请求
  cancelCurrentRequest();

  const es = chatContainer.querySelector(".empty-state");
  if (es) es.remove();

  addUserMessage(query);
  userInput.value = "";
  setProcessing(true);

  // 思考过程容器
  const thinking = addThinkingContainer();
  addThinkingStep(thinking, "👤", "用户提问", query);
  const assistantStream = createAssistantStreamState();
  console.log("[DEBUG] thinking container 已创建");

  // 创建 AbortController（60秒超时）
  chatAbortController = new AbortController();
  const timeoutId = setTimeout(() => {
    chatAbortController.abort();
  }, 60000);
  let streamDone = false;

  try {
    console.log("[DEBUG] 开始 fetch POST /api/chat");
    const response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      keepalive: false,
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: query,
        session_id: SESSION_ID,
        selected_diagnosis: selectedDiagnosis || null,
        selected_kbs,
      }),
      signal: chatAbortController.signal,
    });

    clearTimeout(timeoutId);
    console.log("[DEBUG] fetch 响应状态:", response.status);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    currentReader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventCount = 0;

    console.log("[DEBUG] 开始读取 SSE 流...");
    while (true) {
      const { done, value } = await currentReader.read();
      if (done) {
        console.log("[DEBUG] 流读取完成 (done=true)");
        streamDone = true;
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const data = line.slice(6);
          if (data === "[DONE]") {
            console.log("[DEBUG] 收到 [DONE], 共处理事件数:", eventCount);
            completeAssistantStream(assistantStream);
            streamDone = true;
            break;
          }
          try {
            const event = JSON.parse(data);
            eventCount++;
            console.log(
              "[DEBUG] 事件 #" + eventCount + " type=" + event.type,
              event,
            );
            handleStreamEvent(event, thinking, assistantStream);
          } catch (e) {
            console.warn("[DEBUG] JSON 解析失败:", data.substring(0, 100), e);
          }
        }
      }
      if (streamDone) break;
    }
    completeAssistantStream(assistantStream);
  } catch (e) {
    console.error("[DEBUG] 请求异常:", e.name, e.message);
    if (e.name === "AbortError") {
      addSystemMessage("⏱️ 请求超时，已自动取消");
    } else {
      addSystemMessage(`❌ 请求失败: ${e.message}`);
    }
    if (thinking && thinking.parentNode) {
      thinking.remove();
    }
    abortAssistantStream(assistantStream);
  } finally {
    clearTimeout(timeoutId);

    // 关键修复：仅在流未自然完成时 cancel。
    // 对已 done 的流调用 cancel 会破坏浏览器连接池回收。
    if (currentReader && !streamDone) {
      console.log(
        "[DEBUG] finally: 取消 reader (streamDone=" + streamDone + ")",
      );
      try {
        await currentReader.cancel();
      } catch (e) {
        /* ignore */
      }
    }

    currentReader = null;
  }

  chatAbortController = null;
  setProcessing(false);
  updateStatus("就绪");

  // 自动保存对话到历史记录
  if (!currentConversationId) {
    currentConversationId = generateConvId();
    const history = getChatHistory();
    history.unshift({
      id: currentConversationId,
      session_id: SESSION_ID,
      title: getConversationTitleFromChat(),
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    });
    saveChatHistory(history);
  }
  console.log(">>> 准备调用 saveCurrentConversation <<<");
  saveCurrentConversation();
  console.log(">>> saveCurrentConversation 调用完成 <<<");
  renderChatHistoryList();

  console.log("[DEBUG] sendMessage 结束");
}

function handleStreamEvent(event, thinking, assistantStream) {
  if (assistantStream && event.request_id) {
    if (
      assistantStream.requestId &&
      assistantStream.requestId !== event.request_id
    ) {
      console.warn(
        "[Prompt Trace] 当前流收到不一致的 request_id:",
        assistantStream.requestId,
        event.request_id,
      );
    } else {
      assistantStream.requestId = event.request_id;
    }
  }

  switch (event.type) {
    case "status":
      updateStatusText(event.message);
      break;

    case "intent":
      addThinkingStep(thinking, "🎯", "意图识别", event.intent);
      break;

    case "query_rewrite":
      addThinkingStep(
        thinking,
        "🔄",
        "查询改写",
        `${event.original} → ${event.rewritten}`,
      );
      break;

    case "context_resolve":
      addThinkingStep(
        thinking,
        "🔗",
        "上下文消解",
        `${event.original} → ${event.resolved}`,
      );
      break;

    case "search":
      addThinkingStep(thinking, "🔍", "检索", `正在检索: ${event.query}`);
      break;

    case "recall":
      addRecallDetail(thinking, event);
      break;

    case "rerank":
      addRerankDetail(thinking, event);
      collectAssistantReferences(assistantStream, event.results);
      break;

    case "search_result":
      addSearchResultDetail(thinking, event);
      break;

    case "token":
      appendAssistantToken(assistantStream, event.content);
      break;

    case "reasoning":
      addReasoningStep(thinking, event.text);
      break;

    case "prompt_trace":
      assistantStream.requestId = event.request_id || assistantStream.requestId;
      assistantStream.promptTrace = event;
      bindPromptTraceToMessage(assistantStream, event);
      break;

    case "report":
      finishThinking(thinking);
      completeAssistantStream(assistantStream, event.content, "📑 结构化报告");
      break;

    case "message":
      finishThinking(thinking);
      completeAssistantStream(assistantStream, event.content, "💬 助手");
      break;

    case "error":
      finishThinking(thinking);
      abortAssistantStream(assistantStream);
      addSystemMessage(`❌ ${event.message}`);
      break;

    case "entity_update":
      addThinkingStep(thinking, "📋", "实体更新", JSON.stringify(event.slots));
      break;

    case "intent_switch":
      addThinkingStep(thinking, "🔄", "切换意图", event.message);
      break;

    case "memory_retrieval":
      addMemoryRetrievalDetail(thinking, event);
      break;

    case "tool_executed":
      addToolExecutedDetail(thinking, event);
      break;

    case "ambiguous":
      console.log(
        "[DEBUG] ambiguous 事件触发, options数量=",
        (event.options || []).length,
      );
      finishThinking(thinking);
      addAmbiguousOptions(event);
      break;

    case "context_usage":
      updateContextUsage(event.percent || 0);
      break;

    case "done":
      finishThinking(thinking);
      completeAssistantStream(assistantStream);
      // 在思考容器底部追加耗时信息（即使已折叠也会保留）
      if (thinking) {
        const timeInfo = document.createElement("div");
        timeInfo.className = "thinking-time-info";
        timeInfo.style.cssText =
          "padding:4px 12px;font-size:12px;color:#666;border-top:1px solid #e8e8e8;text-align:right;";
        timeInfo.textContent = `⏱️ 总耗时：${event.total_time}秒`;
        thinking.appendChild(timeInfo);
      }
      break;
  }
}

// ========== 思考过程容器 ==========

function addThinkingContainer() {
  const container = document.createElement("div");
  container.className = "thinking-container thinking-collapsed";
  container.innerHTML = `
    <div class="thinking-header" onclick="this.parentElement.classList.toggle('thinking-collapsed')">
      <span class="thinking-toggle">▼</span>
      <span>🧠 Agent 思考过程</span>
      <span class="thinking-status">进行中...</span>
    </div>
    <div class="thinking-body" id="thinkingBody"></div>
  `;
  chatContainer.appendChild(container);
  scrollToBottom();
  return container;
}

function addThinkingStep(container, icon, label, detail) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step";
  step.innerHTML = `
    <span class="thinking-step-icon">${icon}</span>
    <span class="thinking-step-label">${label}:</span>
    <span class="thinking-step-detail">${escapeHtml(detail)}</span>
  `;
  body.appendChild(step);
  scrollToBottom();
}

function addRecallDetail(container, data) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step thinking-recall";
  step.innerHTML = `
    <span class="thinking-step-icon">📊</span>
    <span class="thinking-step-label">多路召回详情</span>
    <div class="thinking-detail-block">
      <div>路径1（向量检索）: ${data.vector_count} 条</div>
      <div>路径2（元数据过滤）: ${data.metadata_count} 条</div>
      <div>路径3（关键词检索）: ${data.keyword_count} 条</div>
      <div class="thinking-highlight">合并去重: ${data.total_before} 条 → ${data.total_after} 条（去重 ${data.dedup} 条）</div>
    </div>
  `;
  body.appendChild(step);
  scrollToBottom();
}

function addRerankDetail(container, data) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step thinking-rerank";
  let items = "";
  data.results.forEach((r, i) => {
    const textId = "rerank_text_" + Math.random().toString(36).slice(2, 8);
    items += `
      <div class="rerank-item">
        <span class="rerank-rank">[${i + 1}]</span>
        <span class="rerank-score">${(r.score * 100).toFixed(1)}%</span>
        <span class="rerank-source">${escapeHtml(r.source)}</span>
        <span class="rerank-diagnosis">${escapeHtml(r.diagnosis)}</span>
      </div>
      <div class="rerank-text-toggle" onclick="
        var el=document.getElementById('${textId}');
        var btn=document.getElementById('${textId}_btn');
        if(el.classList.toggle('hidden')){
          btn.textContent='展开内容 ▸';
        }else{
          btn.textContent='收起内容 ▾';
        }
      " id="${textId}_btn">展开内容 ▸</div>
      <div class="rerank-text-content hidden" id="${textId}">
        <pre>${escapeHtml(r.text)}</pre>
      </div>`;
  });
  step.innerHTML = `
    <span class="thinking-step-icon">🎯</span>
    <span class="thinking-step-label">Rerank 重排序（top-${data.results.length}）</span>
    <div class="thinking-detail-block rerank-list">${items}</div>
  `;
  body.appendChild(step);
  scrollToBottom();
}

function addSearchResultDetail(container, event) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step thinking-search-result";
  const id = "sr_" + Math.random().toString(36).slice(2, 8);
  step.innerHTML = `
    <span class="thinking-step-icon">📋</span>
    <span class="thinking-step-label">检索结果内容</span>
    <span class="thinking-collapse-btn" onclick="document.getElementById('${id}').classList.toggle('hidden')">
      展开/收起
    </span>
    <div class="thinking-detail-block search-result-content hidden" id="${id}">
      <pre>${escapeHtml(event.result)}</pre>
    </div>
  `;
  body.appendChild(step);
  scrollToBottom();
}

function addReasoningStep(container, text) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step thinking-reasoning";
  const id = "reasoning_" + Math.random().toString(36).slice(2, 8);

  if (text.length > 300) {
    const preview = text.slice(0, 300) + "...";
    step.innerHTML = `
      <span class="thinking-step-icon">💭</span>
      <span class="thinking-step-label">模型推理</span>
      <span class="thinking-collapse-btn" onclick="
        var f=document.getElementById('${id}');
        var p=document.getElementById('${id}_preview');
        var b=document.getElementById('${id}_btn');
        if(f.classList.toggle('hidden')){
          p.classList.remove('hidden');
          b.textContent='展开全文';
        }else{
          p.classList.add('hidden');
          b.textContent='收起';
        }
      " id="${id}_btn">收起</span>
      <div class="thinking-detail-block reasoning-preview hidden" id="${id}_preview">${escapeHtml(preview)}</div>
      <div class="thinking-detail-block reasoning-full" id="${id}">${escapeHtml(text)}</div>
    `;
  } else {
    step.innerHTML = `
      <span class="thinking-step-icon">💭</span>
      <span class="thinking-step-label">模型推理</span>
      <div class="thinking-detail-block reasoning-full">${escapeHtml(text)}</div>
    `;
  }

  body.appendChild(step);
  scrollToBottom();
}

function finalizeThinking(container) {
  const status = container.querySelector(".thinking-status");
  if (status) status.textContent = "✅ 完成";
  container.classList.add("thinking-done");
  scrollToBottom();
}

// ========== 消息气泡 ==========

function addUserMessage(text) {
  const msg = document.createElement("div");
  msg.className = "message user";
  msg.innerHTML = `
    <div class="message-role">你</div>
    <div class="bubble">${escapeHtml(text)}</div>
  `;
  chatContainer.appendChild(msg);
  scrollToBottom();
}

function addReportMessage(content) {
  const msg = document.createElement("div");
  msg.className = "message assistant";
  msg.innerHTML = `
    <div class="message-role">📝 结构化报告</div>
    <div class="bubble">${renderMarkdown(content)}</div>
  `;
  chatContainer.appendChild(msg);
  scrollToBottom();
}

function addSystemMessage(text) {
  const msg = document.createElement("div");
  msg.className = "message system";
  msg.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  chatContainer.appendChild(msg);
  scrollToBottom();
}

// ========== 记忆检索详情 ==========

function addMemoryRetrievalDetail(container, data) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step thinking-memory";

  let html = `<span class="thinking-step-icon">🧠</span>
    <span class="thinking-step-label">记忆检索 <span style="color:#888;font-size:11px">(基于 "${escapeHtml(data.query || "")}")</span></span>
    <div class="thinking-detail-block">`;

  if (data.stm && data.stm.length > 0) {
    html += `<div class="thinking-highlight">💬 当前会话相关对话 (STM): ${data.stm.length} 条</div>`;
    data.stm.forEach(function (item) {
      var short = item.length > 80 ? item.slice(0, 80) + "..." : item;
      html += `<div style="margin-left:12px;color:#059669;">• ${escapeHtml(short)}</div>`;
    });
  } else {
    html += `<div style="color:#888;">💬 当前会话相关对话 (STM): 无</div>`;
  }

  html += `</div>`;
  step.innerHTML = html;
  body.appendChild(step);
  scrollToBottom();
}

// ========== 工具执行详情 ==========

function addToolExecutedDetail(container, data) {
  const body = container.querySelector(".thinking-body");
  const step = document.createElement("div");
  step.className = "thinking-step thinking-tool";

  var toolIcon = "🔧";
  var toolLabel = data.tool;
  if (data.tool === "rag_search") {
    toolIcon = "🔍";
    toolLabel = "RAG 检索";
  } else if (data.tool === "edit_report") {
    toolIcon = "✏️";
    toolLabel = "编辑报告";
  } else if (data.tool === "refine_report") {
    toolIcon = "🔄";
    toolLabel = "精炼报告";
  }

  var paramsHtml = "";
  if (data.params) {
    var keys = Object.keys(data.params);
    keys.forEach(function (k) {
      var v = data.params[k];
      if (typeof v === "string" && v.length > 100) {
        v = v.slice(0, 100) + "...";
      }
      paramsHtml += `<div style="margin-left:12px;color:#666;">• ${escapeHtml(k)}: ${escapeHtml(String(v))}</div>`;
    });
  }

  step.innerHTML = `
    <span class="thinking-step-icon">${toolIcon}</span>
    <span class="thinking-step-label">工具执行: ${toolLabel}</span>
    <div class="thinking-detail-block">
      ${paramsHtml}
      <div class="thinking-highlight">结果长度: ${data.result_length} 字符 ${data.is_final ? "✅ 最终结果" : "⏳ 继续处理"}</div>
    </div>
  `;
  body.appendChild(step);
  scrollToBottom();
}

// ========== 上下文使用率 ==========

function updateContextUsage(percent) {
  const usageEl = document.getElementById("contextUsage");
  const percentEl = document.getElementById("contextPercent");

  if (!usageEl || !percentEl) return;

  usageEl.style.display = "inline-block";
  percentEl.textContent = Math.round(percent);

  // 根据使用率切换样式
  usageEl.classList.remove("warning", "danger");
  if (percent >= 80) {
    usageEl.classList.add("danger");
  } else if (percent >= 60) {
    usageEl.classList.add("warning");
  }
}

// ========== 歧义选项 ==========

function addAmbiguousOptions(data) {
  console.log("[DEBUG] addAmbiguousOptions 被调用, options=", data.options);
  var question = data.question || "请选择：";
  var options = data.options || [];
  var scores = data.scores || [];

  var bubble = document.createElement("div");
  bubble.className = "message assistant";

  var optionsHtml = (options || [])
    .map(function (opt, i) {
      var scoreText = scores[i]
        ? ' <span style="color:#999;font-size:0.8em;">(' +
          (scores[i] * 100).toFixed(1) +
          "%)</span>"
        : "";
      return (
        '<button class="ambiguity-option" onclick="selectAmbiguityOption(\'' +
        escapeHtml(opt) +
        "')\">" +
        (i + 1) +
        ". " +
        escapeHtml(opt) +
        scoreText +
        "</button>"
      );
    })
    .join("");

  bubble.innerHTML = `
    <div class="bubble">
      <p>${escapeHtml(question)}</p>
      <div>${optionsHtml}</div>
      <p style="color:#999;font-size:0.8em;margin-top:8px;">如果以上没有匹配的诊断，请详细描述病情或直接输入完整检查信息（如 "CT 头颅 脑出血"）</p>
    </div>
  `;
  chatContainer.appendChild(bubble);
  scrollToBottom();
}

window.selectAmbiguityOption = function (option) {
  var input = document.getElementById("userInput");
  if (input) {
    cancelCurrentRequest();
    setProcessing(false);
    input.value = option;
    sendMessage(option);
  }
};

// ========== 工具函数 ==========

function setProcessing(processing) {
  isProcessing = processing;
  btnSend.disabled = processing;
  btnClear.disabled = processing;
  const btnText = btnSend.querySelector(".btn-text");
  const btnLoading = btnSend.querySelector(".btn-loading");
  if (processing) {
    if (btnText) btnText.style.display = "none";
    if (btnLoading) btnLoading.style.display = "inline";
    updateStatus("⏳ 处理中...");
  } else {
    if (btnText) btnText.style.display = "inline";
    if (btnLoading) btnLoading.style.display = "none";
  }
}

function updateStatus(text) {
  statusText.textContent = text;
}

function scrollToBottom() {
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function renderMarkdown(text) {
  let html = escapeHtml(text);

  // 标题
  html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
  html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");

  // 粗体
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // 段落
  html = html.replace(/\n\n/g, "</p><p>");
  html = html.replace(/\n/g, "<br>");

  if (!html.startsWith("<h")) {
    html = "<p>" + html + "</p>";
  }

  return html;
}

function updateStatusText(text) {
  statusText.textContent = text;
}

function createAssistantStreamState() {
  return {
    messageEl: null,
    roleEl: null,
    contentEl: null,
    referencesEl: null,
    rawText: "",
    references: [],
    referenceKeys: new Set(),
    completed: false,
    requestId: null,
    promptTrace: null,
    promptTraceActionsEl: null,
  };
}

function ensureStreamingAssistantBubble(state) {
  if (state.messageEl) return state.messageEl;

  const messageEl = document.createElement("div");
  messageEl.className = "message assistant is-streaming";

  const roleEl = document.createElement("div");
  roleEl.className = "message-role";
  roleEl.textContent = "💬 助手";

  const bubbleEl = document.createElement("div");
  bubbleEl.className = "bubble";

  const contentEl = document.createElement("div");
  contentEl.className = "assistant-stream-content";
  bubbleEl.appendChild(contentEl);
  messageEl.append(roleEl, bubbleEl);
  chatContainer.appendChild(messageEl);

  state.messageEl = messageEl;
  state.roleEl = roleEl;
  state.contentEl = contentEl;
  scrollToBottom();
  return messageEl;
}

function appendAssistantToken(state, content) {
  if (!state || typeof content !== "string" || !content) return;
  ensureStreamingAssistantBubble(state);
  state.completed = false;
  state.messageEl.classList.add("is-streaming");
  state.rawText += content;
  state.contentEl.innerHTML = renderMarkdown(state.rawText);
  scrollToBottom();
}

function collectAssistantReferences(state, results) {
  if (!state || !Array.isArray(results)) return;

  results.forEach((result) => {
    const reference = {
      source: String(result?.source ?? ""),
      diagnosis: String(result?.diagnosis ?? ""),
      text: String(result?.text ?? ""),
    };
    const key = `${reference.source}\u0000${reference.diagnosis}\u0000${reference.text}`;
    if (!state.referenceKeys.has(key)) {
      state.referenceKeys.add(key);
      state.references.push(reference);
    }
  });

  if (state.completed && state.messageEl) {
    renderAssistantReferences(state);
  }
}

function renderAssistantReferences(state) {
  if (!state?.messageEl || !state.references.length) return;

  state.referencesEl?.remove();
  const referencesEl = document.createElement("details");
  referencesEl.className = "answer-references";

  const summary = document.createElement("summary");
  summary.textContent = `参考切片 (${state.references.length})`;
  referencesEl.appendChild(summary);

  const list = document.createElement("div");
  list.className = "answer-reference-list";
  state.references.forEach((reference, index) => {
    const item = document.createElement("details");
    item.className = "answer-reference-item";

    const itemSummary = document.createElement("summary");
    const number = document.createElement("span");
    number.className = "reference-index";
    number.textContent = String(index + 1);
    const diagnosis = document.createElement("span");
    diagnosis.className = "reference-diagnosis";
    diagnosis.textContent = reference.diagnosis || `参考切片 ${index + 1}`;
    const source = document.createElement("span");
    source.className = "reference-source";
    source.textContent = reference.source
      ? `来源 ${reference.source}`
      : "来源未标注";
    itemSummary.append(number, diagnosis, source);

    const text = document.createElement("div");
    text.className = "reference-text";
    text.textContent = reference.text || "暂无切片原文";
    item.append(itemSummary, text);
    list.appendChild(item);
  });

  referencesEl.appendChild(list);
  state.messageEl.querySelector(".bubble")?.appendChild(referencesEl);
  state.referencesEl = referencesEl;
}

function bindPromptTraceToMessage(state, trace) {
  if (!state || !trace) return;
  ensureStreamingAssistantBubble(state);
  state.promptTrace = trace;
  state.requestId = trace.request_id || state.requestId;
  state.messageEl._promptTrace = trace;
  renderPromptTraceAction(state.messageEl, trace);
  state.promptTraceActionsEl = state.messageEl.querySelector(
    ".prompt-trace-actions",
  );
}

function renderPromptTraceAction(messageEl, trace) {
  if (!messageEl || !trace) return;
  const bubble = messageEl.querySelector(".bubble");
  if (!bubble) return;

  bubble.querySelector(".prompt-trace-actions")?.remove();
  const actions = document.createElement("div");
  actions.className = "prompt-trace-actions";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "btn btn-sm btn-outline prompt-trace-open";
  button.textContent = "查看本轮 Prompt";
  button.addEventListener("click", () => {
    openPromptTraceModal(messageEl._promptTrace || trace);
  });
  actions.appendChild(button);
  bubble.appendChild(actions);
}

function promptTraceDisplay(value, emptyValue = "-") {
  if (Array.isArray(value)) return value.length ? value.join("、") : emptyValue;
  if (value === null || value === undefined || value === "") return emptyValue;
  return String(value);
}

function addPromptTraceSection(body, title, rows) {
  const section = document.createElement("section");
  section.className = "prompt-trace-section";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const grid = document.createElement("dl");
  grid.className = "prompt-trace-grid";
  rows.forEach(([label, value]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = promptTraceDisplay(value);
    grid.append(term, detail);
  });
  section.append(heading, grid);
  body.appendChild(section);
}

function addFullPromptTraceSection(body, title, text, copyLabel) {
  const section = document.createElement("section");
  section.className = "prompt-trace-section prompt-trace-full";
  const header = document.createElement("div");
  header.className = "prompt-trace-section-header";
  const heading = document.createElement("h4");
  heading.textContent = title;
  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.className = "btn btn-sm btn-outline";
  copyButton.textContent = "复制";
  copyButton.addEventListener("click", () => copyApiText(text, copyLabel));
  const pre = document.createElement("pre");
  pre.textContent = text;
  header.append(heading, copyButton);
  section.append(header, pre);
  body.appendChild(section);
}

function ensurePromptTraceModal() {
  let modal = document.getElementById("promptTraceModal");
  if (modal) return modal;

  modal = document.createElement("div");
  modal.id = "promptTraceModal";
  modal.className = "agent-modal prompt-trace-modal";
  modal.hidden = true;
  modal.setAttribute("role", "dialog");
  modal.setAttribute("aria-modal", "true");
  modal.setAttribute("aria-labelledby", "promptTraceModalTitle");
  modal.innerHTML = `
    <div class="agent-modal-overlay" data-prompt-trace-close></div>
    <div class="agent-modal-dialog prompt-trace-dialog">
      <div class="agent-modal-header">
        <h3 id="promptTraceModalTitle">本轮 Report Prompt</h3>
        <button type="button" class="close-btn" data-prompt-trace-close aria-label="关闭">&times;</button>
      </div>
      <div class="agent-modal-body prompt-trace-body"></div>
    </div>
  `;
  modal.querySelectorAll("[data-prompt-trace-close]").forEach((element) => {
    element.addEventListener("click", () => {
      modal.hidden = true;
    });
  });
  document.body.appendChild(modal);
  return modal;
}

function openPromptTraceModal(trace) {
  if (!trace) return;
  const modal = ensurePromptTraceModal();
  const body = modal.querySelector(".prompt-trace-body");
  const context = trace.context || {};
  const rag = trace.rag || {};
  const rules = trace.rules || {};
  const prompt = trace.prompt || {};
  body.innerHTML = "";

  addPromptTraceSection(body, "Request", [
    ["Request ID", trace.request_id],
    ["Stage", trace.stage],
  ]);
  addPromptTraceSection(body, "Context", [
    ["Modality", context.modality],
    ["Body Parts", context.body_parts],
    ["Diagnosis", context.diagnosis],
  ]);
  addPromptTraceSection(body, "RAG", [
    ["Has Evidence", rag.has_evidence ? "Yes" : "No"],
    ["Result Count", rag.result_count],
    ["Evidence Chars", rag.evidence_chars],
  ]);
  addPromptTraceSection(body, "Rules", [
    ["Injected", rules.injected ? "Yes" : "No"],
    ["Normalized Modality", rules.normalized_modality],
    ["Normalized Body Parts", rules.normalized_body_parts],
    ["Rule IDs", rules.matched_rule_ids],
    ["Rule Files", rules.matched_rule_files],
    ["Unmatched", rules.unmatched_body_parts],
    ["Rule Chars", rules.content_chars],
    ["Rule Hash", rules.content_hash],
  ]);
  addPromptTraceSection(body, "Prompt", [
    ["System Prompt Chars", prompt.system_prompt_chars],
    ["User Message Chars", prompt.user_message_chars],
    ["Body-Part Rules", prompt.has_body_part_rules ? "Injected" : "Not Injected"],
    ["Last Report", prompt.has_last_report ? "Yes" : "No"],
  ]);

  if (Object.prototype.hasOwnProperty.call(prompt, "system_prompt")) {
    addFullPromptTraceSection(
      body,
      "完整 System Prompt",
      prompt.system_prompt || "",
      "System Prompt",
    );
  }
  if (Object.prototype.hasOwnProperty.call(prompt, "user_message")) {
    addFullPromptTraceSection(
      body,
      "完整 User Message / RAG Evidence",
      prompt.user_message || "",
      "User Message",
    );
  }

  modal.hidden = false;
  modal.querySelector(".close-btn")?.focus();
}

function completeAssistantStream(state, finalContent, roleLabel) {
  if (!state) return;
  if (typeof finalContent === "string") state.rawText = finalContent;
  if (!state.messageEl && !state.rawText) return;

  ensureStreamingAssistantBubble(state);
  if (roleLabel) state.roleEl.textContent = roleLabel;
  state.contentEl.innerHTML = renderMarkdown(state.rawText);
  state.messageEl.classList.remove("is-streaming");
  state.completed = true;
  renderAssistantReferences(state);
  if (state.promptTrace) bindPromptTraceToMessage(state, state.promptTrace);
  scrollToBottom();
}

function abortAssistantStream(state) {
  if (!state?.messageEl) return;
  state.messageEl.classList.remove("is-streaming");
  state.completed = true;
  renderAssistantReferences(state);
  if (state.promptTrace) bindPromptTraceToMessage(state, state.promptTrace);
}

function finishThinking(container) {
  const status = container.querySelector(".thinking-status");
  if (status) status.textContent = "✅ 完成";
  container.classList.add("thinking-collapsed", "thinking-done");
}

// ==================== 对话历史管理 ====================

const CHAT_HISTORY_KEY = "chat_history_list";
const CHAT_MESSAGES_PREFIX = "chat_messages_";

let currentConversationId = null;

function getChatHistory() {
  try {
    return JSON.parse(localStorage.getItem(CHAT_HISTORY_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function saveChatHistory(list) {
  localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(list));
}

function getChatMessages(convId) {
  try {
    return JSON.parse(
      localStorage.getItem(CHAT_MESSAGES_PREFIX + convId) || "[]",
    );
  } catch (e) {
    return [];
  }
}

function saveChatMessages(convId, messages) {
  localStorage.setItem(CHAT_MESSAGES_PREFIX + convId, JSON.stringify(messages));
}

function deleteChatMessages(convId) {
  localStorage.removeItem(CHAT_MESSAGES_PREFIX + convId);
}

function generateConvId() {
  // crypto.randomUUID() 仅在 HTTPS 或 localhost 下可用
  if (crypto && crypto.randomUUID) {
    return "conv_" + crypto.randomUUID();
  }
  return (
    "conv_" +
    Date.now().toString(36) +
    "_" +
    Math.random().toString(36).substr(2, 9) +
    Math.random().toString(36).substr(2, 9)
  );
}

function saveCurrentConversation() {
  console.log(
    "[DEBUG] saveCurrentConversation 被调用, currentConversationId=",
    currentConversationId,
  );
  const messages = [];
  chatContainer.querySelectorAll(".message").forEach((msg) => {
    const isUser = msg.classList.contains("user");
    const isAssistant = msg.classList.contains("assistant");
    const isSystem = msg.classList.contains("system");
    const bubble = msg.querySelector(".bubble");
    if (bubble) {
      const persistedBubble = bubble.cloneNode(true);
      persistedBubble.querySelector(".prompt-trace-actions")?.remove();
      const entry = {
        role: isUser ? "user" : isAssistant ? "assistant" : "system",
        content: persistedBubble.innerHTML,
      };
      if (isAssistant) {
        if (msg._promptTrace) entry.promptTrace = msg._promptTrace;
        // 思考过程是 assistant 消息的前一个兄弟节点
        const prev = msg.previousElementSibling;
        console.log(
          "[DEBUG] assistant msg, prev=",
          prev ? prev.className : "null",
        );
        if (prev && prev.classList.contains("thinking-container")) {
          entry.thinking = prev.outerHTML;
          console.log(
            "[DEBUG] ✅ 保存思考过程:",
            prev.outerHTML.length,
            "字符",
          );
        } else {
          console.log("[DEBUG] ❌ 未找到思考过程");
        }
      }
      messages.push(entry);
    }
  });

  console.log("[DEBUG] 共保存", messages.length, "条消息");
  for (let i = 0; i < messages.length; i++) {
    console.log(
      "[DEBUG] 消息[" +
        i +
        "] role=" +
        messages[i].role +
        " thinking=" +
        (messages[i].thinking ? messages[i].thinking.length + "字符" : "无"),
    );
  }

  if (messages.length === 0) return;

  const title = getConversationTitle(messages);

  if (currentConversationId) {
    const history = getChatHistory();
    const idx = history.findIndex((h) => h.id === currentConversationId);
    if (idx !== -1) {
      history[idx].title = title;
      history[idx].updatedAt = new Date().toISOString();
      saveChatHistory(history);
    }
    saveChatMessages(currentConversationId, messages);
  }
}

function getConversationTitle(messages) {
  const firstUserMsg = messages.find((m) => m.role === "user");
  if (firstUserMsg) {
    const text = firstUserMsg.content.replace(/<[^>]*>/g, "").trim();
    return text.length > 30 ? text.slice(0, 30) + "..." : text;
  }
  return "新对话";
}

function getConversationTitleFromChat() {
  const userMsgs = chatContainer.querySelectorAll(".message.user .bubble");
  if (userMsgs.length > 0) {
    const text = userMsgs[0].textContent.trim();
    return text.length > 30 ? text.slice(0, 30) + "..." : text;
  }
  return "新对话";
}

function startNewConversation() {
  saveCurrentConversation();

  currentConversationId = generateConvId();
  SESSION_ID = generateSessionId();
  refreshKnowledgeBases();

  const history = getChatHistory();
  history.unshift({
    id: currentConversationId,
    session_id: SESSION_ID,
    title: "新对话",
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  });
  saveChatHistory(history);

  doClearSession();
  renderChatHistoryList();
}

function loadConversation(convId) {
  console.log(
    "[DEBUG] loadConversation: convId=",
    convId,
    "currentConversationId=",
    currentConversationId,
  );
  if (convId === currentConversationId) return;

  saveCurrentConversation();

  currentConversationId = convId;
  const history = getChatHistory();
  const entry = history.find((h) => h.id === convId);
  if (entry && entry.session_id) {
    SESSION_ID = entry.session_id;
  }
  refreshKnowledgeBases();

  const messages = getChatMessages(convId);
  console.log("[DEBUG] loadConversation: 加载了", messages.length, "条消息");

  chatContainer.innerHTML = "";
  if (messages.length === 0) {
    chatContainer.appendChild(createEmptyState());
  } else {
    messages.forEach((msg, idx) => {
      console.log(
        "[DEBUG] 渲染消息[" +
          idx +
          "] role=" +
          msg.role +
          " thinking=" +
          (msg.thinking ? msg.thinking.length + "字符" : "无"),
      );
      if (msg.role === "assistant" && msg.thinking) {
        // 思考过程作为独立节点插入在 assistant 消息之前
        const thinkingContainer = document.createElement("div");
        thinkingContainer.innerHTML = msg.thinking;
        chatContainer.appendChild(thinkingContainer.firstElementChild);
        console.log("[DEBUG] ✅ 恢复思考过程:", msg.thinking.length, "字符");
      }
      const el = document.createElement("div");
      el.className = "message " + msg.role;
      if (msg.role === "user") {
        el.innerHTML = `<div class="message-role">你</div><div class="bubble">${msg.content}</div>`;
      } else if (msg.role === "assistant") {
        el.innerHTML = `<div class="message-role">📝 结构化报告</div><div class="bubble">${msg.content}</div>`;
        if (msg.promptTrace) {
          el._promptTrace = msg.promptTrace;
          renderPromptTraceAction(el, msg.promptTrace);
        }
      } else {
        el.innerHTML = `<div class="bubble">${msg.content}</div>`;
      }
      chatContainer.appendChild(el);
    });
    scrollToBottom();
  }

  renderChatHistoryList();
  updateStatus("✅ 已加载历史对话");
}

function deleteConversation(convId, e) {
  e.stopPropagation();

  const history = getChatHistory();
  const filtered = history.filter((h) => h.id !== convId);
  saveChatHistory(filtered);
  deleteChatMessages(convId);

  if (convId === currentConversationId) {
    currentConversationId = null;
    SESSION_ID = generateSessionId();
    doClearSession();
    chatContainer.innerHTML = "";
    chatContainer.appendChild(createEmptyState());
  }

  renderChatHistoryList();
}

function renderChatHistoryList() {
  const list = document.getElementById("chatHistoryList");
  if (!list) return;

  const history = getChatHistory();
  if (history.length === 0) {
    list.innerHTML = '<div class="chat-history-empty">暂无历史对话</div>';
    return;
  }

  list.innerHTML = history
    .map((h) => {
      const isActive = h.id === currentConversationId;
      const date = new Date(h.updatedAt || h.createdAt);
      const dateStr = formatDate(date);
      return `
      <div class="chat-history-item ${isActive ? "active" : ""}"
           onclick="loadConversation('${h.id}')">
        <div class="chat-history-item-title" title="${escapeHtml(h.title)}">${escapeHtml(h.title)}</div>
        <div class="chat-history-item-date">${dateStr}</div>
        <div class="chat-history-item-delete" onclick="deleteConversation('${h.id}', event)">🗑</div>
      </div>
    `;
    })
    .join("");
}

function formatDate(date) {
  const now = new Date();
  const diff = now - date;
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return Math.floor(diff / 60000) + "分钟前";
  if (diff < 86400000) return Math.floor(diff / 3600000) + "小时前";
  const month = date.getMonth() + 1;
  const day = date.getDate();
  return month + "/" + day;
}

function formatSessionTime(value) {
  if (!value) return "-";
  const d = new Date(value.replace(" ", "T") + "Z");
  if (isNaN(d.getTime())) return String(value).slice(0, 19);
  return d.toLocaleString("zh-CN");
}

// 初始化：加载历史列表
function initChatHistory() {
  const history = getChatHistory();
  console.log("[DEBUG] initChatHistory: 历史对话数=", history.length);
  if (history.length > 0) {
    // 刷新后自动加载最近一次对话
    console.log("[DEBUG] initChatHistory: 自动加载最近对话", history[0].id);
    loadConversation(history[0].id);
  } else {
    console.log("[DEBUG] initChatHistory: 无历史对话，新建会话");
    currentConversationId = null;
    SESSION_ID = generateSessionId();
    renderChatHistoryList();
  }
}

// 监听清空会话按钮，同时保存当前对话
const originalClearHandler = btnClear.onclick;
btnClear.addEventListener("click", () => {
  saveCurrentConversation();
  currentConversationId = null;
  SESSION_ID = generateSessionId();
  renderChatHistoryList();
});

// 新对话按钮
document
  .getElementById("btnNewChat")
  .addEventListener("click", startNewConversation);

// 页面加载时初始化
initChatHistory();

// 页面刷新/关闭前自动保存当前对话
window.addEventListener("beforeunload", () => {
  saveCurrentConversation();
});

// ==================== 配置管理 ====================

// 模型列表配置：每个模型类型有哪些字段
const MODEL_DEFS = {
  llms: {
    listId: "modelListLlms",
    btnAddId: "btnAddLlm",
    fields: [
      {
        key: "name",
        label: "名称",
        type: "text",
        placeholder: "标识名称",
        sm: false,
      },
      {
        key: "base_url",
        label: "API 地址",
        type: "text",
        placeholder: "http://...",
        sm: false,
      },
      {
        key: "model",
        label: "模型名",
        type: "text",
        placeholder: "qwen36_27b",
        sm: false,
      },
      {
        key: "api_key",
        label: "API Key",
        type: "password",
        placeholder: "可选",
        sm: false,
      },
      { key: "max_tokens", label: "最大 Token", type: "number", sm: true },
      {
        key: "temperature",
        label: "温度",
        type: "number",
        step: "0.1",
        sm: true,
      },
    ],
  },
  embeddings: {
    listId: "modelListEmbeddings",
    btnAddId: "btnAddEmbedding",
    fields: [
      {
        key: "name",
        label: "名称",
        type: "text",
        placeholder: "标识名称",
        sm: false,
      },
      {
        key: "base_url",
        label: "API 地址",
        type: "text",
        placeholder: "http://...",
        sm: false,
      },
      {
        key: "model",
        label: "模型名",
        type: "text",
        placeholder: "bge-m3",
        sm: false,
      },
      {
        key: "api_key",
        label: "API Key",
        type: "password",
        placeholder: "可选",
        sm: false,
      },
      { key: "dimension", label: "维度", type: "number", sm: true },
    ],
  },
  reranks: {
    listId: "modelListReranks",
    btnAddId: "btnAddRerank",
    fields: [
      {
        key: "name",
        label: "名称",
        type: "text",
        placeholder: "标识名称",
        sm: false,
      },
      {
        key: "base_url",
        label: "API 地址",
        type: "text",
        placeholder: "https://...",
        sm: false,
      },
      {
        key: "model",
        label: "模型名",
        type: "text",
        placeholder: "Qwen/Qwen3-VL-Reranker-8B",
        sm: false,
      },
      {
        key: "api_key",
        label: "API Key",
        type: "password",
        placeholder: "可选",
        sm: false,
      },
    ],
  },
};

// 非模型列表的简单字段映射
const SIMPLE_FIELDS = {
  "retrieval.rag_top_k": "cfg_retrieval_rag_top_k",
  "retrieval.rerank_top_k": "cfg_retrieval_rerank_top_k",
  "retrieval.collection_name": "cfg_retrieval_collection_name",
  "milvus.uri": "cfg_retrieval_db_path",
  "short_term_memory.max_rounds": "cfg_stm_max_rounds",
  "short_term_memory.decay_factor": "cfg_stm_decay_factor",
};

// 渲染一个模型列表
function renderModelList(sectionKey) {
  const def = MODEL_DEFS[sectionKey];
  const container = document.getElementById(def.listId);
  if (!container) return;
  container.innerHTML = "";
  const items = _configData[sectionKey] || [];
  items.forEach((item, idx) => {
    const card = document.createElement("div");
    card.className = "model-card";
    card.dataset.index = idx;
    card.dataset.section = sectionKey;

    card.innerHTML = `
      <div class="model-card-header">
        <div class="model-card-title">${escapeHtml(item.name || `模型 ${idx + 1}`)}</div>
        <div class="model-card-actions">
          <span class="model-status model-status-clickable" title="点击测试 API 连通性">验证</span>
          <button type="button" class="btn btn-xs btn-danger model-remove-btn" title="删除此模型">✕</button>
        </div>
      </div>
      <div class="model-card-body">
        ${def.fields
          .map((f) => {
            const val = item[f.key] !== undefined ? item[f.key] : "";
            return `
            <div class="model-field">
              <label class="model-field-label">${
                f.key === "base_url" && sectionKey !== "reranks"
                  ? "Base URL"
                  : f.label
              }</label>
              <input type="${f.type}" ${f.step ? `step="${f.step}"` : ""}
                class="config-input ${f.sm ? "config-input-sm" : ""}"
                data-field="${f.key}" value="${escapeHtml(String(val))}"
                placeholder="${
                  f.key === "base_url" && sectionKey !== "reranks"
                    ? "http://host:port/v1"
                    : f.placeholder || ""
                }">
              ${
                f.key === "base_url" && sectionKey !== "reranks"
                  ? `<small class="model-field-hint">请输入 OpenAI-compatible Base URL，例如 http://host:port/v1，不要填写 ${
                      sectionKey === "llms"
                        ? "/chat/completions"
                        : "/embeddings"
                    }。</small>`
                  : ""
              }
            </div>`;
          })
          .join("")}
      </div>
      <div class="model-card-error" style="display: none"></div>
    `;
    container.appendChild(card);
  });

  // 绑定状态点击（点击重新测试）
  container.querySelectorAll(".model-status").forEach((statusEl) => {
    statusEl.addEventListener("click", async (e) => {
      const card = e.target.closest(".model-card");
      if (!card) return;
      await testModelConnection(sectionKey, card);
    });
  });

  // 绑定删除按钮
  container.querySelectorAll(".model-remove-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      const card = e.target.closest(".model-card");
      if (!card) return;
      card.remove();
      reindexModelCards(sectionKey);
    });
  });
}

function reindexModelCards(sectionKey) {
  const def = MODEL_DEFS[sectionKey];
  const container = document.getElementById(def.listId);
  if (!container) return;
  const cards = container.querySelectorAll(".model-card");
  cards.forEach((card, idx) => {
    card.dataset.index = idx;
  });
}

function addModelItem(sectionKey) {
  const def = MODEL_DEFS[sectionKey];
  const newItem = {};
  def.fields.forEach((f) => {
    newItem[f.key] = "";
  });
  newItem.name = "new";
  if (!_configData[sectionKey]) _configData[sectionKey] = [];
  _configData[sectionKey].push(newItem);
  renderModelList(sectionKey);
}

// 收集模型列表数据
function collectModelData(sectionKey) {
  const def = MODEL_DEFS[sectionKey];
  const container = document.getElementById(def.listId);
  if (!container) return [];
  const cards = container.querySelectorAll(".model-card");
  const items = [];
  cards.forEach((card, idx) => {
    const item = {};
    def.fields.forEach((f) => {
      const input = card.querySelector(`[data-field="${f.key}"]`);
      if (!input) return;
      let val = input.value.trim();
      if (input.type === "number" || input.step) {
        val = val === "" ? "" : Number(val);
        if (input.step === "0.1" && val !== "") val = parseFloat(val);
      }
      if (val !== "" || input.type === "password") {
        item[f.key] = val;
      }
    });
    items.push(item);
  });
  // 丢弃完全空白的占位条目，避免写入无效配置
  return items.filter(
    (it) => (it.name || "").trim() || (it.base_url || "").trim() || (it.model || "").trim()
  );
}

// 测试模型 API 连接
async function testModelConnection(sectionKey, card) {
  const def = MODEL_DEFS[sectionKey];
  const model = {};
  def.fields.forEach((f) => {
    const input = card.querySelector(`[data-field="${f.key}"]`);
    if (!input) return;
    let val = input.value.trim();
    if (input.type === "number" || input.step) {
      val = val === "" ? "" : Number(val);
      if (input.step === "0.1" && val !== "") val = parseFloat(val);
    }
    if (val !== "" || input.type === "password") {
      model[f.key] = val;
    }
  });

  const errorEl = card.querySelector(".model-card-error");
  errorEl.style.display = "none";
  errorEl.textContent = "";

  if (!model.base_url) {
    errorEl.textContent = "❌ API 地址不能为空";
    errorEl.style.display = "block";
    return;
  }
  if (!model.model) {
    errorEl.textContent = "❌ 模型名不能为空";
    errorEl.style.display = "block";
    return;
  }

  const statusEl = card.querySelector(".model-status");
  statusEl.textContent = "验证中";
  statusEl.className =
    "model-status model-status-clickable model-status-verifying";

  try {
    const resp = await fetch(`${API_BASE}/test-model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_config: model, model_type: sectionKey }),
    });
    const data = await resp.json();
    if (data.success) {
      statusEl.textContent = "✅可用";
      statusEl.className =
        "model-status model-status-clickable model-status-ok";
      errorEl.style.display = "none";
      errorEl.textContent = "";
    } else {
      statusEl.textContent = "❌不可用";
      statusEl.className =
        "model-status model-status-clickable model-status-fail";
      const modelName = model.name || "未命名";
      const errorMsg = data.error || data.message || "未知错误";
      console.error(
        `[模型验证] ${sectionKey} - ${modelName} 验证失败:`,
        errorMsg,
      );
      errorEl.textContent = `❌ 验证失败: ${errorMsg}`;
      errorEl.style.display = "block";
    }
  } catch (e) {
    statusEl.textContent = "❌不可用";
    statusEl.className =
      "model-status model-status-clickable model-status-fail";
    const modelName = model.name || "未命名";
    console.error(`[模型验证] ${sectionKey} - ${modelName} 验证失败:`, e);
    errorEl.textContent = `❌ 验证失败: ${e.message}`;
    errorEl.style.display = "block";
  }
}

// 自动验证所有模型
async function testAllModels() {
  for (const sectionKey of Object.keys(MODEL_DEFS)) {
    const def = MODEL_DEFS[sectionKey];
    const container = document.getElementById(def.listId);
    if (!container) continue;
    const cards = container.querySelectorAll(".model-card");
    for (const card of cards) {
      await testModelConnection(sectionKey, card);
    }
  }
}

// 加载配置
let _configData = {};

async function loadConfig() {
  try {
    const resp = await fetch(`${API_BASE}/config?t=${Date.now()}`);
    const data = await resp.json();
    if (data.error) {
      showConfigHint("❌ " + data.error);
      return;
    }
    _configData = data.config || {};

    // 渲染模型列表
    Object.keys(MODEL_DEFS).forEach((sectionKey) => {
      renderModelList(sectionKey);
    });

    // 填充简单字段
    for (const [key, elId] of Object.entries(SIMPLE_FIELDS)) {
      const el = document.getElementById(elId);
      if (!el) continue;
      const keys = key.split(".");
      let val = _configData;
      for (const k of keys) {
        if (val && typeof val === "object") val = val[k];
        else {
          val = undefined;
          break;
        }
      }
      if (val !== undefined && val !== null) {
        el.value = val;
      }
    }
    showConfigHint("✅ 配置已加载");
    // 刷新 Agent 页面的模型下拉框
    refreshModelSelect();
    // 自动验证所有模型
    testAllModels();
  } catch (e) {
    console.error("[Config] 加载配置失败:", e);
    showConfigHint("❌ 加载配置失败: " + e.message);
  }
}

function showConfigHint(msg) {
  const hint = document.getElementById("configHint");
  if (hint) hint.textContent = msg;
}

// 保存配置
async function saveConfig() {
  const btn = document.getElementById("btnSaveConfig");
  btn.disabled = true;
  btn.textContent = "⏳ 保存中...";
  try {
    const cfg = {};

    // 收集模型列表
    Object.keys(MODEL_DEFS).forEach((sectionKey) => {
      cfg[sectionKey] = collectModelData(sectionKey);
    });

    // 收集简单字段
    for (const [key, elId] of Object.entries(SIMPLE_FIELDS)) {
      const el = document.getElementById(elId);
      if (!el) continue;
      const keys = key.split(".");
      let node = cfg;
      for (let i = 0; i < keys.length - 1; i++) {
        if (!node[keys[i]]) node[keys[i]] = {};
        node = node[keys[i]];
      }
      const lastKey = keys[keys.length - 1];
      let val = el.value.trim();
      if (el.type === "number" || el.step) {
        val = val === "" ? "" : Number(val);
        if (el.step === "0.1" && val !== "") val = parseFloat(val);
      }
      if (val !== "") {
        node[lastKey] = val;
      }
    }

    // 保留 active_models
    if (_configData.active_models) {
      cfg.active_models = _configData.active_models;
    }

    const resp = await fetch(`${API_BASE}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: cfg }),
    });
    const data = await resp.json();
    if (data.status === "ok") {
      const milvusUriInput = document.getElementById("cfg_retrieval_db_path");
      if (milvusUriInput && data.milvus_uri) {
        milvusUriInput.value = data.milvus_uri;
      }
      showConfigHint("✅ " + data.message);
      // 保存后刷新报告工作台模型列表，新增模型立即可选
      if (typeof loadReportModels === "function") loadReportModels();
    } else {
      showConfigHint("❌ " + (data.detail || data.error || "保存失败"));
    }
  } catch (e) {
    console.error("[Config] 保存配置失败:", e);
    showConfigHint("❌ 保存失败: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "💾 保存配置";
  }
}

// 当前选中的 LLM 名称
let _currentLlmName = "";

// 刷新当前激活的 LLM 名称
function refreshModelSelect() {
  const llms = _configData.llms || [];
  const activeModels = _configData.active_models || {};
  const savedName = activeModels.chat_llm || "";
  if (savedName && llms.some((m) => m.name === savedName)) {
    _currentLlmName = savedName;
  } else if (llms.length > 0) {
    _currentLlmName = llms[0].name || "";
  }
}
// 打开模型选择弹窗
function openLlmConfigPanel() {
  const panel = document.getElementById("llmConfigPanel");
  if (!panel) return;
  panel.style.display = "flex";

  const activeModels = _configData.active_models || {};

  // 填充 LLM 下拉框
  const llmSelect = document.getElementById("activeChatLlm");
  const llms = _configData.llms || [];
  llmSelect.innerHTML = llms
    .map(
      (m, i) =>
        `<option value="${i}" ${m.name === activeModels.chat_llm ? "selected" : ""}>${escapeHtml(m.name || `模型 ${i + 1}`)}</option>`,
    )
    .join("");

  // 填充 Embedding 下拉框
  const embSelect = document.getElementById("activeEmbedding");
  const embeddings = _configData.embeddings || [];
  embSelect.innerHTML = embeddings
    .map(
      (m, i) =>
        `<option value="${i}" ${m.name === activeModels.embedding ? "selected" : ""}>${escapeHtml(m.name || `模型 ${i + 1}`)}</option>`,
    )
    .join("");

  // 填充 Rerank 下拉框
  const rerankSelect = document.getElementById("activeRerank");
  const reranks = _configData.reranks || [];
  rerankSelect.innerHTML = reranks
    .map(
      (m, i) =>
        `<option value="${i}" ${m.name === activeModels.rerank ? "selected" : ""}>${escapeHtml(m.name || `模型 ${i + 1}`)}</option>`,
    )
    .join("");

  document.getElementById("llmConfigHint").textContent = "";
}

// 弹窗保存激活模型
async function saveLlmConfigFromPanel() {
  const hint = document.getElementById("llmConfigHint");

  // 先收集 Config 页表单中的最新输入，避免内存数据覆盖用户填写内容
  Object.keys(MODEL_DEFS).forEach((sectionKey) => {
    _configData[sectionKey] = collectModelData(sectionKey);
  });

  const llms = _configData.llms || [];
  const embeddings = _configData.embeddings || [];
  const reranks = _configData.reranks || [];

  const llmIdx = parseInt(document.getElementById("activeChatLlm").value);
  const embIdx = parseInt(document.getElementById("activeEmbedding").value);
  const rerankIdx = parseInt(document.getElementById("activeRerank").value);

  const chatLlm = llms[llmIdx];
  const embedding = embeddings[embIdx];
  const rerank = reranks[rerankIdx];

  if (!chatLlm || !chatLlm.name) {
    hint.textContent = "❌ 请选择对话大语言模型";
    return;
  }
  if (!embedding || !embedding.name) {
    hint.textContent = "❌ 请选择向量化模型";
    return;
  }
  if (!rerank || !rerank.name) {
    hint.textContent = "❌ 请选择重排模型";
    return;
  }

  if (!_configData.active_models) _configData.active_models = {};
  _configData.active_models.chat_llm = chatLlm.name;
  _configData.active_models.embedding = embedding.name;
  _configData.active_models.rerank = rerank.name;
  _currentLlmName = chatLlm.name;

  const btn = document.getElementById("btnSaveLlmConfig");
  btn.disabled = true;
  btn.textContent = "⏳ 保存中...";

  try {
    const cfg = { ..._configData };
    cfg.active_models = _configData.active_models;

    const resp = await fetch(`${API_BASE}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ config: cfg }),
    });
    const data = await resp.json();
    if (data.status === "ok") {
      hint.textContent = "✅ " + data.message;
      // 保存后关闭弹窗
      setTimeout(() => {
        document.getElementById("llmConfigPanel").style.display = "none";
      }, 800);
    } else {
      hint.textContent = "❌ " + (data.error || "保存失败");
    }
  } catch (e) {
    hint.textContent = "❌ 保存失败: " + e.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "保存";
  }
}

// ==================== Stateful Agent API Deployments ====================

let _apiDeployments = [];
let _apiDeploymentKnowledgeBases = [];
let _apiDemoLanguage = "curl";
let _selectedApiDeploymentId = null;
let _apiDetailDeploymentId = null;
let _apiDeploymentsLoading = false;
let _apiGuideLoaded = false;

const API_DEPLOYMENT_GUIDE_URL = "/static/docs/api-deployment-guide.md";

function getDefaultApiBaseUrl() {
  const currentOrigin =
    window.location.origin && window.location.origin !== "null"
      ? window.location.origin
      : "http://localhost:8000";
  return `${currentOrigin.replace(/\/$/, "")}/api/v1`;
}

async function apiManagementRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    cache: "no-store",
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || payload.message || `HTTP ${response.status}`;
    const code = typeof detail === "object" ? detail?.code : payload.code;
    throw new Error(code ? `${message}（${code}）` : message);
  }
  return payload;
}

function getDeploymentEndpoint(deployment) {
  const relative =
    deployment?.endpoint ||
    `/api/v1/deployments/${deployment?.deployment_id || "DEPLOYMENT_ID"}/chat`;
  const origin = getDefaultApiBaseUrl().replace(/\/api\/v1$/, "");
  return `${origin}${relative}`;
}

function getSelectedApiDeployment() {
  return (
    _apiDeployments.find(
      (item) => item.deployment_id === _selectedApiDeploymentId,
    ) ||
    _apiDeployments[0] ||
    null
  );
}

function formatApiDate(value) {
  if (!value) return "—";
  return String(value)
    .replace("T", " ")
    .replace(/\.\d+Z?$/, "")
    .slice(0, 19);
}

function renderApiServiceStatus() {
  const statusEl = document.getElementById("apiStatusText");
  const baseUrlInput = document.getElementById("apiBaseUrl");
  const summaryEl = document.getElementById("apiDeploymentSummary");
  if (!statusEl || !baseUrlInput) return;
  const activeCount = _apiDeployments.filter(
    (item) => item.status === "active",
  ).length;
  const readyCount = _apiDeployments.filter(
    (item) => item.status === "active" && item.health === "healthy",
  ).length;
  const degradedCount = _apiDeployments.filter(
    (item) => item.health === "degraded",
  ).length;
  baseUrlInput.value = getDefaultApiBaseUrl();
  statusEl.textContent = readyCount
    ? `${readyCount} 个 Deployment 正在接受请求`
    : activeCount
      ? "Active Deployment 的知识库当前不可用"
      : "尚无 Active Deployment";
  statusEl.classList.toggle("is-enabled", readyCount > 0);
  if (summaryEl) {
    summaryEl.textContent = degradedCount
      ? `${_apiDeployments.length} 个 · ${degradedCount} 个异常`
      : `${_apiDeployments.length} 个 Deployment`;
  }
}

function renderApiKnowledgeBaseChoices(containerId, selectedIds = []) {
  const listEl = document.getElementById(containerId);
  if (!listEl) return;
  const selected = new Set(selectedIds);
  const items = _apiDeploymentKnowledgeBases.filter(
    (kb) => kb.status === "active",
  );

  if (!items.length) {
    listEl.innerHTML =
      '<div class="api-key-empty">暂无 Active 知识库，请先完成知识库构建。</div>';
    return;
  }

  listEl.innerHTML = items
    .map((kb) => {
      const checked = selected.has(kb.kb_id);
      return `
      <label class="api-kb-checkbox-item ${checked ? "is-selected" : ""}">
        <input
          type="checkbox"
          value="${escapeHtml(kb.kb_id)}"
          ${checked ? "checked" : ""}
        />
        <span class="api-kb-checkbox-copy">
          <strong title="${escapeHtml(kb.kb_name)}">${escapeHtml(kb.kb_name)}</strong>
          <span>${escapeHtml(kb.description || kb.kb_id)} · Active</span>
        </span>
      </label>
    `;
    })
    .join("");
}

function renderApiKnowledgeBases() {
  renderApiKnowledgeBaseChoices("apiKbCheckboxList");
}

function renderApiDeploymentList() {
  const listEl = document.getElementById("apiDeploymentList");
  if (!listEl) return;
  if (
    _selectedApiDeploymentId &&
    !_apiDeployments.some(
      (item) => item.deployment_id === _selectedApiDeploymentId,
    )
  ) {
    _selectedApiDeploymentId = _apiDeployments[0]?.deployment_id || null;
  }
  if (!_apiDeployments.length) {
    listEl.innerHTML = `
      <div class="api-deployment-empty">
        <div class="api-deployment-empty-icon">🚀</div>
        <h3>还没有上线 API</h3>
        <p>选择一个或多个知识库，将其发布为支持多轮对话的 Agent API。</p>
        <button type="button" class="btn btn-primary" data-api-action="create-first">创建第一个 API</button>
      </div>
    `;
    return;
  }
  listEl.innerHTML = _apiDeployments
    .map((item) => {
      const selected = item.deployment_id === _selectedApiDeploymentId;
      const kbNames = (item.knowledge_bases || []).map(
        (kb) => kb.kb_name || kb.kb_id,
      );
      const visibleKbNames = kbNames.slice(0, 2);
      const remainingKbCount = Math.max(
        0,
        kbNames.length - visibleKbNames.length,
      );
      const kbSummary =
        [
          ...visibleKbNames,
          ...(remainingKbCount ? [`+${remainingKbCount}`] : []),
        ].join("、") || "无";
      const isActive = item.status === "active";
      const isHealthy = item.health === "healthy";
      return `
      <article class="api-deployment-card ${selected ? "is-selected" : ""}" data-api-select="${escapeHtml(item.deployment_id)}">
        <div class="api-deployment-card-main">
          <div class="api-deployment-title-row">
            <h4>${escapeHtml(item.name)}</h4>
            <span class="api-status-badge ${isActive ? "active" : "disabled"}">${isActive ? "Active" : "Disabled"}</span>
            <span class="api-health-badge ${isHealthy ? "healthy" : "degraded"}">${isHealthy ? "Healthy" : "Degraded"}</span>
          </div>
          <p>${escapeHtml(item.description || "暂无描述")}</p>
          <div class="api-deployment-meta">
            <span title="${escapeHtml(kbNames.join("、"))}"><strong>KB</strong> ${escapeHtml(kbSummary)}</span>
            <span><strong>Key</strong> ${escapeHtml(item.api_key_prefix || "—")}</span>
            <span><strong>创建</strong> ${escapeHtml(formatApiDate(item.created_at))}</span>
            <span><strong>最近调用</strong> ${item.last_used_at ? escapeHtml(formatApiDate(item.last_used_at)) : "从未调用"}</span>
          </div>
          <code>${escapeHtml(item.endpoint)}</code>
        </div>
        <div class="api-deployment-actions">
          <button type="button" class="btn btn-sm btn-primary" data-api-action="view" data-deployment-id="${escapeHtml(item.deployment_id)}">查看详情</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="copy" data-deployment-id="${escapeHtml(item.deployment_id)}">复制 Endpoint</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="edit" data-deployment-id="${escapeHtml(item.deployment_id)}">编辑</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="rotate" data-deployment-id="${escapeHtml(item.deployment_id)}">重新生成密钥</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="toggle" data-deployment-id="${escapeHtml(item.deployment_id)}">${isActive ? "停用" : "启用"}</button>
          <button type="button" class="btn btn-sm btn-danger" data-api-action="delete" data-deployment-id="${escapeHtml(item.deployment_id)}">删除</button>
        </div>
      </article>
    `;
    })
    .join("");
}

function buildApiDemoCode(language = _apiDemoLanguage) {
  const deployment = getSelectedApiDeployment();
  if (!deployment) return "请先创建并选择一个 Deployment。";
  const apiKey = "YOUR_API_KEY";
  const url = getDeploymentEndpoint(deployment);

  if (language === "python") {
    return `import json
import requests

url = "${url}"
headers = {
    "Authorization": "Bearer ${apiKey}",
    "Content-Type": "application/json",
}

def chat(message, conversation_id=None):
    payload = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    current_id = conversation_id
    with requests.post(url, headers=headers, json=payload, stream=True, timeout=600) as response:
        response.raise_for_status()
        for line in response.iter_lines(decode_unicode=True):
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                break
            event = json.loads(raw)
            print(event)
            if event["type"] == "conversation":
                current_id = event["conversation_id"]
    return current_id

conversation_id = chat("CT头颅，考虑脑出血")
chat("把右侧病灶描述得详细一点", conversation_id)`;
  }

  return `# 第一次请求：从 conversation 事件保存 conversation_id
curl -N -X POST "${url}" \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"message":"CT头颅，考虑脑出血"}'

# 第二次请求：复用服务端返回的 conversation_id
curl -N -X POST "${url}" \\
  -H "Authorization: Bearer ${apiKey}" \\
  -H "Content-Type: application/json" \\
  -d '{"message":"把右侧病灶描述得详细一点","conversation_id":"conv_xxx"}'`;
}

function renderApiDemoCode() {
  const codeEl = document.getElementById("apiDemoCode");
  if (!codeEl) return;
  codeEl.textContent = buildApiDemoCode();
  document.querySelectorAll(".api-demo-tab").forEach((tab) => {
    const active = tab.dataset.apiDemoLanguage === _apiDemoLanguage;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function showDeploymentSecret(deployment) {
  const modal = document.getElementById("apiSecretModal");
  if (!modal || !deployment?.api_key) return;
  document.getElementById("apiSecretDeploymentName").textContent =
    deployment.name || deployment.deployment_id;
  document.getElementById("apiRevealedEndpoint").value =
    getDeploymentEndpoint(deployment);
  document.getElementById("apiRevealedKey").value = deployment.api_key;
  modal.hidden = false;
  requestAnimationFrame(() =>
    document.getElementById("btnCopyApiKey")?.focus(),
  );
}

function closeDeploymentSecret() {
  const modal = document.getElementById("apiSecretModal");
  if (modal) modal.hidden = true;
  const endpointInput = document.getElementById("apiRevealedEndpoint");
  const keyInput = document.getElementById("apiRevealedKey");
  const deploymentName = document.getElementById("apiSecretDeploymentName");
  if (endpointInput) endpointInput.value = "";
  if (keyInput) keyInput.value = "";
  if (deploymentName) deploymentName.textContent = "";
}

async function loadApiDeployments() {
  const listEl = document.getElementById("apiDeploymentList");
  if (_apiDeploymentsLoading) return;
  _apiDeploymentsLoading = true;
  if (listEl)
    listEl.innerHTML = '<div class="api-key-empty">正在加载 Deployment…</div>';
  try {
    const [deploymentData, kbData] = await Promise.all([
      apiManagementRequest("/deployments"),
      apiManagementRequest("/kbs"),
    ]);
    _apiDeployments = deploymentData.deployments || [];
    _apiDeploymentKnowledgeBases = kbData.knowledge_bases || [];
    if (!_selectedApiDeploymentId) {
      _selectedApiDeploymentId = _apiDeployments[0]?.deployment_id || null;
    }
    renderApiServiceStatus();
    renderApiKnowledgeBases();
    renderApiDeploymentList();
    renderApiDemoCode();
  } catch (error) {
    if (listEl)
      listEl.innerHTML = `<div class="api-key-empty">加载失败：${escapeHtml(error.message)}</div>`;
    showToast(`Deployment 加载失败：${error.message}`, "error");
  } finally {
    _apiDeploymentsLoading = false;
  }
}

function copyApiTextFallback(text) {
  if (typeof document.execCommand !== "function") return false;

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.inset = "0 auto auto -9999px";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";

  const activeElement = document.activeElement;
  const selection = window.getSelection?.();
  const selectedRange = selection?.rangeCount
    ? selection.getRangeAt(0).cloneRange()
    : null;

  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);

  let copied = false;
  try {
    copied = document.execCommand("copy");
  } finally {
    textarea.remove();
    activeElement?.focus?.();
    if (selection && selectedRange) {
      selection.removeAllRanges();
      selection.addRange(selectedRange);
    }
  }
  return copied;
}

async function copyApiText(text, label) {
  try {
    if (
      navigator.clipboard &&
      typeof navigator.clipboard.writeText === "function"
    ) {
      try {
        await navigator.clipboard.writeText(text);
      } catch (clipboardError) {
        if (!copyApiTextFallback(text)) throw clipboardError;
      }
    } else if (!copyApiTextFallback(text)) {
      throw new Error("当前浏览器不支持自动复制，请手动选择并复制");
    }
    showToast(`${label}已复制`, "success");
  } catch (error) {
    showToast(`复制失败：${error.message}`, "error");
  }
}

function appendApiGuideInline(parent, text) {
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) {
      parent.appendChild(
        document.createTextNode(text.slice(cursor, match.index)),
      );
    }
    const token = match[0];
    const element = document.createElement(
      token.startsWith("**") ? "strong" : "code",
    );
    element.textContent = token.startsWith("**")
      ? token.slice(2, -2)
      : token.slice(1, -1);
    parent.appendChild(element);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    parent.appendChild(document.createTextNode(text.slice(cursor)));
  }
}

function splitApiGuideTableRow(line) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function appendApiGuideCodeBlock(container, language, codeText) {
  const wrapper = document.createElement("div");
  wrapper.className = "api-guide-code-block";

  const toolbar = document.createElement("div");
  toolbar.className = "api-guide-code-toolbar";
  const label = document.createElement("span");
  label.textContent = language || "text";
  const copyButton = document.createElement("button");
  copyButton.type = "button";
  copyButton.className = "btn btn-sm btn-outline";
  copyButton.dataset.apiGuideCopy = "true";
  copyButton.textContent = "复制";
  toolbar.append(label, copyButton);

  const pre = document.createElement("pre");
  const code = document.createElement("code");
  code.textContent = codeText;
  pre.appendChild(code);
  wrapper.append(toolbar, pre);
  container.appendChild(wrapper);
}

function renderApiGuideMarkdown(markdown) {
  const content = document.getElementById("apiGuideContent");
  const toc = document.getElementById("apiGuideToc");
  if (!content || !toc) return;
  content.replaceChildren();
  toc.replaceChildren();

  const lines = String(markdown || "")
    .replace(/\r\n/g, "\n")
    .split("\n");
  const headings = [];
  const isTableDivider = (line) => {
    const cells = splitApiGuideTableRow(line);
    return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
  };
  const isBlockStart = (index) => {
    const line = lines[index] || "";
    return (
      /^```/.test(line.trim()) ||
      /^#{1,4}\s+/.test(line) ||
      /^\s*(?:[-*]|\d+\.)\s+/.test(line) ||
      (line.includes("|") && isTableDivider(lines[index + 1] || ""))
    );
  };

  for (let index = 0; index < lines.length; ) {
    const line = lines[index];
    const trimmed = line.trim();
    if (!trimmed) {
      index += 1;
      continue;
    }

    const fence = trimmed.match(/^```([^`]*)$/);
    if (fence) {
      const codeLines = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index].trim())) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      appendApiGuideCodeBlock(content, fence[1].trim(), codeLines.join("\n"));
      continue;
    }

    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length + 1, 6);
      const element = document.createElement(`h${level}`);
      element.id = `api-guide-heading-${headings.length + 1}`;
      appendApiGuideInline(element, heading[2].trim());
      content.appendChild(element);
      headings.push({ id: element.id, title: heading[2].trim() });
      index += 1;
      continue;
    }

    if (line.includes("|") && isTableDivider(lines[index + 1] || "")) {
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const headerRow = document.createElement("tr");
      splitApiGuideTableRow(line).forEach((cellText) => {
        const cell = document.createElement("th");
        appendApiGuideInline(cell, cellText);
        headerRow.appendChild(cell);
      });
      thead.appendChild(headerRow);
      table.appendChild(thead);

      const tbody = document.createElement("tbody");
      index += 2;
      while (
        index < lines.length &&
        lines[index].trim() &&
        lines[index].includes("|")
      ) {
        const row = document.createElement("tr");
        splitApiGuideTableRow(lines[index]).forEach((cellText) => {
          const cell = document.createElement("td");
          appendApiGuideInline(cell, cellText);
          row.appendChild(cell);
        });
        tbody.appendChild(row);
        index += 1;
      }
      table.appendChild(tbody);
      const tableWrap = document.createElement("div");
      tableWrap.className = "api-guide-table-wrap";
      tableWrap.appendChild(table);
      content.appendChild(tableWrap);
      continue;
    }

    const listMatch = line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);
    if (listMatch) {
      const ordered = /\d+\./.test(listMatch[1]);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length) {
        const itemMatch = lines[index].match(/^\s*([-*]|\d+\.)\s+(.+)$/);
        if (!itemMatch || /\d+\./.test(itemMatch[1]) !== ordered) break;
        const item = document.createElement("li");
        appendApiGuideInline(item, itemMatch[2].trim());
        list.appendChild(item);
        index += 1;
      }
      content.appendChild(list);
      continue;
    }

    const paragraphLines = [trimmed];
    index += 1;
    while (
      index < lines.length &&
      lines[index].trim() &&
      !isBlockStart(index)
    ) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    const paragraph = document.createElement("p");
    appendApiGuideInline(paragraph, paragraphLines.join(" "));
    content.appendChild(paragraph);
  }

  const tocLabels = [
    "快速开始",
    "创建一个 API",
    "API Key",
    "第一次调用",
    "conversation_id",
    "第二轮调用",
    "Public SSE Events",
    "Python 完整示例",
    "常见问题",
  ];
  tocLabels.forEach((labelText) => {
    const heading = headings.find((item) => item.title.includes(labelText));
    if (!heading) return;
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.apiGuideTarget = heading.id;
    button.textContent = labelText;
    toc.appendChild(button);
  });
  toc.hidden = toc.childElementCount === 0;
}

async function loadApiDeploymentGuide() {
  if (_apiGuideLoaded) return;
  const content = document.getElementById("apiGuideContent");
  if (!content) return;
  const loading = document.createElement("p");
  loading.className = "api-guide-loading";
  loading.textContent = "正在加载使用说明…";
  content.replaceChildren(loading);

  try {
    const response = await fetch(API_DEPLOYMENT_GUIDE_URL, {
      cache: "no-store",
      headers: { Accept: "text/markdown, text/plain" },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderApiGuideMarkdown(await response.text());
    _apiGuideLoaded = true;
  } catch (error) {
    const message = document.createElement("p");
    message.className = "api-guide-error";
    message.textContent = `使用说明加载失败：${error.message}`;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn-outline";
    retry.textContent = "重试";
    retry.addEventListener("click", loadApiDeploymentGuide, { once: true });
    content.replaceChildren(message, retry);
  }
}

function openApiDeploymentGuide() {
  const modal = document.getElementById("apiDeploymentGuideModal");
  if (!modal) return;
  modal.hidden = false;
  loadApiDeploymentGuide();
  requestAnimationFrame(() =>
    modal.querySelector(".api-key-modal-close")?.focus(),
  );
}

function closeApiDeploymentGuide() {
  const modal = document.getElementById("apiDeploymentGuideModal");
  if (modal) modal.hidden = true;
}

function openApiDeploymentCreate() {
  const modal = document.getElementById("apiDeploymentCreateModal");
  const form = document.getElementById("apiDeploymentForm");
  const errorEl = document.getElementById("apiDeploymentFormError");
  if (!modal || !form) return;
  form.reset();
  if (errorEl) {
    errorEl.textContent = "";
    errorEl.hidden = true;
  }
  renderApiKnowledgeBaseChoices("apiKbCheckboxList");
  modal.hidden = false;
  requestAnimationFrame(() =>
    document.getElementById("apiDeploymentName")?.focus(),
  );
}

function closeApiDeploymentCreate() {
  const modal = document.getElementById("apiDeploymentCreateModal");
  if (modal) modal.hidden = true;
}

function closeApiDeploymentDetail() {
  const modal = document.getElementById("apiDeploymentDetailModal");
  if (modal) modal.hidden = true;
  _apiDetailDeploymentId = null;
}

async function openApiDeploymentDetail(deployment) {
  const detail = await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}`,
  );
  _apiDetailDeploymentId = detail.deployment_id;
  _selectedApiDeploymentId = detail.deployment_id;
  const itemIndex = _apiDeployments.findIndex(
    (item) => item.deployment_id === detail.deployment_id,
  );
  if (itemIndex >= 0) _apiDeployments[itemIndex] = detail;

  document.getElementById("apiDeploymentDetailTitle").textContent =
    detail.name || "Deployment 详情";
  document.getElementById("apiDetailDescription").textContent =
    detail.description || "暂无描述";
  document.getElementById("apiDetailId").textContent = detail.deployment_id;
  document.getElementById("apiDetailStatus").textContent =
    detail.status === "active" ? "Active" : "Disabled";
  document.getElementById("apiDetailHealth").textContent =
    detail.health === "healthy" ? "Healthy" : "Degraded";
  document.getElementById("apiDetailKeyPrefix").textContent =
    detail.api_key_prefix || "—";
  document.getElementById("apiDetailCreated").textContent = formatApiDate(
    detail.created_at,
  );
  document.getElementById("apiDetailUpdated").textContent = formatApiDate(
    detail.updated_at,
  );
  document.getElementById("apiDetailLastUsed").textContent = detail.last_used_at
    ? formatApiDate(detail.last_used_at)
    : "从未调用";
  document.getElementById("apiDetailEndpoint").textContent = detail.endpoint;

  const kbContainer = document.getElementById("apiDetailKnowledgeBases");
  kbContainer.innerHTML =
    (detail.knowledge_bases || [])
      .map(
        (kb) => `
    <span class="api-detail-kb ${kb.status === "active" ? "healthy" : "degraded"}">
      ${escapeHtml(kb.kb_name || kb.kb_id)} · ${escapeHtml(kb.status || "unknown")}
    </span>
  `,
      )
      .join("") || '<span class="api-key-empty">未绑定知识库</span>';

  const warning = document.getElementById("apiDetailHealthWarning");
  const unavailable = detail.unavailable_kbs || [];
  warning.hidden = !unavailable.length;
  warning.textContent = unavailable.length
    ? `不可用知识库：${unavailable.map((kb) => `${kb.kb_name || kb.kb_id}（${kb.status || "unknown"}）`).join("、")}`
    : "";

  renderApiDeploymentList();
  renderApiDemoCode();
  document.getElementById("apiDeploymentDetailModal").hidden = false;
  requestAnimationFrame(() =>
    document
      .querySelector(".api-key-modal-close[data-api-detail-close]")
      ?.focus(),
  );
}

function closeApiDeploymentModal() {
  const modal = document.getElementById("apiDeploymentModal");
  if (modal) modal.hidden = true;
}

function openApiDeploymentModal(deployment) {
  const modal = document.getElementById("apiDeploymentModal");
  if (!modal || !deployment) return;
  document.getElementById("apiEditDeploymentId").value =
    deployment.deployment_id;
  document.getElementById("apiEditDeploymentName").value =
    deployment.name || "";
  document.getElementById("apiEditDeploymentDescription").value =
    deployment.description || "";
  const errorEl = document.getElementById("apiDeploymentEditError");
  errorEl.hidden = true;
  errorEl.textContent = "";
  renderApiKnowledgeBaseChoices(
    "apiEditKbCheckboxList",
    deployment.selected_kbs || [],
  );
  modal.hidden = false;
  requestAnimationFrame(() =>
    document.getElementById("apiEditDeploymentName")?.focus(),
  );
}

async function refreshApiDeployments() {
  await loadApiDeployments();
}

async function rotateApiDeploymentKey(deployment) {
  if (
    !window.confirm(
      "生成新密钥后，旧密钥将立即失效。使用旧密钥的客户端将无法继续调用。确定继续吗？",
    )
  )
    return;
  const updated = await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}/rotate-key`,
    { method: "POST" },
  );
  _selectedApiDeploymentId = updated.deployment_id;
  await refreshApiDeployments();
  showDeploymentSecret(updated);
}

async function toggleApiDeployment(deployment) {
  const action = deployment.status === "active" ? "disable" : "enable";
  if (
    action === "disable" &&
    !window.confirm(
      "停用后，该 API 将立即停止接受新的调用，但历史 Conversation 不会删除。确定停用吗？",
    )
  )
    return;
  const updated = await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}/${action}`,
    { method: "POST" },
  );
  _selectedApiDeploymentId = updated.deployment_id;
  await refreshApiDeployments();
  showToast(
    action === "enable" ? "Deployment 已启用" : "Deployment 已停用",
    "success",
  );
}

async function deleteApiDeployment(deployment) {
  if (
    !window.confirm(
      `确定删除“${deployment.name}”吗？删除后该 Deployment 将不能继续调用，但不会删除绑定知识库，历史 Conversation 数据也会保留。`,
    )
  )
    return;
  await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}`,
    { method: "DELETE" },
  );
  if (_selectedApiDeploymentId === deployment.deployment_id) {
    _selectedApiDeploymentId = null;
  }
  await refreshApiDeployments();
  showToast("Deployment 已删除", "success");
}

function bindApiDeploymentInteractions() {
  const createForm = document.getElementById("apiDeploymentForm");
  const kbList = document.getElementById("apiKbCheckboxList");
  const deploymentList = document.getElementById("apiDeploymentList");
  const editForm = document.getElementById("apiDeploymentEditForm");

  const bindKbChoiceStyle = (container) => {
    container?.addEventListener("change", (event) => {
      const input = event.target.closest('input[type="checkbox"]');
      if (!input) return;
      input
        .closest(".api-kb-checkbox-item")
        ?.classList.toggle("is-selected", input.checked);
    });
  };
  bindKbChoiceStyle(kbList);
  bindKbChoiceStyle(document.getElementById("apiEditKbCheckboxList"));

  document
    .getElementById("btnOpenApiGuide")
    ?.addEventListener("click", openApiDeploymentGuide);
  document.querySelectorAll("[data-api-guide-close]").forEach((element) => {
    element.addEventListener("click", closeApiDeploymentGuide);
  });
  document
    .getElementById("btnOpenApiDeploymentCreate")
    ?.addEventListener("click", openApiDeploymentCreate);
  document.querySelectorAll("[data-api-create-close]").forEach((element) => {
    element.addEventListener("click", closeApiDeploymentCreate);
  });
  document.querySelectorAll("[data-api-secret-close]").forEach((element) => {
    element.addEventListener("click", closeDeploymentSecret);
  });
  document.querySelectorAll("[data-api-detail-close]").forEach((element) => {
    element.addEventListener("click", closeApiDeploymentDetail);
  });

  createForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.getElementById("btnCreateApiDeployment");
    const errorEl = document.getElementById("apiDeploymentFormError");
    const selectedKbs = Array.from(
      kbList?.querySelectorAll('input[type="checkbox"]:checked') || [],
      (checkbox) => checkbox.value,
    );
    errorEl.hidden = true;
    if (!selectedKbs.length) {
      errorEl.textContent = "请至少选择一个 Active Knowledge Base";
      errorEl.hidden = false;
      return;
    }
    button.disabled = true;
    button.textContent = "上线中…";
    try {
      const deployment = await apiManagementRequest("/deployments", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("apiDeploymentName").value.trim(),
          description: document
            .getElementById("apiDeploymentDescription")
            .value.trim(),
          selected_kbs: selectedKbs,
        }),
      });
      _selectedApiDeploymentId = deployment.deployment_id;
      closeApiDeploymentCreate();
      createForm.reset();
      await refreshApiDeployments();
      showDeploymentSecret(deployment);
    } catch (error) {
      errorEl.textContent = error.message;
      errorEl.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "创建并上线";
    }
  });

  deploymentList?.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-api-action]");
    if (actionButton?.dataset.apiAction === "create-first") {
      openApiDeploymentCreate();
      return;
    }
    const card = event.target.closest("[data-api-select]");
    const deploymentId =
      actionButton?.dataset.deploymentId || card?.dataset.apiSelect;
    const deployment = _apiDeployments.find(
      (item) => item.deployment_id === deploymentId,
    );
    if (!deployment) return;
    _selectedApiDeploymentId = deployment.deployment_id;
    renderApiDeploymentList();
    renderApiDemoCode();
    if (!actionButton) return;
    actionButton.disabled = true;
    try {
      const action = actionButton.dataset.apiAction;
      if (action === "view") await openApiDeploymentDetail(deployment);
      if (action === "copy")
        await copyApiText(getDeploymentEndpoint(deployment), "Endpoint");
      if (action === "edit") openApiDeploymentModal(deployment);
      if (action === "rotate") await rotateApiDeploymentKey(deployment);
      if (action === "toggle") await toggleApiDeployment(deployment);
      if (action === "delete") await deleteApiDeployment(deployment);
    } catch (error) {
      showToast(`操作失败：${error.message}`, "error");
    } finally {
      actionButton.disabled = false;
    }
  });

  document
    .querySelectorAll("[data-api-deployment-modal-close]")
    .forEach((element) => {
      element.addEventListener("click", closeApiDeploymentModal);
    });

  editForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const deploymentId = document.getElementById("apiEditDeploymentId").value;
    const errorEl = document.getElementById("apiDeploymentEditError");
    const button = document.getElementById("btnSaveApiDeployment");
    const selectedKbs = Array.from(
      document.querySelectorAll(
        '#apiEditKbCheckboxList input[type="checkbox"]:checked',
      ),
      (checkbox) => checkbox.value,
    );
    errorEl.hidden = true;
    if (!selectedKbs.length) {
      errorEl.textContent = "请至少选择一个 Active Knowledge Base";
      errorEl.hidden = false;
      return;
    }
    button.disabled = true;
    try {
      await apiManagementRequest(
        `/deployments/${encodeURIComponent(deploymentId)}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            name: document.getElementById("apiEditDeploymentName").value.trim(),
            description: document
              .getElementById("apiEditDeploymentDescription")
              .value.trim(),
            selected_kbs: selectedKbs,
          }),
        },
      );
      closeApiDeploymentModal();
      await refreshApiDeployments();
      showToast("Deployment 已更新", "success");
    } catch (error) {
      errorEl.textContent = error.message;
      errorEl.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  document.querySelectorAll(".api-demo-tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      _apiDemoLanguage = tab.dataset.apiDemoLanguage || "curl";
      renderApiDemoCode();
    });
  });

  document
    .getElementById("btnRefreshApiDeployments")
    ?.addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      try {
        await refreshApiDeployments();
      } catch (error) {
        showToast(error.message, "error");
      } finally {
        button.disabled = false;
      }
    });
  document
    .getElementById("btnCopyApiEndpoint")
    ?.addEventListener("click", () => {
      copyApiText(
        document.getElementById("apiRevealedEndpoint").value,
        "Endpoint",
      );
    });
  document.getElementById("btnCopyApiKey")?.addEventListener("click", () => {
    copyApiText(document.getElementById("apiRevealedKey").value, "API Key");
  });
  document
    .getElementById("btnCopyApiDetailEndpoint")
    ?.addEventListener("click", () => {
      const deployment = _apiDeployments.find(
        (item) => item.deployment_id === _apiDetailDeploymentId,
      );
      if (deployment)
        copyApiText(getDeploymentEndpoint(deployment), "Endpoint");
    });
  document
    .getElementById("apiGuideContent")
    ?.addEventListener("click", (event) => {
      const copyButton = event.target.closest("[data-api-guide-copy]");
      if (!copyButton) return;
      const code =
        copyButton.closest(".api-guide-code-block")?.querySelector("code")
          ?.textContent || "";
      if (code) copyApiText(code, "示例");
    });
  document.getElementById("apiGuideToc")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-api-guide-target]");
    if (!button) return;
    document.getElementById(button.dataset.apiGuideTarget)?.scrollIntoView({
      behavior: "smooth",
      block: "start",
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeApiDeploymentCreate();
    closeApiDeploymentModal();
    closeApiDeploymentDetail();
    closeDeploymentSecret();
    closeApiDeploymentGuide();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  const btnSaveConfig = document.getElementById("btnSaveConfig");
  if (btnSaveConfig) {
    btnSaveConfig.addEventListener("click", saveConfig);
  }
  // 绑定各模型列表的添加按钮
  Object.values(MODEL_DEFS).forEach((def) => {
    const btn = document.getElementById(def.btnAddId);
    if (btn) {
      const sectionKey = Object.keys(MODEL_DEFS).find(
        (k) => MODEL_DEFS[k] === def,
      );
      btn.addEventListener("click", () => addModelItem(sectionKey));
    }
  });
  // 绑定配置模型按钮 - 打开弹窗
  const btnConfigModel = document.getElementById("btnConfigModel");
  if (btnConfigModel) {
    btnConfigModel.addEventListener("click", () => {
      openLlmConfigPanel();
    });
  }
  // 绑定弹窗关闭
  const btnLlmConfigClose = document.getElementById("btnLlmConfigClose");
  if (btnLlmConfigClose) {
    btnLlmConfigClose.addEventListener("click", () => {
      document.getElementById("llmConfigPanel").style.display = "none";
    });
  }
  // 绑定弹窗保存按钮
  const btnSaveLlmConfig = document.getElementById("btnSaveLlmConfig");
  if (btnSaveLlmConfig) {
    btnSaveLlmConfig.addEventListener("click", () => {
      saveLlmConfigFromPanel();
    });
  }
  // 绑定配置导航切换
  const navItems = document.querySelectorAll(".config-nav-item");
  navItems.forEach((item) => {
    item.addEventListener("click", () => {
      const targetKey = item.dataset.config;
      navItems.forEach((i) => i.classList.remove("active"));
      document
        .querySelectorAll(".config-group")
        .forEach((g) => (g.style.display = "none"));
      item.classList.add("active");
      const targetGroup = document.getElementById(
        `configGroup${targetKey.charAt(0).toUpperCase() + targetKey.slice(1)}`,
      );
      if (targetGroup) {
        targetGroup.style.display = "block";
      }
    });
  });
  bindApiDeploymentInteractions();
  // 页面加载后立即加载配置，确保 Agent 页面打开时模型下拉框就有内容
  loadConfig();
  refreshKnowledgeBases();
});

function addAssistantMessage(content) {
  const msg = document.createElement("div");
  msg.className = "message assistant";
  msg.innerHTML = `
    <div class="message-role">💬 助手</div>
    <div class="bubble">${renderMarkdown(content)}</div>
  `;
  chatContainer.appendChild(msg);
  scrollToBottom();
}

function addUserMessage(text) {
  const msg = document.createElement("div");
  msg.className = "message user";
  msg.innerHTML = `
    <div class="message-role">👤 用户</div>
    <div class="bubble">${escapeHtml(text)}</div>
  `;
  chatContainer.appendChild(msg);
  scrollToBottom();
}

// ========== Monitor：概览 / 会话记录 / Trace / 结构化实时日志 ==========
let monitorActiveTab = "overview";
let monitorLogEventSource = null;
let monitorLogItems = [];
let monitorLastLogId = 0;
let monitorPendingTraceRequestId = "";

const monitorTabs = document.querySelectorAll("[data-monitor-tab]");
const monitorPanels = document.querySelectorAll("[data-monitor-panel]");
const monitorTotalSessions = document.getElementById("monitorTotalSessions");
const monitorTotalTurns = document.getElementById("monitorTotalTurns");
const monitorLastUpdate = document.getElementById("monitorLastUpdate");
const monitorSessionsList = document.getElementById("monitorSessionsList");
const monitorServiceStatus = document.getElementById("monitorServiceStatus");
const monitorRequestCount = document.getElementById("monitorRequestCount");
const monitorSuccessRate = document.getElementById("monitorSuccessRate");
const monitorAverageLatency = document.getElementById("monitorAverageLatency");
const monitorP95Latency = document.getElementById("monitorP95Latency");
const monitorErrorCount = document.getElementById("monitorErrorCount");
const monitorTraceList = document.getElementById("monitorTraceList");
const monitorTraceDetail = document.getElementById("monitorTraceDetail");
const monitorTraceRequestFilter = document.getElementById(
  "monitorTraceRequestFilter",
);
const monitorTraceSessionFilter = document.getElementById(
  "monitorTraceSessionFilter",
);
const monitorLogList = document.getElementById("monitorLogList");
const monitorLogStatus = document.getElementById("monitorLogStatus");
const btnRefreshSessions = document.getElementById("btnRefreshSessions");
const btnRefreshTraces = document.getElementById("btnRefreshTraces");
const btnReconnectLogs = document.getElementById("btnReconnectLogs");
const btnClearLogs = document.getElementById("btnClearLogs");
const sessionDetailModal = document.getElementById("sessionDetailModal");
const sessionDetailInfo = document.getElementById("sessionDetailInfo");
const sessionDetailTurns = document.getElementById("sessionDetailTurns");
const btnCloseSessionDetail = document.getElementById("btnCloseSessionDetail");

const TRACE_STAGE_LABELS = {
  request: "Request",
  entity: "Entity",
  memory: "Memory",
  tool: "Tool",
  rag: "RAG",
  recovery: "Recovery",
  report: "Report",
  done: "Done",
  error: "Error",
};

function formatMonitorTimestamp(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? escapeHtml(String(value))
    : date.toLocaleString("zh-CN", { hour12: false });
}

function escapeHtmlAttribute(value) {
  return escapeHtml(String(value ?? ""))
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderMonitorIdentifier(value, kind, { linkToTrace = false } = {}) {
  const rawValue = String(value ?? "");
  if (!rawValue) {
    return '<span class="monitor-identifier monitor-identifier-empty"><code>-</code></span>';
  }
  const normalizedKind = kind === "request_id" ? "request_id" : "session_id";
  const escapedValue = escapeHtml(rawValue);
  const escapedAttribute = escapeHtmlAttribute(rawValue);
  const valueMarkup = linkToTrace
    ? `<button type="button" class="monitor-id-link" data-open-trace-request-id="${escapedAttribute}" title="打开请求 Trace：${escapedAttribute}"><code>${escapedValue}</code></button>`
    : `<code title="${escapedAttribute}">${escapedValue}</code>`;
  return `
    <span class="monitor-identifier monitor-identifier-${normalizedKind}" title="${escapedAttribute}">
      ${valueMarkup}
      <button type="button" class="monitor-id-copy" data-monitor-copy-id="${escapedAttribute}" data-monitor-copy-label="${normalizedKind}" aria-label="复制完整 ${normalizedKind}" title="复制完整 ${normalizedKind}">复制</button>
    </span>`;
}

function activateMonitorTab(tabName) {
  monitorActiveTab = ["overview", "sessions", "traces", "logs"].includes(
    tabName,
  )
    ? tabName
    : "overview";
  monitorTabs.forEach((tab) => {
    const active = tab.dataset.monitorTab === monitorActiveTab;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  monitorPanels.forEach((panel) => {
    const active = panel.dataset.monitorPanel === monitorActiveTab;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
  if (monitorActiveTab === "overview") {
    stopMonitorLogStream();
    loadMonitorOverview();
  } else if (monitorActiveTab === "sessions") {
    stopMonitorLogStream();
    loadSessionsList();
  } else if (monitorActiveTab === "traces") {
    stopMonitorLogStream();
    loadMonitorTraces();
  } else {
    startMonitorLogStream();
  }
}

async function loadMonitorOverview() {
  await loadObservabilityOverview();
}

async function loadObservabilityOverview() {
  try {
    const response = await fetch(`${API_BASE}/observability/overview`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    const healthy = data.service_status === "healthy";
    monitorServiceStatus.textContent = healthy ? "正常" : "降级";
    monitorServiceStatus.classList.toggle("is-healthy", healthy);
    monitorServiceStatus.classList.toggle("is-degraded", !healthy);
    monitorRequestCount.textContent = String(data.request_count ?? 0);
    monitorSuccessRate.textContent = `${Number(data.success_rate ?? 0).toFixed(1)}%`;
    monitorAverageLatency.textContent = `${Number(data.average_latency_ms ?? 0).toFixed(1)} ms`;
    monitorP95Latency.textContent = `${Number(data.p95_latency_ms ?? 0).toFixed(1)} ms`;
    monitorErrorCount.textContent = String(data.error_count ?? 0);
  } catch (error) {
    monitorServiceStatus.textContent = "不可用";
    monitorServiceStatus.classList.remove("is-healthy");
    monitorServiceStatus.classList.add("is-degraded");
    console.warn("Monitor 概览加载失败", error);
  }
}

async function loadMonitorTraces() {
  if (!monitorTraceList) return;
  const params = new URLSearchParams({ limit: "100" });
  const requestId = monitorTraceRequestFilter?.value.trim();
  const sessionId = monitorTraceSessionFilter?.value.trim();
  if (requestId) params.set("request_id", requestId);
  if (sessionId) params.set("session_id", sessionId);
  monitorTraceList.innerHTML = '<div class="monitor-empty">加载中...</div>';
  try {
    const response = await fetch(
      `${API_BASE}/observability/traces?${params.toString()}`,
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    renderMonitorTraceList(data.items || []);
    if (monitorPendingTraceRequestId) {
      const requestIdToOpen = monitorPendingTraceRequestId;
      monitorPendingTraceRequestId = "";
      await loadMonitorTraceDetail(requestIdToOpen);
    }
  } catch (error) {
    monitorTraceList.innerHTML = `<div class="monitor-empty">Trace 加载失败: ${escapeHtml(error.message)}</div>`;
  }
}

function renderMonitorTraceList(items) {
  if (!items.length) {
    monitorTraceList.innerHTML =
      '<div class="monitor-empty">暂无请求 Trace</div>';
    return;
  }
  monitorTraceList.innerHTML = items
    .map((item) => {
      const stages = [...new Set(item.stages || [])]
        .map((stage) => TRACE_STAGE_LABELS[stage] || stage)
        .join(" → ");
      return `
        <div class="monitor-trace-item">
          <div class="monitor-trace-item-head">
            <span class="monitor-trace-list-request"><span>request:</span>${renderMonitorIdentifier(item.request_id, "request_id")}</span>
            <span class="monitor-trace-state state-${escapeHtml(String(item.state || "running"))}">${escapeHtml(String(item.status_code ?? "运行中"))}</span>
          </div>
          <button type="button" class="monitor-trace-open" data-trace-request-id="${escapeHtmlAttribute(item.request_id)}">
            <span class="monitor-trace-path">${escapeHtml(String(item.method || ""))} ${escapeHtml(String(item.path || ""))}</span>
            <span class="monitor-trace-chain">${escapeHtml(stages || "Request")}</span>
            <span class="monitor-trace-meta">${formatMonitorTimestamp(item.started_at)} · ${item.duration_ms == null ? "-" : `${Number(item.duration_ms).toFixed(1)} ms`}</span>
          </button>
          <div class="monitor-trace-list-session"><span>session:</span>${renderMonitorIdentifier(item.session_id, "session_id")}</div>
        </div>`;
    })
    .join("");
  monitorTraceList
    .querySelectorAll("[data-trace-request-id]")
    .forEach((button) => {
      button.addEventListener("click", () =>
        loadMonitorTraceDetail(button.dataset.traceRequestId),
      );
    });
}

async function loadMonitorTraceDetail(requestId) {
  if (!requestId || !monitorTraceDetail) return;
  monitorTraceDetail.innerHTML = '<div class="monitor-empty">加载中...</div>';
  try {
    const response = await fetch(
      `${API_BASE}/observability/traces/${encodeURIComponent(requestId)}`,
    );
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const trace = await response.json();
    const events = trace.events || [];
    monitorTraceDetail.innerHTML = `
      <div class="monitor-trace-summary">
        <span class="monitor-trace-request"><span>request:</span>${renderMonitorIdentifier(trace.request_id, "request_id")}</span>
        <span>${escapeHtml(String(trace.method || ""))} ${escapeHtml(String(trace.path || ""))}</span>
        <span class="monitor-trace-session"><span>session:</span>${renderMonitorIdentifier(trace.session_id, "session_id")}</span>
      </div>
      <div class="monitor-trace-timeline">
        ${events
          .map(
            (event, index) => `
          <div class="monitor-trace-event stage-${escapeHtml(String(event.stage || "request"))}">
            <span class="monitor-trace-index">${index + 1}</span>
            <div><strong>${escapeHtml(TRACE_STAGE_LABELS[event.stage] || event.stage || "Event")}</strong><small>${escapeHtml(String(event.event_type || ""))} · ${formatMonitorTimestamp(event.timestamp)}</small><pre>${escapeHtml(JSON.stringify(event.details || {}, null, 2))}</pre></div>
          </div>`,
          )
          .join("")}
      </div>`;
  } catch (error) {
    monitorTraceDetail.innerHTML = `<div class="monitor-empty">Trace 详情加载失败: ${escapeHtml(error.message)}</div>`;
  }
}

function monitorLogParams() {
  const params = new URLSearchParams({ after_id: String(monitorLastLogId) });
  const fields = [
    ["level", "monitorLogLevel"],
    ["module", "monitorLogModule"],
    ["session_id", "monitorLogSessionId"],
    ["request_id", "monitorLogRequestId"],
  ];
  fields.forEach(([key, id]) => {
    const value = document.getElementById(id)?.value.trim();
    if (value) params.set(key, value);
  });
  return params;
}

function startMonitorLogStream({ resetCursor = false } = {}) {
  stopMonitorLogStream();
  if (resetCursor) monitorLastLogId = 0;
  monitorLogStatus.textContent = "连接中...";
  monitorLogEventSource = new EventSource(
    `${API_BASE}/observability/logs/stream?${monitorLogParams().toString()}`,
  );
  monitorLogEventSource.addEventListener("log", (event) => {
    try {
      const item = JSON.parse(event.data);
      monitorLastLogId = Math.max(monitorLastLogId, Number(item.id || 0));
      monitorLogItems.push(item);
      if (monitorLogItems.length > 500)
        monitorLogItems = monitorLogItems.slice(-500);
      renderMonitorLogs();
    } catch (error) {
      monitorLogStatus.textContent = `日志解析失败: ${error.message}`;
    }
  });
  monitorLogEventSource.onopen = () => {
    monitorLogStatus.textContent = "实时连接中";
  };
  monitorLogEventSource.onerror = () => {
    monitorLogStatus.textContent = "连接中断，浏览器将自动重试";
  };
}

function stopMonitorLogStream() {
  if (monitorLogEventSource) {
    monitorLogEventSource.close();
    monitorLogEventSource = null;
  }
  if (monitorLogStatus && monitorActiveTab !== "logs")
    monitorLogStatus.textContent = "未连接";
}

function renderMonitorLogs() {
  if (!monitorLogList) return;
  if (!monitorLogItems.length) {
    monitorLogList.innerHTML = '<div class="monitor-empty">暂无匹配日志</div>';
    return;
  }
  monitorLogList.innerHTML = monitorLogItems
    .map(
      (item) => `
      <div class="monitor-log-row">
        <time>${formatMonitorTimestamp(item.timestamp)}</time>
        <span class="monitor-log-level level-${escapeHtml(String(item.level || "info").toLowerCase())}">${escapeHtml(String(item.level || "INFO"))}</span>
        <code>${escapeHtml(String(item.module || "-"))}</code>
        <span class="monitor-log-message">${escapeHtml(String(item.message || ""))}</span>
        <span class="monitor-log-context">
          <span class="monitor-log-request"><span>request=</span>${renderMonitorIdentifier(item.request_id, "request_id", { linkToTrace: true })}</span>
          <span class="monitor-log-session"><span>session=</span>${renderMonitorIdentifier(item.session_id, "session_id")}</span>
        </span>
      </div>`,
    )
    .join("");
  monitorLogList.scrollTop = monitorLogList.scrollHeight;
}

// 加载会话列表
async function loadSessionsList() {
  try {
    const res = await fetch(`${API_BASE}/sessions`);
    const data = await res.json();
    const sessions = data.sessions || [];

    // 更新统计
    monitorTotalSessions.textContent = sessions.length;
    const totalTurns = sessions.reduce(
      (sum, s) => sum + (s.turn_count || 0),
      0,
    );
    monitorTotalTurns.textContent = totalTurns;
    monitorLastUpdate.textContent = new Date().toLocaleString("zh-CN");

    if (sessions.length === 0) {
      monitorSessionsList.innerHTML =
        '<div class="monitor-empty">暂无会话</div>';
      return;
    }

    // 按创建时间倒序排序
    sessions.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    monitorSessionsList.innerHTML = sessions
      .map((s) => {
        const sessionId = String(s.id || "");
        const escapedSessionId = escapeHtmlAttribute(sessionId);
        const sessionTitle = String(s.title || "未命名");
        return `
        <div class="monitor-session-row" data-session-id="${escapedSessionId}">
          <span class="monitor-session-id">${renderMonitorIdentifier(sessionId, "session_id")}</span>
          <span class="monitor-session-title" title="${escapeHtmlAttribute(sessionTitle)}">${escapeHtml(sessionTitle)}</span>
          <span class="monitor-session-turns">${s.turn_count || 0}</span>
          <span class="monitor-session-time">${formatSessionTime(s.created_at)}</span>
          <span class="monitor-session-actions">
            <button type="button" class="btn btn-sm btn-outline" data-view-session-id="${escapedSessionId}">查看</button>
            <button type="button" class="btn btn-sm btn-danger" data-delete-session-id="${escapedSessionId}">删除</button>
          </span>
        </div>
      `;
      })
      .join("");
  } catch (e) {
    monitorSessionsList.innerHTML = `<div class="monitor-empty">加载失败: ${e.message}</div>`;
  }
}

// 查看会话详情
async function viewSessionDetail(sessionId) {
  try {
    // 加载会话记忆
    const res = await fetch(
      `${API_BASE}/memory?session_id=${encodeURIComponent(sessionId)}`,
    );
    const data = await res.json();

    // 显示基本信息
    sessionDetailInfo.innerHTML = `
      <span class="session-detail-session-id"><span>会话 ID:</span>${renderMonitorIdentifier(sessionId, "session_id")}</span>
      <span>当前轮次: <strong>${data.current_turns || 0}</strong></span>
      <span>总轮次: <strong>${data.total_turns || 0}</strong></span>
    `;

    // 显示对话历史
    if (data.turns && data.turns.length > 0) {
      sessionDetailTurns.innerHTML = data.turns
        .map(
          (t) => `
        <div class="session-turn">
          <div class="session-turn-user">${escapeHtml(t.user || "(空)")}</div>
          <div class="session-turn-assistant">${renderMarkdown(t.assistant || "(空)")}</div>
        </div>
      `,
        )
        .join("");
    } else {
      sessionDetailTurns.innerHTML =
        '<div class="monitor-empty">暂无对话记录</div>';
    }

    sessionDetailModal.style.display = "flex";
  } catch (e) {
    alert(`加载会话详情失败: ${e.message}`);
  }
}

// 删除会话
async function deleteSession(sessionId) {
  if (!confirm(`确定要删除会话 ${sessionId} 吗？此操作不可恢复。`)) {
    return;
  }

  try {
    await fetch(
      `${API_BASE}/session?session_id=${encodeURIComponent(sessionId)}`,
      {
        method: "DELETE",
      },
    );
    await loadSessionsList();
  } catch (e) {
    alert(`删除会话失败: ${e.message}`);
  }
}

monitorSessionsList?.addEventListener("click", (event) => {
  const viewButton = event.target.closest?.("[data-view-session-id]");
  if (viewButton) {
    viewSessionDetail(viewButton.dataset.viewSessionId);
    return;
  }
  const deleteButton = event.target.closest?.("[data-delete-session-id]");
  if (deleteButton) deleteSession(deleteButton.dataset.deleteSessionId);
});

document.addEventListener("click", (event) => {
  const copyButton = event.target.closest?.("[data-monitor-copy-id]");
  if (copyButton) {
    event.preventDefault();
    event.stopPropagation();
    copyApiText(
      copyButton.dataset.monitorCopyId,
      copyButton.dataset.monitorCopyLabel || "ID",
    );
    return;
  }
  const traceLink = event.target.closest?.("[data-open-trace-request-id]");
  if (!traceLink) return;
  event.preventDefault();
  event.stopPropagation();
  const requestId = traceLink.dataset.openTraceRequestId;
  monitorTraceRequestFilter.value = requestId;
  monitorTraceSessionFilter.value = "";
  monitorPendingTraceRequestId = requestId;
  activateMonitorTab("traces");
});

// 关闭会话详情弹窗
btnCloseSessionDetail.addEventListener("click", () => {
  sessionDetailModal.style.display = "none";
});

sessionDetailModal
  .querySelector(".modal-backdrop")
  .addEventListener("click", () => {
    sessionDetailModal.style.display = "none";
  });

// 刷新按钮
btnRefreshSessions.addEventListener("click", () => {
  loadSessionsList();
});

monitorTabs.forEach((tab) => {
  tab.addEventListener("click", () =>
    activateMonitorTab(tab.dataset.monitorTab),
  );
});

btnRefreshTraces?.addEventListener("click", loadMonitorTraces);
[monitorTraceRequestFilter, monitorTraceSessionFilter].forEach((input) => {
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadMonitorTraces();
  });
});
btnReconnectLogs?.addEventListener("click", () => {
  monitorLogItems = [];
  renderMonitorLogs();
  startMonitorLogStream({ resetCursor: true });
});
btnClearLogs?.addEventListener("click", () => {
  monitorLogItems = [];
  renderMonitorLogs();
});

// 页面加载后自动加载会话列表
document.addEventListener("DOMContentLoaded", () => {
  loadMonitorOverview();
});

// ==================== Report 工作台 ====================

let reportWorkbenchLoaded = false;
let currentReportSession = null;
let reportConversationId = null;
let reportEventSource = null;
let reportAbortController = null; // iterate 流式请求的 AbortController（停止生成用）
let reportStoppedByUser = false;  // 用户手动停止标记（抑制 AbortError 报错气泡）

// ===== 报告工作台模型选择 =====
let reportModels = [];       // [{name, base_url, model}]
let reportSelectedModel = ""; // 选中的模型 name（空=全局激活模型）
let reportRoutesCache = []; // 路由列表缓存（含 quick_tags）
let reportPendingRouteId = ""; // 模板直选：已选定但尚未发送生成的路由ID

// ===== 思考模式开关（按模型分别记忆，默认全部关闭）=====
const REPORT_THINKING_KEY = "report_thinking_enabled";
// 实测结果缓存 { [modelName]: { mode, message, supported } }，避免重复实测
let reportThinkingTestCache = {};

function _readThinkingMap() {
  try {
    const raw = localStorage.getItem(REPORT_THINKING_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (e) {
    return {};
  }
}

function isReportThinkingEnabled(modelName) {
  if (!modelName) return false;
  return !!_readThinkingMap()[modelName];
}

function setReportThinkingEnabled(modelName, enabled) {
  if (!modelName) return;
  const map = _readThinkingMap();
  if (enabled) map[modelName] = true;
  else delete map[modelName];
  localStorage.setItem(REPORT_THINKING_KEY, JSON.stringify(map));
}

async function loadReportModels() {
  try {
    const resp = await fetch(`${API_BASE}/report-workbench/models?t=${Date.now()}`);
    const data = await resp.json();
    reportModels = data.models || [];
    // 保持用户上次选中的模型（localStorage 持久化），无记录时才用全局激活模型
    const saved = localStorage.getItem("report_selected_model") || "";
    if (saved && reportModels.some((m) => m.name === saved)) {
      reportSelectedModel = saved;
    } else {
      reportSelectedModel = data.active || (reportModels[0]?.name || "");
    }
    renderReportModelMenu();
  } catch (e) {
    console.error("加载模型列表失败:", e);
  }
}

function renderReportModelMenu() {
  const list = document.getElementById("reportModelList");
  // 按钮上显示当前选中的模型名 + 思考标记
  const btnLabel = document.querySelector("#reportModelBtn span");
  if (btnLabel) {
    const selected = reportModels.find((m) => m.name === reportSelectedModel);
    let label = selected ? selected.name : "模型";
    label = label.length > 10 ? label.slice(0, 10) + "…" : label;
    if (selected && isReportThinkingEnabled(selected.name)) label += " ·思";
    btnLabel.textContent = label;
    btnLabel.title = selected
      ? `${selected.name} (${selected.model})${isReportThinkingEnabled(selected.name) ? " — 思考模式已开启" : ""}`
      : "选择生成模型";
  }
  if (!list) return;

  list.innerHTML =
    reportModels
      .map((m) => {
        const on = isReportThinkingEnabled(m.name);
        const cached = reportThinkingTestCache[m.name];
        return `
      <div class="report-model-row" data-model-name="${escapeHtml(m.name)}">
        <div class="report-model-main">
          <button class="report-model-item ${m.name === reportSelectedModel ? "active" : ""}" data-model-name="${escapeHtml(m.name)}">
            <span class="model-dot"></span>
            <span class="model-name" title="${escapeHtml(m.model)}">${escapeHtml(m.name)}</span>
          </button>
          <div class="report-model-tools">
            <button class="report-think-test" data-model-name="${escapeHtml(m.name)}" title="实测该模型的思考开关效果">实测</button>
            <label class="report-think-switch" title="开启思考模式（默认关闭）">
              <input type="checkbox" data-model-name="${escapeHtml(m.name)}" ${on ? "checked" : ""}>
              <span class="report-think-slider"></span>
            </label>
          </div>
        </div>
        <div class="report-think-result ${cached ? cached.mode : ""}" data-model-name="${escapeHtml(m.name)}" style="${cached ? "" : "display:none"}">${cached ? escapeHtml(cached.message + (cached.detail || "")) : ""}</div>
      </div>`;
      })
      .join("") +
    `<button class="report-think-test-all" id="reportThinkTestAll">实测全部模型的思考开关</button>`;

  // 选择模型（探活后切换）
  list.querySelectorAll(".report-model-item").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.dataset.modelName;
      if (name === reportSelectedModel) {
        document.getElementById("reportModelMenu").style.display = "none";
        return;
      }
      // 选择前先探活：不可响应则报错提示选择其他模型，保持原选中不变
      const nameEl = btn.querySelector(".model-name");
      const oldText = nameEl.textContent;
      btn.disabled = true;
      nameEl.textContent = "检测中…";
      let ok = true;
      let message = "";
      try {
        const resp = await fetch(`${API_BASE}/report-workbench/models/check?name=${encodeURIComponent(name)}&t=${Date.now()}`);
        if (!resp.ok) {
          // 检测接口本身不可用（后端未重启加载新路由），并非模型问题
          ok = false;
          message = `模型检测接口不可用（HTTP ${resp.status}），请重启后端服务后重试`;
        } else {
          const data = await resp.json();
          ok = !!data.ok;
          message = data.message || "";
        }
      } catch (e) {
        ok = false;
        message = e.message;
      }
      btn.disabled = false;
      nameEl.textContent = oldText;
      if (!ok) {
        alert(`模型「${name}」无法响应：${message}\n请选择其他模型。`);
        return;
      }
      reportSelectedModel = name;
      localStorage.setItem("report_selected_model", name);
      renderReportModelMenu();
      document.getElementById("reportModelMenu").style.display = "none";
    });
  });

  // 单模型思考开关
  list.querySelectorAll(".report-think-switch input").forEach((input) => {
    input.addEventListener("change", async () => {
      const name = input.dataset.modelName;
      const resultEl = list.querySelector(`.report-think-result[data-model-name="${CSS.escape(name)}"]`);
      if (input.checked) {
        // 开启前先实测：确认该模型思考是否走独立通道，避免思考内容混入报告 JSON
        let cached = reportThinkingTestCache[name];
        if (!cached) {
          input.disabled = true;
          setThinkResult(resultEl, "testing", "实测中…（约需数秒）");
          cached = await runThinkingTest(name, resultEl);
          input.disabled = false;
        }
        if (cached && cached.mode === "content_mixed") {
          const go = confirm(
            `模型「${name}」开启思考后，思考内容会混入报告正文：\n\n${cached.message}\n\n` +
              `这可能导致报告生成失败或内容不完整。仍要开启吗？`
          );
          if (!go) {
            input.checked = false;
            return;
          }
        }
        if (cached && cached.mode === "unsupported") {
          alert(`模型「${name}」无法响应，已保持关闭思考。\n${cached.message}`);
          input.checked = false;
          return;
        }
      }
      setReportThinkingEnabled(name, input.checked);
      renderReportModelMenu();
    });
  });

  // 单模型实测按钮
  list.querySelectorAll(".report-think-test").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const name = btn.dataset.modelName;
      const row = btn.closest(".report-model-row");
      const resultEl = row?.querySelector(".report-think-result");
      btn.disabled = true;
      setThinkResult(resultEl, "testing", "实测中…（关/开各请求一次）");
      await runThinkingTest(name, resultEl);
      btn.disabled = false;
    });
  });

  // 全部模型实测
  const testAllBtn = list.querySelector("#reportThinkTestAll");
  if (testAllBtn) {
    testAllBtn.addEventListener("click", async () => {
      testAllBtn.disabled = true;
      const total = reportModels.length;
      for (let i = 0; i < total; i++) {
        const name = reportModels[i].name;
        const resultEl = list.querySelector(`.report-think-result[data-model-name="${CSS.escape(name)}"]`);
        testAllBtn.textContent = `实测中… ${i + 1}/${total}`;
        setThinkResult(resultEl, "testing", "实测中…");
        await runThinkingTest(name, resultEl);
      }
      testAllBtn.disabled = false;
      testAllBtn.textContent = "实测全部模型的思考开关";
    });
  }
}

function setThinkResult(resultEl, mode, message) {
  if (!resultEl) return;
  resultEl.className = `report-think-result ${mode}`;
  resultEl.textContent = message;
  resultEl.style.display = message ? "" : "none";
}

/** 调用后端实测接口，写入缓存并渲染结论；返回缓存对象 */
async function runThinkingTest(modelName, resultEl) {
  try {
    const resp = await fetch(
      `${API_BASE}/report-workbench/models/thinking-test?name=${encodeURIComponent(modelName)}&t=${Date.now()}`
    );
    if (!resp.ok) {
      setThinkResult(resultEl, "unsupported", `实测接口不可用（HTTP ${resp.status}），请重启后端服务`);
      return null;
    }
    const data = await resp.json();
    const off = data.off || {};
    const on = data.on || {};
    const detail =
      data.mode === "unsupported"
        ? ""
        : `  关闭: ${off.reasoning_chars || 0}字思考/${off.content_chars || 0}字正文/${off.elapsed || 0}s · 开启: ${on.reasoning_chars || 0}字思考/${on.content_chars || 0}字正文/${on.elapsed || 0}s`;
    const cached = { mode: data.mode, supported: !!data.supported, message: data.message, detail };
    reportThinkingTestCache[modelName] = cached;
    setThinkResult(resultEl, data.mode, (data.message || "") + detail);
    return cached;
  } catch (e) {
    setThinkResult(resultEl, "unsupported", `实测失败：${e.message}`);
    return null;
  }
}

function toggleReportModelMenu() {
  const menu = document.getElementById("reportModelMenu");
  if (!menu) return;
  const opening = menu.style.display === "none";
  menu.style.display = opening ? "block" : "none";
  // 每次打开时拉取最新模型列表（配置页可能新增了模型）
  if (opening) loadReportModels();
}

// ===== 报告历史 =====
async function loadReportHistory() {
  const list = document.getElementById("reportHistoryList");
  if (!list) return;
  try {
    const resp = await fetch(`/api/report-workbench/history?t=${Date.now()}`);
    const data = await resp.json();
    const items = data.history || [];
    if (items.length === 0) {
      list.innerHTML = '<div class="report-history-empty">暂无历史报告</div>';
      return;
    }
    list.innerHTML = items
      .map((h) => {
        const t = h.updated_at ? new Date(h.updated_at).toLocaleString("zh-CN", { hour12: false }) : "";
        const active = h.conversation_id === reportConversationId ? "active" : "";
        return `
        <div class="report-history-item ${active}" data-conv-id="${escapeHtml(h.conversation_id)}" title="${escapeHtml(h.preview || h.route_name)}">
          <div class="report-history-item-title">${escapeHtml(h.route_name || "报告")}</div>
          <div class="report-history-item-preview">${escapeHtml(h.preview || "（空报告）")}</div>
          <div class="report-history-item-time">${escapeHtml(t)} · v${h.current_version || 1}</div>
        </div>`;
      })
      .join("");
    list.querySelectorAll(".report-history-item").forEach((el) => {
      el.addEventListener("click", () => openHistoryReport(el.dataset.convId));
    });
  } catch (e) {
    console.error("加载报告历史失败:", e);
  }
}

async function openHistoryReport(conversationId) {
  try {
    const resp = await fetch(`/api/report-workbench/history/${encodeURIComponent(conversationId)}?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const session = data.session;
    reportConversationId = conversationId;
    currentReportSession = session;
    updateReportDisplay(session);
    renderQuickTags(session.route_id);
    updateReportStatus("completed", session.route_name || "", session.question_round || 0);
    if (typeof session.elapsed_seconds === "number") setReportElapsed(session.elapsed_seconds);
    renderHistoryMessages(session);
    loadReportHistory(); // 刷新高亮
  } catch (e) {
    console.error("打开历史报告失败:", e);
    alert("打开历史报告失败：" + e.message);
  }
}

// 渲染历史会话的对话记录（用户输入 / AI 回复 / 追问）
function renderHistoryMessages(session) {
  const container = document.getElementById("reportChatMessages");
  if (!container) return;
  container.innerHTML = "";
  const messages = (session && session.messages) || [];
  messages.forEach((m) => {
    const div = addReportChatMessage(m.role === "user" ? "user" : "assistant", m.content || "");
    if (m.role !== "user") {
      setReportChatTime(div, m.created_at);
    }
    if (m.questions && m.questions.length > 0) {
      addReportQuestions(div, m.questions);
    }
  });
  // 历史追问卡默认已答复态；仅当会话最后一条消息仍是带追问的助手回复时保留可点
  const lastMsg = messages[messages.length - 1];
  const keepLastActive = lastMsg && lastMsg.role !== "user" && (lastMsg.questions || []).length > 0;
  const allQ = container.querySelectorAll(".report-chat-questions");
  allQ.forEach((qd, i) => {
    if (!(keepLastActive && i === allQ.length - 1)) markQuestionsAnswered(qd);
  });
}

async function initReportWorkbench() {
  if (reportWorkbenchLoaded) return;
  reportWorkbenchLoaded = true;

  // 初始化DOM引用
  const reportNewSessionBtn = document.getElementById("reportNewSessionBtn");
  const reportSendBtn = document.getElementById("reportSendBtn");
  const reportStopBtn = document.getElementById("reportStopBtn");
  const reportTextInput = document.getElementById("reportTextInput");
  const reportSaveBtn = document.getElementById("reportSaveBtn");
  const reportExportBtn = document.getElementById("reportExportBtn");

  // 绑定事件
  reportNewSessionBtn?.addEventListener("click", createNewReportSession);
  reportSendBtn?.addEventListener("click", sendReportMessage);
  reportStopBtn?.addEventListener("click", stopReportGeneration);
  reportTextInput?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendReportMessage();
    }
  });
  reportSaveBtn?.addEventListener("click", saveCurrentReport);

  // 导出菜单（Word / MD）
  reportExportBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    const menu = document.getElementById("reportExportMenu");
    if (menu) menu.style.display = menu.style.display === "none" ? "block" : "none";
  });
  document.querySelectorAll(".report-export-menu-item").forEach((item) => {
    item.addEventListener("click", () => {
      const menu = document.getElementById("reportExportMenu");
      if (menu) menu.style.display = "none";
      if (item.dataset.fmt === "docx") exportReportWord();
      else exportCurrentReport();
    });
  });
  document.addEventListener("click", (e) => {
    const menu = document.getElementById("reportExportMenu");
    if (menu && menu.style.display !== "none" && !menu.contains(e.target) && !e.target.closest("#reportExportBtn")) {
      menu.style.display = "none";
    }
  });

  // 胶囊栏点击（事件委托）：模板直选按钮仅完成路由选择；快捷标签插入输入框
  document.getElementById("reportQuickTags")?.addEventListener("click", (e) => {
    const tplBtn = e.target.closest(".report-template-btn");
    if (tplBtn) {
      selectReportTemplate(tplBtn.dataset.route);
      return;
    }
    if (e.target.closest(".report-template-reselect")) {
      reportPendingRouteId = "";
      renderTemplatePicker();
      return;
    }
    const btn = e.target.closest(".report-quick-tag");
    if (btn) toggleQuickTag(btn.dataset.tag, btn);
  });

  // 模型选择器
  const reportModelBtn = document.getElementById("reportModelBtn");
  reportModelBtn?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleReportModelMenu();
  });
  document.addEventListener("click", (e) => {
    const menu = document.getElementById("reportModelMenu");
    if (menu && menu.style.display !== "none" && !menu.contains(e.target)) {
      menu.style.display = "none";
    }
  });
  loadReportModels();
  loadReportRoutes();

  // 路由配置管理
  document.getElementById("reportRouteAdminBtn")?.addEventListener("click", openRouteAdmin);
  document.getElementById("routeAdminClose")?.addEventListener("click", closeRouteAdmin);
  document.getElementById("routeAdminNewBtn")?.addEventListener("click", () => openRouteEditor(null));

  // 报告模板库管理 + 收藏
  document.getElementById("reportTemplateAdminBtn")?.addEventListener("click", openTemplateAdmin);
  document.getElementById("templateAdminClose")?.addEventListener("click", closeTemplateAdmin);
  document.getElementById("templateAdminNewBtn")?.addEventListener("click", () => openTemplateEditor(null));
  document.getElementById("reportFavoriteBtn")?.addEventListener("click", favoriteCurrentReport);
  document.getElementById("favoriteClose")?.addEventListener("click", closeFavoritePanel);
  document.getElementById("fvCancel")?.addEventListener("click", closeFavoritePanel);
  document.getElementById("fvSave")?.addEventListener("click", submitFavorite);

  // 复制按钮事件委托
  document.addEventListener("click", (e) => {
    if (e.target.classList.contains("report-copy-btn")) {
      handleReportCopy(e.target);
    }
  });

  // 初始化空会话
  createNewReportSession();
  loadReportHistory();
}

function createNewReportSession() {
  reportConversationId = generateSessionId();
  currentReportSession = null;
  reportPendingRouteId = "";

  // 重置UI
  document.getElementById("reportEmptyState").style.display = "flex";
  document.getElementById("reportSections").style.display = "none";
  document.getElementById("reportChatMessages").innerHTML = "";
  document.getElementById("reportTextInput").value = "";
  const quickTagsBar = document.getElementById("reportQuickTags");
  if (quickTagsBar) { quickTagsBar.innerHTML = ""; quickTagsBar.style.display = "none"; }
  
  const elapsedEl = document.getElementById("reportElapsed");
  if (elapsedEl) elapsedEl.remove();
  stopReportElapsedTimer();
  
  updateReportStatus("idle", "", 0);
  renderTemplatePicker();
}

// ===== 模板直选（胶囊栏位置居中展示6个模板按钮；点选仅完成路由选择并切换出该模板胶囊，生成须由发送触发） =====
function renderTemplatePicker() {
  const bar = document.getElementById("reportQuickTags");
  if (!bar) return;
  if (currentReportSession) {
    bar.style.display = "none";
    return;
  }
  const routes = (reportRoutesCache || []).filter((r) => r.id !== "generic");
  if (routes.length === 0) {
    bar.style.display = "none";
    bar.innerHTML = "";
    return;
  }
  bar.classList.add("template-mode");
  bar.innerHTML =
    `<div class="report-template-picker-title">选择检查模板（可先不输入阅片结论；点选后加载对应术语胶囊，发送后生成报告）</div>` +
    routes
      .map(
        (r) =>
          `<button class="report-template-btn" data-route="${escapeHtml(r.id)}">${escapeHtml(r.name)}</button>`
      )
      .join("");
  bar.style.display = "flex";
}

function selectReportTemplate(routeId) {
  if (!routeId || currentReportSession) return;
  reportPendingRouteId = routeId;
  const route = (reportRoutesCache || []).find((r) => r.id === routeId);
  // 仅完成模板选择：切换出该模板的 quick_tags 胶囊，不触发报告生成
  renderQuickTags(routeId);
  updateReportStatus("idle", route ? route.name : routeId, 0);
  document.getElementById("reportTextInput")?.focus();
}

async function sendReportMessage() {
  const input = document.getElementById("reportTextInput");
  const message = input.value.trim();
  // 生成中禁止重复发送（停止按钮可见即流式进行中）
  const stopBtn = document.getElementById("reportStopBtn");
  if (stopBtn && stopBtn.style.display !== "none") return;
  // 模板直选线路：已选定模板时允许空输入发送（生成全基线报告+追问）
  const pendingRoute = !currentReportSession ? reportPendingRouteId : "";
  if (!message && !pendingRoute) return;
  reportPendingRouteId = "";

  // 上一轮追问卡置为已答复态：灰化禁点，防止旧选项污染新一轮输入
  document
    .querySelectorAll("#reportChatMessages .report-chat-questions:not(.answered)")
    .forEach((qd) => markQuestionsAnswered(qd));

  // 显示用户消息
  addReportChatMessage("user", message || `（未输入阅片结论，按模板「${(reportRoutesCache.find((r) => r.id === pendingRoute) || {}).name || pendingRoute}」生成基线报告）`);

  // 清空输入
  input.value = "";
  
  // 禁用发送按钮，切换为"停止"按钮（流式期间可取消）
  const sendBtn = document.getElementById("reportSendBtn");
  sendBtn.disabled = true;
  setReportGeneratingUI(true);
  reportStoppedByUser = false;

  // 发送即开始实时计时（与生成并行刷新）
  startReportElapsedTimer();

  try {
    if (!currentReportSession) {
      // 首次生成（模板直选时携带 route_id）
      await generateReport(message, pendingRoute);
    } else {
      // 迭代修改
      await iterateReport(message);
    }
  } catch (error) {
    console.error("发送消息失败:", error);
    if (!reportStoppedByUser) {
      addReportChatMessage("assistant", "抱歉，发生了错误：" + error.message);
      updateReportStatus("failed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
    }
    stopReportElapsedTimer();
  } finally {
    sendBtn.disabled = false;
    setReportGeneratingUI(false);
    reportAbortController = null;
  }
}

// 生成中/停止 按钮态切换
function setReportGeneratingUI(generating) {
  const sendBtn = document.getElementById("reportSendBtn");
  const stopBtn = document.getElementById("reportStopBtn");
  if (sendBtn) sendBtn.style.display = generating ? "none" : "";
  if (stopBtn) stopBtn.style.display = generating ? "" : "none";
}

// 停止生成：关闭 SSE / 中断 fetch 流，并将未答复的追问卡置为已终止态
function stopReportGeneration() {
  reportStoppedByUser = true;
  if (reportEventSource) {
    reportEventSource.close();
    reportEventSource = null;
  }
  if (reportAbortController) {
    reportAbortController.abort();
    reportAbortController = null;
  }
  stopReportElapsedTimer();
  updateReportStatus(currentReportSession ? "completed" : "idle", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
  addReportChatMessage("assistant", "已停止生成。可继续输入补充结论后重新发送。");
  setReportGeneratingUI(false);
  const sendBtn = document.getElementById("reportSendBtn");
  if (sendBtn) sendBtn.disabled = false;
}

async function generateReport(input, routeId = "") {
  updateReportStatus("generating", routeId ? "模板已选定" : "解析中...", 0);

  const url = `/api/report-workbench/sessions/${reportConversationId}/generate`;
  
  reportEventSource = new EventSource(
    `${url}?${new URLSearchParams({
      input_text: input,
      modality: "",
      body_part: "",
      route_id: routeId || "",
      llm_name: reportSelectedModel || "",
      enable_thinking: isReportThinkingEnabled(reportSelectedModel) ? "true" : "false"
    })}`
  );

  let assistantMessageDiv = null;
  const streamRenderer = createStreamReportRenderer();
  let thinkingPanel = null; // 思考过程面板（仅开启思考模式时创建）

  reportEventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      
      switch (data.type) {
        case "conversation":
          reportConversationId = data.conversation_id;
          break;

        case "chat":
          // 闲聊标准回复（非报告类输入）
          addReportChatMessage("assistant", data.content);
          stopReportElapsedTimer();
          const chatElapsedEl = document.getElementById("reportElapsed");
          if (chatElapsedEl) chatElapsedEl.remove();
          updateReportStatus("idle", "", 0);
          break;

        case "notice":
          // 系统通知（如模型自动降级切换）
          addReportChatMessage("assistant", `⚠️ ${data.content}`);
          break;

        case "reasoning":
          // 思考过程增量：独立面板展示，不影响报告正文渲染，也不计入首字耗时
          if (!thinkingPanel) thinkingPanel = createReportThinkingPanel();
          thinkingPanel?.push(data.content);
          break;

        case "token":
          // 流式 token：增量解析 JSON，实时渲染报告正文（不显示原始 JSON）
          if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
          if (reportElapsedTimer) stopReportElapsedTimer(); // 首字到达，计时定格
          const partial = streamRenderer.push(data.content);
          if (partial.findings || partial.diagnosis) {
            showReportSectionsLive();
            setLiveSection("reportFindingsContent", partial.findings);
            setLiveSection("reportDiagnosisContent", partial.diagnosis);
          }
          break;

        case "route_resolved":
          updateReportStatus("generating", data.route_name, 0);
          renderQuickTags(data.route_id);
          break;

        case "status":
          updateReportStatus(data.status === "generating" ? "generating" : data.status, document.getElementById("reportRouteName")?.textContent || "", currentReportSession?.question_round || 0);
          break;

        case "report_updated":
          currentReportSession = data.session;
          updateReportDisplay(data.session);
          updateReportStatus("generating", data.session.route_name, data.session.question_round);
          if (typeof data.elapsed_seconds === "number") {
            stopReportElapsedTimer(data.elapsed_seconds);
          }
          setReportChatTime(assistantMessageDiv, data.session.updated_at);
          loadReportHistory();
          break;

        case "report_questions":
          if (data.questions && data.questions.length > 0) {
            if (!assistantMessageDiv) {
              assistantMessageDiv = addReportChatMessage("assistant", "报告已生成，我还有几个问题想确认：");
            }
            addReportQuestions(assistantMessageDiv, data.questions);
            setReportChatTime(assistantMessageDiv, currentReportSession?.updated_at);
          }
          break;

        case "message":
          if (!assistantMessageDiv) {
            assistantMessageDiv = addReportChatMessage("assistant", data.content);
          } else {
            assistantMessageDiv.querySelector(".report-chat-bubble").textContent += data.content;
          }
          setReportChatTime(assistantMessageDiv, currentReportSession?.updated_at);
          break;

        case "done":
          if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
          if (typeof data.elapsed_seconds === "number") {
            stopReportElapsedTimer(data.elapsed_seconds);
          } else {
            stopReportElapsedTimer();
          }
          if (data.status === "chat") {
            updateReportStatus("idle", "", 0);
          } else {
            updateReportStatus("completed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
          }
          reportEventSource?.close();
          reportEventSource = null;
          break;

        case "error":
          if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
          addReportChatMessage("assistant", "错误：" + data.message);
          stopReportElapsedTimer();
          updateReportStatus("failed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
          reportEventSource?.close();
          reportEventSource = null;
          break;
      }
    } catch (error) {
      console.error("解析SSE事件失败:", error);
    }
  };

  reportEventSource.onerror = (error) => {
    console.error("SSE连接错误:", error);
    if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
    reportEventSource?.close();
    reportEventSource = null;
    stopReportElapsedTimer();
    updateReportStatus("failed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
  };
}

async function iterateReport(user_input) {
  updateReportStatus("iterating", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);

  const url = `/api/report-workbench/sessions/${reportConversationId}/iterate`;
  
  reportAbortController = new AbortController();
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    signal: reportAbortController.signal,
    body: JSON.stringify({
      input_text: user_input,
      images: [],
      base_version: currentReportSession.current_version || 0,
      question_round: currentReportSession.question_round || 0,
      llm_name: reportSelectedModel || "",
      enable_thinking: isReportThinkingEnabled(reportSelectedModel),
    })
  });

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const errData = await response.json();
      if (errData && errData.detail) detail = errData.detail;
    } catch (_) { /* 非 JSON 错误体，保留状态码提示 */ }
    throw new Error(detail);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let assistantMessageDiv = null;
  const streamRenderer = createStreamReportRenderer();
  let thinkingPanel = null; // 思考过程面板（仅开启思考模式时创建）
  let sseBuffer = ""; // 跨 chunk 的行缓冲，防止大 JSON 事件被截断丢弃

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    sseBuffer += decoder.decode(value, { stream: true });
    const lines = sseBuffer.split("\n");
    sseBuffer = lines.pop(); // 最后一段可能不完整，留到下次

    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      
      const jsonStr = line.slice(6);
      if (jsonStr === "[DONE]") {
        updateReportStatus("completed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
        continue;
      }

      try {
        const data = JSON.parse(jsonStr);

        switch (data.type) {
          case "notice":
            addReportChatMessage("assistant", `⚠️ ${data.content}`);
            break;

          case "reasoning":
            // 思考过程增量：独立面板展示，不影响报告正文渲染，也不计入首字耗时
            if (!thinkingPanel) thinkingPanel = createReportThinkingPanel();
            thinkingPanel?.push(data.content);
            break;

          case "token":
            if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
            if (reportElapsedTimer) stopReportElapsedTimer(); // 首字到达，计时定格
            const partial = streamRenderer.push(data.content);
            if (partial.findings || partial.diagnosis) {
              showReportSectionsLive();
              setLiveSection("reportFindingsContent", partial.findings);
              setLiveSection("reportDiagnosisContent", partial.diagnosis);
            }
            break;

          case "report_updated":
            currentReportSession = data.session;
            updateReportDisplay(data.session);
            updateReportStatus("iterating", data.session.route_name, data.session.question_round);
            if (typeof data.elapsed_seconds === "number") {
              stopReportElapsedTimer(data.elapsed_seconds);
            }
            setReportChatTime(assistantMessageDiv, data.session.updated_at);
            loadReportHistory();
            break;

          case "report_questions":
            if (data.questions && data.questions.length > 0) {
              if (!assistantMessageDiv) {
                assistantMessageDiv = addReportChatMessage("assistant", "报告已更新，我还有几个问题：");
              }
              addReportQuestions(assistantMessageDiv, data.questions);
              setReportChatTime(assistantMessageDiv, currentReportSession?.updated_at);
            }
            break;

          case "message":
            if (!assistantMessageDiv) {
              assistantMessageDiv = addReportChatMessage("assistant", data.content);
            } else {
              const bubble = assistantMessageDiv.querySelector(".report-chat-bubble");
              const questionsDiv = bubble.querySelector(".report-chat-questions");
              if (questionsDiv) {
                bubble.insertBefore(document.createTextNode(data.content), questionsDiv);
              } else {
                bubble.textContent += data.content;
              }
            }
            setReportChatTime(assistantMessageDiv, currentReportSession?.updated_at);
            break;

          case "done":
            if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
            if (typeof data.elapsed_seconds === "number") {
              stopReportElapsedTimer(data.elapsed_seconds);
            } else {
              stopReportElapsedTimer();
            }
            updateReportStatus("completed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
            break;

          case "error":
            if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }
            addReportChatMessage("assistant", "错误：" + data.message);
            stopReportElapsedTimer();
            updateReportStatus("failed", currentReportSession?.route_name || "", currentReportSession?.question_round || 0);
            break;
        }
      } catch (error) {
        console.error("解析SSE数据失败:", error);
      }
    }
  }

  // 流正常结束但没收到 done/error 时，思考面板仍需收尾（避免一直显示"思考中…"）
  if (thinkingPanel) { thinkingPanel.finish(); thinkingPanel = null; }

  // 处理流结束后残留的最后一行
  if (sseBuffer.startsWith("data: ") && sseBuffer.trim() !== "data: [DONE]") {
    try {
      const data = JSON.parse(sseBuffer.slice(6));
      if (data.type === "report_updated" && data.session) {
        currentReportSession = data.session;
        updateReportDisplay(data.session);
      }
    } catch (e) { /* 忽略不完整尾行 */ }
  }
}

function updateReportDisplay(session) {
  const emptyState = document.getElementById("reportEmptyState");
  const sections = document.getElementById("reportSections");
  const findingsContent = document.getElementById("reportFindingsContent");
  const diagnosisContent = document.getElementById("reportDiagnosisContent");

  emptyState.style.display = "none";
  sections.style.display = "block";

  renderReportContent(findingsContent, session.report.imaging_findings || "");
  renderReportContent(diagnosisContent, session.report.imaging_diagnosis || "");
}

function updateReportStatus(status, routeName, questionRound) {
  const statusBadge = document.getElementById("reportStatusBadge");
  const routeNameEl = document.getElementById("reportRouteName");
  const questionRoundEl = document.getElementById("reportQuestionRound");

  // 更新状态徽章
  statusBadge.className = "report-status-badge";
  switch (status) {
    case "idle":
      statusBadge.classList.add("idle");
      statusBadge.textContent = "等待输入";
      break;
    case "generating":
    case "iterating":
      statusBadge.classList.add("generating");
      statusBadge.textContent = "生成中";
      break;
    case "completed":
      statusBadge.classList.add("completed");
      statusBadge.textContent = "已完成";
      break;
    case "failed":
      statusBadge.classList.add("failed");
      statusBadge.textContent = "失败";
      break;
  }

  routeNameEl.textContent = routeName || "未选择模板";
  questionRoundEl.textContent = `追问轮次: ${questionRound}/2`;
}

function scrollReportChatToBottom() {
  const messagesContainer = document.getElementById("reportChatMessages");
  if (messagesContainer) {
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }
}

function setReportElapsed(seconds) {
  const el = ensureReportElapsedEl();
  el.textContent = `耗时: ${Number(seconds).toFixed(1)}s`;
}

// ===== 耗时实时计时器：生成期间并行刷新，完成后定格为后端精确值 =====
let reportElapsedTimer = null;
let reportElapsedStart = 0;

function ensureReportElapsedEl() {
  let el = document.getElementById("reportElapsed");
  if (!el) {
    el = document.createElement("span");
    el.id = "reportElapsed";
    el.className = "report-elapsed";
    const info = document.querySelector(".report-status-info");
    if (info) info.appendChild(el);
  }
  return el;
}

function startReportElapsedTimer() {
  stopReportElapsedTimer();
  reportElapsedStart = performance.now();
  const el = ensureReportElapsedEl();
  el.textContent = "耗时: 0.0s";
  reportElapsedTimer = setInterval(() => {
    el.textContent = `耗时: ${((performance.now() - reportElapsedStart) / 1000).toFixed(1)}s`;
  }, 100);
}

function stopReportElapsedTimer(finalSeconds) {
  if (reportElapsedTimer) {
    clearInterval(reportElapsedTimer);
    reportElapsedTimer = null;
  }
  if (typeof finalSeconds === "number") {
    setReportElapsed(finalSeconds);
  }
}

// ===== 流式 JSON 增量解析：从 token 流中实时提取报告正文 =====

function extractPartialJsonString(text, key) {
  /** 从（可能不完整的）JSON 文本中提取 key 对应字符串值的部分内容。
   *  返回 {value, done} 或 null（key 尚未出现）。 */
  const marker = `"${key}"`;
  const idx = text.indexOf(marker);
  if (idx === -1) return null;
  const colon = text.indexOf(":", idx + marker.length);
  if (colon === -1) return null;
  const openQuote = text.indexOf('"', colon + 1);
  if (openQuote === -1) return null;
  let out = "";
  let i = openQuote + 1;
  while (i < text.length) {
    const ch = text[i];
    if (ch === "\\") {
      const next = text[i + 1];
      if (next === undefined) break;
      if (next === "n") out += "\n";
      else if (next === "t") out += "\t";
      else if (next === "r") out += "";
      else if (next === "u") {
        const hex = text.substr(i + 2, 4);
        if (hex.length < 4) break;
        out += String.fromCharCode(parseInt(hex, 16));
        i += 4;
      } else out += next;
      i += 2;
      continue;
    }
    if (ch === '"') return { value: out, done: true };
    out += ch;
    i++;
  }
  return { value: out, done: false };
}

/** 定位报告 JSON 的起始位置。
 *  开启思考且模型无独立 reasoning 通道时，思考文本会混入正文，
 *  其中可能自带 "imaging_findings" 等字段名，直接用 indexOf 会误取思考内容。
 *  因此只认真实 JSON 对象的开头：{ "report": ...
 *  未出现时返回 -1（此时不渲染，等后续 token）。 */
function findReportJsonStart(text) {
  const m = /\{\s*"report"\s*:/.exec(text);
  return m ? m.index : -1;
}

function createStreamReportRenderer() {
  let buffer = "";
  let jsonStart = -1;
  return {
    push(chunk) {
      buffer += chunk;
      if (jsonStart === -1) jsonStart = findReportJsonStart(buffer);
      if (jsonStart === -1) return { findings: null, diagnosis: null };
      // 只在真实 JSON 片段内提取，跳过混入正文的思考前缀
      const json = buffer.slice(jsonStart);
      return {
        findings: extractPartialJsonString(json, "imaging_findings"),
        diagnosis: extractPartialJsonString(json, "imaging_diagnosis"),
      };
    },
  };
}

function showReportSectionsLive() {
  const emptyState = document.getElementById("reportEmptyState");
  const sections = document.getElementById("reportSections");
  if (emptyState) emptyState.style.display = "none";
  if (sections) sections.style.display = "block";
}

function setLiveSection(elementId, partial) {
  const el = document.getElementById(elementId);
  if (!el || !partial) return;
  renderReportContent(el, partial.value + (partial.done ? "" : "▌"));
}

function renderReportContent(el, text) {
  /** 轻量渲染：转义HTML后支持 **加粗** 与换行 */
  const escaped = String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  const html = escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>");
  el.innerHTML = html;
}

function addReportChatMessage(role, content) {
  const messagesContainer = document.getElementById("reportChatMessages");
  
  const messageDiv = document.createElement("div");
  messageDiv.className = `report-chat-message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "report-chat-avatar";
  avatar.textContent = role === "user" ? "👤" : "🤖";

  const bubble = document.createElement("div");
  bubble.className = "report-chat-bubble";
  bubble.textContent = content;

  messageDiv.appendChild(avatar);
  messageDiv.appendChild(bubble);
  messagesContainer.appendChild(messageDiv);

  // 滚动到底部
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  return messageDiv;
}

/** 消息时间戳格式化：ISO/空格分隔 → "YYYY-MM-DD HH:MM:SS" */
function formatReportMessageTime(value) {
  if (!value) return "";
  const s = String(value).replace("T", " ").replace(/\.\d+.*$/, "");
  return s.length > 19 ? s.slice(0, 19) : s;
}

/** 在消息气泡下方追加/更新时间戳小字 */
function setReportChatTime(messageDiv, value) {
  if (!messageDiv || !value) return;
  const text = formatReportMessageTime(value);
  if (!text) return;
  let timeDiv = messageDiv.querySelector(".report-chat-time");
  if (!timeDiv) {
    timeDiv = document.createElement("div");
    timeDiv.className = "report-chat-time";
    messageDiv.appendChild(timeDiv);
  }
  timeDiv.textContent = text;
}

/**
 * 思考过程面板（可折叠）。返回 { push, finish } 控制器。
 * 仅在开启思考模式且模型返回 reasoning_content 时创建。
 */
function createReportThinkingPanel() {
  const messagesContainer = document.getElementById("reportChatMessages");
  if (!messagesContainer) return null;

  const panel = document.createElement("div");
  panel.className = "report-thinking-panel";

  const header = document.createElement("div");
  header.className = "report-thinking-header";
  header.innerHTML = '<span class="report-thinking-toggle">▼</span><span>思考过程</span><span class="report-thinking-status">思考中…</span>';

  const body = document.createElement("div");
  body.className = "report-thinking-body";

  panel.appendChild(header);
  panel.appendChild(body);
  messagesContainer.appendChild(panel);

  header.addEventListener("click", () => {
    panel.classList.toggle("report-thinking-collapsed");
    const t = header.querySelector(".report-thinking-toggle");
    if (t) t.textContent = panel.classList.contains("report-thinking-collapsed") ? "▶" : "▼";
  });

  const t0 = Date.now();
  let chars = 0;

  return {
    push(content) {
      if (!content) return;
      chars += content.length;
      body.textContent += content;
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    },
    finish() {
      const status = header.querySelector(".report-thinking-status");
      if (status) {
        status.textContent = `已思考 ${chars} 字 · ${((Date.now() - t0) / 1000).toFixed(1)}s`;
        status.classList.add("done");
      }
      // 默认折叠，避免遮挡报告正文
      panel.classList.add("report-thinking-collapsed");
      const t = header.querySelector(".report-thinking-toggle");
      if (t) t.textContent = "▶";
    },
    remove() {
      panel.remove();
    },
  };
}

function addReportQuestions(messageDiv, questions) {
  const bubble = messageDiv.querySelector(".report-chat-bubble");
  
  const questionsDiv = document.createElement("div");
  questionsDiv.className = "report-chat-questions";

  questions.forEach((q, idx) => {
    const qItem = document.createElement("div");
    qItem.className = "report-chat-question-item";
    const multi = q.multi_select === true;
    // 编号徽标 + 问题标题 + 可多选标记
    qItem.innerHTML = `<div class="report-q-head"><span class="report-q-index">${idx + 1}</span><strong>${escapeHtml(q.question)}</strong>${multi ? '<span class="report-q-multi">可多选</span>' : ""}</div>`;
    // 候选选项按钮：点选后填入输入框，医生可继续补充后发送
    const opts = q.options || [];
    if (opts.length > 0) {
      const optDiv = document.createElement("div");
      optDiv.className = "report-question-options";
      // 点选后以完整问题文本填入输入框（如"请确认LGE延迟强化分布模式：心内膜下"）
      const fullQuestion = String(q.question || "").trim().replace(/[?？\s]+$/, "");
      // 多选题：选项可反复点选切换，输入框中该问题的条目随之整体替换
      const selected = new Set();
      let currentEntry = "";
      opts.forEach((o) => {
        const b = document.createElement("button");
        b.className = "report-question-option-btn";
        b.textContent = o;
        b.addEventListener("click", () => {
          const input = document.getElementById("reportTextInput");
          if (!input) return;
          if (multi) {
            if (selected.has(o)) {
              selected.delete(o);
              b.classList.remove("selected");
            } else {
              selected.add(o);
              b.classList.add("selected");
            }
            // 先移除该问题上一次写入的条目，再追加最新组合
            if (currentEntry && input.value.includes(currentEntry)) {
              input.value = input.value
                .replace(currentEntry, "")
                .replace(/；\s*；/g, "；")
                .replace(/^[；\s]+|[；\s]+$/g, "");
            }
            const nextEntry = selected.size ? `${fullQuestion}：${[...selected].join("、")}` : "";
            if (nextEntry) {
              const cur = input.value.trim();
              input.value = cur ? `${cur}；${nextEntry}` : nextEntry;
            }
            currentEntry = nextEntry;
          } else {
            // 单选题：完整问题+选项值填入（如"请确认LGE延迟强化分布模式：心内膜下"）；再次点击已选项可撤回
            const entry = fullQuestion ? `${fullQuestion}：${o}` : o;
            const wasSelected = b.classList.contains("selected");
            optDiv
              .querySelectorAll(".report-question-option-btn.selected")
              .forEach((x) => x.classList.remove("selected"));
            // 先移除该问题上一次写入的条目
            if (currentEntry && input.value.includes(currentEntry)) {
              input.value = input.value
                .replace(currentEntry, "")
                .replace(/；\s*；/g, "；")
                .replace(/^[；\s]+|[；\s]+$/g, "");
            }
            if (wasSelected) {
              // 撤回：不写回条目
              currentEntry = "";
            } else {
              b.classList.add("selected");
              const cur = input.value.trim();
              input.value = cur ? `${cur}；${entry}` : entry;
              currentEntry = entry;
            }
          }
          input.focus();
        });
        optDiv.appendChild(b);
      });
      qItem.appendChild(optDiv);
    }
    questionsDiv.appendChild(qItem);
  });

  bubble.appendChild(questionsDiv);
}

// 追问卡已答复态：灰化 + 禁点 + 标注"已答复"，防止旧轮次选项被误点写入输入框
function markQuestionsAnswered(questionsDiv) {
  if (!questionsDiv || questionsDiv.classList.contains("answered")) return;
  questionsDiv.classList.add("answered");
  questionsDiv.querySelectorAll(".report-question-option-btn").forEach((b) => {
    b.disabled = true;
  });
  const tag = document.createElement("span");
  tag.className = "report-q-answered-tag";
  tag.textContent = "已答复";
  questionsDiv.prepend(tag);
}

// ===== 快捷输入标签 =====
async function loadReportRoutes() {
  try {
    const resp = await fetch(`/api/report-workbench/routes?t=${Date.now()}`);
    const data = await resp.json();
    reportRoutesCache = data.routes || [];
    // 路由加载完成后，若处于空会话则渲染模板直选区
    if (!currentReportSession) renderTemplatePicker();
  } catch (e) {
    console.error("加载路由列表失败:", e);
  }
}

function renderQuickTags(routeId) {
  const bar = document.getElementById("reportQuickTags");
  if (!bar) return;
  bar.classList.remove("template-mode");
  const route = reportRoutesCache.find((r) => r.id === routeId);
  const tags = (route && route.quick_tags) || [];
  if (tags.length === 0) {
    bar.style.display = "none";
    bar.innerHTML = "";
    return;
  }
  // 已选定模板但尚未发送时，提供重选入口
  const reselect =
    reportPendingRouteId && !currentReportSession
      ? `<button class="report-template-reselect">↺ 重选模板</button>`
      : "";
  bar.innerHTML =
    reselect +
    tags
      .map((t) => `<button class="report-quick-tag" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</button>`)
      .join("");
  bar.style.display = "flex";
}

function toggleQuickTag(tag, btn) {
  // 胶囊点选切换：首次点击插入输入框并高亮；再次点击撤销（从输入框移除该胶囊文本）
  const input = document.getElementById("reportTextInput");
  if (!input || !tag) return;
  if (btn && btn.classList.contains("selected")) {
    const parts = input.value.split(/([；;\n])/);
    const kept = [];
    for (let i = 0; i < parts.length; i += 2) {
      if (parts[i].trim() !== tag) kept.push(parts[i]);
    }
    input.value = kept
      .join("；")
      .replace(/^[；;\s]+/, "")
      .replace(/[；;\s]+$/, "")
      .replace(/([；;])\1+/g, "$1");
    btn.classList.remove("selected");
  } else {
    const cur = input.value;
    const sep = cur && !/[\n；;]$/.test(cur) ? "；" : "";
    input.value = cur + sep + tag;
    if (btn) btn.classList.add("selected");
  }
  input.focus();
}

// ===== 路由配置管理 =====
let routeAdminCache = { routes: [], validator_linkage: null };

function setRouteAdminHint(text, ok = false) {
  const el = document.getElementById("routeAdminHint");
  if (!el) return;
  el.textContent = text || "";
  el.classList.toggle("ok", ok);
}

async function openRouteAdmin() {
  const panel = document.getElementById("routeAdminPanel");
  if (!panel) return;
  panel.style.display = "flex";
  setRouteAdminHint("加载中…");
  try {
    const resp = await fetch(`/api/report-workbench/admin/routes?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    routeAdminCache = await resp.json();
    renderRouteAdminList();
    setRouteAdminHint("");
  } catch (e) {
    console.error("加载路由配置失败:", e);
    setRouteAdminHint("加载失败: " + e.message);
  }
}

function closeRouteAdmin() {
  const panel = document.getElementById("routeAdminPanel");
  if (panel) panel.style.display = "none";
  const editor = document.getElementById("routeAdminEditor");
  if (editor) editor.style.display = "none";
}

function renderRouteAdminList() {
  const list = document.getElementById("routeAdminList");
  const editor = document.getElementById("routeAdminEditor");
  if (!list) return;
  list.style.display = "block";
  if (editor) editor.style.display = "none";

  const routes = routeAdminCache.routes || [];
  const linkage = routeAdminCache.validator_linkage || {};
  const ruleField = (key, label, hint) => `
    <div class="ra-vrule-row">
      <label>${label}<span class="ra-vrule-hint">${hint}</span></label>
      <textarea class="config-input ra-vrule-input" data-rule="${key}">${escapeHtml((linkage[key] || []).join("\n"))}</textarea>
    </div>`;
  const linkageHtml = `
    <div class="ra-card ra-vrule-card">
      <button type="button" class="ra-vrule-toggle" id="raVruleToggle">
        <span class="ra-vrule-arrow">▶</span>
        <span class="ra-vrule-toggle-text">校验器联动规则（可编辑，保存后立即生效）</span>
        <span class="ra-vrule-toggle-hint">点击展开/收起</span>
      </button>
      <div class="ra-vrule-body" id="raVruleBody" hidden>
      <div class="ra-vrule-grid">
        ${ruleField("organ_anchors", "三行切分锚点·器官行", "所见第2行行首完整短语")}
        ${ruleField("region_anchors", "三行切分锚点·区域行", "所见第3行行首完整短语")}
        ${ruleField("exclusive_q_keywords", "互斥单选关键词", "命中且标多选→强制单选")}
        ${ruleField("multi_q_keywords", "可并存多选关键词", "命中→保持多选（优先级更高）")}
        ${ruleField("occ_strong", "强占位/恶性征象词", "命中→诊断必须归纳式三行")}
        ${ruleField("occ_weak", "弱占位词", "与退变词共同判定范式")}
        ${ruleField("degen", "退变/良性征象词", "仅命中→禁用归纳式")}
        ${ruleField("inductive_markers", "归纳式标记", "诊断含标记→必须三行")}
      </div>
      <div class="ra-vrule-readonly">
        <p><strong>固定校验项</strong>：${(linkage.checks || []).map(escapeHtml).join("；")}</p>
        <p><strong>固定归一化</strong>：${(linkage.normalizers || []).map(escapeHtml).join("；")}</p>
      </div>
      <div class="ra-vrule-actions">
        <button class="route-admin-save" id="raSaveRules">💾 保存规则并启用</button>
      </div>
      </div>
    </div>`;

  list.innerHTML =
    linkageHtml +
    routes
      .map((r) => {
        const enabled = r.enabled !== false;
        return `
        <div class="route-admin-item" data-route-id="${escapeHtml(r.id)}">
          <div class="route-admin-item-head">
            <span class="route-admin-item-name">${escapeHtml(r.name)}</span>
            <code class="route-admin-item-id">${escapeHtml(r.id)}</code>
            <span class="route-admin-item-badge ${enabled ? "on" : "off"}">${enabled ? "已启用" : "已停用"}</span>
            <span class="route-admin-item-actions">
              <button class="btn btn-sm" data-act="edit">编辑</button>
              ${r.id !== "generic" ? `<button class="btn btn-sm" data-act="toggle">${enabled ? "停用" : "启用"}</button>` : ""}
            </span>
          </div>
          <div class="route-admin-item-meta">
            别名：${(r.aliases || []).map(escapeHtml).join("、") || "—"} ｜ 部位：${(r.body_parts || []).map(escapeHtml).join("、") || "—"} ｜ 胶囊 ${((r.quick_tags || []).length)} 条
          </div>
        </div>`;
      })
      .join("");

  list.querySelector("#raVruleToggle")?.addEventListener("click", () => {
    const body = list.querySelector("#raVruleBody");
    const card = list.querySelector(".ra-vrule-card");
    const open = body.hidden;
    body.hidden = !open;
    card.classList.toggle("open", open);
  });

  list.querySelector("#raSaveRules")?.addEventListener("click", async () => {
    const payload = {};
    list.querySelectorAll(".ra-vrule-input").forEach((ta) => {
      payload[ta.dataset.rule] = ta.value
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
    });
    setRouteAdminHint("保存规则中…");
    try {
      const resp = await fetch("/api/report-workbench/admin/validator-rules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
      await openRouteAdmin();
      setRouteAdminHint("校验器联动规则已保存并启用", true);
    } catch (e) {
      setRouteAdminHint("规则保存失败: " + e.message);
    }
  });

  list.querySelectorAll(".route-admin-item [data-act]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const item = e.target.closest(".route-admin-item");
      const rid = item?.dataset.routeId;
      const act = btn.dataset.act;
      if (act === "edit") {
        const route = (routeAdminCache.routes || []).find((x) => x.id === rid);
        openRouteEditor(route);
      } else if (act === "toggle") {
        try {
          setRouteAdminHint("切换中…");
          const resp = await fetch(`/api/report-workbench/admin/routes/${encodeURIComponent(rid)}/toggle`, { method: "POST" });
          if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
          await openRouteAdmin();
          loadReportRoutes();
        } catch (err) {
          setRouteAdminHint("操作失败: " + err.message);
        }
      }
    });
  });
}

function openRouteEditor(route) {
  const list = document.getElementById("routeAdminList");
  const editor = document.getElementById("routeAdminEditor");
  if (!editor) return;
  if (list) list.style.display = "none";
  editor.style.display = "block";
  const isNew = !route;
  const r = route || { id: "", name: "", aliases: [], modalities: ["MRI"], body_parts: [], quick_tags: [], prompt_md: "", enabled: true };
  editor.innerHTML = `
    <div class="route-admin-form">
      <div class="route-admin-editor-top">
        <button type="button" class="route-admin-back" id="raBack">← 返回列表</button>
        <div class="route-admin-editor-title">
          <span class="ra-title-text">${isNew ? "新增路由" : "编辑路由"}</span>
          ${isNew ? "" : `<code class="ra-title-id">${escapeHtml(r.id)}</code>`}
          <span class="ra-title-sub">${isNew ? "生成草稿或直接填写，保存后立即生效" : "修改保存后立即热重载生效"}</span>
        </div>
      </div>

      <div class="route-admin-tabs">
        <button type="button" class="route-admin-tab active" data-tab="basic"><span class="ra-tab-ico">⚙️</span>基本配置</button>
        <button type="button" class="route-admin-tab" data-tab="tags"><span class="ra-tab-ico">🏷️</span>诊断结论胶囊（${(r.quick_tags || []).length}）</button>
        <button type="button" class="route-admin-tab" data-tab="prompt"><span class="ra-tab-ico">📝</span>专科 Prompt</button>
      </div>

      <div class="route-admin-tabpane" data-pane="basic">
        <div class="ra-card">
          <div class="ra-card-title">路由标识</div>
          <div class="route-admin-form-grid">
            <div class="route-admin-form-row">
              <label>路由ID${isNew ? "" : "（不可改）"}</label>
              <input class="config-input" id="raId" value="${escapeHtml(r.id)}" ${isNew ? "" : "disabled"} placeholder="如 liver_mri" />
            </div>
            <div class="route-admin-form-row">
              <label>路由名称</label>
              <input class="config-input" id="raName" value="${escapeHtml(r.name)}" placeholder="如 肝脏 MRI" />
            </div>
          </div>
        </div>
        <div class="ra-card">
          <div class="ra-card-title">自动路由依据</div>
          <div class="route-admin-form-grid">
            <div class="route-admin-form-row">
              <label>检查类型（逗号分隔）</label>
              <input class="config-input" id="raModalities" value="${escapeHtml((r.modalities || []).join("，"))}" placeholder="MRI、CT" />
            </div>
            <div class="route-admin-form-row">
              <label>部位关键词（逗号分隔）</label>
              <input class="config-input" id="raBodyParts" value="${escapeHtml((r.body_parts || []).join("，"))}" placeholder="肝脏" />
            </div>
          </div>
          <div class="route-admin-form-row">
            <label>别名（逗号分隔，输入文本同义词）</label>
            <input class="config-input" id="raAliases" value="${escapeHtml((r.aliases || []).join("，"))}" placeholder="肝脏MRI、肝MR" />
          </div>
        </div>
        ${isNew ? `
        <div class="ra-card ra-draft-card">
          <div class="ra-card-title">⚡ AI 草稿生成</div>
          <p class="ra-draft-desc">上传该部位的报告模板/诊断规范/术语表（可选，支持 PDF、Word(docx)、Excel(xlsx)、txt/md/csv），系统会按「路由名称/部位/别名」优先抽取相关材料，再生成 ID、别名、胶囊与专科 Prompt 草稿并自动填充三个页签。</p>
          <textarea class="config-input route-admin-ref" id="raRef" placeholder="可选：参考资料文本；留空则按部位名生成"></textarea>
          <div class="ra-draft-actions">
            <input type="file" id="raRefFile" accept=".pdf,.docx,.xlsx,.txt,.md,.csv,.markdown" hidden />
            <button class="route-admin-uploadbtn" id="raUploadRef" title="上传 pdf/docx/xlsx/txt/md/csv 参考资料，按路由名称抽取相关材料后填入上方文本框">📎 上传文档</button>
            <button class="route-admin-genbtn" id="raGenDraft">⚡ 生成草稿</button>
          </div>
        </div>` : ""}
      </div>

      <div class="route-admin-tabpane" data-pane="tags" hidden>
        <div class="ra-card">
          <div class="ra-card-title">胶囊预览（医生输入框上方点选效果）</div>
          <div class="route-admin-tags-preview" id="raTagsPreview"></div>
        </div>
        <div class="ra-card">
          <div class="ra-card-title">胶囊编辑（每行一条）</div>
          <textarea class="config-input route-admin-tags" id="raTags" placeholder="每行一条诊断结论，如：&#10;肝血管瘤&#10;肝细胞癌（LI-RADS 5类）">${escapeHtml((r.quick_tags || []).join("\n"))}</textarea>
        </div>
      </div>

      <div class="route-admin-tabpane" data-pane="prompt" hidden>
        <details class="route-admin-prompt-help">
          <summary>📋 必备四个章节（保存后自动注入 system prompt）</summary>
          <ol>
            <li>#### {名称}专用基线与模块要素（三行6要素 + 默认基线填充句 + 关键病变速查）</li>
            <li>#### 要素完整性与追问设计（追问参照清单，标注 multi_select 与"（可多选）"）</li>
            <li>#### 两范式影像所见与影像诊断规范（演绎/归纳 + 专业建议三要素 + XX占位铁律）</li>
            <li>#### 标准范例（演绎式范例 + 归纳式范例，所见三行/诊断去标签）</li>
          </ol>
        </details>
        <textarea class="config-input route-admin-prompt" id="raPrompt">${escapeHtml(r.prompt_md || "")}</textarea>
      </div>

      <div class="route-admin-form-actions">
        <button class="route-admin-save" id="raSave">💾 ${isNew ? "保存并启用" : "保存修改"}</button>
        <button class="route-admin-cancel" id="raCancel">取消</button>
      </div>
    </div>`;

  const splitList = (v) =>
    String(v || "")
      .split(/[,，、\n]/)
      .map((s) => s.trim())
      .filter(Boolean);

  // 页签切换
  const switchTab = (tab) => {
    editor.querySelectorAll(".route-admin-tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
    editor.querySelectorAll(".route-admin-tabpane").forEach((p) => {
      p.hidden = p.dataset.pane !== tab;
    });
  };
  editor.querySelectorAll(".route-admin-tab").forEach((t) => {
    t.addEventListener("click", () => switchTab(t.dataset.tab));
  });

  // 胶囊实时预览（与前端渲染样式一致）
  const refreshTagsPreview = () => {
    const preview = editor.querySelector("#raTagsPreview");
    const tagsBox = editor.querySelector("#raTags");
    if (!preview || !tagsBox) return;
    const tags = tagsBox.value.split("\n").map((s) => s.trim()).filter(Boolean);
    preview.innerHTML = tags.length
      ? tags.map((t) => `<span class="report-quick-tag">${escapeHtml(t)}</span>`).join("")
      : `<span class="route-admin-tags-empty">暂无胶囊，医生将无法点选快速输入</span>`;
    const tabBtn = editor.querySelector('.route-admin-tab[data-tab="tags"]');
    if (tabBtn) tabBtn.textContent = `诊断结论胶囊（${tags.length}）`;
  };
  editor.querySelector("#raTags").addEventListener("input", refreshTagsPreview);
  refreshTagsPreview();

  editor.querySelector("#raCancel").addEventListener("click", () => renderRouteAdminList());
  editor.querySelector("#raBack").addEventListener("click", () => renderRouteAdminList());

  if (isNew) {
    editor.querySelector("#raUploadRef").addEventListener("click", () => {
      editor.querySelector("#raRefFile").click();
    });
    editor.querySelector("#raRefFile").addEventListener("change", async (ev) => {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      const btn = editor.querySelector("#raUploadRef");
      btn.disabled = true;
      setRouteAdminHint(`解析文档中：${file.name}…`);
      try {
        const fd = new FormData();
        fd.append("file", file);
        // focus：路由名称+部位+别名，后端据此优先抽取相关材料
        const focus = [
          editor.querySelector("#raName").value.trim(),
          editor.querySelector("#raBodyParts").value.trim(),
          editor.querySelector("#raAliases").value.trim(),
        ]
          .filter(Boolean)
          .join(" ");
        fd.append("focus", focus);
        const resp = await fetch("/api/report-workbench/admin/routes/parse-doc", {
          method: "POST",
          body: fd,
        });
        if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
        const data = await resp.json();
        editor.querySelector("#raRef").value = data.text;
        const detail = data.matched_lines
          ? `，命中 ${data.matched_lines} 条与本路由相关${data.filtered_lines ? `（已过滤 ${data.filtered_lines} 条无关记录）` : ""}`
          : "，未命中与本路由相关的内容（请检查路由名称/部位是否填写，或该文档确实不含本部位材料）";
        setRouteAdminHint(`已解析 ${file.name}（${data.chars} 字${detail}），可点击生成草稿`, !!data.matched_lines);
      } catch (e) {
        setRouteAdminHint("文档解析失败: " + e.message);
      } finally {
        btn.disabled = false;
        ev.target.value = "";
      }
    });

    editor.querySelector("#raGenDraft").addEventListener("click", async () => {
      const btn = editor.querySelector("#raGenDraft");
      btn.disabled = true;
      setRouteAdminHint("草稿生成中（约30~60秒）…");
      try {
        const resp = await fetch("/api/report-workbench/admin/routes/draft", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: editor.querySelector("#raName").value.trim(),
            body_part: splitList(editor.querySelector("#raBodyParts").value)[0] || editor.querySelector("#raName").value.trim(),
            modality: splitList(editor.querySelector("#raModalities").value)[0] || "MRI",
            reference_text: editor.querySelector("#raRef").value,
          }),
        });
        if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
        const { draft } = await resp.json();
        editor.querySelector("#raId").value = draft.id || "";
        editor.querySelector("#raName").value = draft.name || "";
        editor.querySelector("#raAliases").value = (draft.aliases || []).join("，");
        editor.querySelector("#raModalities").value = (draft.modalities || []).join("，");
        editor.querySelector("#raBodyParts").value = (draft.body_parts || []).join("，");
        editor.querySelector("#raTags").value = (draft.quick_tags || []).join("\n");
        editor.querySelector("#raPrompt").value = draft.prompt_md || "";
        refreshTagsPreview();
        switchTab("tags");
        setRouteAdminHint(draft.fallback ? "LLM不可用，已生成最小骨架草稿，请补全后保存" : "草稿已生成并填充，请检查修改后保存");
      } catch (e) {
        setRouteAdminHint("草稿生成失败: " + e.message);
      } finally {
        btn.disabled = false;
      }
    });
  }

  editor.querySelector("#raSave").addEventListener("click", async () => {
    const payload = {
      id: editor.querySelector("#raId").value.trim(),
      name: editor.querySelector("#raName").value.trim(),
      aliases: splitList(editor.querySelector("#raAliases").value),
      modalities: splitList(editor.querySelector("#raModalities").value),
      body_parts: splitList(editor.querySelector("#raBodyParts").value),
      quick_tags: editor.querySelector("#raTags").value.split("\n").map((s) => s.trim()).filter(Boolean),
      prompt_md: editor.querySelector("#raPrompt").value,
      enabled: true,
    };
    if (!payload.id || !payload.name) {
      setRouteAdminHint("ID 与名称必填");
      return;
    }
    setRouteAdminHint("保存中…");
    try {
      const url = isNew
        ? "/api/report-workbench/admin/routes"
        : `/api/report-workbench/admin/routes/${encodeURIComponent(payload.id)}`;
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
      await openRouteAdmin();
      loadReportRoutes();
      setRouteAdminHint(`路由「${payload.name}」已保存并启用`, true);
    } catch (e) {
      setRouteAdminHint("保存失败: " + e.message);
    }
  });
}

function handleReportCopy(button) {
  const section = button.closest(".report-section");
  const content = section.querySelector(".report-section-content").innerText;

  navigator.clipboard.writeText(content).then(() => {
    button.textContent = "已复制";
    button.classList.add("copied");
    setTimeout(() => {
      button.textContent = "复制";
      button.classList.remove("copied");
    }, 2000);
  });
}

async function saveCurrentReport() {
  if (!currentReportSession) {
    alert("当前没有报告可保存");
    return;
  }

  try {
    const response = await fetch(`/api/report-workbench/sessions/${reportConversationId}/save`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        imaging_findings: currentReportSession.report.imaging_findings || "",
        imaging_diagnosis: currentReportSession.report.imaging_diagnosis || "",
        base_version: currentReportSession.current_version || 0,
        source: "manual_edit"
      })
    });

    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const errData = await response.json();
        if (errData && errData.detail) detail = errData.detail;
      } catch (_) { /* 非 JSON 错误体，保留状态码提示 */ }
      throw new Error(detail);
    }

    const data = await response.json();
    if (currentReportSession) currentReportSession.current_version = data.version;
    loadReportHistory();
    alert("报告已保存");
  } catch (error) {
    console.error("保存失败:", error);
    alert("保存失败：" + error.message);
  }
}

async function exportReportWord() {
  if (!currentReportSession) {
    alert("当前没有报告可导出");
    return;
  }
  try {
    const resp = await fetch(`/api/report-workbench/sessions/${reportConversationId}/export/word`, { method: "POST" });
    if (!resp.ok) {
      const err = await resp.json().catch(() => null);
      throw new Error(err?.detail || `HTTP ${resp.status}`);
    }
    const blob = await resp.blob();
    const disposition = resp.headers.get("Content-Disposition") || "";
    let filename = `报告_${currentReportSession.route_name}_${Date.now()}.docx`;
    const m = disposition.match(/filename\*=UTF-8''(.+)/);
    if (m) filename = decodeURIComponent(m[1]);
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    console.error("导出Word失败:", error);
    alert("导出Word失败：" + error.message);
  }
}

function exportCurrentReport() {
  if (!currentReportSession) {
    alert("当前没有报告可导出");
    return;
  }

  const findings = currentReportSession.report.imaging_findings;
  const diagnosis = currentReportSession.report.imaging_diagnosis;
  
  const content = `# ${currentReportSession.route_name}\n\n## 影像所见\n\n${findings}\n\n## 影像诊断\n\n${diagnosis}`;
  
  const blob = new Blob([content], { type: "text/markdown" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `报告_${currentReportSession.route_name}_${Date.now()}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

// ===== 报告模板库管理（收藏/编辑/启停/删除；启用模板在生成时自动注入为范例参照） =====
let templateAdminCache = { templates: [] };

function setTemplateAdminHint(text, ok = false) {
  const el = document.getElementById("templateAdminHint");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = ok ? "#00a854" : "#d92d20";
}

async function openTemplateAdmin() {
  const panel = document.getElementById("templateAdminPanel");
  if (!panel) return;
  panel.style.display = "flex";
  setTemplateAdminHint("加载中…");
  try {
    const resp = await fetch(`/api/report-workbench/admin/templates?t=${Date.now()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    templateAdminCache = await resp.json();
    renderTemplateAdminList();
    setTemplateAdminHint("");
  } catch (e) {
    console.error("加载模板库失败:", e);
    setTemplateAdminHint("加载失败: " + e.message);
  }
}

function closeTemplateAdmin() {
  const panel = document.getElementById("templateAdminPanel");
  if (panel) panel.style.display = "none";
  const editor = document.getElementById("templateAdminEditor");
  if (editor) editor.style.display = "none";
}

function renderTemplateAdminList() {
  const list = document.getElementById("templateAdminList");
  const editor = document.getElementById("templateAdminEditor");
  if (!list) return;
  list.style.display = "block";
  if (editor) editor.style.display = "none";

  const tpls = templateAdminCache.templates || [];
  const routeName = (rid) => (reportRoutesCache.find((r) => r.id === rid) || {}).name || rid;
  if (!tpls.length) {
    list.innerHTML = `<div class="ra-empty">暂无模板。可在报告页点 ⭐收藏 当前优秀报告，或点下方「＋ 新增模板」手工录入；启用后同类报告生成时自动作为范例参照注入。</div>`;
    return;
  }
  list.innerHTML = tpls
    .map((t) => {
      const enabled = t.enabled !== false;
      return `
      <div class="route-admin-item" data-tpl-id="${escapeHtml(t.id)}">
        <div class="route-admin-item-head">
          <span class="route-admin-item-name">${escapeHtml(t.name)}</span>
          <code class="route-admin-item-id">${escapeHtml(t.route_id)} · ${escapeHtml(routeName(t.route_id))}</code>
          <span class="route-admin-item-badge ${enabled ? "on" : "off"}">${enabled ? "已启用" : "已停用"}</span>
          <span class="ra-tpl-src">${escapeHtml(t.source === "session" ? "收藏" : "手工")}</span>
          <span class="route-admin-item-actions">
            <button class="btn btn-sm" data-act="edit">编辑</button>
            <button class="btn btn-sm" data-act="toggle">${enabled ? "停用" : "启用"}</button>
            <button class="btn btn-sm btn-danger" data-act="del">删除</button>
          </span>
        </div>
        <div class="route-admin-item-meta">
          标签：${(t.tags || []).map(escapeHtml).join("、") || "—"} ｜ 命中词：${(t.match_keywords || []).map(escapeHtml).join("、") || "—"} ｜ 引用 ${t.usage_count || 0} 次 ｜ 质量分 ${t.quality_score || 5}
        </div>
        <div class="ra-tpl-preview">
          <div class="ra-tpl-preview-row"><span>所见</span>${escapeHtml((t.imaging_findings || "").split("\n")[0] || "")}</div>
          <div class="ra-tpl-preview-row"><span>诊断</span>${escapeHtml((t.imaging_diagnosis || "").split("\n")[0] || "")}</div>
        </div>
      </div>`;
    })
    .join("");

  list.querySelectorAll(".route-admin-item [data-act]").forEach((btn) => {
    btn.addEventListener("click", async (e) => {
      const item = e.target.closest(".route-admin-item");
      const tid = item?.dataset.tplId;
      const act = btn.dataset.act;
      if (act === "edit") {
        openTemplateEditor((templateAdminCache.templates || []).find((x) => x.id === tid));
      } else if (act === "toggle") {
        try {
          setTemplateAdminHint("切换中…");
          const resp = await fetch(`/api/report-workbench/admin/templates/${encodeURIComponent(tid)}/toggle`, { method: "POST" });
          if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
          await openTemplateAdmin();
          setTemplateAdminHint("已切换启停状态", true);
        } catch (err) {
          setTemplateAdminHint("操作失败: " + err.message);
        }
      } else if (act === "del") {
        if (!confirm("确认删除该模板？删除后不再参与生成注入。")) return;
        try {
          const resp = await fetch(`/api/report-workbench/admin/templates/${encodeURIComponent(tid)}`, { method: "DELETE" });
          if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
          await openTemplateAdmin();
          setTemplateAdminHint("模板已删除", true);
        } catch (err) {
          setTemplateAdminHint("删除失败: " + err.message);
        }
      }
    });
  });
}

function openTemplateEditor(tpl) {
  const list = document.getElementById("templateAdminList");
  const editor = document.getElementById("templateAdminEditor");
  if (!editor) return;
  if (list) list.style.display = "none";
  editor.style.display = "block";
  const isNew = !tpl;
  const t = tpl || { id: "", name: "", route_id: currentReportSession?.route_id || "", tags: [], match_keywords: [], imaging_findings: "", imaging_diagnosis: "", enabled: true, quality_score: 5 };
  const routeOptions = (reportRoutesCache.length ? reportRoutesCache : [{ id: t.route_id, name: t.route_id }])
    .map((r) => `<option value="${escapeHtml(r.id)}" ${r.id === t.route_id ? "selected" : ""}>${escapeHtml(r.name)}</option>`)
    .join("");
  editor.innerHTML = `
    <div class="route-admin-form">
      <div class="route-admin-editor-top">
        <button type="button" class="route-admin-back" id="taBack">← 返回列表</button>
        <div class="route-admin-editor-title">
          <span class="ra-title-text">${isNew ? "新增模板" : "编辑模板"}</span>
          ${isNew ? "" : `<code class="ra-title-id">${escapeHtml(t.id)}</code>`}
          <span class="ra-title-sub">${isNew ? "入库前自动跑范文结构校验" : "保存后立即对生成生效"}</span>
        </div>
      </div>
      <div class="ra-card">
        <div class="route-admin-form-grid">
          <div class="route-admin-form-row">
            <label>模板名称</label>
            <input class="config-input" id="taName" value="${escapeHtml(t.name)}" placeholder="如 肝血管瘤典型演绎式" />
          </div>
          <div class="route-admin-form-row">
            <label>归属路由</label>
            <select class="config-input" id="taRoute">${routeOptions}</select>
          </div>
        </div>
        <div class="route-admin-form-grid">
          <div class="route-admin-form-row">
            <label>标签（逗号分隔，匹配用）</label>
            <input class="config-input" id="taTags" value="${escapeHtml((t.tags || []).join("，"))}" placeholder="肝血管瘤、演绎式" />
          </div>
          <div class="route-admin-form-row">
            <label>命中关键词（逗号分隔，输入含词即优先命中）</label>
            <input class="config-input" id="taKeywords" value="${escapeHtml((t.match_keywords || []).join("，"))}" placeholder="血管瘤、栅栏征" />
          </div>
        </div>
      </div>
      <div class="ra-card">
        <div class="ra-card-title">影像所见范文（三行纯文本）</div>
        <textarea class="config-input route-admin-prompt" id="taFindings" placeholder="第1行 病灶病变&#10;第2行 组织器官基线&#10;第3行 图像区域">${escapeHtml(t.imaging_findings)}</textarea>
      </div>
      <div class="ra-card">
        <div class="ra-card-title">影像诊断范文</div>
        <textarea class="config-input route-admin-prompt" id="taDiagnosis" placeholder="演绎式单段 / 归纳式三行">${escapeHtml(t.imaging_diagnosis)}</textarea>
      </div>
      <div class="ra-card ra-tpl-actions">
        <label class="ra-tpl-enabled"><input type="checkbox" id="taEnabled" ${t.enabled !== false ? "checked" : ""} /> 启用（启用后同类报告生成时自动注入为范例参照）</label>
        <button class="route-admin-save" id="taSave">💾 保存模板</button>
      </div>
    </div>`;

  editor.querySelector("#taBack").addEventListener("click", () => {
    editor.style.display = "none";
    if (list) list.style.display = "block";
  });
  editor.querySelector("#taSave").addEventListener("click", async () => {
    const splitCsv = (v) => v.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    const payload = {
      name: editor.querySelector("#taName").value.trim(),
      route_id: editor.querySelector("#taRoute").value,
      tags: splitCsv(editor.querySelector("#taTags").value),
      match_keywords: splitCsv(editor.querySelector("#taKeywords").value),
      imaging_findings: editor.querySelector("#taFindings").value.trim(),
      imaging_diagnosis: editor.querySelector("#taDiagnosis").value.trim(),
      enabled: editor.querySelector("#taEnabled").checked,
      quality_score: t.quality_score || 5,
      source: t.source || "manual",
    };
    setTemplateAdminHint("保存中…");
    try {
      const url = isNew
        ? "/api/report-workbench/admin/templates"
        : `/api/report-workbench/admin/templates/${encodeURIComponent(t.id)}`;
      const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
      await openTemplateAdmin();
      setTemplateAdminHint("模板已保存并生效", true);
    } catch (e) {
      setTemplateAdminHint("保存失败: " + e.message);
    }
  });
}

// 收藏弹窗模板名称自动填充：从影像诊断首句提炼病种摘要，拼路由名前缀
// 规则：取首行→截首句（。；）→剥离建议/随访子句与枚举前缀→超长按逗号边界截断→重名加序号
function buildFavoriteName() {
  const routeName = currentReportSession.route_name || "报告";
  const diag = ((currentReportSession.report || {}).imaging_diagnosis || "").trim();
  const fallback = `${routeName}优秀范例_${new Date().toISOString().slice(5, 10)}`;
  if (!diag) return fallback;
  let first = diag.split(/\n+/).map((s) => s.trim()).filter(Boolean)[0] || "";
  first = first.split(/[。；;]/)[0] || first;
  first = first.replace(/^[\d一二三四五六七八九十]+[\.、\)\]】]\s*/, "");
  first = first.replace(/[，,]?\s*(建议|推荐|随访|复查请|请结合).*/ , "");
  first = first.trim();
  if (!first) return fallback;
  if (first.length > 26) {
    const cut = first.slice(0, 26);
    const idx = Math.max(cut.lastIndexOf("，"), cut.lastIndexOf(","), cut.lastIndexOf("、"));
    first = idx > 8 ? cut.slice(0, idx) : cut;
  }
  let name = `${routeName}·${first}`;
  const exists = (n) => (templateAdminCache.templates || []).some((t) => t.name === n);
  if (exists(name)) {
    let i = 2;
    while (exists(`${name}_${i}`)) i += 1;
    name = `${name}_${i}`;
  }
  return name;
}

// 收藏当前报告为模板：弹窗预览所见/诊断并可编辑，确认后入模板库
async function favoriteCurrentReport() {
  if (!currentReportSession) {
    alert("当前没有报告可收藏");
    return;
  }
  const panel = document.getElementById("favoritePanel");
  if (!panel) return;
  // 先拉模板列表：用于名称重名去重
  try {
    const resp = await fetch(`/api/report-workbench/admin/templates?t=${Date.now()}`);
    if (resp.ok) templateAdminCache = await resp.json();
  } catch (e) {
    console.warn("模板列表加载失败（忽略去重）:", e);
  }
  const routes = reportRoutesCache.length ? reportRoutesCache : [{ id: currentReportSession.route_id, name: currentReportSession.route_name || currentReportSession.route_id }];
  document.getElementById("fvRoute").innerHTML = routes
    .map((r) => `<option value="${escapeHtml(r.id)}" ${r.id === currentReportSession.route_id ? "selected" : ""}>${escapeHtml(r.name)}</option>`)
    .join("");
  document.getElementById("fvName").value = buildFavoriteName();
  document.getElementById("fvTags").value = "";
  document.getElementById("fvKeywords").value = "";
  document.getElementById("fvFindings").value = currentReportSession.report.imaging_findings || "";
  document.getElementById("fvDiagnosis").value = currentReportSession.report.imaging_diagnosis || "";
  document.getElementById("fvEnabled").checked = true;
  setFavoriteHint("");
  panel.style.display = "flex";
}

function closeFavoritePanel() {
  const panel = document.getElementById("favoritePanel");
  if (panel) panel.style.display = "none";
}

function setFavoriteHint(text, ok = false) {
  const el = document.getElementById("favoriteHint");
  if (!el) return;
  el.textContent = text || "";
  el.style.color = ok ? "#00a854" : "#d92d20";
}

async function submitFavorite() {
  const splitCsv = (v) => v.split(/[,，]/).map((s) => s.trim()).filter(Boolean);
  const payload = {
    name: document.getElementById("fvName").value.trim(),
    route_id: document.getElementById("fvRoute").value,
    tags: splitCsv(document.getElementById("fvTags").value),
    match_keywords: splitCsv(document.getElementById("fvKeywords").value),
    imaging_findings: document.getElementById("fvFindings").value.trim(),
    imaging_diagnosis: document.getElementById("fvDiagnosis").value.trim(),
    enabled: document.getElementById("fvEnabled").checked,
  };
  setFavoriteHint("保存中…");
  try {
    const resp = await fetch(`/api/report-workbench/sessions/${encodeURIComponent(reportConversationId)}/favorite`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) throw new Error((await resp.json()).detail || `HTTP ${resp.status}`);
    closeFavoritePanel();
    setTemplateAdminHint("已加入模板库" + (payload.enabled ? "并启用" : "（停用状态）"), true);
    openTemplateAdmin();
  } catch (e) {
    setFavoriteHint("收藏失败: " + e.message);
  }
}
