/* SongNhat AI-02 | Sprint 4 Browser Console (PLPT-55..PLPT-58 & DoD) */

let s4LatestOverview = null;
let s4DevSandboxEnabled = false;
let s4ActiveIncidentId = null;

function setS4Msg(elId, type, text) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.className = `msg ${type}`;
  el.textContent = text;
}

function setS4ConsoleOutput(title, text, passed = null) {
  const box = document.getElementById("s4-console-output");
  const titleEl = document.getElementById("s4-console-title");
  if (!box || !titleEl) return;
  titleEl.textContent = title;
  if (passed === true) {
    titleEl.className = "badge badge-ok";
  } else if (passed === false) {
    titleEl.className = "badge badge-hold";
  } else {
    titleEl.className = "badge badge-info";
  }
  box.textContent = text;
}

function renderS4Table(tbodyId, rows, cols, emptyText) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.replaceChildren();
  if (!rows || rows.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols.length;
    td.textContent = emptyText;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }
  const frag = document.createDocumentFragment();
  for (const r of rows) {
    const tr = document.createElement("tr");
    for (const c of cols) {
      const td = document.createElement("td");
      td.textContent = String(r[c] ?? "");
      tr.appendChild(td);
    }
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
}

function updateSummaryCount(id, labelPrefix, shown, total, limitNum = 15) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = `${labelPrefix} (hiển thị ${shown}/${total}, tối đa ${limitNum} bản ghi mới nhất)`;
}

function nowClockVi() {
  return new Date().toLocaleTimeString("vi-VN", { hour12: false });
}

