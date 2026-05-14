const stateUrl = "/api/state";

const elements = {
  instrumentValue: document.getElementById("instrumentValue"),
  modeValue: document.getElementById("modeValue"),
  operatingModeValue: document.getElementById("operatingModeValue"),
  aiValue: document.getElementById("aiValue"),
  progressValue: document.getElementById("progressValue"),
  balanceValue: document.getElementById("balanceValue"),
  realizedValue: document.getElementById("realizedValue"),
  unrealizedValue: document.getElementById("unrealizedValue"),
  latestCandleStamp: document.getElementById("latestCandleStamp"),
  spotCloseValue: document.getElementById("spotCloseValue"),
  pdhValue: document.getElementById("pdhValue"),
  pdlValue: document.getElementById("pdlValue"),
  pdcValue: document.getElementById("pdcValue"),
  liveStatusValue: document.getElementById("liveStatusValue"),
  liveSecurityValue: document.getElementById("liveSecurityValue"),
  liveLtpValue: document.getElementById("liveLtpValue"),
  liveTicksValue: document.getElementById("liveTicksValue"),
  liveErrorValue: document.getElementById("liveErrorValue"),
  syncStatusValue: document.getElementById("syncStatusValue"),
  syncMessageValue: document.getElementById("syncMessageValue"),
  syncPreviousValue: document.getElementById("syncPreviousValue"),
  syncIntradayValue: document.getElementById("syncIntradayValue"),
  syncTotalValue: document.getElementById("syncTotalValue"),
  syncOpenValue: document.getElementById("syncOpenValue"),
  syncUpdatedValue: document.getElementById("syncUpdatedValue"),
  savedClientIdValue: document.getElementById("savedClientIdValue"),
  savedDhanTokenValue: document.getElementById("savedDhanTokenValue"),
  savedOpenAIApiKeyValue: document.getElementById("savedOpenAIApiKeyValue"),
  savedOpenAIModelValue: document.getElementById("savedOpenAIModelValue"),
  savedDeepSeekApiKeyValue: document.getElementById("savedDeepSeekApiKeyValue"),
  savedDeepSeekModelValue: document.getElementById("savedDeepSeekModelValue"),
  savedFullAIProviderValue: document.getElementById("savedFullAIProviderValue"),
  savedOperatingModeValue: document.getElementById("savedOperatingModeValue"),
  savedPathValue: document.getElementById("savedPathValue"),
  savedUpdatedValue: document.getElementById("savedUpdatedValue"),
  aiStatusValue: document.getElementById("aiStatusValue"),
  aiModelStatusValue: document.getElementById("aiModelStatusValue"),
  aiHealthMessage: document.getElementById("aiHealthMessage"),
  lastActionValue: document.getElementById("lastActionValue"),
  lastActionMessage: document.getElementById("lastActionMessage"),
  rulebookJobValue: document.getElementById("rulebookJobValue"),
  rulebookJobMessage: document.getElementById("rulebookJobMessage"),
  instrumentNote: document.getElementById("instrumentNote"),
  decisionAction: document.getElementById("decisionAction"),
  decisionConfidence: document.getElementById("decisionConfidence"),
  decisionSource: document.getElementById("decisionSource"),
  decisionStrike: document.getElementById("decisionStrike"),
  decisionOptionLabel: document.getElementById("decisionOptionLabel"),
  decisionOption: document.getElementById("decisionOption"),
  decisionReason: document.getElementById("decisionReason"),
  pendingSetup: document.getElementById("pendingSetup"),
  liquidityZones: document.getElementById("liquidityZones"),
  operatorZones: document.getElementById("operatorZones"),
  signalTape: document.getElementById("signalTape"),
  activeTrade: document.getElementById("activeTrade"),
  tradeHistory: document.getElementById("tradeHistory"),
  heuristicNarrative: document.getElementById("heuristicNarrative"),
  heuristicTrace: document.getElementById("heuristicTrace"),
  rulebookView: document.getElementById("rulebookView"),
  learningLog: document.getElementById("learningLog"),
  toast: document.getElementById("toast"),
  chart: document.getElementById("priceChart"),
};

const uiStatus = {
  lastActionKind: "idle",
  lastActionTitle: "Waiting for an action.",
  lastActionMessage: "Upload a rulebook or sync data to see the latest result here.",
};

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }
  return data;
}

