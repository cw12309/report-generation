console.log("[APP] app.js v50 已加载, 时间:", new Date().toISOString());

// 会话管理 - 每个对话独立的 session_id
let SESSION_ID = generateSessionId();

function generateSessionId() {
  // crypto.randomUUID() 仅在 HTTPS 或 localhost 下可用
  if (crypto && crypto.randomUUID) {
    return "web_" + crypto.randomUUID();
  }
  // 降级方案：使用 Math.random 生成 UUID
  return "web_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 9) + Math.random().toString(36).substr(2, 9);
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
  { param: "kb_name", optionsKey: "kb_names", element: document.getElementById("filterKbName") },
  { param: "exam_type", optionsKey: "exam_types", element: document.getElementById("filterExamType") },
  { param: "level1", optionsKey: "level1", element: document.getElementById("filterLevel1") },
  { param: "level2", optionsKey: "level2", element: document.getElementById("filterLevel2") },
  { param: "level3", optionsKey: "level3", element: document.getElementById("filterLevel3") },
  { param: "level4", optionsKey: "level4", element: document.getElementById("filterLevel4") },
];
const evalCaseError = document.getElementById("evalCaseError");
const btnCloseEvalCase = document.getElementById("btnCloseEvalCase");
const btnCancelEvalCase = document.getElementById("btnCancelEvalCase");
const btnSaveEvalCase = document.getElementById("btnSaveEvalCase");
const btnAutoGenerateCases = document.getElementById("btnAutoGenerateCases");
const evalAutoGenerateModal = document.getElementById("evalAutoGenerateModal");
const evalAutoGenerateForm = document.getElementById("evalAutoGenerateForm");
const evalAutoGenerateCount = document.getElementById("evalAutoGenerateCount");
const evalAutoGenerateMinLength = document.getElementById("evalAutoGenerateMinLength");
const evalAutoProgressText = document.getElementById("evalAutoProgressText");
const evalAutoProgressPercent = document.getElementById("evalAutoProgressPercent");
const evalAutoProgressBar = document.getElementById("evalAutoProgressBar");
const evalAutoProgressTrack = evalAutoGenerateModal?.querySelector(".eval-auto-progress-track");
const evalAutoGenerateLog = document.getElementById("evalAutoGenerateLog");
const evalAutoGenerateError = document.getElementById("evalAutoGenerateError");
const btnCloseEvalAutoGenerate = document.getElementById("btnCloseEvalAutoGenerate");
const btnCancelEvalAutoGenerate = document.getElementById("btnCancelEvalAutoGenerate");
const btnStartEvalAutoGenerate = document.getElementById("btnStartEvalAutoGenerate");
const appToast = document.getElementById("appToast");

// 状态
let isProcessing = false;
let currentReader = null; // 当前 SSE 读取器
let chatAbortController = null; // 当前请求的 AbortController
let knowledgeBases = [];
const SELECTED_KBS_STORAGE_KEY = "selectedKnowledgeBaseIds";