async function loadSprint4Console() {
  const queueCard = document.getElementById("queue-card");
  const monPanel = document.getElementById("monitoring-panel");
  const pipePanel = document.getElementById("pipeline-panel");
  const g1Panel = document.getElementById("gate-g1-panel");
  const s4Panel = document.getElementById("sprint4-panel");
  if (!s4Panel) return;

  if (queueCard) queueCard.style.display = "none";
  if (monPanel) monPanel.style.display = "none";
  if (pipePanel) pipePanel.style.display = "none";
  if (g1Panel) g1Panel.style.display = "none";
  s4Panel.style.display = "block";

  try {
    const res = await apiFetch("/api/v1/sprint4/overview");
    const data = await res.json();
    if (!res.ok) {
      setS4Msg("s4-msg", "err", data.detail || "Vai trò hiện tại không có quyền mở bảng điều khiển Sprint 4.");
      return;
    }

    s4LatestOverview = data;
    s4DevSandboxEnabled = Boolean(data.dev_sandbox_enabled);

    const envBadge = document.getElementById("s4-env-mode");
    if (envBadge) {
      envBadge.textContent = `ENV_MODE: ${data.env_mode}`;
      envBadge.className = `badge ${s4DevSandboxEnabled ? "badge-warn" : "badge-info"}`;
    }

    const freshBadge = document.getElementById("s4-freshness-badge");
    if (freshBadge) {
      const ageTxt = data.freshness.source_age_hours == null ? "N/A" : `${data.freshness.source_age_hours}h`;
      freshBadge.textContent = `DB Freshness: ${data.freshness.status} (${ageTxt})`;
      freshBadge.className = `badge ${data.freshness.stale_locked ? "badge-hold" : "badge-ok"}`;
    }

    document.querySelectorAll("#sprint4-panel .dev-only-btn").forEach((btn) => {
      btn.disabled = !s4DevSandboxEnabled;
      btn.title = s4DevSandboxEnabled
        ? "Khả dụng trong ENV_MODE=development"
        : "Bị khóa ngoài ENV_MODE=development (SEC-06)";
    });

    // Bước 1: Security, Incident Hold & .env.example
    const sec = data.security || {};
    const hold = sec.active_incident_hold;
    s4ActiveIncidentId = hold ? hold.incident_id : null;

    const holdBadge = document.getElementById("s4-hold-status-badge");
    if (hold) {
      holdBadge.textContent = `ĐANG KHÓA SỰ CỐ #${hold.incident_id} (${hold.severity}) | SLA 4h hạn: ${hold.notify_deadline_at}`;
      holdBadge.className = "badge badge-hold";
    } else {
      holdBadge.textContent = "Trạng thái khóa sự cố 4h: KHÔNG ACTIVE (Bình thường)";
      holdBadge.className = "badge badge-ok";
    }

    const envEx = data.env_example || {};
    const envExBadge = document.getElementById("s4-env-example-badge");
    envExBadge.textContent = envEx.all_required_present
      ? ".env.example: ĐỦ 4 khóa bắt buộc (ĐẠT)"
      : `.env.example thiếu: ${(envEx.missing_keys || []).join(", ")}`;
    envExBadge.className = `badge ${envEx.all_required_present ? "badge-ok" : "badge-hold"}`;

    document.getElementById("s4-revoked-count").textContent = `revoked_sessions: ${sec.revoked_sessions_count ?? 0}`;
    document.getElementById("s4-audit-count").textContent = `security_audit_logs: ${sec.security_audit_logs_count ?? 0}`;

    updateSummaryCount(
      "s4-revoked-summary",
      "Xem bảng revoked_sessions",
      (sec.revoked_sessions_preview || []).length,
      sec.revoked_sessions_count ?? 0
    );
    renderS4Table(
      "s4-revoked-body",
      sec.revoked_sessions_preview,
      ["session_id", "user_id", "revoked_at", "reason"],
      "Chưa có phiên nào bị thu hồi trong revoked_sessions."
    );

    updateSummaryCount(
      "s4-audit-summary",
      "Xem bảng security_audit_logs",
      (sec.security_audit_logs_preview || []).length,
      sec.security_audit_logs_count ?? 0
    );
    renderS4Table(
      "s4-audit-body",
      sec.security_audit_logs_preview,
      ["id", "event_type", "user_id", "role", "endpoint", "status_code", "detail", "created_at"],
      "Chưa có bản ghi trong security_audit_logs."
    );

    updateSummaryCount(
      "s4-incident-summary",
      "Xem lịch sử bảng incident_holds",
      (sec.incident_history || []).length,
      (sec.incident_history || []).length
    );
    renderS4Table(
      "s4-incident-body",
      sec.incident_history,
      ["incident_id", "status", "severity", "reason", "triggered_at", "notify_deadline_at", "purchaser_notified_at", "within_4h_sla", "released_at"],
      "Chưa có sự cố nào được ghi trong incident_holds."
    );

    // Bước 2: Shadow & Fallback
    const sh = data.shadow || {};
    document.getElementById("s4-shadow-logs-count").textContent = `shadow_score_logs: ${sh.shadow_score_logs_count ?? 0}`;
    const mfLock = document.getElementById("s4-manifest-lock-badge");
    mfLock.textContent = `manifest.serving_mode_locked: ${sh.manifest_serving_mode_locked || "-"}`;
    mfLock.className = `badge ${sh.manifest_serving_mode_locked === "RULES_BASELINE" ? "badge-ok" : "badge-hold"}`;

    updateSummaryCount(
      "s4-batch-summary",
      "Xem bảng batch_run_logs",
      (sh.batch_run_logs_preview || []).length,
      (sh.batch_run_logs_preview || []).length,
      10
    );
    renderS4Table(
      "s4-batch-body",
      sh.batch_run_logs_preview,
      ["id", "run_type", "scoring_mode", "status", "rows_processed", "started_at"],
      "Chưa có phiên chạy batch nào trong batch_run_logs."
    );

    // Bước 3: Pilot 20BD & Isolation
    const pl = data.pilot || {};
    document.getElementById("s4-pilot-assign-count").textContent = `pilot_assignments: ${pl.pilot_assignments_count ?? 0}`;
    const confBadge = document.getElementById("s4-pilot-conflict-badge");
    confBadge.textContent =
      (pl.conflicting_accounts ?? 0) === 0
        ? "conflicting_accounts: 0 (ĐẠT — 100% nhất quán)"
        : `CẢNH BÁO xung đột: ${pl.conflicting_accounts}`;
    confBadge.className = `badge ${(pl.conflicting_accounts ?? 0) === 0 ? "badge-ok" : "badge-hold"}`;
    document.getElementById("s4-pilot-iso-counts").textContent =
      `exposure_logs: ${pl.exposure_logs_count ?? 0} | staff_override_logs: ${pl.staff_override_logs_count ?? 0} | raw_outcomes: ${pl.raw_outcomes_count ?? 0}`;

    updateSummaryCount(
      "s4-pilot-assign-summary",
      "Xem bảng pilot_assignments",
      (pl.pilot_assignments_preview || []).length,
      pl.pilot_assignments_count ?? 0
    );
    renderS4Table(
      "s4-pilot-assign-body",
      pl.pilot_assignments_preview,
      ["id", "assignment_key", "account_key", "treatment_arm", "assigned_at"],
      "Chưa có bản ghi trong pilot_assignments."
    );

    updateSummaryCount(
      "s4-exposure-summary",
      "Xem bảng exposure_logs",
      (pl.exposure_logs_preview || []).length,
      pl.exposure_logs_count ?? 0
    );
    renderS4Table(
      "s4-exposure-body",
      pl.exposure_logs_preview,
      ["id", "enquiry_id", "account_key", "staff_id", "treatment_arm", "scoring_mode", "exposed_at"],
      "Chưa có bản ghi trong exposure_logs."
    );

    updateSummaryCount(
      "s4-effort-summary",
      "Xem bảng pilot_effort_logs",
      (pl.pilot_effort_logs_preview || []).length,
      pl.pilot_effort_logs_count ?? 0
    );
    renderS4Table(
      "s4-effort-body",
      pl.pilot_effort_logs_preview,
      ["id", "business_date", "staff_id", "treatment_arm", "enquiries_reviewed", "queue_prep_minutes", "triage_duration_seconds", "logged_at"],
      "Chưa có bản ghi trong pilot_effort_logs."
    );

    // Bước 4: Drift Snapshots & Alerts
    const dr = data.drift || {};
    document.getElementById("s4-drift-db-counts").textContent =
      `daily_drift_snapshots: ${dr.daily_drift_snapshots_count ?? 0} | drift_investigation_alerts: ${dr.drift_investigation_alerts_count ?? 0}`;

    updateSummaryCount(
      "s4-drift-alerts-summary",
      "Xem bảng drift_investigation_alerts",
      (dr.drift_investigation_alerts_preview || []).length,
      dr.drift_investigation_alerts_count ?? 0
    );
    renderS4Table(
      "s4-drift-alerts-body",
      dr.drift_investigation_alerts_preview,
      ["id", "snapshot_id", "as_of_date", "alert_code", "column_name", "detail", "status", "created_at"],
      "Chưa có cảnh báo nào trong drift_investigation_alerts."
    );

    updateSummaryCount(
      "s4-drift-snaps-summary",
      "Xem bảng daily_drift_snapshots",
      (dr.daily_drift_snapshots_preview || []).length,
      dr.daily_drift_snapshots_count ?? 0
    );
    renderS4Table(
      "s4-drift-snaps-body",
      dr.daily_drift_snapshots_preview,
      ["id", "as_of_date", "daily_volume", "score_coverage_pct", "unscored_count", "investigation_triggered", "auto_retrain_allowed", "created_at"],
      "Chưa có snapshot nào trong daily_drift_snapshots."
    );
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setS4Msg("s4-msg", "err", `Lỗi tải bảng điều khiển Sprint 4: ${err.message}`);
    }
  }
}

