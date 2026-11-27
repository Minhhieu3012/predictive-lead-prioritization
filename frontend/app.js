/* SongNhat AI-02 | Sales Workspace Client Logic (PLPT-24 / PLPT-25) */

const API_BASE =
  window.location.protocol.startsWith("http") && window.location.port === "8000"
    ? window.location.origin
    : "http://127.0.0.1:8000";

let currentView = "active";
let pendingOverrideEnqId = "";
let triageStartMs = performance.now();

function stripVietnameseDiacritics(str) {
  return String(str || "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function getSession() {
  const role = sessionStorage.getItem("sn_role");
  const userId = sessionStorage.getItem("sn_user_id");
  const token = sessionStorage.getItem("sn_token");
  const expired = sessionStorage.getItem("sn_expired") === "true";
  if (!role || !token || expired) return null;
  return { role, userId: userId || "sales_01", token };
}

function showLogin(errMsg = "") {
  document.getElementById("login-screen").style.display = "block";
  document.getElementById("workspace-screen").style.display = "none";
  const msgEl = document.getElementById("login-msg");
  if (errMsg) {
    msgEl.className = "msg err";
    msgEl.textContent = errMsg;
  } else {
    msgEl.className = "msg";
    msgEl.textContent = "";
  }
}

function showWorkspace() {
  const sess = getSession();
  if (!sess) {
    showLogin("Phiên đăng nhập chưa khởi tạo hoặc đã hết hạn (401).");
    return;
  }
  document.getElementById("login-screen").style.display = "none";
  document.getElementById("workspace-screen").style.display = "block";
  document.getElementById("current-role").textContent = sess.role;
  const pipeTab = document.getElementById("tab-btn-pipeline");
  if (pipeTab) {
    const canSeePipe = ["DATA_STEWARD", "SUPERVISOR", "MODEL_REVIEWER", "ADMINISTRATOR"].includes(sess.role);
    pipeTab.style.display = canSeePipe ? "inline-block" : "none";
    if (!canSeePipe && currentView === "pipeline") {
      currentView = "active";
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === "active"));
    }
  }
  loadQueue();
  loadPilotTelemetry();
}

function showMessage(type, text) {
  const msgEl = document.getElementById("action-msg");
  msgEl.className = `msg ${type}`;
  msgEl.textContent = text;
}

