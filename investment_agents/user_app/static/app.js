let sessionToken = "";
let lastSessionSummary = null;
let lastSnapshot = null;
let lastAnalysis = null;
let lastTradeReview = null;
const $ = (selector, root = document) => root.querySelector(selector);
const translations = {
  "zh-CN": {
    "document.title": "Sentinel · 投资风控台", "brand.subtitle": "投资风控台", "nav.aria": "主导航",
    "nav.overview": "风险总览", "nav.connections": "接口配置", "nav.portfolio": "组合分析",
    "safety.title": "实仓只读 · 写入隔离", "safety.detail": "可读持仓 · 不下单/改单/撤单",
    "hero.title": "先守住风险，再讨论机会。", "language.label": "语言", "privacy": "密钥仅存本次进程内存",
    "metric.connection": "连接状态", "metric.risk": "组合风险", "metric.authorization": "执行授权", "metric.account_data": "账户数据",
    "state.not_configured": "尚未配置", "state.start_hint": "选择账户与 AI 接口开始", "state.pending_analysis": "待分析",
    "state.deterministic_first": "确定性规则优先", "state.not_requested": "未请求", "state.denied": "已阻断",
    "state.signal_no_auth": "信号不能自行授权", "state.manual_input": "手动输入", "state.readonly_available": "可切换实仓只读同步",
    "state.real_readonly": "实仓只读", "state.synced_now": "刚刚同步",
    "connections.title": "账户与模型接口", "connections.description": "连接配置不会写入磁盘。实仓接口仅同步现金、持仓和行情；代码不提供任何券商写操作。",
    "readiness.checking": "正在检查本地运行环境…", "readiness.model_runtime": "模型运行库", "readiness.ready": "已就绪",
    "readiness.not_installed": "未安装", "readiness.listening": "已监听", "readiness.not_running": "未启动",
    "readiness.local_port": "本地端口 {port}", "readiness.no_writes": "券商写入 · 不存在", "readiness.failed": "无法读取本地环境状态",
    "readiness.preview_only": "当前是静态预览 · 请使用启动脚本连接本地服务",
    "broker.title": "券商账户", "broker.connection_type": "连接方式", "broker.moomoo_real": "Moomoo 实仓（只读）",
    "broker.ibkr_real": "IBKR 实仓（只读）", "broker.moomoo_sim": "Moomoo 模拟账户（只读）", "broker.ibkr_paper": "IBKR Paper（只读）",
    "broker.host": "本地地址", "broker.port": "端口", "broker.readonly_account": "只读账户", "broker.detect_first": "请先检测本地网关账户",
    "broker.discover": "检测只读账户", "broker.discovering": "正在通过只读连接检测账户…", "broker.discovery_failed": "账户检测失败",
    "broker.none_found": "未发现匹配环境的账户", "broker.found": "发现 {count} 个只读账户。", "broker.gateway_no_match": "网关已连接，但未发现匹配环境的账户。",
    "ai.title": "AI 服务", "ai.provider": "服务商", "ai.disabled": "不使用 AI", "ai.ollama": "Ollama（本地）",
    "ai.compatible": "OpenAI 兼容接口", "ai.model": "模型", "ai.model_placeholder": "例如 gpt-5-mini", "ai.base_url": "Base URL（可选）",
    "ai.base_url_placeholder": "https://… 或本地地址", "ai.key_placeholder": "不会保存到磁盘",
    "button.resync": "重新同步实仓", "button.connect": "连接并分析实仓", "button.review": "复核风险变化",
    "trade.title": "买卖点风险复核", "trade.description": "由用户输入方向、价格、数量和计划时间。系统分析届时的数据时效与交易前后风险，不替用户决定，也不会创建订单。",
    "trade.side": "方向", "trade.buy": "拟买入", "trade.sell": "拟卖出", "trade.symbol": "代码", "trade.quantity": "数量",
    "trade.price": "用户价格", "trade.sector": "行业", "trade.planned_at": "计划时间", "trade.horizon": "复核周期",
    "trade.horizon_now": "即时风险", "trade.horizon_5": "5 个交易日", "trade.horizon_20": "20 个交易日", "trade.horizon_60": "60 个交易日",
    "portfolio.title": "实仓组合风险", "portfolio.description": "连接后自动同步现金、持仓与行情并立即运行风控，无需手工录入或点击分析。",
    "portfolio.waiting": "等待连接实仓", "portfolio.snapshot_hint": "同步后将在此显示只读持仓快照", "portfolio.not_synced": "尚未同步实仓",
    "portfolio.no_positions": "账户当前没有支持的多头持仓", "portfolio.summary": "{count} 个持仓 · 现金 {cash}", "portfolio.sync_failed": "同步失败",
    "table.symbol": "代码", "table.quantity": "数量", "table.price": "行情 USD", "table.sector": "行业", "table.value": "市值",
    "limits.title": "风险限制", "limits.single": "单一持仓上限", "limits.sector": "行业暴露上限", "limits.gross": "总持仓上限",
    "limits.cash": "最低现金缓冲", "limits.count": "持仓数量上限", "report.title": "风险报告", "report.findings": "限制与警告", "report.sectors": "行业暴露",
    "evidence.fact": "事实", "evidence.fact_text": "使用刚同步的账户现金、持仓和券商行情。", "evidence.inference": "推断",
    "evidence.inference_text": "集中度反映风险暴露，不代表未来收益。", "evidence.advice": "建议", "evidence.advice_text": "到达用户计划时间前必须重新同步并复核。",
    "status.PASS": "通过", "status.WARNING": "警告", "status.BLOCKED": "已阻断", "status.RECHECK_REQUIRED": "需要重新复核",
    "risk.nav": "账户净值", "risk.cash_weight": "现金比例", "risk.gross": "总持仓暴露", "risk.largest": "最大单仓", "risk.hhi": "集中度 HHI",
    "risk.no_findings": "未发现超限项。", "risk.no_sector": "暂无行业暴露",
    "finding.STALE_OR_FUTURE_PORTFOLIO": "持仓时间戳超过 15 分钟或来自未来，禁止形成执行授权。",
    "finding.NON_POSITIVE_NAV": "账户净值必须大于零。", "finding.MAX_SINGLE_POSITION_EXCEEDED": "单一持仓占比超过限制。",
    "finding.UNKNOWN_SECTOR": "{actual} 缺少行业分类，行业集中度可能被低估。", "finding.MAX_SECTOR_EXPOSURE_EXCEEDED": "行业暴露超过限制。",
    "finding.MAX_GROSS_EXPOSURE_EXCEEDED": "总持仓暴露超过限制。", "finding.MINIMUM_CASH_BUFFER_BREACHED": "现金缓冲低于限制。",
    "finding.MAX_POSITIONS_EXCEEDED": "持仓数量超过组合复杂度限制。", "finding.NO_POSITIONS": "当前没有证券持仓。",
    "message.session_first": "请先建立本地会话。", "message.syncing": "正在只读同步实仓并自动运行风控…", "message.sync_failed": "实仓同步失败",
    "message.sync_done": "同步和风控完成；只读连接已断开。", "message.validating": "正在校验本地接口…", "message.config_failed": "配置失败",
    "message.connect_first": "请先连接并同步实仓。", "message.choose_time": "请选择计划时间。", "message.reviewing": "正在复核价格、时间与组合风险…",
    "message.review_failed": "复核失败", "message.review_done": "复核完成；到达计划时间前仍须刷新实仓数据。", "message.server_required": "当前是静态预览。请运行启动脚本后访问 http://127.0.0.1:8765。",
    "trade.planned_time_text": "计划时间：{local}；美东：{eastern}；{freshness}", "trade.refresh_required": "届时必须重新同步实仓",
    "trade.snapshot_fresh": "当前快照仍在 15 分钟时效窗内", "trade.user_decides": "用户自行决定", "trade.blocked_projection": "无法完成风险投影",
    "trade.cash_delta": "现金比例变化", "trade.gross_delta": "总暴露变化", "trade.largest_delta": "最大单仓变化", "trade.hhi_delta": "HHI 变化",
    "trade.note": "复核周期 {count} 个交易日；未创建订单，未调用券商写接口。", "trade.no_order": "未创建订单，未调用券商写接口。",
    "blocker.PLANNED_TIME_IS_IN_THE_PAST": "计划时间早于允许范围", "blocker.PLANNED_TIME_TOO_FAR_AHEAD": "计划时间超过一年",
    "blocker.INSUFFICIENT_CASH_AT_USER_PRICE": "按用户价格计算现金不足", "blocker.SELL_QUANTITY_EXCEEDS_CURRENT_LONG_POSITION": "拟卖数量超过当前多头持仓"
  },
  en: {
    "document.title": "Sentinel · Investment Risk Console", "brand.subtitle": "Investment Risk Console", "nav.aria": "Primary navigation",
    "nav.overview": "Risk Overview", "nav.connections": "Connections", "nav.portfolio": "Portfolio Analysis",
    "safety.title": "Live account read-only · Writes isolated", "safety.detail": "Positions readable · No place/modify/cancel",
    "hero.title": "Protect against risk before discussing opportunity.", "language.label": "Language", "privacy": "Keys stay in process memory only",
    "metric.connection": "Connection", "metric.risk": "Portfolio Risk", "metric.authorization": "Execution Authorization", "metric.account_data": "Account Data",
    "state.not_configured": "Not configured", "state.start_hint": "Select an account and AI provider", "state.pending_analysis": "Pending",
    "state.deterministic_first": "Deterministic rules first", "state.not_requested": "Not requested", "state.denied": "Denied",
    "state.signal_no_auth": "Signals cannot self-authorize", "state.manual_input": "Manual input", "state.readonly_available": "Read-only live sync available",
    "state.real_readonly": "Live read-only", "state.synced_now": "Synced just now",
    "connections.title": "Account and Model Connections", "connections.description": "Connection settings are not written to disk. Live broker interfaces only synchronize cash, positions, and quotes; no broker write operation is exposed.",
    "readiness.checking": "Checking local runtime…", "readiness.model_runtime": "Model runtime", "readiness.ready": "Ready",
    "readiness.not_installed": "Not installed", "readiness.listening": "Listening", "readiness.not_running": "Not running",
    "readiness.local_port": "Local port {port}", "readiness.no_writes": "Broker writes · Absent", "readiness.failed": "Unable to read local runtime status",
    "readiness.preview_only": "Static preview · use the launcher to connect the local service",
    "broker.title": "Broker Account", "broker.connection_type": "Connection type", "broker.moomoo_real": "Moomoo Live (read-only)",
    "broker.ibkr_real": "IBKR Live (read-only)", "broker.moomoo_sim": "Moomoo Simulate (read-only)", "broker.ibkr_paper": "IBKR Paper (read-only)",
    "broker.host": "Local host", "broker.port": "Port", "broker.readonly_account": "Read-only account", "broker.detect_first": "Detect local gateway accounts first",
    "broker.discover": "Detect read-only accounts", "broker.discovering": "Detecting accounts through a read-only connection…", "broker.discovery_failed": "Account discovery failed",
    "broker.none_found": "No account matched this environment", "broker.found": "Found {count} read-only account(s).", "broker.gateway_no_match": "Gateway connected, but no account matched the selected environment.",
    "ai.title": "AI Service", "ai.provider": "Provider", "ai.disabled": "Do not use AI", "ai.ollama": "Ollama (local)",
    "ai.compatible": "OpenAI-compatible API", "ai.model": "Model", "ai.model_placeholder": "e.g. gpt-5-mini", "ai.base_url": "Base URL (optional)",
    "ai.base_url_placeholder": "https://… or a local address", "ai.key_placeholder": "Never written to disk",
    "button.resync": "Resync live account", "button.connect": "Connect and analyze", "button.review": "Review risk change",
    "trade.title": "Proposed Trade Risk Review", "trade.description": "The user supplies direction, price, quantity, and planned time. The system analyzes data freshness and before/after risk; it does not decide or create an order.",
    "trade.side": "Direction", "trade.buy": "Proposed buy", "trade.sell": "Proposed sell", "trade.symbol": "Symbol", "trade.quantity": "Quantity",
    "trade.price": "User price", "trade.sector": "Sector", "trade.planned_at": "Planned time", "trade.horizon": "Review horizon",
    "trade.horizon_now": "Immediate risk", "trade.horizon_5": "5 trading days", "trade.horizon_20": "20 trading days", "trade.horizon_60": "60 trading days",
    "portfolio.title": "Live Portfolio Risk", "portfolio.description": "After connection, cash, positions, and quotes are synchronized and analyzed automatically without manual portfolio entry.",
    "portfolio.waiting": "Waiting for live account", "portfolio.snapshot_hint": "The read-only snapshot will appear here after sync", "portfolio.not_synced": "Live account not yet synchronized",
    "portfolio.no_positions": "The account has no supported long positions", "portfolio.summary": "{count} position(s) · Cash {cash}", "portfolio.sync_failed": "Sync failed",
    "table.symbol": "Symbol", "table.quantity": "Quantity", "table.price": "Quote USD", "table.sector": "Sector", "table.value": "Market Value",
    "limits.title": "Risk Limits", "limits.single": "Max single position", "limits.sector": "Max sector exposure", "limits.gross": "Max gross exposure",
    "limits.cash": "Minimum cash buffer", "limits.count": "Maximum positions", "report.title": "Risk Report", "report.findings": "Limits and Warnings", "report.sectors": "Sector Exposure",
    "evidence.fact": "Fact", "evidence.fact_text": "Uses the latest synchronized account cash, positions, and broker quotes.", "evidence.inference": "Inference",
    "evidence.inference_text": "Concentration measures risk exposure, not future return.", "evidence.advice": "Guidance", "evidence.advice_text": "Resynchronize and review again before the user's planned time.",
    "status.PASS": "Pass", "status.WARNING": "Warning", "status.BLOCKED": "Blocked", "status.RECHECK_REQUIRED": "Recheck required",
    "risk.nav": "Net Asset Value", "risk.cash_weight": "Cash Weight", "risk.gross": "Gross Exposure", "risk.largest": "Largest Position", "risk.hhi": "Concentration HHI",
    "risk.no_findings": "No limit breach detected.", "risk.no_sector": "No sector exposure",
    "finding.STALE_OR_FUTURE_PORTFOLIO": "The portfolio timestamp is over 15 minutes old or in the future; execution authorization is blocked.",
    "finding.NON_POSITIVE_NAV": "Account net asset value must be positive.", "finding.MAX_SINGLE_POSITION_EXCEEDED": "A single-position weight exceeds its limit.",
    "finding.UNKNOWN_SECTOR": "{actual} has no sector classification; sector concentration may be understated.", "finding.MAX_SECTOR_EXPOSURE_EXCEEDED": "Sector exposure exceeds its limit.",
    "finding.MAX_GROSS_EXPOSURE_EXCEEDED": "Gross exposure exceeds its limit.", "finding.MINIMUM_CASH_BUFFER_BREACHED": "Cash buffer is below its limit.",
    "finding.MAX_POSITIONS_EXCEEDED": "Position count exceeds the portfolio complexity limit.", "finding.NO_POSITIONS": "The account currently has no security positions.",
    "message.session_first": "Create a local session first.", "message.syncing": "Synchronizing the live account read-only and running risk controls…", "message.sync_failed": "Live account sync failed",
    "message.sync_done": "Sync and risk analysis complete; the read-only connection has closed.", "message.validating": "Validating local interfaces…", "message.config_failed": "Configuration failed",
    "message.connect_first": "Connect and synchronize a live account first.", "message.choose_time": "Select a planned time.", "message.reviewing": "Reviewing price, time, and portfolio risk…",
    "message.review_failed": "Review failed", "message.review_done": "Review complete; refresh the live account again before the planned time.", "message.server_required": "This is a static preview. Run the launcher, then open http://127.0.0.1:8765.",
    "trade.planned_time_text": "Planned: {local}; US Eastern: {eastern}; {freshness}", "trade.refresh_required": "the live account must be resynchronized at that time",
    "trade.snapshot_fresh": "the current snapshot remains inside the 15-minute freshness window", "trade.user_decides": "User decision required", "trade.blocked_projection": "Risk projection unavailable",
    "trade.cash_delta": "Cash weight change", "trade.gross_delta": "Gross exposure change", "trade.largest_delta": "Largest position change", "trade.hhi_delta": "HHI change",
    "trade.note": "Review horizon: {count} trading day(s). No order was created and no broker write API was called.", "trade.no_order": "No order was created and no broker write API was called.",
    "blocker.PLANNED_TIME_IS_IN_THE_PAST": "Planned time is earlier than the allowed window", "blocker.PLANNED_TIME_TOO_FAR_AHEAD": "Planned time is more than one year ahead",
    "blocker.INSUFFICIENT_CASH_AT_USER_PRICE": "Insufficient cash at the user's price", "blocker.SELL_QUANTITY_EXCEEDS_CURRENT_LONG_POSITION": "Proposed sell quantity exceeds the current long position"
  }
};
let language = localStorage.getItem("sentinel-language") || "en";
if (!translations[language]) language = "en";
let money;
let number;
let percent;
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));

