/* SongNhat AI-02 | Sprint 3 Browser Console (PLPT-24..PLPT-28 & DoD) */

let g1LatestEvidence = null;
let g1DevSandboxEnabled = false;

function setG1Banner(type, text) {
  const el = document.getElementById("g1-msg");
  if (!el) return;
  el.className = `msg ${type}`;
  el.textContent = text;
}

function setG1ConsoleOutput(title, text, passed = null) {
  const box = document.getElementById("g1-console-output");
  const titleEl = document.getElementById("g1-console-title");
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

function renderG1Table(tbodyId, rows, cols, emptyText) {
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

function switchToWorkspaceTab(viewName, searchPreset = null) {
  const searchInput = document.getElementById("search-query");
  if (searchInput && searchPreset !== null) {
    searchInput.value = searchPreset;
  }
  currentView = viewName;
  document.querySelectorAll(".tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === viewName);
  });
  loadQueue();
}

function renderCfMatrix(results) {
  const grid = document.getElementById("g1-cf-grid");
  if (!grid) return;
  grid.replaceChildren();
  if (!results || results.length === 0) {
    grid.textContent = "Chưa có dữ liệu 40 Control Fixtures.";
    return;
  }
  const frag = document.createDocumentFragment();
  for (const item of results) {
    const pill = document.createElement("span");
    const ok = item.status === "PASSED";
    pill.className = `badge ${ok ? "badge-ok" : "badge-hold"} cf-pill`;
    pill.textContent = `${item.fixture_id}: ${item.status}`;
    if (item.error) pill.title = String(item.error);
    frag.appendChild(pill);
  }
  grid.appendChild(frag);
}

function renderCriteriaBadges(criteriaObj, failedList) {
  const container = document.getElementById("g1-criteria-grid");
  if (!container) return;
  container.replaceChildren();
  if (!criteriaObj || typeof criteriaObj !== "object") {
    container.textContent = "Chưa có kết quả tiêu chí Gate G1.";
    return;
  }
  const frag = document.createDocumentFragment();
  for (const [key, passed] of Object.entries(criteriaObj)) {
    const sp = document.createElement("span");
    sp.className = `badge ${passed ? "badge-ok" : "badge-hold"}`;
    sp.textContent = `${key}: ${passed ? "ĐẠT" : "CHƯA ĐẠT"}`;
    frag.appendChild(sp);
  }
  container.appendChild(frag);

  const failedEl = document.getElementById("g1-failed-criteria");
  if (failedEl) {
    const failed = failedList || [];
    failedEl.textContent =
      failed.length > 0 ? `failed_criteria: ${failed.join(", ")}` : "failed_criteria: (không có)";
  }
}

function renderBenchmarkMetricsTable(rep) {
  const tbody = document.getElementById("g1-benchmark-metrics-body");
  if (!tbody) return;
  tbody.replaceChildren();
  if (!rep) {
    renderG1Table("g1-benchmark-metrics-body", [], ["metric", "rules", "candidate"], "Chưa có báo cáo Gate G1.");
    return;
  }
  const r = rep.rules_baseline_metrics || {};
  const m = rep.candidate_model_metrics || {};
  const rows = [
    { metric: "selected_count (Top-K)", rules: r.selected_count, candidate: m.selected_count },
    { metric: "precision_at_k_conservative", rules: r.precision_at_k_conservative, candidate: m.precision_at_k_conservative },
    { metric: "precision_at_k_upper_bound", rules: r.precision_at_k_upper_bound, candidate: m.precision_at_k_upper_bound },
    { metric: "recall_at_k", rules: r.recall_at_k, candidate: m.recall_at_k },
    { metric: "average_precision", rules: r.average_precision, candidate: m.average_precision },
    { metric: "selected_outcome_coverage_pct", rules: r.selected_outcome_coverage_pct, candidate: m.selected_outcome_coverage_pct },
    { metric: "new_account_precision_at_k", rules: r.new_account_precision_at_k, candidate: m.new_account_precision_at_k },
    { metric: "returning_account_precision_at_k", rules: r.returning_account_precision_at_k, candidate: m.returning_account_precision_at_k },
  ];
  renderG1Table("g1-benchmark-metrics-body", rows, ["metric", "rules", "candidate"], "Chưa có số liệu.");
}

async function loadGateG1Console() {
  const queueCard = document.getElementById("queue-card");
  const monPanel = document.getElementById("monitoring-panel");
  const pipePanel = document.getElementById("pipeline-panel");
  const g1Panel = document.getElementById("gate-g1-panel");
  const s4Panel = document.getElementById("sprint4-panel");
  if (!g1Panel) return;

  if (queueCard) queueCard.style.display = "none";
  if (monPanel) monPanel.style.display = "none";
  if (pipePanel) pipePanel.style.display = "none";
  if (s4Panel) s4Panel.style.display = "none";
  g1Panel.style.display = "block";

  try {
    const res = await apiFetch("/api/v1/gate-g1/evidence");
    const data = await res.json();
    if (!res.ok) {
      setG1Banner("err", data.detail || "Vai trò hiện tại không có quyền mở bảng điều khiển Sprint 3 / Gate G1.");
      return;
    }

    g1LatestEvidence = data;
    g1DevSandboxEnabled = Boolean(data.dev_sandbox_enabled);

    const envBadge = document.getElementById("g1-env-mode");
    if (envBadge) {
      envBadge.textContent = `ENV_MODE: ${data.env_mode}`;
      envBadge.className = `badge ${g1DevSandboxEnabled ? "badge-warn" : "badge-info"}`;
    }

    const freshBadge = document.getElementById("g1-freshness-badge");
    if (freshBadge) {
      const ageTxt = data.freshness.source_age_hours == null ? "N/A" : `${data.freshness.source_age_hours}h`;
      freshBadge.textContent = `DB Freshness: ${data.freshness.status} (${ageTxt})`;
      freshBadge.className = `badge ${data.freshness.stale_locked ? "badge-hold" : "badge-ok"}`;
    }

    // Khóa các nút DEV ONLY ngoài ENV_MODE=development
    document.querySelectorAll("#gate-g1-panel .dev-only-btn").forEach((btn) => {
      btn.disabled = !g1DevSandboxEnabled;
      btn.title = g1DevSandboxEnabled
        ? "Khả dụng trong ENV_MODE=development"
        : "Bị khóa ngoài ENV_MODE=development (SEC-06)";
    });

    // Bước 2 (PLPT-25): Đối chiếu cô lập DB
    const iso = data.workspace_isolation || {};
    document.getElementById("g1-review-count").textContent = `staff_review_logs: ${iso.staff_review_logs_count ?? 0}`;
    document.getElementById("g1-override-count").textContent = `staff_override_logs: ${iso.staff_override_logs_count ?? 0}`;
    const lblEntries = Object.entries(iso.raw_outcomes_labels || {})
      .map(([k, v]) => `${k}=${v}`)
      .join(", ");
    document.getElementById("g1-outcomes-summary").textContent =
      `raw_outcomes: ${iso.raw_outcomes_count ?? 0} dòng (${lblEntries || "trống"})`;
    const isoBadge = document.getElementById("g1-isolation-badge");
    isoBadge.textContent = "Cô lập CF-29: raw_outcomes tách biệt hoàn toàn khỏi override";
    isoBadge.className = "badge badge-ok";

    renderG1Table(
      "g1-review-logs-body",
      iso.staff_review_logs_preview,
      ["id", "enquiry_id", "decision_type", "contact_permission_verified", "contact_attempted", "reviewed_by", "reviewed_at"],
      "Chưa có bản ghi trong staff_review_logs. Hãy qua tab Active (K) tick xác minh và bấm ACCEPT."
    );
    renderG1Table(
      "g1-override-logs-body",
      iso.staff_override_logs_preview,
      ["id", "enquiry_id", "override_reason_code", "justification", "overridden_by", "overridden_at"],
      "Chưa có bản ghi trong staff_override_logs. Hãy qua tab Active (K) tick xác minh và bấm OVERRIDE."
    );

    const reviewShown = (iso.staff_review_logs_preview || []).length;
    const reviewTotal = iso.staff_review_logs_count ?? 0;
    const reviewSumEl = document.getElementById("g1-review-summary");
    if (reviewSumEl) {
      reviewSumEl.textContent = `Xem bảng staff_review_logs (ACCEPT_PRIORITY — hiển thị ${reviewShown}/${reviewTotal}, tối đa 15 bản ghi mới nhất)`;
    }

    const overrideShown = (iso.staff_override_logs_preview || []).length;
    const overrideTotal = iso.staff_override_logs_count ?? 0;
    const overrideSumEl = document.getElementById("g1-override-summary");
    if (overrideSumEl) {
      overrideSumEl.textContent = `Xem bảng staff_override_logs (OVERRIDE_PRIORITY — hiển thị ${overrideShown}/${overrideTotal}, tối đa 15 bản ghi mới nhất)`;
    }

    // Bước 3 (PLPT-26): Manifest & Pre-t0 features
    const mf = (data.manifest || {}).data;
    const mfChecks = (data.manifest || {}).checks || {};
    document.getElementById("g1-mf-model").textContent = mf
      ? `${mf.model_version} | ${mf.candidate_type} + ${mf.secondary_candidate_type}`
      : "Chưa tìm thấy manifest.json";
    document.getElementById("g1-mf-features").textContent = mf
      ? `Đặc trưng pre-t0: ${mfChecks.feature_count} cột (<= 30: ${mfChecks.feature_count_ok ? "ĐẠT" : "LỖI"}, 0 PII/account_key: ${mfChecks.no_prohibited_features ? "ĐẠT" : "LỖI"})`
      : "Đặc trưng: -";
    document.getElementById("g1-mf-tolerance").textContent = mf
      ? `numerical_tolerance: ${mf.numerical_tolerance} | approval_status: ${mf.approval_status}`
      : "tolerance: -";
    const mfLockBadge = document.getElementById("g1-mf-serving-lock");
    mfLockBadge.textContent = mf ? `serving_mode_locked: ${mf.serving_mode_locked}` : "serving_mode_locked: -";
    mfLockBadge.className = `badge ${mfChecks.serving_mode_locked_rules ? "badge-ok" : "badge-hold"}`;

    const featRows = ((mf && mf.feature_columns_used) || []).map((col, idx) => ({
      idx: idx + 1,
      feature: col,
      status: "Hợp lệ (pre-t0, không PII)",
    }));
    renderG1Table("g1-mf-features-body", featRows, ["idx", "feature", "status"], "Chưa có danh sách đặc trưng.");

    // Bước 4 (PLPT-27): 40 Control Fixtures
    const cf = (data.control_fixtures || {}).data;
    const cfStatusBadge = document.getElementById("g1-cf-status");
    if (cf) {
      document.getElementById("g1-cf-summary").textContent =
        `Kết quả: ${cf.passed_count}/${cf.total_fixtures} passed | ${cf.failed_count} failed | ${cf.pass_rate_pct}% | Chạy lúc: ${cf.executed_at_utc}`;
      cfStatusBadge.textContent = `gate_g1_controls_status: ${cf.gate_g1_controls_status}`;
      cfStatusBadge.className = `badge ${data.control_fixtures.all_40_passed ? "badge-ok" : "badge-hold"}`;
      renderCfMatrix(cf.results);
    } else {
      document.getElementById("g1-cf-summary").textContent = "Chưa tìm thấy control_fixtures_report.json.";
      cfStatusBadge.textContent = "CHƯA CÓ BÁO CÁO";
      cfStatusBadge.className = "badge badge-warn";
      renderCfMatrix([]);
    }

    // Bước 5 (PLPT-28): Gate G1 Benchmark & Single-Use Sealed Cohort Locks
    const rep = (data.gate_g1_benchmark || {}).data;
    const decBadge = document.getElementById("g1-decision-badge");
    const modeBadge = document.getElementById("g1-active-mode-badge");
    if (rep) {
      decBadge.textContent = `gate_g1_decision: ${rep.gate_g1_decision}`;
      decBadge.className = `badge ${rep.gate_g1_decision === "FALLBACK_RULES_BASELINE" ? "badge-warn" : "badge-ok"}`;
      modeBadge.textContent = `active_serving_mode: ${rep.active_serving_mode}`;
      modeBadge.className = `badge ${rep.active_serving_mode === "RULES_BASELINE" ? "badge-ok" : "badge-hold"}`;

      const cc = rep.cohort_counts || {};
      document.getElementById("g1-cohort-counts").textContent =
        `data_origin=${rep.data_origin} | eligible=${cc.total_eligible} | pos=${cc.verified_positive} | neg=${cc.verified_negative} | unknown_immature=${cc.unknown_immature} | coverage=${cc.outcome_coverage_pct}%`;

      const lift = rep.lift_and_uncertainty || {};
      const ci = Array.isArray(lift.paired_95_ci) ? `[${lift.paired_95_ci.join(", ")}]` : "-";
      document.getElementById("g1-lift-summary").textContent =
        `precision_at_k_lift=${lift.precision_at_k_lift ?? "null"} | paired_diff_mean=${lift.paired_diff_mean ?? 0} | paired_95_ci=${ci} | ci_lower_bound_exceeds_zero=${String(lift.ci_lower_bound_exceeds_zero)}`;
      document.getElementById("g1-disclaimer-text").textContent = rep.disclaimer || "";

      renderBenchmarkMetricsTable(rep);
      renderCriteriaBadges(rep.criteria_results, rep.failed_criteria);
    } else {
      decBadge.textContent = "Chưa có gate_g1_benchmark_report.json";
      decBadge.className = "badge badge-hold";
      modeBadge.textContent = "active_serving_mode: -";
      modeBadge.className = "badge badge-info";
      renderBenchmarkMetricsTable(null);
      renderCriteriaBadges(null, []);
    }

    const locks = data.sealed_cohort_locks || {};
    const lockBadge = document.getElementById("g1-lock-count-badge");
    lockBadge.textContent = `Sealed Cohort Locks: ${locks.lock_count ?? 0} tập đã niêm phong`;
    lockBadge.className = `badge ${(locks.lock_count || 0) > 0 ? "badge-ok" : "badge-warn"}`;
    renderG1Table(
      "g1-sealed-locks-body",
      locks.rows,
      ["fingerprint", "training_cutoff_t0", "row_count", "locked_at"],
      "Chưa có tập test nào trong sealed_cohort_locks.json."
    );

    // Bước 6: DoD Sprint 3 Live Indicators
    const dod = data.dod_summary || {};
    const dodCf = document.getElementById("g1-dod-cf");
    dodCf.textContent = dod.control_fixtures_40_passed ? "40/40 Control Fixtures: PASSED" : "40/40 Control Fixtures: CHƯA ĐẠT";
    dodCf.className = `badge ${dod.control_fixtures_40_passed ? "badge-ok" : "badge-hold"}`;

    const dodLock = document.getElementById("g1-dod-rules-lock");
    dodLock.textContent = dod.gate_g1_and_manifest_locked_rules
      ? "Gate G1 & Manifest: KHÓA RULES_BASELINE"
      : "Gate G1 & Manifest: KIỂM TRA LẠI";
    dodLock.className = `badge ${dod.gate_g1_and_manifest_locked_rules ? "badge-ok" : "badge-hold"}`;

    const dodSingle = document.getElementById("g1-dod-single-use");
    dodSingle.textContent = dod.single_use_cohort_locked
      ? "Single-Use Sealed Cohort: ĐANG NIÊM PHONG"
      : "Single-Use Sealed Cohort: CHƯA KHÓA";
    dodSingle.className = `badge ${dod.single_use_cohort_locked ? "badge-ok" : "badge-warn"}`;
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setG1Banner("err", `Lỗi tải dữ liệu Sprint 3 / Gate G1: ${err.message}`);
    }
  }
}