async function apiFetch(path, options = {}) {
  const sess = getSession();
  if (!sess) {
    showLogin("Phiên đăng nhập không hợp lệ (401).");
    throw new Error("Unauthorized");
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sess.token}`,
    ...(options.headers || {}),
  };
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (res.status === 401) {
    sessionStorage.removeItem("sn_role");
    showLogin("Phiên đã hết hạn hoặc truy cập trái phép (HTTP 401). Vui lòng đăng nhập lại.");
    throw new Error("HTTP 401");
  }
  return res;
}

function openOverrideModal(enqId) {
  pendingOverrideEnqId = enqId;
  document.getElementById("ov-enquiry-label").textContent = enqId;
  document.getElementById("override-justification").value = "";
  document.getElementById("override-modal").style.display = "flex";
}

function buildQueueRow(row) {
  const tr = document.createElement("tr");
  tr.dataset.enquiryId = String(row.enquiry_id || "");
  tr.dataset.accountKey = String(row.account_key || "");

  if (currentView === "quarantine") {
    const tdQ = document.createElement("td");
    tdQ.colSpan = 8;
    tdQ.textContent = `${row.enquiry_id || "(no-id)"} | ${row.quarantine_source || ""} | Lý do: ${row.quarantine_reason || ""}`;
    tr.appendChild(tdQ);
    return tr;
  }

  const tdModelRank = document.createElement("td");
  tdModelRank.textContent = `#${row.model_rank}`;

  const tdWorkOrder = document.createElement("td");
  const strongOrder = document.createElement("strong");
  strongOrder.textContent = `#${row.final_work_order}`;
  tdWorkOrder.appendChild(strongOrder);
  if (
    row.allocation_type === "EXPLORATION_10PCT" ||
    row.allocation_type === "OVERFLOW_OUTSIDE_K" ||
    row.allocation_type === "OVERRIDE_PROMOTED"
  ) {
    const expTag = document.createElement("div");
    expTag.className = row.allocation_type === "OVERFLOW_OUTSIDE_K" ? "badge badge-warn" : "badge badge-info";
    expTag.textContent =
      row.allocation_type === "OVERFLOW_OUTSIDE_K" ? "OVERFLOW_OUTSIDE_K (Ngoài Top-K)" : row.allocation_type;
    tdWorkOrder.appendChild(expTag);
  }

  const tdCohortOwner = document.createElement("td");
  tdCohortOwner.textContent = `${row.cohort_id || "-"} / ${row.owner || "Sales Team"}`;

  const tdLead = document.createElement("td");
  const leadIdEl = document.createElement("strong");
  leadIdEl.textContent = String(row.enquiry_id || "");
  const acctEl = document.createElement("div");
  acctEl.textContent = String(row.account_key || "");
  const prodEl = document.createElement("div");
  prodEl.className = "product-family";
  prodEl.textContent = String(row.product_family || "");
  tdLead.append(leadIdEl, acctEl, prodEl);
  if (row.spec_completeness === "Minimal") {
    const minBadge = document.createElement("span");
    minBadge.className = "badge badge-warn";
    minBadge.textContent = "Minimal spec";
    tdLead.appendChild(minBadge);
  }

  const tdScore = document.createElement("td");
  // Tuyệt đối không gắn dấu % vào điểm
  const scoreStrong = document.createElement("strong");
  scoreStrong.textContent = String(row.rule_score ?? "");
  const scoreUnit = document.createTextNode(" điểm");
  const modeBr = document.createElement("br");
  const modeSpan = document.createElement("span");
  modeSpan.className = "badge badge-info";
  modeSpan.textContent = String(row.scoring_mode || "RULES_BASELINE");
  tdScore.append(scoreStrong, scoreUnit, modeBr, modeSpan);

  const tdSla = document.createElement("td");
  tdSla.textContent = `Tuổi snapshot: ${row.snapshot_age_hours ?? "-"}h | SLA: ${row.sla_due_timestamp || "-"}`;
  if (row.is_urgent) {
    const sp = document.createElement("span");
    sp.className = "tier0";
    sp.textContent = "TIER 0";
    tdSla.appendChild(document.createElement("br"));
    tdSla.appendChild(sp);
  }
  if (row.is_overdue_2bd) {
    const sp2 = document.createElement("span");
    sp2.className = "overdue";
    sp2.textContent = "QUÁ HẠN 2BD";
    tdSla.appendChild(document.createElement("br"));
    tdSla.appendChild(sp2);
  }

  const tdFactors = document.createElement("td");
  const factors =
    row.contributing_factors || (row.reasons || []).map((r) => ({ factor: r, source_timestamp: row.t0_time }));
  for (const f of factors.slice(0, 3)) {
    const d = document.createElement("div");
    d.textContent = `• ${f.factor}`;
    const ts = document.createElement("div");
    ts.className = "factor-ts";
    ts.textContent = `Nguồn t0: ${f.source_timestamp || row.t0_time || "-"}`;
    tdFactors.append(d, ts);
  }
  const noteEl = document.createElement("div");
  noteEl.className = "factor-ts";
  noteEl.textContent =
    row.non_causal_disclaimer ||
    "Điểm ưu tiên chỉ mang tính hỗ trợ sắp xếp, không phải xác suất thắng đơn hay quan hệ nhân quả.";
  tdFactors.appendChild(noteEl);

  const tdAction = document.createElement("td");
  const consentLabel = document.createElement("label");
  consentLabel.className = "consent-box";
  const consentCb = document.createElement("input");
  consentCb.type = "checkbox";
  consentLabel.append(consentCb, document.createTextNode("Đã xác minh quyền liên hệ hợp pháp"));

  const btnAccept = document.createElement("button");
  btnAccept.type = "button";
  btnAccept.className = "btn-primary btn-sm";
  btnAccept.textContent = "ACCEPT";
  btnAccept.disabled = true;

  const btnOverride = document.createElement("button");
  btnOverride.type = "button";
  btnOverride.className = "btn-secondary btn-sm";
  btnOverride.textContent = "OVERRIDE";
  btnOverride.disabled = true;

  consentCb.addEventListener("change", () => {
    btnAccept.disabled = !consentCb.checked;
    btnOverride.disabled = !consentCb.checked;
  });

  btnAccept.addEventListener("click", async () => {
    if (!consentCb.checked) {
      showMessage("err", "Vui lòng tick xác nhận quyền liên hệ hợp pháp trước khi ACCEPT.");
      return;
    }
    const resAcc = await apiFetch("/api/v1/workspace/review", {
      method: "POST",
      body: JSON.stringify({
        enquiry_id: row.enquiry_id,
        decision_type: "ACCEPT_PRIORITY",
        contact_permission_verified: consentCb.checked,
        contact_attempted: consentCb.checked,
        review_completed: true,
      }),
    });
    const bodyAcc = await resAcc.json();
    showMessage(
      resAcc.ok ? "ok" : "err",
      resAcc.ok ? `Đã ghi nhận ACCEPT_PRIORITY cho ${row.enquiry_id}` : bodyAcc.detail || "Lỗi"
    );
    if (resAcc.ok) loadQueue();
  });

  btnOverride.addEventListener("click", () => {
    if (!consentCb.checked) {
      showMessage("err", "Vui lòng tick xác nhận quyền liên hệ hợp pháp trước khi OVERRIDE.");
      return;
    }
    openOverrideModal(row.enquiry_id);
  });

  tdAction.append(consentLabel, btnAccept, document.createTextNode(" "), btnOverride);
  tr.append(tdModelRank, tdWorkOrder, tdCohortOwner, tdLead, tdScore, tdSla, tdFactors, tdAction);
  return tr;
}