function t(key, variables = {}, fallback = key) {
  let value = translations[language][key] ?? translations.en[key] ?? fallback;
  for (const [name, replacement] of Object.entries(variables)) value = value.replaceAll(`{${name}}`, String(replacement));
  return value;
}

function statusLabel(status) { return t(`status.${status}`, {}, status); }
function findingText(item) { return t(`finding.${item.code}`, { actual: item.actual ?? "" }, item.message); }
function blockerText(code) { return t(`blocker.${code}`, {}, code); }

function applyLanguage() {
  const locale = language === "zh-CN" ? "zh-CN" : "en-US";
  document.documentElement.lang = language;
  document.title = t("document.title");
  money = new Intl.NumberFormat(locale, { style: "currency", currency: "USD" });
  number = new Intl.NumberFormat(locale, { maximumFractionDigits: 4 });
  percent = new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 1 });
  document.querySelectorAll("[data-i18n]").forEach((element) => { element.textContent = t(element.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => { element.placeholder = t(element.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => { element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel)); });
  $("#language-select").value = language;
  $("#language-select").setAttribute("aria-label", t("language.label"));
  if (lastSessionSummary) {
    $("#connection-state").textContent = t("state.real_readonly");
    $("#connection-detail").textContent = `${lastSessionSummary.broker.account_environment} · ${lastSessionSummary.ai.provider}`;
  }
  if (lastSnapshot) renderPositions(lastSnapshot, false);
  if (lastAnalysis) renderRisk(lastAnalysis, false);
  if (lastTradeReview) renderTradeReview(lastTradeReview, false);
  setMessage($("#config-message"), "");
  setMessage($("#trade-message"), "");
  loadReadiness();
}

function setLanguage(nextLanguage) {
  language = translations[nextLanguage] ? nextLanguage : "en";
  localStorage.setItem("sentinel-language", language);
  applyLanguage();
}

function requireLocalServer(messageElement) {
  if (window.location.protocol !== "file:") return true;
  setMessage(messageElement, t("message.server_required"), true);
  return false;
}

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
  $("#broker-account").innerHTML = `<option value="">${escapeHtml(t("broker.detect_first"))}</option>`;
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
  if (window.location.protocol === "file:") {
    container.innerHTML = `<span class="offline preview-mode">${escapeHtml(t("readiness.preview_only"))}</span>`;
    return;
  }
  try {
    const response = await fetch("/api/runtime/readiness");
    const data = await response.json();
    if (!response.ok) throw new Error("readiness unavailable");
    const readiness = data.readiness;
    const modulePills = [
      ["Moomoo SDK", readiness.modules.moomoo],
      ["IBKR API", readiness.modules.ibapi],
      [t("readiness.model_runtime"), readiness.modules.sklearn],
    ];
    const port = $("#broker-port").value;
    const gatewayReady = Boolean(readiness.loopback_ports[port]);
    container.innerHTML = modulePills.map(([label, ready]) => `<span class="${ready ? "ready" : "offline"}">${escapeHtml(label)} · ${escapeHtml(ready ? t("readiness.ready") : t("readiness.not_installed"))}</span>`).join("") + `<span class="${gatewayReady ? "ready" : "offline"}">${escapeHtml(t("readiness.local_port", { port }))} · ${escapeHtml(gatewayReady ? t("readiness.listening") : t("readiness.not_running"))}</span><span class="ready">${escapeHtml(t("readiness.no_writes"))}</span>`;
  } catch (error) {
    container.innerHTML = `<span class="offline">${escapeHtml(t("readiness.failed"))}</span>`;
  }
}