function money(value) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function instrumentSideLabel(state, optionType) {
  if (state.instrument && !state.instrument.supports_options) {
    if (optionType === "CE") {
      return "LONG";
    }
    if (optionType === "PE") {
      return "SHORT";
    }
  }
  return optionType || "-";
}

function toIstDate(value) {
  if (!value) {
    return null;
  }
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(value) && !/[zZ]|[+-]\d{2}:\d{2}$/.test(value)) {
    const parsed = new Date(`${value}+05:30`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatIstDateTime(value) {
  const parsed = toIstDate(value);
  if (!parsed) {
    return "-";
  }
  return `${parsed.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })} IST`;
}

function formatSignalTime(value) {
  const parsed = toIstDate(value);
  if (!parsed) {
    return "-";
  }
  return parsed.toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }) + " IST";
}

function setToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  window.clearTimeout(elements.toastTimer);
  elements.toastTimer = window.setTimeout(() => elements.toast.classList.add("hidden"), 3000);
}

function setLastAction(kind, title, message) {
  uiStatus.lastActionKind = kind;
  uiStatus.lastActionTitle = title;
  uiStatus.lastActionMessage = message;
  elements.lastActionValue.textContent = `${title} (${kind})`;
  elements.lastActionMessage.textContent = message;
}

function renderList(container, items, renderItem) {
  container.innerHTML = "";
  if (!items || items.length === 0) {
    container.innerHTML = `<div class="list-item empty-state">No items yet.</div>`;
    return;
  }
  items.forEach((item) => {
    container.insertAdjacentHTML("beforeend", renderItem(item));
  });
}

function renderTrade(trade) {
  if (!trade) {
    elements.activeTrade.className = "trade-card empty-state";
    elements.activeTrade.textContent = "No active paper trade.";
    return;
  }
  const stopLabel = trade.price_mode === "cash" ? "Spot Stop" : "Stop";
  const targetLabel = trade.price_mode === "cash" ? "Spot Target" : "Target";
  const openQty = trade.open_quantity ?? trade.quantity;
  elements.activeTrade.className = "trade-card";
  elements.activeTrade.innerHTML = `
    <strong>${trade.symbol}</strong>
    <p>${trade.direction} | Qty ${trade.quantity} | Open ${openQty} | Entry ${money(trade.entry_price)} | Current ${money(trade.current_price)}</p>
    <p>${targetLabel} ${money(trade.target_price)} | ${stopLabel} ${money(trade.stop_price)} | P&L ${money(trade.pnl)}</p>
    <p>Booked P&L ${money(trade.booked_pnl)} | Partial Exits ${trade.partial_exit_count || 0}</p>
    <p>Entry Time ${formatIstDateTime(trade.entry_time)} | Entry Source ${trade.entry_quote_source || "-"}</p>
    <p>Latest Quote ${formatIstDateTime(trade.current_quote_time)} | Current Source ${trade.current_quote_source || "-"}</p>
    <p>${trade.notes || "No notes"}</p>
  `;
}

function renderPendingSetup(setup, state) {
  if (!setup || ["consumed", "invalidated"].includes(setup.status)) {
    const extra = setup && ["consumed", "invalidated"].includes(setup.status)
      ? `<p>Latest setup is now historical and can be reviewed in Heuristic Narrative or Heuristic Trace.</p>`
      : "";
    elements.pendingSetup.innerHTML = `<div class="list-item empty-state">No live pending setup locked.${extra}</div>`;
    return;
  }
  elements.pendingSetup.innerHTML = `
    <div class="list-item">
      <strong>${setup.setup_type || "pending-setup"}</strong>
      <span class="pill">${setup.status || "armed"}</span>
      <p>${setup.direction || "-"} | ${instrumentSideLabel(state, setup.option_type)} | Strike ${state.instrument.supports_options ? (setup.strike || "-") : "-"}</p>
      <p>Trigger ${money(setup.trigger_price)} (${setup.trigger_basis || "-"}) | Invalidation ${money(setup.invalidation_level)}</p>
      <p>Created ${formatIstDateTime(setup.created_at)} | Updated ${formatIstDateTime(setup.updated_at)}</p>
      <p>Triggered ${formatIstDateTime(setup.triggered_at)} | Consumed ${formatIstDateTime(setup.consumed_at)} | Invalidated ${formatIstDateTime(setup.invalidated_at)}</p>
      <p>Confidence ${Math.round((setup.confidence || 0) * 100)}% | Source ${setup.source || "-"}</p>
      <p>Trade ID ${setup.executed_trade_id || "-"}</p>
      <p>${setup.status_reason || "No status note."}</p>
      <p>${setup.notes || "No setup notes."}</p>
    </div>
  `;
}