async function loadQueue() {
  triageStartMs = performance.now();
  const queueCard = document.getElementById("queue-card");
  const monPanel = document.getElementById("monitoring-panel");
  const pipePanel = document.getElementById("pipeline-panel");
  if (currentView === "monitoring") {
    if (pipePanel) pipePanel.style.display = "none";
    await loadMonitoringDrift();
    return;
  }
  if (currentView === "pipeline") {
    if (typeof loadPipelineConsole === "function") {
      await loadPipelineConsole();
    }
    return;
  }
  if (queueCard && monPanel) {
    queueCard.style.display = "block";
    monPanel.style.display = "none";
  }
  if (pipePanel) {
    pipePanel.style.display = "none";
  }
  const qVal = document.getElementById("search-query").value.trim();
  const incOverflow = document.getElementById("overflow-toggle").value === "true";
  const params = new URLSearchParams({
    view: currentView,
    page: "1",
    limit: incOverflow ? "500" : "50",
    include_overflow: String(incOverflow),
  });
  if (qVal) params.set("q", qVal);

  try {
    const res = await apiFetch(`/api/v1/workspace/queue?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      showMessage("err", data.detail || "Không tải được hàng đợi.");
      return;
    }

    document.getElementById("scoring-mode").textContent = data.scoring_mode || "RULES_BASELINE";
    document.getElementById("cohort-id").textContent = data.cohort_id || "-";
    document.getElementById("capacity-k").textContent = data.capacity_k ?? 25;
    document.getElementById("explore-count").textContent = data.exploration_slots_allocated ?? 0;
    document.getElementById("overdue-count").textContent = data.overdue_2bd_count ?? 0;
    document.getElementById("quarantine-count").textContent = data.quarantine_count ?? 0;
    const ageHours = data.snapshot_age_hours;
    document.getElementById("age-hours").textContent =
      ageHours == null ? "Chưa xác định" : `${Number(ageHours).toFixed(1)} giờ`;

    const badge = document.getElementById("status-badge");
    const staleBanner = document.getElementById("stale-banner");
    const overloadBanner = document.getElementById("overload-banner");
    const queueCard = document.getElementById("queue-card");
    const isHeld = data.status === "HOLD_STALE_DATA" || data.locked === true;
    const isOverloaded = data.status === "CAPACITY_EXCEEDED" || data.is_overloaded === true;

    badge.textContent = data.status;
    badge.className = `badge ${isHeld ? "badge-hold" : isOverloaded ? "badge-warn" : "badge-ok"}`;

    if (isOverloaded) {
      overloadBanner.style.display = "block";
      overloadBanner.textContent =
        "CẢNH BÁO QUÁ TẢI: Số lượng hồ sơ TIER_0 hoặc quá hạn 2 ngày làm việc vượt công suất K.";
    } else {
      overloadBanner.style.display = "none";
    }

    if (isHeld && currentView !== "quarantine") {
      staleBanner.style.display = "block";
      staleBanner.textContent = data.warning || "HOLD_STALE_DATA: Dữ liệu nguồn quá 24 giờ.";
      queueCard.style.display = "none";
      return;
    }

    staleBanner.style.display = "none";
    queueCard.style.display = "block";

    const savedScrollY = window.scrollY;
    const tbody = document.getElementById("queue-body");
    const frag = document.createDocumentFragment();

    const items = data.items || [];
    const sess = getSession();
    const agingPanel = document.getElementById("supervisor-aging-panel");
    const agingList = document.getElementById("supervisor-aging-list");
    const overdueItems = items.filter((x) => x.is_overdue_2bd);
    if (sess && (sess.role === "SUPERVISOR" || sess.role === "ADMINISTRATOR") && overdueItems.length > 0) {
      agingPanel.style.display = "block";
      agingList.textContent = overdueItems.map((x) => `${x.enquiry_id} (${x.account_key})`).join(", ");
    } else {
      agingPanel.style.display = "none";
    }

    for (const row of items) {
      frag.appendChild(buildQueueRow(row));
    }
    tbody.replaceChildren(frag);
    window.scrollTo({ top: savedScrollY, behavior: "instant" });
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      showMessage("err", `Không kết nối được API tại ${API_BASE}.`);
    }
  }
}

async function animateAndRemoveBlockedRows(blockedEnqId, blockedAcctKey) {
  const tbody = document.getElementById("queue-body");
  const targets = Array.from(tbody.querySelectorAll("tr")).filter((tr) => {
    const eq = tr.dataset.enquiryId || "";
    const ac = tr.dataset.accountKey || "";
    if (blockedEnqId && eq === blockedEnqId) return true;
    if (!blockedEnqId && blockedAcctKey && ac === blockedAcctKey) return true;
    return false;
  });
  if (targets.length === 0) return;

  for (const tr of targets) {
    tr.classList.add("row-blocking");
  }
  await new Promise((resolve) => setTimeout(resolve, 300));
  for (const tr of targets) {
    tr.remove();
  }
  // Đánh lại số thứ tự hiển thị tạm thời trước khi đồng bộ nền từ API
  Array.from(tbody.querySelectorAll("tr")).forEach((tr, idx) => {
    const orderStrong = tr.querySelector("td:nth-child(2) strong");
    if (orderStrong) orderStrong.textContent = `#${idx + 1}`;
  });
}