async function discoverBrokerAccounts() {
  const message = $("#config-message");
  if (!requireLocalServer(message)) return;
  setMessage(message, t("broker.discovering"));
  const payload = { broker: {
    kind: $("#broker-kind").value, host: $("#broker-host").value,
    port: $("#broker-port").value, client_id: $("#broker-client-id").value,
  }};
  try {
    const response = await fetch("/api/broker/discover", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || t("broker.discovery_failed"));
    const select = $("#broker-account");
    select.innerHTML = data.accounts.length ? data.accounts.map((account) => `<option value="${escapeHtml(account.account_id)}">${escapeHtml(account.label)}</option>`).join("") : `<option value="">${escapeHtml(t("broker.none_found"))}</option>`;
    setMessage(message, data.accounts.length ? t("broker.found", { count: data.accounts.length }) : t("broker.gateway_no_match"), !data.accounts.length);
    await loadReadiness();
  } catch (error) { setMessage(message, error.message, true); await loadReadiness(); }
}

function renderPositions(snapshot, remember = true) {
  if (remember) lastSnapshot = snapshot;
  const rows = snapshot.portfolio.positions;
  $("#positions-body").innerHTML = rows.length ? rows.map((position) => {
    const marketValue = position.quantity * position.price;
    return `<tr class="readonly-row"><td>${escapeHtml(position.symbol)}</td><td>${number.format(position.quantity)}</td><td>${money.format(position.price)}</td><td>${escapeHtml(position.sector)}</td><td>${money.format(marketValue)}</td></tr>`;
  }).join("") : `<tr><td colspan="5" class="empty-row">${escapeHtml(t("portfolio.no_positions"))}</td></tr>`;
  $("#portfolio-sync-state").textContent = t("portfolio.summary", { count: rows.length, cash: money.format(snapshot.portfolio.cash) });
  $("#portfolio-sync-detail").textContent = `${snapshot.source} · ${snapshot.account_environment} · ${new Date(snapshot.portfolio.as_of).toLocaleString(language === "zh-CN" ? "zh-CN" : "en-US")}`;
  $("#data-source").textContent = snapshot.source;
  $("#data-source-detail").textContent = `${snapshot.account_environment} · ${t("state.synced_now")}`;
}