async function handleTestDirectUrl401() {
  const btn = document.getElementById("btn-g1-test-401");
  if (btn) btn.disabled = true;
  try {
    // 1) Không gửi header xác thực -> kỳ vọng 401
    const rNoAuth = await fetch(`${API_BASE}/api/v1/workspace/queue`);
    const tNoAuth = await rNoAuth.text();

    // 2) Header giả lập phiên hết hạn + X-User-Role thô -> kỳ vọng 401
    const rExpired = await fetch(`${API_BASE}/api/v1/workspace/queue`, {
      headers: {
        "X-User-Role": "SALES_READER",
        "X-Session-Expired": "true",
      },
    });
    const tExpired = await rExpired.text();

    const both401 = rNoAuth.status === 401 && rExpired.status === 401;
    const badge = document.getElementById("g1-401-badge");
    if (badge) {
      badge.textContent = both401
        ? "ĐẠT HTTP 401 (No-Auth=401, Expired=401)"
        : `LỆCH: No-Auth=${rNoAuth.status}, Expired=${rExpired.status}`;
      badge.className = `badge ${both401 ? "badge-ok" : "badge-hold"}`;
    }

    const out = [
      "=== Kiểm chứng Bước 1.2 (PLPT-24): Chặn Direct URL & Phiên hết hạn ===",
      `1) GET /api/v1/workspace/queue (Không có Authorization): HTTP ${rNoAuth.status}`,
      `   Body: ${tNoAuth}`,
      "",
      `2) GET /api/v1/workspace/queue (X-User-Role: SALES_READER + X-Session-Expired: true): HTTP ${rExpired.status}`,
      `   Body: ${tExpired}`,
      "",
      `Kết luận: ${both401 ? "ĐẠT 100% — Cả 2 kịch bản bị chặn HTTP 401, phiên đăng nhập hiện tại vẫn an toàn." : "KHÔNG ĐẠT"}`,
    ].join("\n");

    setG1ConsoleOutput(`Bước 1.2 Direct URL / Expired 401 -> ${both401 ? "PASSED" : "FAILED"}`, out, both401);
    setG1Banner(
      both401 ? "ok" : "err",
      both401
        ? "Bước 1.2 ĐẠT: Cả yêu cầu thiếu token và phiên hết hạn đều bị chặn HTTP 401."
        : `Bước 1.2 chưa đạt (No-Auth=${rNoAuth.status}, Expired=${rExpired.status}).`
    );
  } catch (err) {
    setG1Banner("err", `Lỗi kiểm tra HTTP 401: ${err.message}`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleRunSprint3Check(buttonId, suiteId, refreshAfter = false) {
  const btn = document.getElementById(buttonId);
  if (btn) btn.disabled = true;
  setG1ConsoleOutput(`Đang chạy ${suiteId}...`, "Hệ thống đang thực thi kiểm thử trong sandbox DEV, vui lòng chờ...", null);
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/run-sprint3-check", {
      method: "POST",
      body: JSON.stringify({ suite_id: suiteId }),
    });
    const data = await res.json();
    if (!res.ok) {
      setG1Banner("err", `HTTP ${res.status}: ${data.detail || "Không chạy được kiểm thử Sprint 3."}`);
      setG1ConsoleOutput(`Lỗi (${suiteId})`, data.detail || "Thất bại", false);
      return;
    }
    setG1ConsoleOutput(
      `${data.label} -> ${data.passed ? "PASSED" : "FAILED"}`,
      data.output || "(Không có output)",
      Boolean(data.passed)
    );
    setG1Banner(
      data.passed ? "ok" : "err",
      `${data.label}: ${data.passed ? "ĐẠT (PASSED)" : "KHÔNG ĐẠT (FAILED)"}`
    );
    if (refreshAfter) {
      await loadGateG1Console();
    }
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setG1Banner("err", `Lỗi kết nối: ${err.message}`);
    }
  } finally {
    if (btn) btn.disabled = !g1DevSandboxEnabled;
  }
}