function drawChart(state) {
  const ctx = elements.chart.getContext("2d");
  ctx.clearRect(0, 0, elements.chart.width, elements.chart.height);

  const recent = state.recent_candles || [];
  if (!recent.length) {
    ctx.fillStyle = "#5d6b60";
    ctx.font = "16px Segoe UI";
    ctx.fillText("Step or load data to render candles.", 24, 48);
    return;
  }

  const closes = recent.map((candle) => candle.close);
  const highs = recent.map((candle) => candle.high);
  const lows = recent.map((candle) => candle.low);
  const maxPrice = Math.max(...highs);
  const minPrice = Math.min(...lows);
  const padding = 24;
  const width = elements.chart.width - padding * 2;
  const height = elements.chart.height - padding * 2;

  ctx.strokeStyle = "#cad5cf";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i += 1) {
    const y = padding + (height / 3) * i;
    ctx.beginPath();
    ctx.moveTo(padding, y);
    ctx.lineTo(elements.chart.width - padding, y);
    ctx.stroke();
  }

  ctx.strokeStyle = "#0f766e";
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  closes.forEach((close, index) => {
    const x = padding + (index / Math.max(closes.length - 1, 1)) * width;
    const y = padding + ((maxPrice - close) / Math.max(maxPrice - minPrice, 1)) * height;
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.stroke();
}

function renderState(state) {
  elements.instrumentValue.textContent = state.instrument.label;
  elements.modeValue.textContent = state.mode;
  elements.operatingModeValue.textContent = state.operating_mode;
  elements.aiValue.textContent = state.credentials.full_ai_provider || "openai";
  elements.progressValue.textContent = `${state.current_index + 1} / ${state.total_candles}`;
  elements.balanceValue.textContent = money(state.balance);
  elements.realizedValue.textContent = money(state.realized_pnl);
  elements.unrealizedValue.textContent = money(state.unrealized_pnl);
  elements.liveStatusValue.textContent = state.live_feed.status;
  elements.liveSecurityValue.textContent = state.live_feed.security_id;
  elements.liveLtpValue.textContent = money(state.live_feed.last_ltp);
  elements.liveTicksValue.textContent = state.live_feed.ticks_received;
  elements.liveErrorValue.textContent = state.live_feed.error || "None";
  elements.instrumentNote.textContent = `The live feed subscribes to Dhan security ID ${state.instrument.security_id} for ${state.instrument.label} in paper mode.`;
  elements.decisionOptionLabel.textContent = state.instrument.supports_options ? "Option" : "Bias";
  document.getElementById("connectLiveBtn").textContent = `Connect Live ${state.instrument.label}`;
  document.getElementById("simulateTodayBtn").textContent = `Start ${state.instrument.label} Today Simulation`;
  elements.syncStatusValue.textContent = `${state.data_sync.status} (${state.data_sync.source})`;
  elements.syncMessageValue.textContent = state.data_sync.message || "No Dhan chart sync yet.";
  elements.syncPreviousValue.textContent = state.data_sync.previous_day_candles || 0;
  elements.syncIntradayValue.textContent = state.data_sync.intraday_candles || 0;
  elements.syncTotalValue.textContent = state.data_sync.total_loaded || 0;
  elements.syncOpenValue.textContent = state.data_sync.has_live_open_candle ? "Yes" : "No";
  elements.syncUpdatedValue.textContent = state.data_sync.last_synced_at
    ? new Date(state.data_sync.last_synced_at).toLocaleString()
    : "-";
  elements.savedClientIdValue.textContent = state.credentials.client_id || "Not saved";
  elements.savedDhanTokenValue.textContent = state.credentials.dhan_access_token_saved ? "Saved locally" : "Not saved";
  elements.savedOpenAIApiKeyValue.textContent = state.credentials.openai_api_key_saved ? "Saved locally" : "Not saved";
  elements.savedOpenAIModelValue.textContent = state.credentials.openai_model || "gpt-5.4-mini";
  elements.savedDeepSeekApiKeyValue.textContent = state.credentials.deepseek_api_key_saved ? "Saved locally" : "Not saved";
  elements.savedDeepSeekModelValue.textContent = state.credentials.deepseek_model || "deepseek-v4-flash";
  elements.savedFullAIProviderValue.textContent = state.credentials.full_ai_provider || "openai";
  elements.savedOperatingModeValue.textContent = state.credentials.operating_mode || "full-ai";
  elements.savedPathValue.textContent = state.credentials.storage_path || "-";
  elements.savedUpdatedValue.textContent = state.credentials.last_updated
    ? new Date(state.credentials.last_updated).toLocaleString()
    : "-";
  if (state.rulebook_job && state.rulebook_job.status !== "idle") {
    const jobStatus = state.rulebook_job.status;
    elements.rulebookJobValue.textContent = `${jobStatus}${state.rulebook_job.source_name ? ` (${state.rulebook_job.source_name})` : ""}`;
    elements.rulebookJobMessage.textContent = state.rulebook_job.message || "No job message.";
    if (jobStatus === "running") {
      uiStatus.lastActionKind = "pending";
      uiStatus.lastActionTitle = "Rulebook learning";
      uiStatus.lastActionMessage = state.rulebook_job.message || "Rulebook learning is running.";
    } else if (jobStatus === "success") {
      uiStatus.lastActionKind = "success";
      uiStatus.lastActionTitle = "Rulebook learning";
      uiStatus.lastActionMessage = state.rulebook_job.message || "Rulebook learning completed.";
    } else if (jobStatus === "error") {
      uiStatus.lastActionKind = "error";
      uiStatus.lastActionTitle = "Rulebook learning";
      uiStatus.lastActionMessage = state.rulebook_job.message || "Rulebook learning failed.";
    }
  } else {
    elements.rulebookJobValue.textContent = "idle";
    elements.rulebookJobMessage.textContent = "No rulebook learning job has run yet.";
  }
  elements.lastActionValue.textContent = `${uiStatus.lastActionTitle} (${uiStatus.lastActionKind})`;
  elements.lastActionMessage.textContent = uiStatus.lastActionMessage;

  if (state.latest_candle) {
    elements.latestCandleStamp.textContent = new Date(state.latest_candle.timestamp).toLocaleString();
    elements.spotCloseValue.textContent = money(state.latest_candle.close);
  } else {
    elements.latestCandleStamp.textContent = "No candle loaded";
    elements.spotCloseValue.textContent = "-";
  }

  elements.pdhValue.textContent = money(state.previous_day.high);
  elements.pdlValue.textContent = money(state.previous_day.low);
  elements.pdcValue.textContent = money(state.previous_day.close);

  if (state.decision) {
    elements.decisionAction.textContent = state.decision.action;
    elements.decisionConfidence.textContent = `${Math.round((state.decision.confidence || 0) * 100)}%`;
    elements.decisionSource.textContent = state.decision.decision_source || "-";
    elements.decisionStrike.textContent = state.instrument.supports_options ? (state.decision.strike || "-") : "-";
    elements.decisionOption.textContent = instrumentSideLabel(state, state.decision.option_type);
    elements.decisionReason.textContent = state.decision.reason || "No reason returned.";
  } else {
    elements.decisionAction.textContent = "-";
    elements.decisionConfidence.textContent = "-";
    elements.decisionSource.textContent = "-";
    elements.decisionStrike.textContent = "-";
    elements.decisionOption.textContent = "-";
    elements.decisionReason.textContent = "No decision yet.";
  }

  renderPendingSetup(state.pending_setup, state);

  renderList(elements.liquidityZones, state.liquidity_zones, (zone) => `
    <div class="list-item">
      <strong>${zone.label}</strong>
      <span class="pill">${zone.zone_type}</span>
      <p>Price ${money(zone.price)} | Upper ${money(zone.upper)} | Lower ${money(zone.lower)}</p>
      <p>${zone.notes}</p>
    </div>
  `);

  renderList(elements.operatorZones, state.operator_zones, (zone) => `
    <div class="list-item">
      <strong>${zone.label}</strong>
      <span class="pill">${zone.zone_type}</span>
      <p>Price ${money(zone.price)} | Upper ${money(zone.upper)} | Lower ${money(zone.lower)}</p>
      <p>${zone.notes}</p>
    </div>
  `);

  renderList(elements.signalTape, state.signal_history || state.signal_events, (event) => `
    <div class="list-item">
      <strong>${event.title}</strong>
      <span class="pill ${event.sentiment}">${event.sentiment}</span>
      <p>Signal Time ${formatSignalTime(event.timestamp)}</p>
      <p>${event.description}</p>
    </div>
  `);

  renderTrade(state.active_trade);

  renderList(elements.tradeHistory, state.trade_history, (trade) => `
    <div class="list-item">
      <strong>${trade.symbol}</strong>
      <p>${trade.status} | Entry ${money(trade.entry_price)} | Exit ${money(trade.exit_price)}</p>
      <p>Total Qty ${trade.quantity} | Closed Qty ${trade.closed_quantity || 0} | Booked P&amp;L ${money(trade.booked_pnl)}</p>
      <p>Entry Time ${formatIstDateTime(trade.entry_time)} | Exit Time ${formatIstDateTime(trade.exit_time)}</p>
      <p>Entry Source ${trade.entry_quote_source || "-"} | Exit Source ${trade.exit_quote_source || "-"}</p>
      <p>P&amp;L ${money(trade.pnl)} | ${trade.notes || "No notes"}</p>
    </div>
  `);

  renderList(elements.heuristicNarrative, state.heuristic_narrative, (event) => `
    <div class="list-item">
      <strong>${event.title}</strong>
      <span class="pill">${event.status || event.event_type}</span>
      <p>${formatSignalTime(event.timestamp)} | ${event.direction || "-"}</p>
      <p>Price ${money(event.price)} | ${event.detail || "No detail."}</p>
    </div>
  `);

  renderList(elements.heuristicTrace, state.heuristic_trace, (entry) => `
    <div class="list-item">
      <strong>${entry.title}</strong>
      <span class="pill">${entry.status || entry.market_state || entry.event_type}</span>
      <p>${formatSignalTime(entry.timestamp)} | Action ${entry.action || "-"} | Score ${entry.setup_score != null ? entry.setup_score.toFixed(1) : "-"}</p>
      <p>${entry.setup_type || "No setup"} | ${entry.option_type || "-"} | Confidence ${entry.confidence != null ? Math.round(entry.confidence * 100) : "-"}%</p>
      <p>Trigger ${entry.trigger_price != null ? money(entry.trigger_price) : "-"} | Invalidation ${entry.invalidation_level != null ? money(entry.invalidation_level) : "-"}</p>
      <p>${entry.block_reason || entry.market_state || "-"}</p>
      <p>${entry.detail || "No detail."}</p>
    </div>
  `);

  elements.rulebookView.textContent = state.rulebook;

  const liveConnectForm = document.getElementById("liveConnectForm");
  if (liveConnectForm && state.credentials.client_id && !liveConnectForm.elements.client_id.value) {
    liveConnectForm.elements.client_id.value = state.credentials.client_id;
  }
  const instrumentModeForm = document.getElementById("instrumentModeForm");
  if (instrumentModeForm) {
    instrumentModeForm.elements.instrument_mode.value = state.instrument.mode;
  }

  const credentialSaveForm = document.getElementById("credentialSaveForm");
  if (credentialSaveForm && state.credentials.client_id && !credentialSaveForm.elements.client_id.value) {
    credentialSaveForm.elements.client_id.value = state.credentials.client_id;
  }
  if (credentialSaveForm && state.credentials.openai_model && !credentialSaveForm.elements.openai_model.value) {
    credentialSaveForm.elements.openai_model.value = state.credentials.openai_model;
  }
  if (credentialSaveForm && state.credentials.deepseek_model && !credentialSaveForm.elements.deepseek_model.value) {
    credentialSaveForm.elements.deepseek_model.value = state.credentials.deepseek_model;
  }
  if (credentialSaveForm && state.credentials.full_ai_provider) {
    credentialSaveForm.elements.full_ai_provider.value = state.credentials.full_ai_provider;
  }
  if (credentialSaveForm && state.credentials.operating_mode) {
    credentialSaveForm.elements.operating_mode.value = state.credentials.operating_mode;
  }

  renderList(elements.learningLog, state.learning_log, (item) => `
    <div class="list-item">
      <strong>Update</strong>
      <p>${item}</p>
    </div>
  `);

  drawChart(state);
}

async function refreshState() {
  const state = await fetchJson(stateUrl);
  renderState(state);
}

async function postForm(url, formData) {
  const data = await fetchJson(url, { method: "POST", body: formData });
  if (data.state) {
    renderState(data.state);
  }
  if (data.message) {
    if (data.job && data.job.status === "running") {
      setLastAction("pending", "Rulebook learning", data.job.message || data.message);
    } else {
      setLastAction("success", "Request completed", data.message);
    }
    setToast(data.message);
  }
}

async function runAction(action) {
  try {
    await action();
  } catch (error) {
    setLastAction("error", "Request failed", error.message || "Request failed");
    setToast(error.message || "Request failed");
  }
}

async function refreshAiHealth() {
  try {
    const health = await fetchJson("/api/health/ai");
    elements.aiStatusValue.textContent = health.reachable ? `Configured (${health.provider})` : "Not configured";
    elements.aiModelStatusValue.textContent = health.model_available ? `Yes (${health.model})` : `No (${health.model})`;
    elements.aiHealthMessage.textContent = health.message || "No health message.";
  } catch (error) {
    elements.aiStatusValue.textContent = "Unknown";
    elements.aiModelStatusValue.textContent = "Unknown";
    elements.aiHealthMessage.textContent = error.message || "Unable to load AI health.";
  }
}

document.getElementById("loadSampleBtn").addEventListener("click", () => runAction(async () => {
  const data = await fetchJson("/api/simulation/load-sample", { method: "POST" });
  renderState(data);
  setToast(`Loaded sample ${data.instrument.label} session.`);
}));

document.getElementById("simulateTodayBtn").addEventListener("click", () => runAction(async () => {
  const liveConnectForm = document.getElementById("liveConnectForm");
  const formData = new FormData();
  formData.append("client_id", liveConnectForm.elements.client_id.value || "");
  formData.append("access_token", liveConnectForm.elements.access_token.value || "");
  await postForm("/api/simulation/today", formData);
}));

document.getElementById("instrumentModeForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
    const formData = new FormData(form);
    await postForm("/api/instrument-mode", formData);
  });
});