function renderRisk(analysis, remember = true) {
  if (remember) lastAnalysis = analysis;
  const status = analysis.overall_status.toLowerCase();
  $("#results").classList.remove("hidden");
  $("#result-badge").className = `result-badge ${status}`;
  $("#result-badge").textContent = statusLabel(analysis.overall_status);
  $("#risk-state").textContent = statusLabel(analysis.overall_status);
  $("#authorization-state").textContent = analysis.execution_authorization === "DENIED" ? t("state.denied") : t("state.not_requested");
  const summary = analysis.summary;
  $("#result-metrics").innerHTML = [
    metric(t("risk.nav"), money.format(summary.net_asset_value)),
    metric(t("risk.cash_weight"), percent.format(summary.cash_weight)),
    metric(t("risk.gross"), percent.format(summary.gross_exposure)),
    metric(t("risk.largest"), percent.format(summary.largest_position_weight)),
    metric(t("risk.hhi"), summary.concentration_hhi.toFixed(3)),
  ].join("");
  $("#findings").innerHTML = analysis.findings.length ? analysis.findings.map((item) =>
    `<div class="finding"><span class="tag ${item.level.toLowerCase()}">${escapeHtml(statusLabel(item.level))}</span><span>${escapeHtml(findingText(item))}</span></div>`
  ).join("") : `<div class="finding"><span class="tag warning">${escapeHtml(statusLabel("PASS"))}</span><span>${escapeHtml(t("risk.no_findings"))}</span></div>`;
  const sectors = Object.entries(analysis.sector_exposures).sort((left, right) => right[1] - left[1]);
  $("#sectors").innerHTML = sectors.length ? sectors.map(([name, value]) =>
    `<div class="bar-row"><span>${escapeHtml(name)}</span><div class="bar"><i style="width:${Math.min(100, value * 100)}%"></i></div><strong>${percent.format(value)}</strong></div>`
  ).join("") : `<p class="message">${escapeHtml(t("risk.no_sector"))}</p>`;
}