function loadSelectedKnowledgeBaseIds() {
  try {
    const stored = JSON.parse(localStorage.getItem(SELECTED_KBS_STORAGE_KEY) || "[]");
    return new Set(Array.isArray(stored) ? stored.filter((id) => typeof id === "string" && id) : []);
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
    html += '<div class="memory-section"><h4>实体追踪</h4><div class="memory-entities">';
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

sidebarBtns.forEach(btn => {
  btn.addEventListener("click", () => {
    // 关闭弹窗
    const panel = document.getElementById("llmConfigPanel");
    if (panel) panel.style.display = "none";

    const page = btn.dataset.page;
    sidebarBtns.forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    pages.forEach(p => p.classList.remove("active"));
    const pageMap = {
      agent: "pageAgent",
      kb: "pageKB",
      eval: "pageEval",
      config: "pageConfig",
      monitor: "pageMonitor",
    };
    const target = document.getElementById(pageMap[page] || "pageAgent");
    if (target) target.classList.add("active");
    if (page === "kb") {
      refreshKnowledgeBases();
    } else if (page === "agent") {
      refreshKnowledgeBases();
    } else if (page === "eval") {
      restoreEvaluationState();
    }
    if (page === "config") {
      loadConfig();
    }
    if (page === "monitor") {
      loadSessionsList();
    }
  });
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

function appendSelectedChunkFilters(params, maxIndex = chunkFilterControls.length - 1) {
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
  normalizedValues.forEach((value) => fragment.appendChild(new Option(value, value)));
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
    const response = await fetch(`${API_BASE}/kb/filter-options?${params.toString()}`, {
      method: "GET",
      headers: { "Accept": "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    if (requestId !== chunkFilterRequestId) return false;
    let selectionWasReset = false;
    chunkFilterControls.forEach((control) => {
      selectionWasReset = updateChunkFilterSelect(
        control,
        payload[control.optionsKey],
      ) || selectionWasReset;
    });
    if (selectionWasReset && retryAfterReset) {
      // A stale child value was removed. Fetch once more with the normalized
      // path so its descendants show the correct broader option set.
      return loadChunkFilterOptions(chunkFilterControls.length - 2, false);
    }
    return true;
  } catch (error) {
    if (error.name === "AbortError" || requestId !== chunkFilterRequestId) return false;
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
  const chunkId = String(chunk.chunk_id || "").trim().toLowerCase();
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
      headers: { "Accept": "application/json" },
      cache: "no-store",
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => []);
    if (!response.ok) {
      throw new Error(payload.detail || `HTTP ${response.status}`);
    }
    if (requestId !== chunkSearchRequestId) return;
    renderChunkSearchResults(Array.isArray(payload) ? payload.slice(0, 20) : []);
  } catch (error) {
    if (error.name === "AbortError" || requestId !== chunkSearchRequestId) return;
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
  const selectedChunkId = selectedChunkIdInput?.value.trim().toLowerCase() || "";
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
        "Accept": "application/json",
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
evalCaseModal?.querySelector("[data-eval-modal-close]")
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
  if (evalAutoProgressPercent) evalAutoProgressPercent.textContent = `${safePercent}%`;
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
  evalAutoProgressTrack?.classList.remove("is-running", "is-error", "is-complete");
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
        "Accept": "text/event-stream",
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
btnCancelEvalAutoGenerate?.addEventListener("click", cancelOrCloseEvalAutoGenerate);
evalAutoGenerateModal?.querySelector("[data-eval-auto-close]")
  ?.addEventListener("click", closeEvalAutoGenerateModal);
evalAutoGenerateForm?.addEventListener("submit", startEvalAutoGenerate);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && evalAutoGenerateModal && !evalAutoGenerateModal.hidden) {
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
    evalProgressTrack.classList.remove("is-running", "is-complete", "is-cancelled");
  }
  if (evalProgressPercent) evalProgressPercent.hidden = true;
  if (btnCancelEval) {
    btnCancelEval.hidden = true;
    btnCancelEval.disabled = false;
  }
}

function showEvalCancelledState() {
  const currentPercent = Number(evalProgressTrack?.getAttribute("aria-valuenow")) || 0;
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
      headers: { "Accept": "application/json" },
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

  evalDetailsBody.innerHTML = items.map((item, index) => {
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
  }).join("");
}

async function loadEvalDataset() {
  if (!evalDetailsBody) return;
  try {
    const response = await fetch(`${API_BASE}/eval/dataset?_t=${Date.now()}`, {
      method: "GET",
      headers: { "Accept": "application/json" },
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
      headers: { "Accept": "application/json" },
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
        headers: { "Accept": "application/json" },
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

  evalDetailsBody.innerHTML = details.map((detail, index) => {
    const expectedIds = Array.isArray(detail.expected_chunk_ids)
      ? detail.expected_chunk_ids
      : [];
    const expectedHtml = expectedIds.length
      ? expectedIds.map((id) => `<code>${escapeHtml(String(id))}</code>`).join("<br>")
      : "-";
    const rank = detail.hit_rank == null
      ? '<span class="eval-rank-miss">MISS</span>'
      : `<span class="eval-rank-hit">#${Number(detail.hit_rank)}</span>`;
    const score = detail.rerank_score == null
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
  }).join("");
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
      headers: { "Accept": "application/json" },
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
      setEvalStatusMessage(`已恢复最后一次评测：共 ${total} 条用例${completedAt}。`, "success");
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
      headers: { "Accept": "text/event-stream" },
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
        setEvalRunProgress(100, `评测计算完成 (${event.total}/${event.total})，正在渲染结果...`);
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
    const resp = await fetch(`${API_BASE}/kbs?_t=${Date.now()}`, { cache: "no-store" });
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
    const chunksBecameAvailable = currentSelectedKb
      && currentSelectedKb.status === "processing"
      && Number(previousSelectedKb?.progress || 0) < 15
      && Number(currentSelectedKb.progress || 0) >= 15;
    const buildFinished = previousSelectedKb?.status === "processing"
      && currentSelectedKb
      && currentSelectedKb.status !== "processing";
    if (chunksBecameAvailable || buildFinished) {
      loadKnowledgeBaseChunks(selectedManageKbId);
    }
  } catch (error) {
    console.error("[KB] 获取知识库列表失败:", error);
    if (manageKbList) {
      manageKbList.innerHTML = '<div class="kb-manage-empty">知识库加载失败</div>';
      delete manageKbList.dataset.renderKey;
    }
    if (agentKbSelectorList) agentKbSelectorList.innerHTML = '<span class="kb-chip-empty">知识库加载失败</span>';
    updateKbSelectedCount();
  }
}

function kbStatusLabel(status) {
  return {
    uploaded: "待构建",
    processing: "构建中...",
    active: "已激活",
    failed: "构建失败",
  }[status] || status || "未知";
}

function formatKbCreatedAt(value) {
  if (!value) return "—";
  return String(value).replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 19);
}

function renderKbDetail(kb) {
  if (!kbDetailArea || !detailKbName || !detailKbCount || !kbChunkList) return;
  if (!kb) {
    kbChunkAbortController?.abort();
    detailKbName.textContent = "请在左侧选择知识库";
    detailKbCount.textContent = "";
    delete kbChunkList.dataset.kbId;
    kbChunkList.innerHTML = '<div class="kb-empty-tip">点击左侧列表查看具体的切片内容...</div>';
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
  const fallbackText = !findings && !opinion && item.combined_text
    ? `<div class="chunk-field"><span>切片原文</span><p>${escapeHtml(item.combined_text)}</p></div>`
    : "";
  const sourceParts = [item.source_file, item.sheet_name ? `Sheet: ${item.sheet_name}` : "",
    item.source_row ? `第 ${item.source_row} 行` : ""].filter(Boolean);

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
  kbChunkList.innerHTML = '<div class="kb-empty-tip"><span class="spinner-icon"></span> 正在加载切片...</div>';
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

  if (selectedManageKbId && !data.some((kb) => kb.kb_id === selectedManageKbId)) {
    selectedManageKbId = null;
  }

  if (!data.length) {
    selectedManageKbId = null;
    if (manageKbList.dataset.renderKey !== "empty") {
      manageKbList.innerHTML = '<div class="kb-manage-empty">暂无知识库，请先上传 Excel</div>';
      manageKbList.dataset.renderKey = "empty";
    }
    renderKbDetail(null);
    return;
  }

  const renderKey = JSON.stringify({
    selectedManageKbId,
    items: data.map((kb) => [kb.kb_id, kb.kb_name, kb.status, kb.progress, kb.created_at]),
  });
  if (manageKbList.dataset.renderKey === renderKey) {
    renderKbDetail(data.find((kb) => kb.kb_id === selectedManageKbId));
    return;
  }

  const listMarkup = data.map((kb) => {
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
  }).join("");

  const previousScrollTop = manageKbList.scrollTop;
  manageKbList.innerHTML = listMarkup;
  manageKbList.dataset.renderKey = renderKey;
  manageKbList.scrollTop = previousScrollTop;

  manageKbList.querySelectorAll(".manage-kb-card").forEach((card) => {
    const selectCard = () => {
      selectedManageKbId = card.dataset.kbId;
      renderManageKbList(knowledgeBases);
      loadKnowledgeBaseChunks(selectedManageKbId);
    };
    card.addEventListener("click", selectCard);
    card.addEventListener("keydown", (event) => {
      if (event.target === card && (event.key === "Enter" || event.key === " ")) {
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
    validSelectedIds.size !== selectedKnowledgeBaseIds.size
    || [...validSelectedIds].some((kbId) => !selectedKnowledgeBaseIds.has(kbId))
  ) {
    selectedKnowledgeBaseIds = validSelectedIds;
    persistSelectedKnowledgeBaseIds();
  }
  updateKbSelectedCount();

  if (!active.length) {
    agentKbSelectorList.innerHTML = '<span class="kb-chip-empty">暂无已激活知识库，请先前往知识库页面构建</span>';
    return;
  }

  agentKbSelectorList.innerHTML = active.map((kb) => `
    <label class="kb-chip-item" title="${escapeHtml(kb.kb_id)}">
      <input type="checkbox" value="${escapeHtml(kb.kb_id)}"
        ${selectedKnowledgeBaseIds.has(kb.kb_id) ? "checked" : ""}>
      <span>${escapeHtml(kb.kb_name || kb.kb_id)}</span>
    </label>`).join("");

  agentKbSelectorList.querySelectorAll('input[type="checkbox"]').forEach((input) => {
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
    const resp = await fetch(`${API_BASE}/kb/${encodeURIComponent(kbId)}/build`, { method: "POST" });
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
    const resp = await fetch(`${API_BASE}/kb/${encodeURIComponent(kbId)}`, { method: "DELETE" });
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
    [btnSidebarUpload, btnBuildIncremental, btnBuildRebuild, btnExtractMeta].filter(Boolean)
      .forEach((button) => button.classList.add("btn-disabled"));
  } else {
    [btnSidebarUpload, btnBuildIncremental, btnBuildRebuild, btnExtractMeta].filter(Boolean)
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
      "此操作不可逆，是否继续？"
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

if (btnBuildIncremental) btnBuildIncremental.addEventListener("click", () => runKBBuild(false));
if (btnBuildRebuild) btnBuildRebuild.addEventListener("click", () => runKBBuild(true));
if (btnExtractMeta) btnExtractMeta.addEventListener("click", () => runExtractMetadata());

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
  appendKBLog(`[上传] 已选择 ${files.length} 个 Excel 文件，正在保存...`, "info");
  try {
    const formData = new FormData();
    files.forEach((file) => formData.append("files", file));
    const resp = await fetch(`${API_BASE}/kb/upload`, { method: "POST", body: formData });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
    (data.knowledge_bases || []).forEach((kb) => {
      appendKBLog(`✅ ${kb.kb_name} 已上传，状态：待构建，kb_id=${kb.kb_id}`, "done");
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
            console.log("[DEBUG] 事件 #" + eventCount + " type=" + event.type, event);
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
      console.log("[DEBUG] finally: 取消 reader (streamDone=" + streamDone + ")");
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
      console.log("[DEBUG] ambiguous 事件触发, options数量=", (event.options || []).length);
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
        timeInfo.style.cssText = "padding:4px 12px;font-size:12px;color:#666;border-top:1px solid #e8e8e8;text-align:right;";
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
    <span class="thinking-step-label">记忆检索 <span style="color:#888;font-size:11px">(基于 "${escapeHtml(data.query || '')}")</span></span>
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

  var optionsHtml = (options || []).map(function (opt, i) {
    var scoreText = scores[i] ? ' <span style="color:#999;font-size:0.8em;">(' + (scores[i] * 100).toFixed(1) + '%)</span>' : '';
    return '<button class="ambiguity-option" onclick="selectAmbiguityOption(\'' + escapeHtml(opt) + '\')">' + (i + 1) + '. ' + escapeHtml(opt) + scoreText + '</button>';
  }).join("");

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
    source.textContent = reference.source ? `来源 ${reference.source}` : "来源未标注";
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
  scrollToBottom();
}

function abortAssistantStream(state) {
  if (!state?.messageEl) return;
  state.messageEl.classList.remove("is-streaming");
  state.completed = true;
  renderAssistantReferences(state);
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
    return JSON.parse(localStorage.getItem(CHAT_MESSAGES_PREFIX + convId) || "[]");
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
  return "conv_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 9) + Math.random().toString(36).substr(2, 9);
}

function saveCurrentConversation() {
  console.log("[DEBUG] saveCurrentConversation 被调用, currentConversationId=", currentConversationId);
  const messages = [];
  chatContainer.querySelectorAll(".message").forEach(msg => {
    const isUser = msg.classList.contains("user");
    const isAssistant = msg.classList.contains("assistant");
    const isSystem = msg.classList.contains("system");
    const bubble = msg.querySelector(".bubble");
    if (bubble) {
      const entry = {
        role: isUser ? "user" : isAssistant ? "assistant" : "system",
        content: bubble.innerHTML,
      };
      if (isAssistant) {
        // 思考过程是 assistant 消息的前一个兄弟节点
        const prev = msg.previousElementSibling;
        console.log("[DEBUG] assistant msg, prev=", prev ? prev.className : "null");
        if (prev && prev.classList.contains("thinking-container")) {
          entry.thinking = prev.outerHTML;
          console.log("[DEBUG] ✅ 保存思考过程:", prev.outerHTML.length, "字符");
        } else {
          console.log("[DEBUG] ❌ 未找到思考过程");
        }
      }
      messages.push(entry);
    }
  });

  console.log("[DEBUG] 共保存", messages.length, "条消息");
  for (let i = 0; i < messages.length; i++) {
    console.log("[DEBUG] 消息[" + i + "] role=" + messages[i].role + " thinking=" + (messages[i].thinking ? messages[i].thinking.length + "字符" : "无"));
  }

  if (messages.length === 0) return;

  const title = getConversationTitle(messages);

  if (currentConversationId) {
    const history = getChatHistory();
    const idx = history.findIndex(h => h.id === currentConversationId);
    if (idx !== -1) {
      history[idx].title = title;
      history[idx].updatedAt = new Date().toISOString();
      saveChatHistory(history);
    }
    saveChatMessages(currentConversationId, messages);
  }
}

function getConversationTitle(messages) {
  const firstUserMsg = messages.find(m => m.role === "user");
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
  console.log("[DEBUG] loadConversation: convId=", convId, "currentConversationId=", currentConversationId);
  if (convId === currentConversationId) return;

  saveCurrentConversation();

  currentConversationId = convId;
  const history = getChatHistory();
  const entry = history.find(h => h.id === convId);
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
      console.log("[DEBUG] 渲染消息[" + idx + "] role=" + msg.role + " thinking=" + (msg.thinking ? msg.thinking.length + "字符" : "无"));
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
  const filtered = history.filter(h => h.id !== convId);
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

  list.innerHTML = history.map(h => {
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
  }).join("");
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
document.getElementById("btnNewChat").addEventListener("click", startNewConversation);

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
      { key: "name", label: "名称", type: "text", placeholder: "标识名称", sm: false },
      { key: "base_url", label: "API 地址", type: "text", placeholder: "http://...", sm: false },
      { key: "model", label: "模型名", type: "text", placeholder: "qwen36_27b", sm: false },
      { key: "api_key", label: "API Key", type: "password", placeholder: "可选", sm: false },
      { key: "max_tokens", label: "最大 Token", type: "number", sm: true },
      { key: "temperature", label: "温度", type: "number", step: "0.1", sm: true },
    ],
  },
  embeddings: {
    listId: "modelListEmbeddings",
    btnAddId: "btnAddEmbedding",
    fields: [
      { key: "name", label: "名称", type: "text", placeholder: "标识名称", sm: false },
      { key: "base_url", label: "API 地址", type: "text", placeholder: "http://...", sm: false },
      { key: "model", label: "模型名", type: "text", placeholder: "bge-m3", sm: false },
      { key: "api_key", label: "API Key", type: "password", placeholder: "可选", sm: false },
      { key: "dimension", label: "维度", type: "number", sm: true },
    ],
  },
  reranks: {
    listId: "modelListReranks",
    btnAddId: "btnAddRerank",
    fields: [
      { key: "name", label: "名称", type: "text", placeholder: "标识名称", sm: false },
      { key: "base_url", label: "API 地址", type: "text", placeholder: "https://...", sm: false },
      { key: "model", label: "模型名", type: "text", placeholder: "Qwen/Qwen3-VL-Reranker-8B", sm: false },
      { key: "api_key", label: "API Key", type: "password", placeholder: "可选", sm: false },
    ],
  },
};

// 非模型列表的简单字段映射
const SIMPLE_FIELDS = {
  "retrieval.rag_top_k": "cfg_retrieval_rag_top_k",
  "retrieval.rerank_top_k": "cfg_retrieval_rerank_top_k",
  "retrieval.collection_name": "cfg_retrieval_collection_name",
  "retrieval.db_path": "cfg_retrieval_db_path",
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
        ${def.fields.map(f => {
          const val = item[f.key] !== undefined ? item[f.key] : "";
          return `
            <div class="model-field">
              <label class="model-field-label">${f.label}</label>
              <input type="${f.type}" ${f.step ? `step="${f.step}"` : ""}
                class="config-input ${f.sm ? "config-input-sm" : ""}"
                data-field="${f.key}" value="${escapeHtml(String(val))}"
                placeholder="${f.placeholder || ""}">
            </div>`;
        }).join("")}
      </div>
      <div class="model-card-error" style="display: none"></div>
    `;
    container.appendChild(card);
  });

  // 绑定状态点击（点击重新测试）
  container.querySelectorAll(".model-status").forEach(statusEl => {
    statusEl.addEventListener("click", async (e) => {
      const card = e.target.closest(".model-card");
      if (!card) return;
      await testModelConnection(sectionKey, card);
    });
  });

  // 绑定删除按钮
  container.querySelectorAll(".model-remove-btn").forEach(btn => {
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
  def.fields.forEach(f => { newItem[f.key] = ""; });
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
    def.fields.forEach(f => {
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
  return items;
}

// 测试模型 API 连接
async function testModelConnection(sectionKey, card) {
  const def = MODEL_DEFS[sectionKey];
  const model = {};
  def.fields.forEach(f => {
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
  statusEl.className = "model-status model-status-clickable model-status-verifying";

  try {
    const resp = await fetch(`${API_BASE}/test-model`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_config: model, model_type: sectionKey }),
    });
    const data = await resp.json();
    if (data.success) {
      statusEl.textContent = "✅可用";
      statusEl.className = "model-status model-status-clickable model-status-ok";
      errorEl.style.display = "none";
      errorEl.textContent = "";
    } else {
      statusEl.textContent = "❌不可用";
      statusEl.className = "model-status model-status-clickable model-status-fail";
      const modelName = model.name || '未命名';
      const errorMsg = data.error || data.message || '未知错误';
      console.error(`[模型验证] ${sectionKey} - ${modelName} 验证失败:`, errorMsg);
      errorEl.textContent = `❌ 验证失败: ${errorMsg}`;
      errorEl.style.display = "block";
    }
  } catch (e) {
    statusEl.textContent = "❌不可用";
    statusEl.className = "model-status model-status-clickable model-status-fail";
    const modelName = model.name || '未命名';
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
    Object.keys(MODEL_DEFS).forEach(sectionKey => {
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
        else { val = undefined; break; }
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
    Object.keys(MODEL_DEFS).forEach(sectionKey => {
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
      showConfigHint("✅ " + data.message);
    } else {
      showConfigHint("❌ " + (data.error || "保存失败"));
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
  if (savedName && llms.some(m => m.name === savedName)) {
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
  llmSelect.innerHTML = llms.map((m, i) =>
    `<option value="${i}" ${m.name === activeModels.chat_llm ? "selected" : ""}>${escapeHtml(m.name || `模型 ${i + 1}`)}</option>`
  ).join("");

  // 填充 Embedding 下拉框
  const embSelect = document.getElementById("activeEmbedding");
  const embeddings = _configData.embeddings || [];
  embSelect.innerHTML = embeddings.map((m, i) =>
    `<option value="${i}" ${m.name === activeModels.embedding ? "selected" : ""}>${escapeHtml(m.name || `模型 ${i + 1}`)}</option>`
  ).join("");

  // 填充 Rerank 下拉框
  const rerankSelect = document.getElementById("activeRerank");
  const reranks = _configData.reranks || [];
  rerankSelect.innerHTML = reranks.map((m, i) =>
    `<option value="${i}" ${m.name === activeModels.rerank ? "selected" : ""}>${escapeHtml(m.name || `模型 ${i + 1}`)}</option>`
  ).join("");

  document.getElementById("llmConfigHint").textContent = "";
}

// 弹窗保存激活模型
async function saveLlmConfigFromPanel() {
  const hint = document.getElementById("llmConfigHint");
  const llms = _configData.llms || [];
  const embeddings = _configData.embeddings || [];
  const reranks = _configData.reranks || [];

  const llmIdx = parseInt(document.getElementById("activeChatLlm").value);
  const embIdx = parseInt(document.getElementById("activeEmbedding").value);
  const rerankIdx = parseInt(document.getElementById("activeRerank").value);

  const chatLlm = llms[llmIdx];
  const embedding = embeddings[embIdx];
  const rerank = reranks[rerankIdx];

  if (!chatLlm || !chatLlm.name) { hint.textContent = "❌ 请选择对话大语言模型"; return; }
  if (!embedding || !embedding.name) { hint.textContent = "❌ 请选择向量化模型"; return; }
  if (!rerank || !rerank.name) { hint.textContent = "❌ 请选择重排模型"; return; }

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
let _revealedApiCredential = null;

function getDefaultApiBaseUrl() {
  const currentOrigin = window.location.origin && window.location.origin !== "null"
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
    const message = typeof detail === "string"
      ? detail
      : detail?.message || payload.message || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return payload;
}

function getDeploymentEndpoint(deployment) {
  const relative = deployment?.endpoint
    || `/api/v1/deployments/${deployment?.deployment_id || "DEPLOYMENT_ID"}/chat`;
  const origin = getDefaultApiBaseUrl().replace(/\/api\/v1$/, "");
  return `${origin}${relative}`;
}

function getSelectedApiDeployment() {
  return _apiDeployments.find((item) => item.deployment_id === _selectedApiDeploymentId)
    || _apiDeployments[0]
    || null;
}

function formatApiDate(value) {
  if (!value) return "—";
  return String(value).replace("T", " ").replace(/\.\d+Z?$/, "").slice(0, 19);
}

function renderApiServiceStatus() {
  const statusEl = document.getElementById("apiStatusText");
  const baseUrlInput = document.getElementById("apiBaseUrl");
  const summaryEl = document.getElementById("apiDeploymentSummary");
  if (!statusEl || !baseUrlInput) return;
  const activeCount = _apiDeployments.filter((item) => item.status === "active").length;
  const readyCount = _apiDeployments.filter(
    (item) => item.status === "active" && item.health === "healthy",
  ).length;
  const degradedCount = _apiDeployments.filter((item) => item.health === "degraded").length;
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
  const items = _apiDeploymentKnowledgeBases.filter((kb) => kb.status === "active");

  if (!items.length) {
    listEl.innerHTML = '<div class="api-key-empty">暂无 Active 知识库，请先完成知识库构建。</div>';
    return;
  }

  listEl.innerHTML = items.map((kb) => {
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
          <span>${escapeHtml(kb.kb_id)} · Active</span>
        </span>
      </label>
    `;
  }).join("");
}

function renderApiKnowledgeBases() {
  renderApiKnowledgeBaseChoices("apiKbCheckboxList");
}

function renderApiDeploymentList() {
  const listEl = document.getElementById("apiDeploymentList");
  if (!listEl) return;
  if (_selectedApiDeploymentId && !_apiDeployments.some(
    (item) => item.deployment_id === _selectedApiDeploymentId,
  )) {
    _selectedApiDeploymentId = _apiDeployments[0]?.deployment_id || null;
  }
  if (!_apiDeployments.length) {
    listEl.innerHTML = '<div class="api-key-empty">尚未上线任何 API Deployment</div>';
    return;
  }
  listEl.innerHTML = _apiDeployments.map((item) => {
    const selected = item.deployment_id === _selectedApiDeploymentId;
    const kbNames = (item.knowledge_bases || []).map((kb) => kb.kb_name || kb.kb_id);
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
            <span><strong>KB</strong> ${escapeHtml(kbNames.join("、") || "无")}</span>
            <span><strong>Key</strong> ${escapeHtml(item.api_key_prefix || "—")}</span>
            <span><strong>创建</strong> ${escapeHtml(formatApiDate(item.created_at))}</span>
            <span><strong>最近调用</strong> ${escapeHtml(formatApiDate(item.last_used_at))}</span>
          </div>
          <code>${escapeHtml(item.endpoint)}</code>
        </div>
        <div class="api-deployment-actions">
          <button type="button" class="btn btn-sm btn-outline" data-api-action="copy" data-deployment-id="${escapeHtml(item.deployment_id)}">复制 Endpoint</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="edit" data-deployment-id="${escapeHtml(item.deployment_id)}">编辑 KB</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="rotate" data-deployment-id="${escapeHtml(item.deployment_id)}">重新生成密钥</button>
          <button type="button" class="btn btn-sm btn-outline" data-api-action="toggle" data-deployment-id="${escapeHtml(item.deployment_id)}">${isActive ? "停用" : "启用"}</button>
          <button type="button" class="btn btn-sm btn-danger" data-api-action="delete" data-deployment-id="${escapeHtml(item.deployment_id)}">删除</button>
        </div>
      </article>
    `;
  }).join("");
}

function buildApiDemoCode(language = _apiDemoLanguage) {
  const deployment = getSelectedApiDeployment();
  if (!deployment) return "请先创建并选择一个 Deployment。";
  const apiKey = _revealedApiCredential?.deployment_id === deployment.deployment_id
    ? _revealedApiCredential.api_key
    : "rg_live_YOUR_SECRET";
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

function renderApiSecret(deployment) {
  const section = document.getElementById("apiSecretReveal");
  if (!section || !deployment?.api_key) return;
  _revealedApiCredential = {
    deployment_id: deployment.deployment_id,
    api_key: deployment.api_key,
  };
  document.getElementById("apiRevealedEndpoint").value = getDeploymentEndpoint(deployment);
  document.getElementById("apiRevealedKey").value = deployment.api_key;
  section.hidden = false;
  renderApiDemoCode();
}

async function renderApiConfig() {
  const listEl = document.getElementById("apiDeploymentList");
  if (listEl) listEl.innerHTML = '<div class="api-key-empty">正在加载 Deployment…</div>';
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
    if (listEl) listEl.innerHTML = `<div class="api-key-empty">加载失败：${escapeHtml(error.message)}</div>`;
    showToast(`Deployment 加载失败：${error.message}`, "error");
  }
}

async function copyApiText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    showToast(`${label}已复制`, "success");
  } catch (error) {
    showToast(`复制失败：${error.message}`, "error");
  }
}

function closeApiDeploymentModal() {
  const modal = document.getElementById("apiDeploymentModal");
  if (modal) modal.hidden = true;
}

function openApiDeploymentModal(deployment) {
  const modal = document.getElementById("apiDeploymentModal");
  if (!modal || !deployment) return;
  document.getElementById("apiEditDeploymentId").value = deployment.deployment_id;
  document.getElementById("apiEditDeploymentName").value = deployment.name || "";
  document.getElementById("apiEditDeploymentDescription").value = deployment.description || "";
  const errorEl = document.getElementById("apiDeploymentEditError");
  errorEl.hidden = true;
  errorEl.textContent = "";
  renderApiKnowledgeBaseChoices("apiEditKbCheckboxList", deployment.selected_kbs || []);
  modal.hidden = false;
  requestAnimationFrame(() => document.getElementById("apiEditDeploymentName")?.focus());
}

async function refreshApiDeployments({ preserveSecret = true } = {}) {
  if (!preserveSecret) {
    _revealedApiCredential = null;
    const secretSection = document.getElementById("apiSecretReveal");
    if (secretSection) secretSection.hidden = true;
  }
  await renderApiConfig();
}

async function rotateApiDeploymentKey(deployment) {
  if (!window.confirm(`确定重新生成“${deployment.name}”的密钥吗？旧密钥会立即失效。`)) return;
  const updated = await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}/rotate-key`,
    { method: "POST" },
  );
  _selectedApiDeploymentId = updated.deployment_id;
  renderApiSecret(updated);
  await refreshApiDeployments();
  showToast("新密钥已生成，请立即保存", "success");
}

async function toggleApiDeployment(deployment) {
  const action = deployment.status === "active" ? "disable" : "enable";
  const updated = await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}/${action}`,
    { method: "POST" },
  );
  _selectedApiDeploymentId = updated.deployment_id;
  await refreshApiDeployments();
  showToast(action === "enable" ? "Deployment 已启用" : "Deployment 已停用", "success");
}

async function deleteApiDeployment(deployment) {
  if (!window.confirm(`确定删除“${deployment.name}”吗？入口会立即关闭，历史会话仍会保留。`)) return;
  await apiManagementRequest(
    `/deployments/${encodeURIComponent(deployment.deployment_id)}`,
    { method: "DELETE" },
  );
  if (_selectedApiDeploymentId === deployment.deployment_id) {
    _selectedApiDeploymentId = null;
  }
  await refreshApiDeployments({ preserveSecret: false });
  showToast("Deployment 已删除", "success");
}

function bindApiConfigInteractions() {
  const createForm = document.getElementById("apiDeploymentForm");
  const kbList = document.getElementById("apiKbCheckboxList");
  const deploymentList = document.getElementById("apiDeploymentList");
  const editForm = document.getElementById("apiDeploymentEditForm");

  const bindKbChoiceStyle = (container) => {
    container?.addEventListener("change", (event) => {
      const input = event.target.closest('input[type="checkbox"]');
      if (!input) return;
      input.closest(".api-kb-checkbox-item")?.classList.toggle("is-selected", input.checked);
    });
  };
  bindKbChoiceStyle(kbList);
  bindKbChoiceStyle(document.getElementById("apiEditKbCheckboxList"));

  createForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = document.getElementById("btnCreateApiDeployment");
    const errorEl = document.getElementById("apiDeploymentFormError");
    const selectedKbs = Array.from(
      kbList?.querySelectorAll('input[type="checkbox"]:checked') || [],
      (checkbox) => checkbox.value,
    );
    errorEl.hidden = true;
    button.disabled = true;
    button.textContent = "上线中…";
    try {
      const deployment = await apiManagementRequest("/deployments", {
        method: "POST",
        body: JSON.stringify({
          name: document.getElementById("apiDeploymentName").value.trim(),
          description: document.getElementById("apiDeploymentDescription").value.trim(),
          selected_kbs: selectedKbs,
        }),
      });
      _selectedApiDeploymentId = deployment.deployment_id;
      renderApiSecret(deployment);
      createForm.reset();
      await refreshApiDeployments();
      showToast("API 已上线，请立即保存密钥", "success");
    } catch (error) {
      errorEl.textContent = error.message;
      errorEl.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "上线 API";
    }
  });

  deploymentList?.addEventListener("click", async (event) => {
    const actionButton = event.target.closest("[data-api-action]");
    const card = event.target.closest("[data-api-select]");
    const deploymentId = actionButton?.dataset.deploymentId || card?.dataset.apiSelect;
    const deployment = _apiDeployments.find((item) => item.deployment_id === deploymentId);
    if (!deployment) return;
    _selectedApiDeploymentId = deployment.deployment_id;
    renderApiDeploymentList();
    renderApiDemoCode();
    if (!actionButton) return;
    actionButton.disabled = true;
    try {
      const action = actionButton.dataset.apiAction;
      if (action === "copy") await copyApiText(getDeploymentEndpoint(deployment), "Endpoint");
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

  document.querySelectorAll("[data-api-deployment-modal-close]").forEach((element) => {
    element.addEventListener("click", closeApiDeploymentModal);
  });

  editForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const deploymentId = document.getElementById("apiEditDeploymentId").value;
    const errorEl = document.getElementById("apiDeploymentEditError");
    const button = document.getElementById("btnSaveApiDeployment");
    const selectedKbs = Array.from(
      document.querySelectorAll('#apiEditKbCheckboxList input[type="checkbox"]:checked'),
      (checkbox) => checkbox.value,
    );
    errorEl.hidden = true;
    button.disabled = true;
    try {
      await apiManagementRequest(`/deployments/${encodeURIComponent(deploymentId)}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: document.getElementById("apiEditDeploymentName").value.trim(),
          description: document.getElementById("apiEditDeploymentDescription").value.trim(),
          selected_kbs: selectedKbs,
        }),
      });
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

  document.getElementById("btnRefreshApiDeployments")?.addEventListener("click", () => {
    refreshApiDeployments().catch((error) => showToast(error.message, "error"));
  });
  document.getElementById("btnCopyApiEndpoint")?.addEventListener("click", () => {
    copyApiText(document.getElementById("apiRevealedEndpoint").value, "Endpoint");
  });
  document.getElementById("btnCopyApiKey")?.addEventListener("click", () => {
    copyApiText(document.getElementById("apiRevealedKey").value, "API Key");
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeApiDeploymentModal();
  });
}

  document.addEventListener("DOMContentLoaded", () => {
  const btnSaveConfig = document.getElementById("btnSaveConfig");
  if (btnSaveConfig) {
    btnSaveConfig.addEventListener("click", saveConfig);
  }
  // 绑定各模型列表的添加按钮
  Object.values(MODEL_DEFS).forEach(def => {
    const btn = document.getElementById(def.btnAddId);
    if (btn) {
      const sectionKey = Object.keys(MODEL_DEFS).find(k => MODEL_DEFS[k] === def);
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
  const sharedConfigActions = document.querySelector("#configForm > .config-actions");
  navItems.forEach(item => {
    item.addEventListener("click", () => {
      const targetKey = item.dataset.config;
      navItems.forEach(i => i.classList.remove("active"));
      document.querySelectorAll(".config-group").forEach(g => g.style.display = "none");
      item.classList.add("active");
      const targetGroup = document.getElementById(`configGroup${targetKey.charAt(0).toUpperCase() + targetKey.slice(1)}`);
      if (targetGroup) {
        targetGroup.style.display = "block";
      }
      if (sharedConfigActions) {
        sharedConfigActions.style.display = targetKey === "api" ? "none" : "flex";
      }
      if (targetKey === "api") {
        renderApiConfig();
      }
    });
  });
  bindApiConfigInteractions();
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

// ========== 会话监控 ==========
const monitorTotalSessions = document.getElementById("monitorTotalSessions");
const monitorTotalTurns = document.getElementById("monitorTotalTurns");
const monitorLastUpdate = document.getElementById("monitorLastUpdate");
const monitorSessionsList = document.getElementById("monitorSessionsList");
const btnRefreshSessions = document.getElementById("btnRefreshSessions");
const sessionDetailModal = document.getElementById("sessionDetailModal");
const sessionDetailInfo = document.getElementById("sessionDetailInfo");
const sessionDetailTurns = document.getElementById("sessionDetailTurns");
const btnCloseSessionDetail = document.getElementById("btnCloseSessionDetail");

// 加载会话列表
async function loadSessionsList() {
  try {
    const res = await fetch(`${API_BASE}/sessions`);
    const data = await res.json();
    const sessions = data.sessions || [];

    // 更新统计
    monitorTotalSessions.textContent = sessions.length;
    const totalTurns = sessions.reduce((sum, s) => sum + (s.turn_count || 0), 0);
    monitorTotalTurns.textContent = totalTurns;
    monitorLastUpdate.textContent = new Date().toLocaleString("zh-CN");

    if (sessions.length === 0) {
      monitorSessionsList.innerHTML = '<div class="monitor-empty">暂无会话</div>';
      return;
    }

    // 按创建时间倒序排序
    sessions.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    monitorSessionsList.innerHTML = sessions
      .map(
        (s) => `
        <div class="monitor-session-row" data-session-id="${s.id}">
          <span class="monitor-session-id" title="${s.id}">${s.id.slice(0, 20)}...</span>
          <span class="monitor-session-title" title="${s.title || "未命名"}">${s.title || "未命名"}</span>
          <span class="monitor-session-turns">${s.turn_count || 0}</span>
          <span class="monitor-session-time">${s.created_at || "-"}</span>
          <span class="monitor-session-actions">
            <button class="btn btn-sm btn-outline" onclick="viewSessionDetail('${s.id}')">查看</button>
            <button class="btn btn-sm btn-danger" onclick="deleteSession('${s.id}')">删除</button>
          </span>
        </div>
      `
      )
      .join("");
  } catch (e) {
    monitorSessionsList.innerHTML = `<div class="monitor-empty">加载失败: ${e.message}</div>`;
  }
}

// 查看会话详情
async function viewSessionDetail(sessionId) {
  try {
    // 加载会话记忆
    const res = await fetch(`${API_BASE}/memory?session_id=${sessionId}`);
    const data = await res.json();

    // 显示基本信息
    sessionDetailInfo.innerHTML = `
      <span>会话 ID: <strong>${sessionId.slice(0, 30)}...</strong></span>
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
      `
        )
        .join("");
    } else {
      sessionDetailTurns.innerHTML = '<div class="monitor-empty">暂无对话记录</div>';
    }

    sessionDetailModal.style.display = "flex";
  } catch (e) {
    alert(`加载会话详情失败: ${e.message}`);
  }
}

// 删除会话
async function deleteSession(sessionId) {
  if (!confirm(`确定要删除会话 ${sessionId.slice(0, 20)}... 吗？此操作不可恢复。`)) {
    return;
  }

  try {
    await fetch(`${API_BASE}/session?session_id=${sessionId}`, {
      method: "DELETE",
    });
    await loadSessionsList();
  } catch (e) {
    alert(`删除会话失败: ${e.message}`);
  }
}

// 关闭会话详情弹窗
btnCloseSessionDetail.addEventListener("click", () => {
  sessionDetailModal.style.display = "none";
});

sessionDetailModal.querySelector(".modal-backdrop").addEventListener("click", () => {
  sessionDetailModal.style.display = "none";
});

// 刷新按钮
btnRefreshSessions.addEventListener("click", () => {
  loadSessionsList();
});

// 页面加载后自动加载会话列表
document.addEventListener("DOMContentLoaded", () => {
  loadSessionsList();
});
