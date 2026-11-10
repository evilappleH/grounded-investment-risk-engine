let sessionToken = "";
const $ = (selector, root = document) => root.querySelector(selector);
const money = new Intl.NumberFormat("zh-CN", { style: "currency", currency: "USD" });
const number = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 });
const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 });
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));

function setMessage(element, text, error = false) {
  element.textContent = text;
  element.classList.toggle("error", error);
}

function syncBrokerFields() {
  const kind = $("#broker-kind").value;
  $("#client-id-row").classList.toggle("hidden", !kind.startsWith("ibkr_"));
  if (kind.startsWith("moomoo_")) $("#broker-port").value = "11111";
  if (kind === "ibkr_real_readonly") $("#broker-port").value = "7496";
  if (kind === "ibkr_paper_readonly") $("#broker-port").value = "7497";
  $("#broker-account").innerHTML = '<option value="">请先检测本地网关账户</option>';
}

function syncAiFields() {
  $("#ai-settings").classList.toggle("hidden", $("#ai-provider").value === "disabled");
}

function currentLimits() {
  return {
    max_single_position: Number($("#limit-single").value) / 100,
    max_sector_exposure: Number($("#limit-sector").value) / 100,
    max_gross_exposure: Number($("#limit-gross").value) / 100,
    minimum_cash_buffer: Number($("#limit-cash").value) / 100,
    max_positions: Number($("#limit-count").value),
  };
}