async function syncPortfolio() {
  const message = $("#config-message");
  if (!requireLocalServer(message)) return;
  if (!sessionToken) { setMessage(message, t("message.session_first"), true); return; }
  setMessage(message, t("message.syncing"));
  try {
    const response = await fetch("/api/portfolio/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Session-Token": sessionToken },
      body: JSON.stringify({ limits: currentLimits() }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || t("message.sync_failed"));
    renderPositions(data.snapshot);
    renderRisk(data.analysis);
    setMessage(message, t("message.sync_done"), false);
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    $("#portfolio-sync-state").textContent = t("portfolio.sync_failed");
    setMessage(message, error.message, true);
  }
}

async function saveConfig() {
  const message = $("#config-message");
  if (!requireLocalServer(message)) return;
  setMessage(message, t("message.validating"));
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
    if (!response.ok) throw new Error(data.error?.message || t("message.config_failed"));
    sessionToken = data.session_token;
    lastSessionSummary = data.session;
    $("#ai-api-key").value = "";
    $("#connection-state").textContent = t("state.real_readonly");
    $("#connection-detail").textContent = `${data.session.broker.account_environment} · ${data.session.ai.provider}`;
    $("#sync-portfolio").classList.remove("hidden");
    await syncPortfolio();
  } catch (error) { setMessage(message, error.message, true); }
}