document.getElementById("stepBtn").addEventListener("click", () => runAction(async () => {
  const formData = new FormData();
  formData.append("steps", "1");
  const data = await fetchJson("/api/simulation/step", { method: "POST", body: formData });
  renderState(data);
}));

document.getElementById("runFiveBtn").addEventListener("click", () => runAction(async () => {
  const formData = new FormData();
  formData.append("steps", "5");
  const data = await fetchJson("/api/simulation/step", { method: "POST", body: formData });
  renderState(data);
}));

document.getElementById("candleUploadForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
  const formData = new FormData(form);
  await postForm("/api/upload/candles", formData);
  form.reset();
  });
});

document.getElementById("rulebookUploadForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const fileInput = form.elements.file;
  runAction(async () => {
    if (!fileInput.files || fileInput.files.length === 0) {
      throw new Error("Choose a .txt, .text, .md, or .pdf file first.");
    }
    setLastAction("pending", "Uploading rulebook", `Sending ${fileInput.files[0].name} to the backend...`);
    const formData = new FormData(form);
    await postForm("/api/upload/rulebook", formData);
    form.reset();
  });
});

document.getElementById("noteLearnForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
  const formData = new FormData(form);
  await postForm("/api/learn/text", formData);
  });
});

document.getElementById("liveConnectForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
  const formData = new FormData(form);
  await postForm("/api/live/connect", formData);
  });
});

document.getElementById("credentialSaveForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
  const formData = new FormData(form);
  await postForm("/api/settings/credentials", formData);
  form.elements.access_token.value = "";
  form.elements.openai_api_key.value = "";
  form.elements.deepseek_api_key.value = "";
  });
});

document.getElementById("disconnectLiveBtn").addEventListener("click", () => runAction(async () => {
  const data = await fetchJson("/api/live/disconnect", { method: "POST" });
  renderState(data.state);
  setToast(data.message);
}));

document.getElementById("syncHistoryBtn").addEventListener("click", () => runAction(async () => {
  const liveConnectForm = document.getElementById("liveConnectForm");
  const formData = new FormData();
  formData.append("client_id", liveConnectForm.elements.client_id.value || "");
  formData.append("access_token", liveConnectForm.elements.access_token.value || "");
  await postForm("/api/live/sync-history", formData);
}));

refreshState().catch((error) => setToast(error.message));
refreshAiHealth().catch(() => {});
window.setInterval(() => {
  refreshState().catch(() => {});
  refreshAiHealth().catch(() => {});
}, 3000);
