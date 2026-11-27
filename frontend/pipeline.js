/* SongNhat AI-02 | Sprint 2 Browser Console (PLPT-14..PLPT-17 & DoD) */

function setPipelineBanner(type, text) {
  const el = document.getElementById("pipeline-msg");
  if (!el) return;
  el.className = `msg ${type}`;
  el.textContent = text;
}

function setPipelineConsoleOutput(title, text, passed = null) {
  const box = document.getElementById("pipeline-console-output");
  const titleEl = document.getElementById("pipeline-console-title");
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

function renderSimpleTable(tbodyId, rows, cols, emptyText) {
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

async function loadPipelineConsole() {
  const queueCard = document.getElementById("queue-card");
  const monPanel = document.getElementById("monitoring-panel");
  const pipePanel = document.getElementById("pipeline-panel");
  if (!pipePanel) return;

  if (queueCard) queueCard.style.display = "none";
  if (monPanel) monPanel.style.display = "none";
  pipePanel.style.display = "block";

  try {
    const res = await apiFetch("/api/v1/pipeline/status");
    const data = await res.json();
    if (!res.ok) {
      setPipelineBanner("err", data.detail || "Vai trò hiện tại không có quyền mở bảng điều khiển Pipeline.");
      return;
    }

    const envBadge = document.getElementById("pipe-env-mode");
    if (envBadge) {
      envBadge.textContent = `ENV_MODE: ${data.env_mode}`;
      envBadge.className = `badge ${data.dev_sandbox_enabled ? "badge-warn" : "badge-info"}`;
    }

    // Ẩn/khóa các nút DEV ONLY nếu không phải ENV_MODE=development
    document.querySelectorAll(".dev-only-btn").forEach((btn) => {
      btn.disabled = !data.dev_sandbox_enabled;
      btn.title = data.dev_sandbox_enabled ? "Khả dụng trong ENV_MODE=development" : "Bị khóa ngoài ENV_MODE=development (SEC-06)";
    });

    // PLPT-14 KPIs
    document.getElementById("pipe-csv-count").textContent =
      `Enquiries: ${data.csv_files.enquiries_rows} | Outcomes: ${data.csv_files.outcomes_rows}`;
    document.getElementById("pipe-raw-count").textContent =
      `raw_enquiries: ${data.raw_enquiries.row_count} (${data.raw_enquiries.columns_count} cột DB) | raw_outcomes: ${data.raw_outcomes.row_count}`;
    const piiEl = document.getElementById("pipe-pii-status");
    const dnuCount = (data.raw_enquiries.do_not_use_columns_present || []).length;
    piiEl.textContent = dnuCount === 0 ? "0 cột *_DO_NOT_USE (SẠCH)" : `Cảnh báo: còn ${dnuCount} cột cấm`;
    piiEl.className = `badge ${dnuCount === 0 ? "badge-ok" : "badge-hold"}`;
    document.getElementById("pipe-unknown-count").textContent =
      `Unknown (< 60 ngày): ${data.raw_outcomes.unknown_count} | Quarantine: ${data.quarantine.enquiries_count} dòng`;

    renderSimpleTable(
      "pipe-quarantine-body",
      data.quarantine.preview,
      ["enquiry_id", "account_key", "t0_time", "product_family", "quarantine_reason"],
      "Không có dòng lỗi trong Quarantine."
    );

    // PLPT-15 Dedup KPIs
    const d = data.deduplication;
    document.getElementById("pipe-dedup-summary").textContent =
      `raw=${d.raw_count} | clean=${d.clean_count} | merged=${d.merged_count} | steward_review=${d.steward_review_count}`;
    const mathBadge = document.getElementById("pipe-dedup-math");
    mathBadge.textContent = d.math_check_ok ? "raw - clean == merged (ĐẠT)" : "Chưa gộp / lệch đối soát";
    mathBadge.className = `badge ${d.math_check_ok ? "badge-ok" : "badge-warn"}`;

    renderSimpleTable(
      "pipe-merge-body",
      d.merge_audit_preview,
      ["canonical_enquiry_id", "merged_enquiry_id", "account_key", "product_family", "canonical_t0_time", "merge_reason"],
      "Chưa có bản ghi trong merge_audit_log."
    );

    renderSimpleTable(
      "pipe-steward-body",
      d.steward_review_preview,
      ["enquiry_id_a", "enquiry_id_b", "account_key", "product_family", "conflict_reason", "status"],
      "Không có ca xung đột cần Data Steward rà soát (0 xung đột trên dữ liệu hiện tại)."
    );

    // PLPT-17 Freshness & Blocked Accounts
    const freshBadge = document.getElementById("pipe-freshness-badge");
    const ageTxt = data.freshness.source_age_hours == null ? "Chưa xác định" : `${data.freshness.source_age_hours} giờ`;
    freshBadge.textContent = `${data.freshness.status} (Tuổi dữ liệu: ${ageTxt})`;
    freshBadge.className = `badge ${data.freshness.stale_locked ? "badge-hold" : "badge-ok"}`;

    renderSimpleTable(
      "pipe-blocked-body",
      data.blocked_accounts.preview,
      ["enquiry_id", "account_key", "reason", "blocked_at", "created_by"],
      "Chưa có bản ghi nào trong blocked_accounts."
    );
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setPipelineBanner("err", `Lỗi tải trạng thái Pipeline: ${err.message}`);
    }
  }
}

async function runPipelineAction(buttonId, path, method, bodyObj, onSuccessText) {
  const btn = document.getElementById(buttonId);
  if (btn) btn.disabled = true;
  try {
    const opts = { method };
    if (bodyObj !== undefined) opts.body = JSON.stringify(bodyObj);
    const res = await apiFetch(path, opts);
    const data = await res.json();
    if (!res.ok) {
      setPipelineBanner("err", `HTTP ${res.status}: ${data.detail || "Thao tác thất bại."}`);
      return null;
    }
    setPipelineBanner("ok", onSuccessText(data));
    await loadPipelineConsole();
    return data;
  } catch (err) {
    if (String(err.message) !== "HTTP 401" && String(err.message) !== "Unauthorized") {
      setPipelineBanner("err", `Lỗi kết nối: ${err.message}`);
    }
    return null;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleRunUs03Audit() {
  const data = await runPipelineAction(
    "btn-pipe-us03",
    "/api/v1/pipeline/us03-audit",
    "GET",
    undefined,
    (d) => `US-03 Audit: PASS=${d.pass_count} | FAIL=${d.fail_count} | QUARANTINE=${d.quarantine_count} | Cột cấm phát hiện: ${(d.forbidden_columns_detected || []).join(", ")}`
  );
  if (!data) return;
  renderSimpleTable(
    "pipe-us03-body",
    data.checks,
    ["name", "status", "detail"],
    "Chưa chạy kiểm toán US-03."
  );
  const summaryLines = [
    `Enquiry rows: ${data.enquiry_rows_total} | Outcome rows: ${data.outcome_rows_total}`,
    `PASS: ${data.pass_count} | FAIL: ${data.fail_count} | QUARANTINE: ${data.quarantine_count}`,
    `Cột *_DO_NOT_USE phát hiện: ${(data.forbidden_columns_detected || []).join(", ")}`,
  ];
  setPipelineConsoleOutput("US-03 Data Audit", summaryLines.join("\n"), data.fail_count === 0);
}

async function handleInspectApiQueue() {
  const btn = document.getElementById("btn-pipe-inspect-queue");
  if (btn) btn.disabled = true;
  try {
    const res = await apiFetch("/api/queue?limit=5");
    const data = await res.json();
    if (!res.ok) {
      setPipelineBanner("err", `HTTP ${res.status}: ${data.detail || "Không đọc được /api/queue"}`);
      return;
    }
    const items = data.items || [];
    const allRulesMode = items.every((x) => x.score_mode === "RULES_BASELINE");
    const max3Reasons = items.every((x) => (x.reasons || []).length <= 3);
    const noPercentSign = items.every((x) => !String(x.rule_score).includes("%"));

    const badgeEl = document.getElementById("pipe-queue-checks");
    if (badgeEl) {
      badgeEl.textContent = `status=${data.status} | locked=${data.locked} | total=${data.total} | RULES_BASELINE=${allRulesMode} | <=3 lý do=${max3Reasons} | không %= ${noPercentSign}`;
      badgeEl.className = `badge ${data.locked ? "badge-hold" : "badge-ok"}`;
    }
    setPipelineConsoleOutput("GET /api/queue?limit=5 (PLPT-16)", JSON.stringify(data, null, 2), true);
    setPipelineBanner("ok", `Đã kiểm tra GET /api/queue: status=${data.status}, total=${data.total}, hiển thị Top ${items.length}.`);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleTestBlockRbac() {
  const btn = document.getElementById("btn-pipe-test-block-rbac");
  if (btn) btn.disabled = true;
  try {
    const sess = getSession();
    const res = await apiFetch("/api/block", {
      method: "POST",
      body: JSON.stringify({ enquiry_id: "SN-RBAC-TEST", reason: "Kiểm tra phân quyền RBAC trên /api/block" }),
    });
    const data = await res.json();
    const isExpected403 = sess && sess.role === "SALES_READER" && res.status === 403;
    setPipelineConsoleOutput(
      `POST /api/block (Role=${sess ? sess.role : "?"} -> HTTP ${res.status})`,
      JSON.stringify(data, null, 2),
      res.ok || isExpected403
    );
    if (res.status === 403) {
      setPipelineBanner("ok", `ĐẠT RBAC (HTTP 403): ${data.detail}`);
    } else if (res.ok) {
      setPipelineBanner("ok", `HTTP 200 (${sess ? sess.role : ""} có quyền chặn): Đã chặn SN-RBAC-TEST.`);
      await loadPipelineConsole();
    } else {
      setPipelineBanner("err", `HTTP ${res.status}: ${data.detail || "Lỗi"}`);
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function handleRunSprint2Check(buttonId, suiteId) {
  const btn = document.getElementById(buttonId);
  if (btn) btn.disabled = true;
  setPipelineConsoleOutput(`Đang chạy ${suiteId}...`, "Vui lòng chờ vài giây...", null);
  try {
    const res = await apiFetch("/api/v1/dev-sandbox/run-sprint2-check", {
      method: "POST",
      body: JSON.stringify({ suite_id: suiteId }),
    });
    const data = await res.json();
    if (!res.ok) {
      setPipelineBanner("err", `HTTP ${res.status}: ${data.detail || "Không chạy được kiểm thử."}`);
      setPipelineConsoleOutput(`Lỗi (${suiteId})`, data.detail || "Thất bại", false);
      return;
    }
    setPipelineConsoleOutput(
      `${data.label} -> ${data.passed ? "PASSED" : "FAILED"}`,
      data.output || "(Không có output)",
      Boolean(data.passed)
    );
    setPipelineBanner(
      data.passed ? "ok" : "err",
      `${data.label}: ${data.passed ? "ĐẠT (PASSED)" : "KHÔNG ĐẠT (FAILED)"}`
    );
  } finally {
    if (btn) btn.disabled = false;
  }
}

function initPipelineConsoleEvents() {
  const bind = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  };

  bind("btn-pipe-refresh", loadPipelineConsole);
  bind("btn-pipe-gen-syn", () =>
    runPipelineAction(
      "btn-pipe-gen-syn",
      "/api/v1/dev-sandbox/generate-synthetic",
      "POST",
      {},
      (d) => `[${d.data_origin}] Đã sinh Enquiries=${d.enquiries_rows} dòng, Outcomes=${d.outcomes_rows} dòng.`
    )
  );
  bind("btn-pipe-us03", handleRunUs03Audit);
  bind("btn-pipe-ingest", () =>
    runPipelineAction(
      "btn-pipe-ingest",
      "/api/v1/pipeline/ingest",
      "POST",
      {},
      (d) =>
        `Nạp CSV hoàn tất: raw_enquiries=${d.after_raw_enquiries} (thêm ${d.delta_raw_enquiries >= 0 ? "+" : ""}${d.delta_raw_enquiries} dòng` +
        `${d.idempotent_no_duplicates ? " — IDEMPOTENT ĐẠT" : ""}), Quarantine=${d.quarantine_enquiries}, cột *_DO_NOT_USE=${(d.do_not_use_columns_present || []).length}.`
    )
  );
  bind("btn-pipe-dedup", () =>
    runPipelineAction(
      "btn-pipe-dedup",
      "/api/v1/pipeline/deduplicate",
      "POST",
      {},
      (d) => `Gộp trùng xong: raw=${d.raw_count}, clean=${d.clean_count}, merged=${d.merged_count}, steward_review=${d.steward_review_count} (math_ok=${d.math_check_passed}).`
    )
  );
  bind("btn-pipe-inspect-queue", handleInspectApiQueue);
  bind("btn-pipe-test-block-rbac", handleTestBlockRbac);
  bind("btn-pipe-sim-stale", () =>
    runPipelineAction(
      "btn-pipe-sim-stale",
      "/api/v1/dev-sandbox/simulate-stale",
      "POST",
      {},
      (d) => `Đã giả lập dữ liệu cũ > 24h (${d.updated_rows} dòng -> ${d.extraction_time}). Hãy bấm kiểm tra GET /api/queue hoặc quay lại tab Active (K) để xem khóa HOLD_STALE_DATA.`
    )
  );
  bind("btn-pipe-restore-fresh", () =>
    runPipelineAction(
      "btn-pipe-restore-fresh",
      "/api/v1/dev-sandbox/restore-fresh",
      "POST",
      {},
      (d) => `Đã khôi phục dữ liệu tươi (< 24h) lúc ${d.restored_at}: clean=${d.deduplication.clean_count}, merged=${d.deduplication.merged_count}.`
    )
  );

  bind("btn-test-plpt14", () => handleRunSprint2Check("btn-test-plpt14", "plpt14_sync"));
  bind("btn-test-plpt15", () => handleRunSprint2Check("btn-test-plpt15", "plpt15_dedup"));
  bind("btn-test-plpt16", () => handleRunSprint2Check("btn-test-plpt16", "plpt16_rules"));
  bind("btn-test-plpt17", () => handleRunSprint2Check("btn-test-plpt17", "plpt17_blocking"));
  bind("btn-test-sprint2-all", () => handleRunSprint2Check("btn-test-sprint2-all", "sprint2_all"));
  bind("btn-test-git-sec", () => handleRunSprint2Check("btn-test-git-sec", "git_security"));
}

initPipelineConsoleEvents();