function renderTradeReview(review, remember = true) {
  if (remember) lastTradeReview = review;
  const result = $("#trade-result");
  result.classList.remove("hidden");
  const timeReview = review.time_review;
  const locale = language === "zh-CN" ? "zh-CN" : "en-US";
  const timeText = t("trade.planned_time_text", {
    local: new Date(timeReview.planned_at).toLocaleString(locale),
    eastern: new Date(timeReview.planned_at_us_eastern).toLocaleString(locale, { timeZone: "America/New_York" }),
    freshness: timeReview.refresh_required ? t("trade.refresh_required") : t("trade.snapshot_fresh"),
  });
  if (review.projected_risk) {
    const delta = review.risk_delta;
    result.innerHTML = `<h3>${escapeHtml(statusLabel(review.review_status))} · ${escapeHtml(t("trade.user_decides"))}</h3><div class="trade-deltas">${metric(t("trade.cash_delta"), percent.format(delta.cash_weight))}${metric(t("trade.gross_delta"), percent.format(delta.gross_exposure))}${metric(t("trade.largest_delta"), percent.format(delta.largest_position_weight))}${metric(t("trade.hhi_delta"), delta.concentration_hhi.toFixed(3))}</div><div class="time-review">${escapeHtml(timeText)}</div><p class="trade-note">${escapeHtml(t("trade.note", { count: review.intent.review_horizon_sessions }))}</p>`;
  } else {
    const separator = language === "zh-CN" ? "、" : "; ";
    result.innerHTML = `<h3>${escapeHtml(statusLabel("BLOCKED"))} · ${escapeHtml(t("trade.blocked_projection"))}</h3><p>${escapeHtml(review.blockers.map(blockerText).join(separator))}</p><div class="time-review">${escapeHtml(timeText)}</div><p class="trade-note">${escapeHtml(t("trade.no_order"))}</p>`;
  }
}