async function submitBlock() {
  const enqInput = document.getElementById("enquiry_id");
  const acctInput = document.getElementById("account_key");
  const btnBlock = document.getElementById("btn-submit-block");
  const enquiry_id = enqInput.value.trim();
  const account_key = acctInput.value.trim();
  const reason = document.getElementById("reason").value.trim();
  if (!enquiry_id && !account_key) {
    showMessage("err", "Vui lòng nhập enquiry_id hoặc account_key.");
    return;
  }
  btnBlock.disabled = true;
  try {
    const res = await apiFetch("/api/block", {
      method: "POST",
      body: JSON.stringify({ enquiry_id, account_key, reason }),
    });
    const result = await res.json();
    if (!res.ok) {
      showMessage("err", result.detail || "Chặn thất bại.");
      return;
    }
    showMessage("ok", `Đã chặn thành công: ${result.enquiry_id || result.account_key}`);
    enqInput.value = "";
    acctInput.value = "";
    await animateAndRemoveBlockedRows(result.enquiry_id || "", result.account_key || "");
    await loadQueue();
  } finally {
    btnBlock.disabled = false;
  }
}

function initWorkspaceEvents() {
  document.getElementById("btn-login").addEventListener("click", async () => {
    const role = document.getElementById("login-role").value;
    const userId = document.getElementById("login-userid").value.trim() || "sales_01";
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, role }),
    });
    const data = await res.json();
    if (!res.ok) {
      showLogin(data.detail || "Đăng nhập thất bại.");
      return;
    }
    sessionStorage.setItem("sn_role", data.role);
    sessionStorage.setItem("sn_user_id", data.user_id);
    sessionStorage.setItem("sn_token", data.token);
    sessionStorage.removeItem("sn_expired");
    showWorkspace();
  });

  document.getElementById("btn-logout").addEventListener("click", async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch (_) {}
    sessionStorage.clear();
    showLogin("Đã đăng xuất.");
  });

  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      currentView = btn.dataset.view;
      loadQueue();
    });
  });

  document.getElementById("btn-search").addEventListener("click", loadQueue);
  document.getElementById("overflow-toggle").addEventListener("change", loadQueue);
  document.getElementById("btn-refresh").addEventListener("click", loadQueue);
  document.getElementById("btn-submit-block").addEventListener("click", submitBlock);

  document.getElementById("btn-ov-cancel").addEventListener("click", () => {
    document.getElementById("override-modal").style.display = "none";
  });

  document.getElementById("btn-ov-submit").addEventListener("click", async () => {
    const code = document.getElementById("override-reason-code").value;
    const just = document.getElementById("override-justification").value.trim();
    if (!just) {
      showMessage("err", "Giải trình nghiệp vụ không được để trống.");
      return;
    }
    const resOv = await apiFetch("/api/v1/workspace/override", {
      method: "POST",
      body: JSON.stringify({
        enquiry_id: pendingOverrideEnqId,
        override_reason_code: code,
        justification: just,
      }),
    });
    const bodyOv = await resOv.json();
    document.getElementById("override-modal").style.display = "none";
    showMessage(
      resOv.ok ? "ok" : "err",
      resOv.ok ? `Đã ghi staff_override_logs (${code}) cho ${pendingOverrideEnqId}` : bodyOv.detail || "Lỗi"
    );
    if (resOv.ok) loadQueue();
  });
}

