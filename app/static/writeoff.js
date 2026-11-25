/* 交接单核销核对工作台前端（无框架，原生 JS）。 */
(function () {
  "use strict";

  var state = {
    scenarios: [],
    scenario: "standard",
    detail: null,
    step: 0,
    total: 0,
    lastPayload: null,
  };

  var $ = function (id) { return document.getElementById(id); };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function reasonText(sample) {
    if (sample.disputed) return "不同回执报告了不同处置日，已结清也退回待核";
    if (sample.block_reason === "LEGAL_HOLD") return "保全标记优先，处置被阻止，不能销账";
    if (sample.block_reason === "RETAIN_ON_DISPOSAL_DAY") return "处置当天仍须留存，不能销账";
    if (sample.block_reason === "RETENTION_NOT_DUE") return "处置日不晚于留存截止日，当天仍须留存";
    if (sample.state === "AWAITING_RECEIPT") return "回执未到，暂不核销";
    return "";
  }

  async function api(path, options) {
    var resp = await fetch(path, Object.assign({
      headers: { "Content-Type": "application/json" },
    }, options || {}));
    var data = null;
    try { data = await resp.json(); } catch (e) { data = null; }
    if (!resp.ok) {
      var msg = (data && (data.error || data.code)) || ("HTTP " + resp.status);
      throw new Error(msg);
    }
    return data;
  }

  function renderKpis(t) {
    $("k-orders").textContent = t.orders;
    $("k-samples").textContent = t.samples;
    $("k-settled").textContent = t.settled;
    $("k-pending").textContent = t.pending;
    $("k-blocked").textContent = t.blocked;
    $("k-waiting").textContent = t.waiting;
  }

  function renderExceptions(board) {
    var box = $("exceptions");
    var list = board.exceptions || [];
    if (!list.length) {
      box.innerHTML = '<li class="empty">无</li>';
      return;
    }
    box.innerHTML = list.map(function (e) {
      return "<li><code>" + esc(e.code) + "</code> 回执 " + esc(e.receipt_id) +
        (e.sample_id ? " · 样品 " + esc(e.sample_id) : "") + "</li>";
    }).join("");
  }

  function receiptChips(sample) {
    if (!sample.receipts || !sample.receipts.length) return "";
    var chips = sample.receipts.map(function (r) {
      var cls = r.status === "effective" ? "effective"
        : r.status === "ineffective" ? "ineffective"
        : r.status === "superseded" ? "superseded"
        : (sample.active_receipt_ids.indexOf(r.receipt_id) >= 0 ? "active" : "");
      var title = (r.kind === "CORRECTION" ? "更正" : "回执") + " " +
        (r.disposal_date || "无日期") + " · " + r.status;
      return '<span class="wo-chip" data-r="' + esc(cls) + '" title="' + esc(title) + '">' +
        esc(r.receipt_id) + "</span>";
    }).join("");
    return '<div class="wo-chips">' + chips + "</div>";
  }

  function renderOrders(board) {
    var root = $("orders");
    if (!board.orders || !board.orders.length) {
      root.innerHTML = '<div class="wo-panel empty">没有交接单</div>';
      return;
    }
    root.innerHTML = board.orders.map(function (order) {
      var rows = order.samples.map(function (s) {
        return (
          "<tr>" +
          '<td><span class="wo-sid">' + esc(s.sample_id) + "</span>" +
            '<div class="wo-reason">' + esc(reasonText(s)) + "</div>" +
            receiptChips(s) + "</td>" +
          '<td><span class="wo-badge" data-s="' + esc(s.state) + '">' + esc(s.state_label) + "</span></td>" +
          '<td><span class="wo-date">' + esc(s.disposal_date || "—") + "</span></td>" +
          '<td><span class="wo-due">' + esc(s.retention_due || "未提供") + "</span></td>" +
          "</tr>"
        );
      }).join("");
      var c = order.counts;
      return (
        '<article class="wo-order">' +
          '<div class="wo-order-head"><h3>' + esc(order.order_id) + "</h3>" +
          '<span class="wo-order-counts">共 ' + c.samples + " · 已核 " + c.settled +
          " · 待核 " + c.pending + " · 阻断 " + c.blocked + " · 待回执 " + c.waiting + "</span></div>" +
          "<table><thead><tr><th>样品 / 回执留痕</th><th>核销状态</th><th>报告处置日</th><th>留存截止</th></tr></thead>" +
          "<tbody>" + rows + "</tbody></table>" +
        "</article>"
      );
    }).join("");
  }

  function renderBoard(board) {
    if (!board) return;
    renderKpis(board.totals || board.counts);
    renderOrders(board);
    renderExceptions(board);
  }

  function renderTimeline() {
    var arrivals = (state.detail && (state.detail.arrivals || state.detail.receipts)) || [];
    state.total = arrivals.length;
    $("step-progress").textContent = state.step + " / " + state.total;
    $("step-prev").disabled = state.step <= 0;
    $("step-next").disabled = state.step >= state.total;
    var box = $("timeline");
    box.innerHTML = arrivals.map(function (a, i) {
      var on = i < state.step;
      var kind = (a.kind || "").toLowerCase().indexOf("correct") >= 0 || a.kind === "更正" ? "CORRECTION" : "RECEIPT";
      return '<li data-on="' + on + '">' +
        '<span class="wo-tl-id">' + esc(a.receipt_id) + "</span>" +
        '<span class="wo-tl-tag" data-k="' + kind + '">' + (kind === "CORRECTION" ? "更正" : "回执") + "</span>" +
        " · " + esc(a.sample_id) + " · " + esc(a.disposed_at) +
        (a.replaces && a.replaces.length ? " → 替换 " + esc(a.replaces.join(", ")) : "") +
        (a.note ? '<span class="wo-tl-note">' + esc(a.note) + "</span>" : "") +
        "</li>";
    }).join("");
  }

  function populateForm() {
    var orders = (state.detail && state.detail.orders) || [];
    $("f-order").innerHTML = orders.map(function (o) {
      return '<option value="' + esc(o.order_id) + '">' + esc(o.order_id) + "</option>";
    }).join("");
    populateSamples();
  }

  function populateSamples() {
    var oid = $("f-order").value;
    var order = ((state.detail && state.detail.orders) || []).filter(function (o) {
      return o.order_id === oid;
    })[0];
    var samples = order ? order.samples : [];
    $("f-sample").innerHTML = samples.map(function (s) {
      return '<option value="' + esc(s.sample_id) + '">' + esc(s.sample_id) + "</option>";
    }).join("");
  }

  function setFlash(text, tone) {
    var el = $("flash");
    el.textContent = text || "";
    el.setAttribute("data-tone", tone || "");
  }

  async function loadScenario(name, stepFinal) {
    state.scenario = name;
    state.detail = await api("/api/writeoff/scenarios/" + encodeURIComponent(name)).then(function (r) { return r.data; });
    $("scenario-desc").textContent = state.detail.description || "";
    populateForm();
    var total = ((state.detail.arrivals || state.detail.receipts) || []).length;
    state.step = stepFinal ? total : 0;
    renderTimeline();
    await refreshStepBoard();
  }

  async function refreshStepBoard() {
    renderTimeline();
    var data = await api("/api/writeoff/verify", {
      method: "POST",
      body: JSON.stringify({ scenario: state.scenario, through: state.step }),
    });
    renderBoard(data.board);
  }

  function receiptPayloadFromForm() {
    var replaces = $("f-replaces").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var isCorrection = $("f-is-correction").checked || replaces.length > 0;
    var payload = {
      board_id: "live",
      receipt_id: $("f-rid").value.trim(),
      order_id: $("f-order").value,
      sample_id: $("f-sample").value,
      disposed_at: $("f-date").value,
    };
    if (isCorrection) {
      payload.kind = "correction";
      payload.replaces = replaces;
    }
    return payload;
  }

  async function ensureLiveBoard() {
    await api("/api/writeoff/boards", {
      method: "POST",
      body: JSON.stringify({ board_id: "live", scenario: state.scenario }),
    });
  }

  async function submitPayload(payload) {
    await ensureLiveBoard();
    var data = await api("/api/writeoff/receipts", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    state.lastPayload = payload;
    renderBoard(data.board);
    if (data.duplicate) {
      setFlash("回执 " + payload.receipt_id + " 已登记过，重试未重复入账（duplicate）。", "warn");
    } else if (data.receipt && data.receipt.status === "ineffective") {
      setFlash("更正未指明全部冲突回执，争议仍未消除。", "warn");
    } else {
      setFlash("已登记 " + payload.receipt_id + "，核对结果已刷新。", "ok");
    }
  }

  function bind() {
    api("/api/writeoff/scenarios").then(function (data) {
      state.scenarios = data.scenarios || [];
      $("scenario-select").innerHTML = state.scenarios.map(function (s) {
        return '<option value="' + esc(s.name) + '">' + esc(s.title || s.name) + "</option>";
      }).join("");
      return loadScenario("standard", true);
    }).catch(function (err) { setFlash("加载场景失败：" + err.message, "bad"); });

    $("scenario-select").addEventListener("change", function (e) {
      loadScenario(e.target.value, true).catch(function (err) { setFlash(err.message, "bad"); });
    });
    $("reload-final").addEventListener("click", function () {
      loadScenario(state.scenario, true);
    });
    $("step-next").addEventListener("click", function () {
      if (state.step < state.total) { state.step += 1; refreshStepBoard(); }
    });
    $("step-prev").addEventListener("click", function () {
      if (state.step > 0) { state.step -= 1; refreshStepBoard(); }
    });
    $("step-reset").addEventListener("click", function () { state.step = 0; refreshStepBoard(); });
    $("step-final").addEventListener("click", function () {
      state.step = state.total; refreshStepBoard();
    });
    $("f-order").addEventListener("change", populateSamples);

    $("receipt-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var payload = receiptPayloadFromForm();
      if (!payload.receipt_id) { setFlash("请填写回执编号", "bad"); return; }
      submitPayload(payload).catch(function (err) { setFlash(err.message, "bad"); });
    });
    $("retry-last").addEventListener("click", function () {
      if (!state.lastPayload) { setFlash("还没有可重发的回执，请先登记一次。", "warn"); return; }
      submitPayload(state.lastPayload).catch(function (err) { setFlash(err.message, "bad"); });
    });
    $("live-reset").addEventListener("click", async function () {
      try {
        await api("/api/writeoff/boards/live/reset", { method: "POST" });
        var data = await api("/api/writeoff/boards/live");
        renderBoard(data.board);
        setFlash("交互台账已清空回执（交接单保留）。", "ok");
      } catch (err) { setFlash(err.message, "bad"); }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