async function handleS4AuthLifecycleTest() {
  const btn = document.getElementById("btn-s4-auth-flow");
  if (btn) btn.disabled = true;
  try {
    // 1) Login tạm SALES_READER (không đụng sessionStorage của phiên hiện tại)
    const r1 = await fetch(`${API_BASE}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "sales_01", role: "SALES_READER" }),
    });
    const d1 = await r1.json();
    const tok = d1.token || "";

    // 2) Thử POST /api/block với token SALES_READER -> kỳ vọng 403
    const r2 = await fetch(`${API_BASE}/api/block`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tok}` },
      body: JSON.stringify({ enquiry_id: "SN-0001" }),
    });
    const d2 = await r2.json();

    // 3) Logout thu hồi token -> kỳ vọng 200 & revoked=true
    const r3 = await fetch(`${API_BASE}/api/v1/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${tok}` },
    });
    const d3 = await r3.json();

    // 4) Dùng lại token vừa logout -> kỳ vọng 401
    const r4 = await fetch(`${API_BASE}/api/v1/workspace/queue`, {
      headers: { Authorization: `Bearer ${tok}` },
    });
    const d4 = await r4.json();

    const okAll =
      r1.status === 200 &&
      d1.role === "SALES_READER" &&
      r2.status === 403 &&
      r3.status === 200 &&
      d3.revoked === true &&
      r4.status === 401;

    const lines = [
      `=== Bước 1.2 (PLPT-55): Chuỗi Auth HMAC + Revocation (${nowClockVi()}) ===`,
      `1. POST /api/v1/auth/login (SALES_READER): HTTP ${r1.status} | role=${d1.role} | session_id=${d1.session_id}`,
      `2. POST /api/block bằng SALES_READER (kỳ vọng 403): HTTP ${r2.status} | detail=${d2.detail}`,
      `3. POST /api/v1/auth/logout (thu hồi phiên): HTTP ${r3.status} | ${JSON.stringify(d3)}`,
      `4. GET /api/v1/workspace/queue dùng lại token đã logout (kỳ vọng 401): HTTP ${r4.status} | detail=${d4.detail}`,
      "",
      `Kết luận: ${okAll ? "ĐẠT 100% — Phiên hiện tại của bạn vẫn giữ nguyên; hãy mở bảng revoked_sessions & security_audit_logs bên dưới để đối chiếu." : "KHÔNG ĐẠT"}`,
    ].join("\n");

    setS4ConsoleOutput(`Bước 1.2 Auth HMAC Flow -> ${okAll ? "PASSED" : "FAILED"}`, lines, okAll);
    setS4Msg(
      "s4-step1-msg",
      okAll ? "ok" : "err",
      okAll
        ? `Bước 1.2 ĐẠT lúc ${nowClockVi()}: Login=200 → /api/block=403 → Logout=200 (revoked=true) → Reuse=401. Đã cập nhật revoked_sessions & security_audit_logs.`
        : "Bước 1.2 không khớp kỳ vọng — xem chi tiết trong khung kết quả bên dưới."
    );
    await loadSprint4Console();
  } catch (err) {
    setS4Msg("s4-step1-msg", "err", `Lỗi chạy chuỗi Auth: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleS4IncidentAction(action) {
  const severity = document.getElementById("s4-inc-severity").value;
  const reason = document.getElementById("s4-inc-reason").value.trim() || "Demo test khóa sự cố 4 giờ";
  const channel = document.getElementById("s4-inc-channel").value.trim() || "OFFICIAL_EMAIL";
  const resolution = document.getElementById("s4-inc-resolution").value.trim() || "Hoàn tất kiểm thử demo";

  try {
    if (action === "get") {
      const res = await apiFetch("/api/v1/admin/incident-hold");
      const data = await res.json();
      setS4ConsoleOutput(`GET /api/v1/admin/incident-hold (HTTP ${res.status})`, JSON.stringify(data, null, 2), res.ok);
      setS4Msg(
        "s4-step1-msg",
        res.ok ? "ok" : "err",
        res.ok
          ? `Đã kiểm tra trạng thái khóa sự cố lúc ${nowClockVi()}: active=${data.active}, sla_hours=${data.sla_hours}h.`
          : `HTTP ${res.status}: ${data.detail || "Lỗi"}`
      );
      return;
    }

    let payload = { action };
    if (action === "activate") {
      payload = { action: "activate", severity, reason, affected_scope: "ALL_QUEUES" };
    } else if (action === "notify") {
      if (!s4ActiveIncidentId) {
        setS4Msg("s4-step1-msg", "err", "Chưa có khóa sự cố đang hoạt động. Hãy bấm '1.3a Kích hoạt khóa sự cố' trước.");
        return;
      }
      payload = { action: "notify", incident_id: s4ActiveIncidentId, channel };
    } else if (action === "release") {
      if (!s4ActiveIncidentId) {
        setS4Msg("s4-step1-msg", "err", "Chưa có khóa sự cố đang hoạt động để mở khóa.");
        return;
      }
      payload = { action: "release", incident_id: s4ActiveIncidentId, resolution_note: resolution };
    }

    const res = await apiFetch("/api/v1/admin/incident-hold", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    setS4ConsoleOutput(
      `POST /api/v1/admin/incident-hold (${action}) -> HTTP ${res.status}`,
      JSON.stringify(data, null, 2),
      res.ok
    );
    if (!res.ok) {
      setS4Msg("s4-step1-msg", "err", `HTTP ${res.status}: ${data.detail || "Không thực hiện được."}`);
      return;
    }
    setS4Msg(
      "s4-step1-msg",
      "ok",
      `Đã thực thi incident-hold (${action}) lúc ${nowClockVi()}: incident_id=${data.incident_id}, status=${data.status || "OK"}.`
    );
    await loadSprint4Console();
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setS4Msg("s4-step1-msg", "err", `Lỗi khóa sự cố: ${err.message}`);
    }
  }
}

async function handleS4SecurityReadiness() {
  const btn = document.getElementById("btn-s4-sec-readiness");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/admin/security-readiness");
    const data = await res.json();
    setS4ConsoleOutput(`GET /api/v1/admin/security-readiness (HTTP ${res.status})`, JSON.stringify(data, null, 2), res.ok);
    if (!res.ok) {
      setS4Msg("s4-step1-msg", "err", `HTTP ${res.status}: ${data.detail || "Không có quyền xem Security Readiness."}`);
      return;
    }
    const legalCount = ((data.vn_legal_compliance || {}).instruments || []).length;
    const trainStatus = (data.sales_training_status || {}).status || "-";
    setS4Msg(
      "s4-step1-msg",
      "ok",
      `Security Readiness (${nowClockVi()}): env=${data.env_mode}, rbac_v1=${data.rbac_enforced_v1}, legacy_header=${data.allow_legacy_role_header}, Pháp lý VN=${legalCount} văn bản, Đào tạo Sales=${trainStatus}.`
    );
    await loadSprint4Console();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleS4ShadowSummary() {
  const btn = document.getElementById("btn-s4-shadow-summary");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/shadow/summary");
    const data = await res.json();
    setS4ConsoleOutput(`GET /api/v1/shadow/summary (HTTP ${res.status})`, JSON.stringify(data, null, 2), res.ok);
    if (!res.ok) {
      setS4Msg("s4-step2-msg", "err", `HTTP ${res.status}: ${data.detail || "Lỗi đọc Shadow Summary."}`);
      return;
    }
    const rt = data.serving_route || {};
    const modeBadge = document.getElementById("s4-shadow-mode-badge");
    modeBadge.textContent = `active_mode: ${rt.active_mode} | rules_only_enforced=${rt.rules_only_enforced}`;
    modeBadge.className = `badge ${rt.rules_only_enforced ? "badge-ok" : "badge-hold"}`;
    setS4Msg(
      "s4-step2-msg",
      "ok",
      `Shadow Summary (${nowClockVi()}): active_mode=${rt.active_mode}, fallback_reason=${rt.fallback_reason || "NONE"}, shadow_score_logs=${data.shadow_score_logs_count}.`
    );
    await loadSprint4Console();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleS4ShadowFallback(driftTriggered, controlOk) {
  try {
    const res = await apiFetch("/api/v1/shadow/fallback", {
      method: "POST",
      body: JSON.stringify({
        drift_fallback_triggered: driftTriggered,
        control_health_ok: controlOk,
      }),
    });
    const data = await res.json();
    setS4ConsoleOutput(`POST /api/v1/shadow/fallback (HTTP ${res.status})`, JSON.stringify(data, null, 2), res.ok);
    if (!res.ok) {
      setS4Msg("s4-step2-msg", "err", `HTTP ${res.status}: ${data.detail || "Chỉ MODEL_REVIEWER / ADMINISTRATOR có quyền kích hoạt fallback."}`);
      return;
    }
    const modeBadge = document.getElementById("s4-shadow-mode-badge");
    modeBadge.textContent = `active_mode: ${data.active_mode} | reason: ${data.fallback_reason}`;
    modeBadge.className = `badge ${data.rules_only_enforced ? "badge-ok" : "badge-hold"}`;
    setS4Msg(
      "s4-step2-msg",
      "ok",
      `Fallback kiểm chứng ĐẠT (${nowClockVi()}): active_mode=${data.active_mode}, rules_only_enforced=${data.rules_only_enforced}, fallback_reason=${data.fallback_reason}.`
    );
    await loadSprint4Console();
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setS4Msg("s4-step2-msg", "err", `Lỗi fallback: ${err.message}`);
    }
  }
}

async function handleS4PilotPairTest() {
  const btn = document.getElementById("btn-s4-pilot-pair");
  if (btn) btn.disabled = true;
  const acct = document.getElementById("s4-pilot-acct").value.trim() || "ACCT-DEMO-01";
  try {
    const r1 = await apiFetch("/api/v1/pilot/effort", {
      method: "POST",
      body: JSON.stringify({
        enquiry_id: "SN-0001",
        account_key: acct,
        enquiries_reviewed: 5,
        queue_prep_minutes: 3,
        triage_duration_seconds: 900,
        due_total: 5,
        due_on_time: 5,
      }),
    });
    const d1 = await r1.json();

    const r2 = await apiFetch("/api/v1/pilot/effort", {
      method: "POST",
      body: JSON.stringify({
        enquiry_id: "SN-0002",
        account_key: acct,
        enquiries_reviewed: 5,
        queue_prep_minutes: 2,
        triage_duration_seconds: 840,
        due_total: 5,
        due_on_time: 5,
      }),
    });
    const d2 = await r2.json();

    const consistent = r1.ok && r2.ok && d1.treatment_arm && d1.treatment_arm === d2.treatment_arm;
    setS4ConsoleOutput(
      `Bước 3.2a POST /api/v1/pilot/effort ×2 (${acct}) -> ${consistent ? "PASSED" : "FAILED"}`,
      JSON.stringify({ account_key: acct, consistent_treatment_arm: consistent, call_1: d1, call_2: d2 }, null, 2),
      consistent
    );
    setS4Msg(
      "s4-step3-msg",
      consistent ? "ok" : "err",
      consistent
        ? `Bước 3.2 ĐẠT (${nowClockVi()}): Cả SN-0001 và SN-0002 của ${acct} đều nhận cùng treatment_arm=${d1.treatment_arm} (0 xung đột). Đã cập nhật pilot_assignments & exposure_logs.`
        : `HTTP ${r1.status}/${r2.status}: ${d1.detail || d2.detail || "Không khớp treatment_arm."}`
    );
    if (typeof loadPilotTelemetry === "function") await loadPilotTelemetry();
    await loadSprint4Console();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleS4PilotTelemetry() {
  const btn = document.getElementById("btn-s4-pilot-telemetry");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/pilot/telemetry");
    const data = await res.json();
    setS4ConsoleOutput(`GET /api/v1/pilot/telemetry (HTTP ${res.status})`, JSON.stringify(data, null, 2), res.ok);
    if (!res.ok) {
      setS4Msg("s4-step3-msg", "err", `HTTP ${res.status}: ${data.detail || "Lỗi"}`);
      return;
    }
    const red = data.effort_reduction_pct !== null ? `${data.effort_reduction_pct}%` : "N/A";
    setS4Msg(
      "s4-step3-msg",
      "ok",
      `Pilot Telemetry (${nowClockVi()}): ${data.observed_business_days}/20 ngày làm việc | Giảm công sức: ${red} | Đạt >=20%: ${data.effort_target_20pct_met} | Exposure logs: ${data.exposure_logs_count}.`
    );
    if (typeof loadPilotTelemetry === "function") await loadPilotTelemetry();
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleS4CheckDrift(showBannerPrefix = "Kiểm tra Drift") {
  const res = await apiFetch("/api/v1/monitoring/drift");
  const data = await res.json();
  setS4ConsoleOutput(`GET /api/v1/monitoring/drift (HTTP ${res.status})`, JSON.stringify(data, null, 2), res.ok);
  if (!res.ok) {
    setS4Msg("s4-step4-msg", "err", `HTTP ${res.status}: ${data.detail || "Không đọc được drift."}`);
    return null;
  }

  const alertCodes = (data.alerts || []).map((a) => a.code);
  const driftBadge = document.getElementById("s4-drift-status-badge");
  driftBadge.textContent = data.investigation_triggered
    ? `CẢNH BÁO DRIFT (${alertCodes.join(", ")}) | coverage=${data.score_coverage_pct}% | auto_retrain=${data.auto_retrain_enabled}`
    : `Bình thường | coverage=${data.score_coverage_pct}% | ${data.ranking_drift_status} | auto_retrain=${data.auto_retrain_enabled}`;
  driftBadge.className = `badge ${data.investigation_triggered ? "badge-hold" : "badge-ok"}`;

  setS4Msg(
    "s4-step4-msg",
    data.investigation_triggered ? "err" : "ok",
    `${showBannerPrefix} (${nowClockVi()}): daily_volume=${data.daily_volume}, coverage=${data.score_coverage_pct}%, investigation_triggered=${data.investigation_triggered}, auto_retrain_enabled=${data.auto_retrain_enabled}` +
      (alertCodes.length ? `, alerts=[${alertCodes.join(", ")}].` : " (Không có cảnh báo).")
  );
  await loadSprint4Console();
  return data;
}

async function handleS4SimulateDrift() {
  const btn = document.getElementById("btn-s4-sim-drift");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/simulate-drift", {
      method: "POST",
      body: JSON.stringify({ rows: 200 }),
    });
    const data = await res.json();
    if (!res.ok) {
      setS4Msg("s4-step4-msg", "err", `HTTP ${res.status}: ${data.detail || "Không giả lập được drift."}`);
      return;
    }
    await handleS4CheckDrift(`Bước 4.3a Đã giả lập làm trống ${data.updated_rows} dòng spec_completeness`);
  } finally {
    if (btn) btn.disabled = !s4DevSandboxEnabled;
  }
}

async function handleS4RestoreCleanDrift() {
  const btn = document.getElementById("btn-s4-restore-drift");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/restore-clean-drift", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) {
      setS4Msg("s4-step4-msg", "err", `HTTP ${res.status}: ${data.detail || "Không khôi phục được dữ liệu sạch."}`);
      return;
    }
    await handleS4CheckDrift(
      `Bước 4.3b Đã khôi phục dữ liệu sạch (clean=${data.deduplication.clean_count}, merged=${data.deduplication.merged_count})`
    );
  } finally {
    if (btn) btn.disabled = !s4DevSandboxEnabled;
  }
}

async function handleS4RestoreFreshAll() {
  const btn = document.getElementById("btn-s4-restore-fresh");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/restore-fresh", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) {
      setS4Msg("s4-msg", "err", `HTTP ${res.status}: ${data.detail || "Lỗi làm tươi dữ liệu."}`);
      return;
    }
    setS4Msg(
      "s4-msg",
      "ok",
      `Bước 0 hoàn tất lúc ${nowClockVi()}: Đã làm tươi DB (< 24h) — clean=${data.deduplication.clean_count}, merged=${data.deduplication.merged_count}.`
    );
    await loadSprint4Console();
  } finally {
    if (btn) btn.disabled = !s4DevSandboxEnabled;
  }
}

async function handleRunSprint4Check(buttonId, suiteId, msgTarget = "s4-msg") {
  const btn = document.getElementById(buttonId);
  if (btn) btn.disabled = true;
  setS4ConsoleOutput(`Đang chạy ${suiteId}...`, "Đang thực thi kiểm thử trong sandbox DEV, vui lòng chờ...", null);
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/run-sprint4-check", {
      method: "POST",
      body: JSON.stringify({ suite_id: suiteId }),
    });
    const data = await res.json();
    if (!res.ok) {
      setS4Msg(msgTarget, "err", `HTTP ${res.status}: ${data.detail || "Không chạy được kiểm thử Sprint 4."}`);
      setS4ConsoleOutput(`Lỗi (${suiteId})`, data.detail || "Thất bại", false);
      return;
    }
    setS4ConsoleOutput(
      `${data.label} -> ${data.passed ? "PASSED" : "FAILED"}`,
      data.output || "(Không có output)",
      Boolean(data.passed)
    );
    setS4Msg(
      msgTarget,
      data.passed ? "ok" : "err",
      `${data.label} (${nowClockVi()}): ${data.passed ? "ĐẠT (PASSED)" : "KHÔNG ĐẠT (FAILED)"}`
    );
    await loadSprint4Console();
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setS4Msg(msgTarget, "err", `Lỗi kết nối: ${err.message}`);
    }
  } finally {
    if (btn) btn.disabled = !s4DevSandboxEnabled;
  }
}

function initSprint4ConsoleEvents() {
  const bind = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  };

  // Header
  bind("btn-s4-refresh", async () => {
    await loadSprint4Console();
    setS4Msg("s4-msg", "ok", `Đã đồng bộ toàn bộ dữ liệu Sprint 4 từ SQLite lúc ${nowClockVi()}.`);
  });
  bind("btn-s4-restore-fresh", handleS4RestoreFreshAll);

  // Bước 1: PLPT-55
  bind("btn-s4-test-plpt55", () => handleRunSprint4Check("btn-s4-test-plpt55", "plpt55_rbac_incident", "s4-step1-msg"));
  bind("btn-s4-auth-flow", handleS4AuthLifecycleTest);
  bind("btn-s4-inc-activate", () => handleS4IncidentAction("activate"));
  bind("btn-s4-inc-get", () => handleS4IncidentAction("get"));
  bind("btn-s4-inc-notify", () => handleS4IncidentAction("notify"));
  bind("btn-s4-inc-release", () => handleS4IncidentAction("release"));
  bind("btn-s4-sec-readiness", handleS4SecurityReadiness);
  bind("btn-s4-view-env-example", () => {
    const ex = s4LatestOverview && s4LatestOverview.env_example;
    if (!ex || !ex.exists) {
      setS4Msg("s4-step1-msg", "err", "Không tìm thấy tệp .env.example.");
      return;
    }
    setS4ConsoleOutput(".env.example (Kiểm tra khóa cấu hình môi trường)", ex.content, ex.all_required_present);
    setS4Msg("s4-step1-msg", "ok", `Đang hiển thị .env.example (${ex.present_keys.length} khóa cấu hình) ở khung kết quả bên dưới.`);
  });
  bind("btn-s4-git-secrets", () => handleRunSprint4Check("btn-s4-git-secrets", "plpt55_git_secrets", "s4-step1-msg"));

  // Bước 2: PLPT-56
  bind("btn-s4-test-plpt56", () => handleRunSprint4Check("btn-s4-test-plpt56", "plpt56_shadow", "s4-step2-msg"));
  bind("btn-s4-shadow-summary", handleS4ShadowSummary);
  bind("btn-s4-shadow-fb-drift", () => handleS4ShadowFallback(true, true));
  bind("btn-s4-shadow-fb-ctrl", () => handleS4ShadowFallback(false, false));

  // Bước 3: PLPT-57
  bind("btn-s4-test-plpt57", () => handleRunSprint4Check("btn-s4-test-plpt57", "plpt57_pilot_perf", "s4-step3-msg"));
  bind("btn-s4-pilot-pair", handleS4PilotPairTest);
  bind("btn-s4-pilot-telemetry", handleS4PilotTelemetry);

  // Bước 4: PLPT-58
  bind("btn-s4-test-plpt58", () => handleRunSprint4Check("btn-s4-test-plpt58", "plpt58_drift_only", "s4-step4-msg"));
  bind("btn-s4-check-drift", () => handleS4CheckDrift("Bước 4.2a Kiểm tra GET /api/v1/monitoring/drift"));
  bind("btn-s4-jump-monitoring", () => {
    currentView = "monitoring";
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === "monitoring"));
    loadQueue();
  });
  bind("btn-s4-sim-drift", handleS4SimulateDrift);
  bind("btn-s4-restore-drift", handleS4RestoreCleanDrift);

  // Bước 5: DoD Sprint 4
  bind("btn-s4-dod-4suites", () => handleRunSprint4Check("btn-s4-dod-4suites", "sprint4_four_suites", "s4-msg"));
  bind("btn-s4-dod-full", () => handleRunSprint4Check("btn-s4-dod-full", "sprint4_full_regression", "s4-msg"));
  bind("btn-s4-dod-gitleaks", () => handleRunSprint4Check("btn-s4-dod-gitleaks", "sprint4_gitleaks", "s4-msg"));
}

initSprint4ConsoleEvents();