function metric(label, value) {
  return `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

async function loadReadiness() {
  const container = $("#runtime-readiness");
  try {
    const response = await fetch("/api/runtime/readiness");
    const data = await response.json();
    if (!response.ok) throw new Error("readiness unavailable");
    const readiness = data.readiness;
    const modulePills = [
      ["Moomoo SDK", readiness.modules.moomoo],
      ["IBKR API", readiness.modules.ibapi],
      ["模型运行库", readiness.modules.sklearn],
    ];
    const port = $("#broker-port").value;
    const gatewayReady = Boolean(readiness.loopback_ports[port]);
    container.innerHTML = modulePills.map(([label, ready]) => `<span class="${ready ? "ready" : "offline"}">${escapeHtml(label)} · ${ready ? "已就绪" : "未安装"}</span>`).join("") + `<span class="${gatewayReady ? "ready" : "offline"}">本地端口 ${escapeHtml(port)} · ${gatewayReady ? "已监听" : "未启动"}</span><span class="ready">券商写入 · 不存在</span>`;
  } catch (error) {
    container.innerHTML = '<span class="offline">无法读取本地环境状态</span>';
  }
}

async function discoverBrokerAccounts() {
  const message = $("#config-message");
  setMessage(message, "正在通过只读连接检测账户…");
  const payload = { broker: {
    kind: $("#broker-kind").value, host: $("#broker-host").value,
    port: $("#broker-port").value, client_id: $("#broker-client-id").value,
  }};
  try {
    const response = await fetch("/api/broker/discover", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "账户检测失败");
    const select = $("#broker-account");
    select.innerHTML = data.accounts.length ? data.accounts.map((account) => `<option value="${escapeHtml(account.account_id)}">${escapeHtml(account.label)}</option>`).join("") : '<option value="">未发现匹配环境的账户</option>';
    setMessage(message, data.accounts.length ? `发现 ${data.accounts.length} 个只读账户。` : "网关已连接，但未发现匹配环境的账户。", !data.accounts.length);
    await loadReadiness();
  } catch (error) { setMessage(message, error.message, true); await loadReadiness(); }
}

function renderPositions(snapshot) {
  const rows = snapshot.portfolio.positions;
  $("#positions-body").innerHTML = rows.length ? rows.map((position) => {
    const marketValue = position.quantity * position.price;
    return `<tr class="readonly-row"><td>${escapeHtml(position.symbol)}</td><td>${number.format(position.quantity)}</td><td>${money.format(position.price)}</td><td>${escapeHtml(position.sector)}</td><td>${money.format(marketValue)}</td></tr>`;
  }).join("") : '<tr><td colspan="5" class="empty-row">账户当前没有支持的多头持仓</td></tr>';
  $("#portfolio-sync-state").textContent = `${rows.length} 个持仓 · 现金 ${money.format(snapshot.portfolio.cash)}`;
  $("#portfolio-sync-detail").textContent = `${snapshot.source} · ${snapshot.account_environment} · ${new Date(snapshot.portfolio.as_of).toLocaleString()}`;
}

function renderRisk(analysis) {
  const status = analysis.overall_status.toLowerCase();
  $("#results").classList.remove("hidden");
  $("#result-badge").className = `result-badge ${status}`;
  $("#result-badge").textContent = analysis.overall_status;
  $("#risk-state").textContent = analysis.overall_status;
  $("#authorization-state").textContent = analysis.execution_authorization === "DENIED" ? "已阻断" : "未请求";
  const summary = analysis.summary;
  $("#result-metrics").innerHTML = [
    metric("账户净值", money.format(summary.net_asset_value)),
    metric("现金比例", percent.format(summary.cash_weight)),
    metric("总持仓暴露", percent.format(summary.gross_exposure)),
    metric("最大单仓", percent.format(summary.largest_position_weight)),
    metric("集中度 HHI", summary.concentration_hhi.toFixed(3)),
  ].join("");
  $("#findings").innerHTML = analysis.findings.length ? analysis.findings.map((item) =>
    `<div class="finding"><span class="tag ${item.level.toLowerCase()}">${escapeHtml(item.level)}</span><span>${escapeHtml(item.message)}</span></div>`
  ).join("") : '<div class="finding"><span class="tag warning">PASS</span><span>未发现超限项。</span></div>';
  const sectors = Object.entries(analysis.sector_exposures).sort((left, right) => right[1] - left[1]);
  $("#sectors").innerHTML = sectors.length ? sectors.map(([name, value]) =>
    `<div class="bar-row"><span>${escapeHtml(name)}</span><div class="bar"><i style="width:${Math.min(100, value * 100)}%"></i></div><strong>${percent.format(value)}</strong></div>`
  ).join("") : '<p class="message">暂无行业暴露</p>';
}

async function syncPortfolio() {
  const message = $("#config-message");
  if (!sessionToken) { setMessage(message, "请先建立本地会话。", true); return; }
  setMessage(message, "正在只读同步实仓并自动运行风控…");
  try {
    const response = await fetch("/api/portfolio/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": sessionToken },
      body: JSON.stringify({ limits: currentLimits() }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "实仓同步失败");
    renderPositions(data.snapshot);
    renderRisk(data.analysis);
    $("#data-source").textContent = data.snapshot.source;
    $("#data-source-detail").textContent = `${data.snapshot.account_environment} · 刚刚同步`;
    setMessage(message, "同步和风控完成；只读连接已断开。", false);
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#portfolio-sync-state").textContent = "同步失败";
    setMessage(message, error.message, true);
  }
}

async function saveConfig() {
  const message = $("#config-message");
  setMessage(message, "正在校验本地接口…");
  const payload = {
    broker: {
      kind: $("#broker-kind").value, host: $("#broker-host").value,
      port: $("#broker-port").value, account_id: $("#broker-account").value,
      client_id: $("#broker-client-id").value,
    },
    ai: {
      provider: $("#ai-provider").value, model: $("#ai-model").value,
      base_url: $("#ai-base-url").value, api_key: $("#ai-api-key").value,
    },
  };
  try {
    const response = await fetch("/api/session/configure", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "配置失败");
    sessionToken = data.session_token;
    $("#ai-api-key").value = "";
    $("#connection-state").textContent = "实仓只读";
    $("#connection-detail").textContent = `${data.session.broker.account_environment} · ${data.session.ai.provider}`;
    $("#sync-portfolio").classList.remove("hidden");
    await syncPortfolio();
  } catch (error) { setMessage(message, error.message, true); }
}

async function reviewTradePoint() {
  const message = $("#trade-message");
  if (!sessionToken) { setMessage(message, "请先连接并同步实仓。", true); return; }
  const planned = new Date($("#trade-planned-at").value);
  if (Number.isNaN(planned.getTime())) { setMessage(message, "请选择计划时间。", true); return; }
  const payload = {
    intent: {
      side: $("#trade-side").value, symbol: $("#trade-symbol").value,
      quantity: Number($("#trade-quantity").value), price: Number($("#trade-price").value),
      sector: $("#trade-sector").value || "UNKNOWN", planned_at: planned.toISOString(),
      review_horizon_sessions: Number($("#trade-horizon").value),
    },
    limits: currentLimits(),
  };
  setMessage(message, "正在复核价格、时间与组合风险…");
  try {
    const response = await fetch("/api/trade-point/analyze", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Session-Token": sessionToken },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || "复核失败");
    const review = data.review;
    const result = $("#trade-result");
    result.classList.remove("hidden");
    const timeReview = review.time_review;
    const timeText = `计划时间：${new Date(timeReview.planned_at).toLocaleString()}；美东：${new Date(timeReview.planned_at_us_eastern).toLocaleString("zh-CN", { timeZone: "America/New_York" })}；${timeReview.refresh_required ? "届时必须重新同步实仓" : "当前快照仍在 15 分钟时效窗内"}`;
    if (review.projected_risk) {
      const delta = review.risk_delta;
      result.innerHTML = `<h3>${escapeHtml(review.review_status)} · 用户自行决定</h3><div class="trade-deltas">${metric("现金比例变化", percent.format(delta.cash_weight))}${metric("总暴露变化", percent.format(delta.gross_exposure))}${metric("最大单仓变化", percent.format(delta.largest_position_weight))}${metric("HHI 变化", delta.concentration_hhi.toFixed(3))}</div><div class="time-review">${escapeHtml(timeText)}</div><p class="trade-note">复核周期 ${escapeHtml(review.intent.review_horizon_sessions)} 个交易日；未创建订单，未调用券商写接口。</p>`;
    } else {
      result.innerHTML = `<h3>BLOCKED · 无法完成风险投影</h3><p>${escapeHtml(review.blockers.join("、"))}</p><div class="time-review">${escapeHtml(timeText)}</div><p class="trade-note">未创建订单，未调用券商写接口。</p>`;
    }
    setMessage(message, "复核完成；到达计划时间前仍须刷新实仓数据。", false);
  } catch (error) { setMessage(message, error.message, true); }
}

function localDatetimeValue(date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

$("#broker-kind").addEventListener("change", syncBrokerFields);
$("#broker-kind").addEventListener("change", loadReadiness);
$("#broker-port").addEventListener("change", loadReadiness);
$("#ai-provider").addEventListener("change", syncAiFields);
$("#save-config").addEventListener("click", saveConfig);
$("#sync-portfolio").addEventListener("click", syncPortfolio);
$("#discover-accounts").addEventListener("click", discoverBrokerAccounts);
$("#review-trade").addEventListener("click", reviewTradePoint);
syncBrokerFields();
syncAiFields();
loadReadiness();
$("#trade-planned-at").value = localDatetimeValue(new Date(Date.now() + 5 * 60 * 1000));