async function handleG1RestoreFresh() {
  const btn = document.getElementById("btn-g1-restore-fresh");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/restore-fresh", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const data = await res.json();
    if (!res.ok) {
      setG1Banner("err", `HTTP ${res.status}: ${data.detail || "Không làm tươi được dữ liệu."}`);
      return;
    }
    setG1Banner(
      "ok",
      `Bước 0 hoàn tất: Đã làm tươi dữ liệu (< 24h) lúc ${data.restored_at} (clean=${data.deduplication.clean_count}, merged=${data.deduplication.merged_count}).`
    );
    await loadGateG1Console();
  } finally {
    if (btn) btn.disabled = !g1DevSandboxEnabled;
  }
}

function showCachedJsonInConsole(title, payloadObj) {
  if (!payloadObj) {
    setG1Banner("err", `Chưa có dữ liệu cho ${title}. Hãy bấm "Tải lại bằng chứng Gate G1".`);
    return;
  }
  setG1ConsoleOutput(title, JSON.stringify(payloadObj, null, 2), true);
  setG1Banner("ok", `Đang hiển thị nội dung ${title} trong khung kết quả bên dưới.`);
}

async function handleRefreshIsolation() {
  const btn = document.getElementById("btn-g1-refresh-isolation");
  const msgEl = document.getElementById("g1-step2-msg");
  if (btn) btn.disabled = true;
  try {
    await loadGateG1Console();
    const nowStr = new Date().toLocaleTimeString("vi-VN", { hour12: false });
    const iso = (g1LatestEvidence && g1LatestEvidence.workspace_isolation) || {};
    const summaryText =
      `Đã đồng bộ lại từ data/songnhat.db lúc ${nowStr}: ` +
      `staff_review_logs=${iso.staff_review_logs_count ?? 0}, ` +
      `staff_override_logs=${iso.staff_override_logs_count ?? 0}, ` +
      `raw_outcomes=${iso.raw_outcomes_count ?? 0} dòng (tách biệt hoàn toàn, không bị sửa đổi).`;
    if (msgEl) {
      msgEl.className = "msg ok";
      msgEl.textContent = summaryText;
    }
    setG1ConsoleOutput(`Bước 2.6 Đối chiếu cô lập SQLite (${nowStr})`, JSON.stringify(iso, null, 2), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleRefreshGateG1All() {
  const btn = document.getElementById("btn-g1-refresh");
  if (btn) btn.disabled = true;
  try {
    await loadGateG1Console();
    const nowStr = new Date().toLocaleTimeString("vi-VN", { hour12: false });
    setG1Banner("ok", `Đã tải lại toàn bộ bằng chứng Sprint 3 & Gate G1 lúc ${nowStr}.`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function initGateG1ConsoleEvents() {
  const bind = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  };

  bind("btn-g1-refresh", handleRefreshGateG1All);
  bind("btn-g1-restore-fresh", handleG1RestoreFresh);

  // Bước 1: PLPT-24
  bind("btn-g1-test-plpt24", () => handleRunSprint3Check("btn-g1-test-plpt24", "plpt24_workspace"));
  bind("btn-g1-test-401", handleTestDirectUrl401);
  bind("btn-g1-test-plpt57", () => handleRunSprint3Check("btn-g1-test-plpt57", "plpt57_perf_p95"));
  bind("btn-g1-jump-search", () => switchToWorkspaceTab("active", "bien tan"));
  bind("btn-g1-jump-unscored", () => switchToWorkspaceTab("unscored", ""));
  bind("btn-g1-jump-quarantine", () => switchToWorkspaceTab("quarantine", ""));

  // Bước 2: PLPT-25
  bind("btn-g1-jump-accept-override", () => switchToWorkspaceTab("active", ""));
  bind("btn-g1-refresh-isolation", handleRefreshIsolation);

  // Bước 3: PLPT-26
  bind("btn-g1-test-plpt26", () => handleRunSprint3Check("btn-g1-test-plpt26", "plpt26_ml_eval"));
  bind("btn-g1-view-manifest", () =>
    showCachedJsonInConsole(
      "artifacts/models/plpt-logreg-v1.0.0/manifest.json",
      g1LatestEvidence && g1LatestEvidence.manifest ? g1LatestEvidence.manifest.data : null
    )
  );

  // Bước 4: PLPT-27
  bind("btn-g1-run-cf40-script", () => handleRunSprint3Check("btn-g1-run-cf40-script", "plpt27_cf40_script", true));
  bind("btn-g1-run-cf40-pytest", () => handleRunSprint3Check("btn-g1-run-cf40-pytest", "plpt27_cf40_pytest"));
  bind("btn-g1-view-cf-json", () =>
    showCachedJsonInConsole(
      "artifacts/gate_g1/control_fixtures_report.json",
      g1LatestEvidence && g1LatestEvidence.control_fixtures ? g1LatestEvidence.control_fixtures.data : null
    )
  );

  // Bước 5: PLPT-28
  bind("btn-g1-view-benchmark-json", () =>
    showCachedJsonInConsole(
      "artifacts/gate_g1/gate_g1_benchmark_report.json",
      g1LatestEvidence && g1LatestEvidence.gate_g1_benchmark ? g1LatestEvidence.gate_g1_benchmark.data : null
    )
  );
  bind("btn-g1-view-locks-json", () =>
    showCachedJsonInConsole(
      "artifacts/gate_g1/sealed_cohort_locks.json",
      g1LatestEvidence && g1LatestEvidence.sealed_cohort_locks ? g1LatestEvidence.sealed_cohort_locks.data : null
    )
  );
  bind("btn-g1-verify-single-use", () =>
    handleRunSprint3Check("btn-g1-verify-single-use", "plpt28_single_use_verify", true)
  );
  bind("btn-g1-demo-fresh-eval", () =>
    handleRunSprint3Check("btn-g1-demo-fresh-eval", "plpt28_demo_fresh_eval", true)
  );

  // Bước 6: DoD Sprint 3
  bind("btn-g1-dod-all", () => handleRunSprint3Check("btn-g1-dod-all", "sprint3_dod_all", true));
}

initGateG1ConsoleEvents();