async function reviewTradePoint() {
  const message = $("#trade-message");
  if (!requireLocalServer(message)) return;
  if (!sessionToken) { setMessage(message, t("message.connect_first"), true); return; }
  const planned = new Date($("#trade-planned-at").value);
  if (Number.isNaN(planned.getTime())) { setMessage(message, t("message.choose_time"), true); return; }
  const payload = {
    intent: {
      side: $("#trade-side").value, symbol: $("#trade-symbol").value,
      quantity: Number($("#trade-quantity").value), price: Number($("#trade-price").value),
      sector: $("#trade-sector").value || "UNKNOWN", planned_at: planned.toISOString(),
      review_horizon_sessions: Number($("#trade-horizon").value),
    },
    limits: currentLimits(),
  };
  setMessage(message, t("message.reviewing"));
  try {
    const response = await fetch("/api/trade-point/analyze", {
      method: "POST", headers: { "Content-Type": "application/json", "X-Session-Token": sessionToken },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || t("message.review_failed"));
    const review = data.review;
    renderTradeReview(review);
    setMessage(message, t("message.review_done"), false);
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
$("#language-select").addEventListener("change", (event) => setLanguage(event.target.value));
syncBrokerFields();
syncAiFields();
applyLanguage();
$("#trade-planned-at").value = localDatetimeValue(new Date(Date.now() + 5 * 60 * 1000));