initWorkspaceEvents();
showWorkspace();



async function loadPilotTelemetry() {
  const el = document.getElementById("pilot-telemetry-summary");
  if (!el) return;
  try {
    const res = await apiFetch("/pilot/telemetry");
    if (!res.ok) return;
    const data = await res.json();
    const red = data.effort_reduction_pct !== null ? `${data.effort_reduction_pct}%` : "N/A";
    const targetBadge = data.effort_target_20pct_met ? "ĐẠT (>= 20%)" : "Chưa đạt (< 20%)";
    el.textContent = `Ngày làm việc: ${data.observed_business_days}/20 | Giảm công sức: ${red} (${targetBadge}) | Exposure logs: ${data.exposure_logs_count || 0}`;
  } catch (_) {}
}

async function recordPilotTriageEffort(enquiryId, accountKey, overrideCount, modelRank) {
  const elapsedSec = Math.max(1, Math.round((performance.now() - triageStartMs) / 1000));
  triageStartMs = performance.now();
  try {
    await apiFetch("/pilot/effort", {
      method: "POST",
      body: JSON.stringify({
        enquiry_id: enquiryId,
        account_key: accountKey || "",
        enquiries_reviewed: 1,
        queue_prep_minutes: 0.5,
        triage_duration_seconds: elapsedSec,
        override_count: overrideCount,
        model_rank: modelRank,
      }),
    });
    loadPilotTelemetry();
  } catch (_) {}
}

async function loadMonitoringDrift() {
  const queueCard = document.getElementById("queue-card");
  const monPanel = document.getElementById("monitoring-panel");
  if (!monPanel || !queueCard) return;
  queueCard.style.display = "none";
  monPanel.style.display = "block";

  const metaEl = document.getElementById("monitoring-meta");
  const alertsEl = document.getElementById("monitoring-alerts");
  const tbody = document.getElementById("monitoring-body");
  tbody.innerHTML = "";

  const res = await apiFetch("/api/v1/monitoring/drift");
  const data = await res.json();
  if (!res.ok) {
    alertsEl.className = "msg err";
    alertsEl.textContent = data.detail || "Không có quyền xem Monitoring.";
    return;
  }

  const mature = data.mature_60d_status || {};
  const rankingStatus = data.ranking_drift_status || mature.ranking_drift_status || "DEFERRED_UNTIL_60D_MATURITY";
  const sourceMixEntries = Object.entries(data.source_mix_pct || {})
    .map(([k, v]) => `${k}: ${v}%`)
    .join(", ");
  metaEl.innerHTML = `
    <strong>Khối lượng hằng ngày:</strong> ${data.daily_volume ?? 0} |
    <strong>Độ bao phủ chấm điểm:</strong> ${data.score_coverage_pct}% (Ngưỡng &ge; 90%) |
    <strong>Trạng thái đánh giá xếp hạng:</strong> <code>${rankingStatus}</code> (${mature.immature_excluded_count || 0} hồ sơ chưa chín 60 ngày) |
    <strong>auto_retrain_enabled:</strong> <code>${String(data.auto_retrain_enabled)}</code>
    ${sourceMixEntries ? `<div style="margin-top:4px;"><strong>Cơ cấu kênh/nguồn:</strong> ${sourceMixEntries}</div>` : ""}
  `;
  if (data.alerts && data.alerts.length > 0) {
    alertsEl.className = "msg err";
    alertsEl.textContent = data.alerts.map((a) => a.detail).join(" | ");
  } else {
    alertsEl.className = "msg ok";
    alertsEl.textContent = "Không có cảnh báo trôi dạt dữ liệu.";
  }

  const missingMap = data.missingness_pct || {};
  const deltaMap = data.missingness_delta_pp || {};
  Object.keys(missingMap).forEach((col) => {
    const tr = document.createElement("tr");
    const delta = deltaMap[col] !== undefined ? deltaMap[col] : 0;
    const isSpike = delta >= 10.0;
    if (isSpike) {
      tr.style.backgroundColor = "#ffe5e5";
      tr.style.color = "#a10000";
    }
    tr.innerHTML = `<td>${col}</td><td>${missingMap[col]}%</td><td>${delta >= 0 ? "+" : ""}${delta} pp</td><td>${isSpike ? "CẢNH BÁO (>= 10 pp)" : "Bình thường"}</td>`;
    tbody.appendChild(tr);
  });
}
