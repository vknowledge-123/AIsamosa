const stateUrl = "/api/state";
const browserSettingsStorageKey = "dhan-trader-ui-settings-v1";

const elements = {
  instrumentValue: document.getElementById("instrumentValue"),
  modeValue: document.getElementById("modeValue"),
  operatingModeValue: document.getElementById("operatingModeValue"),
  aiValue: document.getElementById("aiValue"),
  progressValue: document.getElementById("progressValue"),
  balanceValue: document.getElementById("balanceValue"),
  realizedValue: document.getElementById("realizedValue"),
  unrealizedValue: document.getElementById("unrealizedValue"),
  integratedPnlValue: document.getElementById("integratedPnlValue"),
  integratedPnlDetail: document.getElementById("integratedPnlDetail"),
  integratedPnlMaxValue: document.getElementById("integratedPnlMaxValue"),
  integratedPnlMaxTime: document.getElementById("integratedPnlMaxTime"),
  integratedPnlMinValue: document.getElementById("integratedPnlMinValue"),
  integratedPnlMinTime: document.getElementById("integratedPnlMinTime"),
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
  executionEnabledValue: document.getElementById("executionEnabledValue"),
  executionStatusValue: document.getElementById("executionStatusValue"),
  executionErrorValue: document.getElementById("executionErrorValue"),
  syncStatusValue: document.getElementById("syncStatusValue"),
  operationJobValue: document.getElementById("operationJobValue"),
  operationJobMessage: document.getElementById("operationJobMessage"),
  syncMessageValue: document.getElementById("syncMessageValue"),
  syncPreviousValue: document.getElementById("syncPreviousValue"),
  syncIntradayValue: document.getElementById("syncIntradayValue"),
  syncTotalValue: document.getElementById("syncTotalValue"),
  syncReplayDayValue: document.getElementById("syncReplayDayValue"),
  syncPreviousContextDayValue: document.getElementById("syncPreviousContextDayValue"),
  syncOpenValue: document.getElementById("syncOpenValue"),
  syncUpdatedValue: document.getElementById("syncUpdatedValue"),
  savedClientIdValue: document.getElementById("savedClientIdValue"),
  savedDhanCredentialMessage: document.getElementById("savedDhanCredentialMessage"),
  savedDhanTokenValue: document.getElementById("savedDhanTokenValue"),
  savedOpenAIApiKeyValue: document.getElementById("savedOpenAIApiKeyValue"),
  savedOpenAIModelValue: document.getElementById("savedOpenAIModelValue"),
  savedDeepSeekApiKeyValue: document.getElementById("savedDeepSeekApiKeyValue"),
  savedDeepSeekModelValue: document.getElementById("savedDeepSeekModelValue"),
  savedFullAIProviderValue: document.getElementById("savedFullAIProviderValue"),
  savedOperatingModeValue: document.getElementById("savedOperatingModeValue"),
  savedNiftyLotsValue: document.getElementById("savedNiftyLotsValue"),
  savedStockCapitalValue: document.getElementById("savedStockCapitalValue"),
  savedExpiryPreferenceValue: document.getElementById("savedExpiryPreferenceValue"),
  savedNiftyOptionTradeModeValue: document.getElementById("savedNiftyOptionTradeModeValue"),
  savedNiftyTradeBiasValue: document.getElementById("savedNiftyTradeBiasValue"),
  savedStockPartialProfitValue: document.getElementById("savedStockPartialProfitValue"),
  savedStockTrailingStopValue: document.getElementById("savedStockTrailingStopValue"),
  savedStockHeuristicExitValue: document.getElementById("savedStockHeuristicExitValue"),
  savedNiftyTrailingStopValue: document.getElementById("savedNiftyTrailingStopValue"),
  savedNiftyHeuristicExitValue: document.getElementById("savedNiftyHeuristicExitValue"),
  savedNiftyCostSlValue: document.getElementById("savedNiftyCostSlValue"),
  savedNiftyCostSlPointsValue: document.getElementById("savedNiftyCostSlPointsValue"),
  savedNiftyMinSlPointsValue: document.getElementById("savedNiftyMinSlPointsValue"),
  savedNiftyMaxSlPointsValue: document.getElementById("savedNiftyMaxSlPointsValue"),
  savedNiftyTargetValue: document.getElementById("savedNiftyTargetValue"),
  savedNiftyTargetPointsValue: document.getElementById("savedNiftyTargetPointsValue"),
  savedPyramidingValue: document.getElementById("savedPyramidingValue"),
  savedIntelligentPyramidingValue: document.getElementById("savedIntelligentPyramidingValue"),
  savedNiftyPointPyramidingValue: document.getElementById("savedNiftyPointPyramidingValue"),
  savedNiftyPointPyramidingPointsValue: document.getElementById("savedNiftyPointPyramidingPointsValue"),
  savedPathValue: document.getElementById("savedPathValue"),
  savedUpdatedValue: document.getElementById("savedUpdatedValue"),
  credentialSaveStatus: document.getElementById("credentialSaveStatus"),
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
  niftyMechanicsBias: document.getElementById("niftyMechanicsBias"),
  niftyMechanicsRisk: document.getElementById("niftyMechanicsRisk"),
  niftyMechanicsSummary: document.getElementById("niftyMechanicsSummary"),
  niftyMechanicsPrevious: document.getElementById("niftyMechanicsPrevious"),
  marketStructureView: document.getElementById("marketStructureView"),
  pendingSetup: document.getElementById("pendingSetup"),
  stockSearchResults: document.getElementById("stockSearchResults"),
  stockWatchlist: document.getElementById("stockWatchlist"),
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

const stockUiState = {
  lastQuery: "",
  searchResults: [],
};

const runtimeUiState = {
  dashboard: null,
  stateRevision: 0,
  stateRefreshInFlight: false,
  aiHealthRefreshInFlight: false,
  statePollTimer: null,
  aiHealthPollTimer: null,
  stateEventSource: null,
  stateEventSourceRetryTimer: null,
};

const settingsUiState = {
  dirtyFields: new Set(),
  liveCredentialDirtyFields: new Set(),
  saveTimer: null,
  saveInFlight: false,
  saveQueued: false,
  lastSerializedPayload: "",
  lastSavedPayload: "",
};

function browserStorage() {
  try {
    return window.localStorage;
  } catch (error) {
    return null;
  }
}

function readBrowserSettings() {
  const storage = browserStorage();
  if (!storage) {
    return {};
  }
  try {
    return JSON.parse(storage.getItem(browserSettingsStorageKey) || "{}");
  } catch (error) {
    return {};
  }
}

function writeBrowserSettings(partialSettings) {
  const storage = browserStorage();
  if (!storage) {
    return;
  }
  const current = readBrowserSettings();
  storage.setItem(browserSettingsStorageKey, JSON.stringify({ ...current, ...partialSettings }));
}

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

function formatCandleSummary(candleRef) {
  if (!candleRef || !candleRef.candle) {
    return "";
  }
  const candle = candleRef.candle;
  const barLabel = typeof candleRef.index === "number" ? `Bar ${candleRef.index + 1}` : "Bar -";
  return `${candleRef.label} | ${formatSignalTime(candle.timestamp)} | ${barLabel} | O ${money(candle.open)} H ${money(candle.high)} L ${money(candle.low)} C ${money(candle.close)} V ${money(candle.volume)}`;
}

function renderHeuristicCandleRefs(candleRefs) {
  if (!Array.isArray(candleRefs) || candleRefs.length === 0) {
    return "<p>Candle Match -</p>";
  }
  return candleRefs
    .map((candleRef) => `<p>${formatCandleSummary(candleRef)}</p>`)
    .join("");
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

function formatIsoDate(value) {
  if (!value) {
    return "-";
  }
  return value;
}

function localIsoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function setHistoricalReplayDefaults() {
  const form = document.getElementById("historicalReplayForm");
  const rangeForm = document.getElementById("historicalRangeReplayForm");
  if (!form) {
    return;
  }
  const replayField = form.elements.replay_date;
  const previousField = form.elements.previous_context_date;
  if (!replayField || !previousField) {
    return;
  }
  if (replayField.value && previousField.value) {
    return;
  }
  const now = new Date();
  const replay = new Date(now);
  replay.setDate(replay.getDate() - 1);
  const previous = new Date(now);
  previous.setDate(previous.getDate() - 2);
  if (!replayField.value) {
    replayField.value = localIsoDate(replay);
  }
  if (!previousField.value) {
    previousField.value = localIsoDate(previous);
  }
  if (rangeForm) {
    const startField = rangeForm.elements.replay_start_date;
    const endField = rangeForm.elements.replay_end_date;
    if (startField && !startField.value) {
      startField.value = localIsoDate(previous);
    }
    if (endField && !endField.value) {
      endField.value = localIsoDate(replay);
    }
  }
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

function setCredentialSaveStatus(message, tone = "neutral") {
  if (!elements.credentialSaveStatus) {
    return;
  }
  elements.credentialSaveStatus.textContent = message;
  elements.credentialSaveStatus.dataset.tone = tone;
}

function syncCredentialField(form, fieldName, value) {
  if (!form || !form.elements[fieldName]) {
    return;
  }
  const field = form.elements[fieldName];
  if (settingsUiState.dirtyFields.has(fieldName) || document.activeElement === field) {
    return;
  }
  if (field.type === "checkbox") {
    field.checked = Boolean(value);
    return;
  }
  if (value == null || value === "") {
    return;
  }
  if (field.value !== value) {
    field.value = value;
  }
}

function setFormFieldValue(form, fieldName, value) {
  if (!form || !form.elements[fieldName]) {
    return;
  }
  const field = form.elements[fieldName];
  if (document.activeElement === field) {
    return;
  }
  if (field.type === "checkbox") {
    field.checked = value === true || value === "true" || value === "1" || value === "on";
    return;
  }
  const normalizedValue = value == null ? "" : String(value);
  if (field.value !== normalizedValue) {
    field.value = normalizedValue;
  }
}

function buildCredentialPayload(form) {
  if (!form) {
    return null;
  }
  const payload = {
    client_id: (form.elements.client_id?.value || "").trim(),
    access_token: (form.elements.access_token?.value || "").trim(),
    openai_api_key: (form.elements.openai_api_key?.value || "").trim(),
    openai_model: (form.elements.openai_model?.value || "").trim(),
    deepseek_api_key: (form.elements.deepseek_api_key?.value || "").trim(),
    deepseek_model: (form.elements.deepseek_model?.value || "").trim(),
    full_ai_provider: (form.elements.full_ai_provider?.value || "").trim(),
    operating_mode: (form.elements.operating_mode?.value || "").trim(),
    nifty_order_lots: (form.elements.nifty_order_lots?.value || "1").trim(),
    stock_trade_capital: (form.elements.stock_trade_capital?.value || "25000").trim(),
    nifty_expiry_preference: (form.elements.nifty_expiry_preference?.value || "current-weekly").trim(),
    stock_partial_profit_enabled: form.elements.stock_partial_profit_enabled?.checked ? "true" : "false",
    stock_trailing_stop_enabled: form.elements.stock_trailing_stop_enabled?.checked ? "true" : "false",
    stock_heuristic_early_exit_enabled: form.elements.stock_heuristic_early_exit_enabled?.checked ? "true" : "false",
    nifty_trailing_stop_enabled: form.elements.nifty_trailing_stop_enabled?.checked ? "true" : "false",
    nifty_heuristic_early_exit_enabled: form.elements.nifty_heuristic_early_exit_enabled?.checked ? "true" : "false",
    nifty_cost_sl_enabled: form.elements.nifty_cost_sl_enabled?.checked ? "true" : "false",
    nifty_cost_sl_points: (form.elements.nifty_cost_sl_points?.value || "35").trim(),
    nifty_min_sl_points: (form.elements.nifty_min_sl_points?.value || "40").trim(),
    nifty_max_sl_points: (form.elements.nifty_max_sl_points?.value || "60").trim(),
    nifty_target_enabled: form.elements.nifty_target_enabled?.checked ? "true" : "false",
    nifty_target_points: (form.elements.nifty_target_points?.value || "90").trim(),
    pyramiding_enabled: form.elements.pyramiding_enabled?.checked ? "true" : "false",
    intelligent_pyramiding_enabled: form.elements.intelligent_pyramiding_enabled?.checked ? "true" : "false",
    nifty_point_pyramiding_enabled: form.elements.nifty_point_pyramiding_enabled?.checked ? "true" : "false",
    nifty_point_pyramiding_points: (form.elements.nifty_point_pyramiding_points?.value || "50").trim(),
    nifty_trade_bias: (form.elements.nifty_trade_bias?.value || "both").trim(),
    nifty_option_trade_mode: (form.elements.nifty_option_trade_mode?.value || "selling").trim(),
  };
  return Object.values(payload).some((value) => value) ? payload : null;
}

function serializeCredentialPayload(payload) {
  if (!payload) {
    return "";
  }
  return JSON.stringify({
    access_token: payload.access_token,
    client_id: payload.client_id,
    deepseek_api_key: payload.deepseek_api_key,
    deepseek_model: payload.deepseek_model,
    full_ai_provider: payload.full_ai_provider,
    nifty_expiry_preference: payload.nifty_expiry_preference,
    nifty_order_lots: payload.nifty_order_lots,
    openai_api_key: payload.openai_api_key,
    openai_model: payload.openai_model,
    operating_mode: payload.operating_mode,
    stock_trade_capital: payload.stock_trade_capital,
    stock_partial_profit_enabled: payload.stock_partial_profit_enabled,
    stock_trailing_stop_enabled: payload.stock_trailing_stop_enabled,
    stock_heuristic_early_exit_enabled: payload.stock_heuristic_early_exit_enabled,
    nifty_trailing_stop_enabled: payload.nifty_trailing_stop_enabled,
    nifty_heuristic_early_exit_enabled: payload.nifty_heuristic_early_exit_enabled,
    nifty_cost_sl_enabled: payload.nifty_cost_sl_enabled,
    nifty_cost_sl_points: payload.nifty_cost_sl_points,
    nifty_min_sl_points: payload.nifty_min_sl_points,
    nifty_max_sl_points: payload.nifty_max_sl_points,
    nifty_target_enabled: payload.nifty_target_enabled,
    nifty_target_points: payload.nifty_target_points,
    pyramiding_enabled: payload.pyramiding_enabled,
    intelligent_pyramiding_enabled: payload.intelligent_pyramiding_enabled,
    nifty_point_pyramiding_enabled: payload.nifty_point_pyramiding_enabled,
    nifty_point_pyramiding_points: payload.nifty_point_pyramiding_points,
    nifty_trade_bias: payload.nifty_trade_bias,
    nifty_option_trade_mode: payload.nifty_option_trade_mode,
  });
}

function persistBrowserSettingsFromForms() {
  const credentialSaveForm = document.getElementById("credentialSaveForm");
  const liveConnectForm = document.getElementById("liveConnectForm");
  const instrumentModeForm = document.getElementById("instrumentModeForm");
  writeBrowserSettings({
    credentialPayload: buildCredentialPayload(credentialSaveForm) || {},
    liveConnect: {
      client_id: (liveConnectForm?.elements.client_id?.value || "").trim(),
      access_token: (liveConnectForm?.elements.access_token?.value || "").trim(),
    },
    instrumentMode: instrumentModeForm?.elements.instrument_mode?.value || "",
  });
}

function restoreBrowserSettings() {
  const cached = readBrowserSettings();
  const credentialSaveForm = document.getElementById("credentialSaveForm");
  const liveConnectForm = document.getElementById("liveConnectForm");
  const instrumentModeForm = document.getElementById("instrumentModeForm");
  const credentialPayload = cached.credentialPayload || {};
  Object.entries(credentialPayload).forEach(([fieldName, value]) => {
    setFormFieldValue(credentialSaveForm, fieldName, value);
  });
  if (cached.liveConnect) {
    setFormFieldValue(liveConnectForm, "client_id", cached.liveConnect.client_id || "");
    setFormFieldValue(liveConnectForm, "access_token", cached.liveConnect.access_token || "");
  }
  if (cached.instrumentMode) {
    setFormFieldValue(instrumentModeForm, "instrument_mode", cached.instrumentMode);
  }
}

function syncLiveCredentialsFromSavedForm() {
  const credentialSaveForm = document.getElementById("credentialSaveForm");
  const liveConnectForm = document.getElementById("liveConnectForm");
  if (!credentialSaveForm || !liveConnectForm) {
    return;
  }
  const savedClientId = (credentialSaveForm.elements.client_id?.value || "").trim();
  const savedAccessToken = (credentialSaveForm.elements.access_token?.value || "").trim();
  if (
    savedClientId
    && !liveConnectForm.elements.client_id.value.trim()
    && !settingsUiState.liveCredentialDirtyFields.has("client_id")
    && document.activeElement !== liveConnectForm.elements.client_id
  ) {
    liveConnectForm.elements.client_id.value = savedClientId;
  }
  if (
    savedAccessToken
    && !liveConnectForm.elements.access_token.value.trim()
    && !settingsUiState.liveCredentialDirtyFields.has("access_token")
    && document.activeElement !== liveConnectForm.elements.access_token
  ) {
    liveConnectForm.elements.access_token.value = savedAccessToken;
  }
}

function mirrorLiveCredentialsIntoSavedForm() {
  const credentialSaveForm = document.getElementById("credentialSaveForm");
  const liveConnectForm = document.getElementById("liveConnectForm");
  if (!credentialSaveForm || !liveConnectForm) {
    return;
  }
  const clientId = (liveConnectForm.elements.client_id?.value || "").trim();
  const accessToken = (liveConnectForm.elements.access_token?.value || "").trim();
  if (clientId) {
    credentialSaveForm.elements.client_id.value = clientId;
  }
  if (accessToken) {
    credentialSaveForm.elements.access_token.value = accessToken;
  }
}

function currentDhanCredentials() {
  const liveConnectForm = document.getElementById("liveConnectForm");
  const credentialSaveForm = document.getElementById("credentialSaveForm");
  const clientId = (liveConnectForm?.elements.client_id?.value || credentialSaveForm?.elements.client_id?.value || "").trim();
  const accessToken = (liveConnectForm?.elements.access_token?.value || credentialSaveForm?.elements.access_token?.value || "").trim();
  return {
    client_id: clientId,
    access_token: accessToken,
  };
}

function noteLiveCredentialEdit(fieldName) {
  settingsUiState.liveCredentialDirtyFields.add(fieldName);
}

async function saveCredentialSettings({ immediate = false, notify = false } = {}) {
  const form = document.getElementById("credentialSaveForm");
  const payload = buildCredentialPayload(form);
  if (!payload) {
    setCredentialSaveStatus("Autosave idle. Changes save automatically.");
    return;
  }
  const serialized = serializeCredentialPayload(payload);
  if (!immediate && !settingsUiState.dirtyFields.size) {
    return;
  }
  if (!immediate && serialized === settingsUiState.lastSavedPayload) {
    settingsUiState.dirtyFields.clear();
    setCredentialSaveStatus("Saved locally. Autosave is active.", "saved");
    return;
  }
  if (!immediate && serialized === settingsUiState.lastSerializedPayload) {
    return;
  }
  if (settingsUiState.saveInFlight) {
    settingsUiState.saveQueued = true;
    return;
  }

  settingsUiState.saveInFlight = true;
  settingsUiState.lastSerializedPayload = serialized;
  setCredentialSaveStatus("Saving settings...", "saving");

  const formData = new FormData();
  Object.entries(payload).forEach(([key, value]) => {
    formData.append(key, value);
  });

  try {
    const data = await fetchJson("/api/settings/credentials", { method: "POST", body: formData });
    settingsUiState.lastSavedPayload = serialized;
    settingsUiState.liveCredentialDirtyFields.clear();
    syncLiveCredentialsFromSavedForm();
    persistBrowserSettingsFromForms();
    if (data.state) {
      renderState(data.state);
    }
    if (serialized === serializeCredentialPayload(buildCredentialPayload(form))) {
      settingsUiState.dirtyFields.clear();
    }
    setCredentialSaveStatus("Saved locally. Autosave is active.", "saved");
    if (notify && data.message) {
      setLastAction("success", "Settings saved", data.message);
      setToast(data.message);
    }
  } catch (error) {
    settingsUiState.saveQueued = true;
    setCredentialSaveStatus(error.message || "Unable to save settings.", "error");
    if (notify) {
      throw error;
    }
  } finally {
    settingsUiState.saveInFlight = false;
    if (settingsUiState.saveQueued) {
      settingsUiState.saveQueued = false;
      window.clearTimeout(settingsUiState.saveTimer);
      settingsUiState.saveTimer = window.setTimeout(() => {
        saveCredentialSettings({ immediate: true }).catch(() => {});
      }, 250);
    }
  }
}

function scheduleCredentialAutosave(fieldName) {
  settingsUiState.dirtyFields.add(fieldName);
  syncLiveCredentialsFromSavedForm();
  persistBrowserSettingsFromForms();
  setCredentialSaveStatus("Unsaved changes. Autosaving shortly...", "dirty");
  window.clearTimeout(settingsUiState.saveTimer);
  settingsUiState.saveTimer = window.setTimeout(() => {
    saveCredentialSettings().catch(() => {});
  }, 500);
}

function nextStatePollMs() {
  if (document.hidden) {
    return 60000;
  }
  if (runtimeUiState.stateEventSource) {
    if (runtimeUiState.dashboard?.live_feed?.connected) {
      return 15000;
    }
    return 30000;
  }
  if (runtimeUiState.dashboard?.live_feed?.connected) {
    return 2500;
  }
  return 5000;
}

function scheduleStatePoll() {
  window.clearTimeout(runtimeUiState.statePollTimer);
  runtimeUiState.statePollTimer = window.setTimeout(() => {
    refreshState().catch(() => {});
  }, nextStatePollMs());
}

function ensureStateStream() {
  if (runtimeUiState.stateEventSource) {
    return;
  }
  const eventSource = new EventSource("/api/state/stream");
  runtimeUiState.stateEventSource = eventSource;
  eventSource.addEventListener("state", (event) => {
    try {
      const payload = JSON.parse(event.data || "{}");
      if ((payload.revision || 0) > (runtimeUiState.stateRevision || 0)) {
        refreshState().catch(() => {});
      }
    } catch (error) {
      console.error("Unable to parse state stream event", error);
    }
  });
  eventSource.onerror = () => {
    eventSource.close();
    if (runtimeUiState.stateEventSource === eventSource) {
      runtimeUiState.stateEventSource = null;
    }
    window.clearTimeout(runtimeUiState.stateEventSourceRetryTimer);
    runtimeUiState.stateEventSourceRetryTimer = window.setTimeout(() => {
      ensureStateStream();
    }, 2000);
  };
}

function scheduleAiHealthPoll() {
  window.clearTimeout(runtimeUiState.aiHealthPollTimer);
  runtimeUiState.aiHealthPollTimer = window.setTimeout(() => {
    refreshAiHealth().catch(() => {});
  }, 15000);
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
    elements.activeTrade.textContent = "No active tracked trade.";
    return;
  }
  const stopLabel = trade.price_mode === "cash" ? "Spot Stop" : "Stop";
  const targetLabel = trade.price_mode === "cash" ? "Spot Target" : "Target";
  const openQty = trade.open_quantity ?? trade.quantity;
  const openPyramidLegs = (trade.pyramid_legs || []).filter((leg) => leg.status === "OPEN" && (leg.open_quantity || 0) > 0);
  const pyramidLegText = openPyramidLegs.length
    ? openPyramidLegs.map((leg) => `#${leg.add_number}: qty ${leg.open_quantity}, stop ${money(leg.invalidation_level)}`).join(" | ")
    : "No open add legs";
  elements.activeTrade.className = "trade-card";
  elements.activeTrade.innerHTML = `
    <strong>${trade.symbol}</strong>
    <p>${trade.status} | ${trade.direction} | ${trade.price_mode === "cash" ? "Cash" : "Option"} | Qty ${trade.quantity} | Open ${openQty} | Closed ${trade.closed_quantity || 0}</p>
    <p>Base Qty ${trade.base_quantity || trade.quantity} | Pyramid Adds ${trade.pyramid_count || 0}/2 | Last Add ${formatIstDateTime(trade.last_pyramid_time)} @ ${money(trade.last_pyramid_price)}</p>
    <p>Add Legs ${pyramidLegText}</p>
    <p>Entry ${money(trade.entry_price)} | Current ${money(trade.current_price)} | ${targetLabel} ${money(trade.target_price)} | ${stopLabel} ${money(trade.stop_price)}</p>
    <p>Spot Entry ${money(trade.entry_spot_price)} | Invalidation ${money(trade.invalidation_level)} | Target Spot ${money(trade.target_spot_price)} | First Target ${money(trade.first_target_price)}</p>
    <p>Setup ${trade.setup_type || "-"} | Score ${trade.setup_score != null ? trade.setup_score.toFixed(1) : "-"} | State ${trade.market_state || "-"}</p>
    <p>Broker ${trade.broker_status || "-"} | Entry Order ${trade.broker_order_id || "-"} | Exit Order ${trade.broker_exit_order_id || "-"} | Product ${trade.broker_product_type || "-"}</p>
    <p>Booked P&L ${money(trade.booked_pnl)} | Live P&L ${money(trade.pnl)} | Partial Exits ${trade.partial_exit_count || 0}</p>
    <p>Entry Time ${formatIstDateTime(trade.entry_time)} | Entry Source ${trade.entry_quote_source || "-"} | Entry Quote ${formatIstDateTime(trade.entry_quote_time)}</p>
    <p>Latest Quote ${formatIstDateTime(trade.current_quote_time)} | Current Source ${trade.current_quote_source || "-"} | Exit Time ${formatIstDateTime(trade.exit_time)}</p>
    <p>${trade.broker_status_message || trade.notes || "No notes"}</p>
  `;
}

function tradeEntryDateLabel(trade) {
  const parsed = toIstDate(trade.entry_time);
  if (!parsed) {
    return "-";
  }
  return parsed.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function renderTradeHistoryList(trades) {
  elements.tradeHistory.innerHTML = "";
  if (!trades || trades.length === 0) {
    elements.tradeHistory.innerHTML = `<div class="list-item empty-state">No items yet.</div>`;
    return;
  }
  let currentDate = null;
  trades.forEach((trade) => {
    const dateLabel = tradeEntryDateLabel(trade);
    if (dateLabel !== currentDate) {
      currentDate = dateLabel;
      elements.tradeHistory.insertAdjacentHTML("beforeend", `
        <div class="list-item">
          <strong>${dateLabel}</strong>
          <span class="pill">Replay Day</span>
        </div>
      `);
    }
    elements.tradeHistory.insertAdjacentHTML("beforeend", `
      <div class="list-item">
        <strong>${trade.symbol}</strong>
        <p>${trade.status} | ${trade.direction} | ${trade.price_mode === "cash" ? "Cash" : "Option"} | Entry ${money(trade.entry_price)} | Exit ${money(trade.exit_price)}</p>
        <p>Setup ${trade.setup_type || "-"} | Score ${trade.setup_score != null ? trade.setup_score.toFixed(1) : "-"} | State ${trade.market_state || "-"}</p>
        <p>Total Qty ${trade.quantity} | Base Qty ${trade.base_quantity || trade.quantity} | Open Qty ${trade.open_quantity ?? 0} | Closed Qty ${trade.closed_quantity || 0} | Pyramid Adds ${trade.pyramid_count || 0}/2 | Booked P&amp;L ${money(trade.booked_pnl)}</p>
        <p>Add Legs ${(trade.pyramid_legs || []).map((leg) => `#${leg.add_number} ${leg.status} qty ${leg.open_quantity}/${leg.quantity} stop ${money(leg.invalidation_level)}`).join(" | ") || "No add legs"}</p>
        <p>Target ${money(trade.target_price)} | Stop ${money(trade.stop_price)} | Invalidation ${money(trade.invalidation_level)} | First Target ${money(trade.first_target_price)}</p>
        <p>Broker ${trade.broker_status || "-"} | Entry Order ${trade.broker_order_id || "-"} | Exit Order ${trade.broker_exit_order_id || "-"} | Product ${trade.broker_product_type || "-"}</p>
        <p>Entry Time ${formatIstDateTime(trade.entry_time)} | Exit Time ${formatIstDateTime(trade.exit_time)} | Latest Quote ${formatIstDateTime(trade.current_quote_time)}</p>
        <p>Entry Source ${trade.entry_quote_source || "-"} | Current Source ${trade.current_quote_source || "-"} | Exit Source ${trade.exit_quote_source || "-"}</p>
        <p>Entry Spot ${money(trade.entry_spot_price)} | Target Spot ${money(trade.target_spot_price)} | Trade Security ${trade.trade_security_id || trade.option_security_id || "-"}</p>
        <p>Entry Logic ${trade.entry_notes || trade.notes || "No entry notes"}</p>
        <p>Exit Logic ${trade.exit_notes || (trade.status === "OPEN" ? "Trade is still open." : trade.notes || "No exit notes")}</p>
        <p>P&amp;L ${money(trade.pnl)} | ${trade.broker_status_message || "No broker message"}</p>
      </div>
    `);
  });
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

function renderStockSearchResults(state) {
  if (!elements.stockSearchResults) {
    return;
  }
  if (state.instrument.mode !== "stock") {
    elements.stockSearchResults.innerHTML = `<div class="list-item empty-state">Switch to stock mode to search the NSE stock universe.</div>`;
    return;
  }
  renderList(elements.stockSearchResults, stockUiState.searchResults, (item) => `
    <div class="list-item">
      <strong>${item.symbol}</strong>
      <p>${item.label || item.symbol}</p>
      <p>Security ${item.security_id || "resolve on add"}</p>
      <div class="button-row">
        <button type="button" class="secondary-btn stock-add-btn" data-symbol="${item.symbol}">Add & Select</button>
      </div>
    </div>
  `);
}

function renderStockWatchlist(state) {
  if (!elements.stockWatchlist) {
    return;
  }
  if (state.instrument.mode !== "stock") {
    elements.stockWatchlist.innerHTML = `<div class="list-item empty-state">Switch to stock mode to manage a live stock watchlist.</div>`;
    return;
  }
  if (!state.stock_watchlist || state.stock_watchlist.length === 0) {
    elements.stockWatchlist.innerHTML = `<div class="list-item empty-state">No stocks in the watchlist. Search and add a stock to start a chart.</div>`;
    return;
  }
  renderList(elements.stockWatchlist, state.stock_watchlist || [], (item) => `
    <div class="list-item">
      <strong>${item.symbol}</strong>
      <span class="pill">${item.selected ? "active" : (item.subscribed ? "subscribed" : "queued")}</span>
      <span class="pill">${item.trade_bias === "long" ? "long-only" : (item.trade_bias === "short" ? "short-only" : "both-side")}</span>
      <p>${item.label || item.symbol} | Security ${item.security_id}</p>
      <p>LTP ${money(item.last_ltp)} | Ticks ${item.ticks_received || 0} | Last Tick ${formatSignalTime(item.last_tick_at)}</p>
      <p>History ${item.history_status || "idle"} | ${item.previous_day_candles || 0} previous day | ${item.intraday_candles || 0} intraday</p>
      <p>5m Turnover ${item.last_5m_turnover != null ? `${(item.last_5m_turnover / 10000000).toFixed(2)} Cr` : "-"} | ${item.last_5m_turnover_passed == null ? "waiting" : (item.last_5m_turnover_passed ? "pass" : "below 3 Cr")} | ${formatSignalTime(item.last_5m_turnover_start)}-${formatSignalTime(item.last_5m_turnover_end)}</p>
      <p>Heuristic ${item.decision_action || "NO_DATA"} | Confidence ${item.decision_confidence != null ? `${Math.round(item.decision_confidence * 100)}%` : "-"}</p>
      <p>${item.decision_reason || "No heuristic analysis yet for this stock session."}</p>
      <p>Trades ${item.trade_count || 0} (${item.closed_trade_count || 0} closed) | Last ${item.last_trade_status || "-"} | Realized ${money(item.realized_pnl)}</p>
      <p>Trade ${item.has_active_trade ? `${item.active_trade_direction || "-"} | P&L ${money(item.active_trade_pnl)}` : "No active trade"}</p>
      <p>Broker ${item.live_order_error ? `Error: ${item.live_order_error}` : (item.live_order_message || "No live order activity")} | Update ${formatIstDateTime(item.live_order_updated_at)}</p>
      <div class="button-row">
        <button type="button" class="${item.selected ? "" : "secondary-btn"} stock-select-btn" data-symbol="${item.symbol}">${item.selected ? "Active Stock" : "View Details"}</button>
        <button type="button" class="secondary-btn stock-remove-btn" data-symbol="${item.symbol}">Remove</button>
      </div>
    </div>
  `);
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
  runtimeUiState.dashboard = state;
  runtimeUiState.stateRevision = state.state_revision || 0;
  elements.instrumentValue.textContent = state.instrument.label;
  elements.modeValue.textContent = state.mode;
  elements.operatingModeValue.textContent = state.operating_mode;
  elements.aiValue.textContent = state.credentials.full_ai_provider || "openai";
  elements.progressValue.textContent = `${state.current_index + 1} / ${state.total_candles}`;
  elements.balanceValue.textContent = money(state.balance);
  elements.realizedValue.textContent = money(state.realized_pnl);
  elements.unrealizedValue.textContent = money(state.unrealized_pnl);
  elements.integratedPnlValue.textContent = money(state.integrated_pnl?.total_pnl);
  elements.integratedPnlDetail.textContent = `Realized ${money(state.integrated_pnl?.realized_pnl)} | Open ${money(state.integrated_pnl?.unrealized_pnl)}`;
  elements.integratedPnlMaxValue.textContent = money(state.integrated_pnl?.max_total_pnl);
  elements.integratedPnlMaxTime.textContent = formatIstDateTime(state.integrated_pnl?.max_total_pnl_at);
  elements.integratedPnlMinValue.textContent = money(state.integrated_pnl?.min_total_pnl);
  elements.integratedPnlMinTime.textContent = formatIstDateTime(state.integrated_pnl?.min_total_pnl_at);
  elements.liveStatusValue.textContent = state.live_feed.status;
  elements.liveSecurityValue.textContent = state.live_feed.security_id;
  elements.liveLtpValue.textContent = money(state.live_feed.last_ltp);
  elements.liveTicksValue.textContent = state.live_feed.ticks_received;
  elements.liveErrorValue.textContent = state.live_feed.status_message || state.live_feed.error || "None";
  elements.executionEnabledValue.textContent = state.execution?.live_trading_enabled ? "armed" : "disabled";
  elements.executionStatusValue.innerHTML = state.execution
    ? `${state.execution.order_updates_status || "disconnected"} | Update ${formatIstDateTime(state.execution.last_order_update_at)}${state.execution.last_order_message ? `<br>${state.execution.last_order_message}` : ""}${state.execution.order_updates_message ? `<br>${state.execution.order_updates_message}` : ""}`
    : "Order updates disconnected.";
  elements.executionErrorValue.textContent = state.execution?.last_order_error
    ? `Last order error${state.execution.last_order_symbol ? ` (${state.execution.last_order_symbol})` : ""}: ${state.execution.last_order_error} | ${formatIstDateTime(state.execution.last_order_error_at)}`
    : "No live order error.";
  elements.instrumentNote.textContent = state.instrument.mode === "stock"
    ? `The stock watchlist currently targets ${state.stock_watchlist.length || 0} NSE cash symbol(s). ${state.instrument.label} is the active heuristic chart on security ID ${state.instrument.security_id}. Real orders trigger only when live trading is armed.`
    : `The live feed subscribes to Dhan security ID ${state.instrument.security_id} for ${state.instrument.label}. Real orders trigger only when live trading is armed.`;
  elements.decisionOptionLabel.textContent = state.instrument.supports_options ? "Option" : "Bias";
  const connectLiveBtn = document.getElementById("connectLiveBtn");
  const disconnectLiveBtn = document.getElementById("disconnectLiveBtn");
  const liveFeedBusy = ["connecting", "connected", "reconnecting"].includes(state.live_feed.status);
  connectLiveBtn.disabled = liveFeedBusy;
  disconnectLiveBtn.disabled = state.live_feed.status === "disconnected";
  if (state.live_feed.status === "connected") {
    connectLiveBtn.textContent = `Live Connected ${state.instrument.label}`;
  } else if (state.live_feed.status === "reconnecting") {
    connectLiveBtn.textContent = `Reconnecting ${state.instrument.label}`;
  } else if (state.live_feed.status === "connecting") {
    connectLiveBtn.textContent = `Connecting ${state.instrument.label}`;
  } else {
    connectLiveBtn.textContent = `Connect Live ${state.instrument.label}`;
  }
  document.getElementById("simulateTodayBtn").textContent = `Start ${state.instrument.label} Today Simulation`;
  document.getElementById("startTradingBtn").textContent = state.execution?.live_trading_enabled ? "Trading Armed" : "Start Trading";
  elements.syncStatusValue.textContent = `${state.data_sync.status} (${state.data_sync.source})`;
  elements.operationJobValue.textContent = `${state.operation_job?.job_type || "idle"} (${state.operation_job?.status || "idle"})`;
  elements.operationJobMessage.textContent = state.operation_job?.message || "No background sync or replay job is running.";
  elements.syncMessageValue.textContent = state.data_sync.message || "No Dhan chart sync yet.";
  elements.syncPreviousValue.textContent = state.data_sync.previous_day_candles || 0;
  elements.syncIntradayValue.textContent = state.data_sync.intraday_candles || 0;
  elements.syncTotalValue.textContent = state.data_sync.total_loaded || 0;
  elements.syncReplayDayValue.textContent = formatIsoDate(state.data_sync.replay_session_day);
  elements.syncPreviousContextDayValue.textContent = formatIsoDate(state.data_sync.previous_context_day);
  elements.syncOpenValue.textContent = state.data_sync.has_live_open_candle ? "Yes" : "No";
  elements.syncUpdatedValue.textContent = state.data_sync.last_synced_at
    ? new Date(state.data_sync.last_synced_at).toLocaleString()
    : "-";
  const resolvedClientId = state.credentials.resolved_client_id || state.credentials.client_id || "";
  elements.savedClientIdValue.textContent = resolvedClientId || "Not saved";
  elements.savedDhanCredentialMessage.textContent = state.credentials.dhan_credential_message || "No credential warnings.";
  elements.savedDhanTokenValue.textContent = state.credentials.dhan_access_token_saved ? "Saved locally" : "Not saved";
  elements.savedOpenAIApiKeyValue.textContent = state.credentials.openai_api_key_saved ? "Saved locally" : "Not saved";
  elements.savedOpenAIModelValue.textContent = state.credentials.openai_model || "gpt-5.4-mini";
  elements.savedDeepSeekApiKeyValue.textContent = state.credentials.deepseek_api_key_saved ? "Saved locally" : "Not saved";
  elements.savedDeepSeekModelValue.textContent = state.credentials.deepseek_model || "deepseek-v4-flash";
  elements.savedFullAIProviderValue.textContent = state.credentials.full_ai_provider || "openai";
  elements.savedOperatingModeValue.textContent = state.credentials.operating_mode || "full-ai";
  elements.savedNiftyLotsValue.textContent = state.credentials.nifty_order_lots || 1;
  elements.savedStockCapitalValue.textContent = money(state.credentials.stock_trade_capital);
  elements.savedExpiryPreferenceValue.textContent = state.credentials.nifty_expiry_preference || "current-weekly";
  elements.savedNiftyOptionTradeModeValue.textContent = state.credentials.nifty_option_trade_mode || "selling";
  elements.savedNiftyTradeBiasValue.textContent = state.credentials.nifty_trade_bias || "both";
  elements.savedStockPartialProfitValue.textContent = state.credentials.stock_partial_profit_enabled ? "Enabled" : "Disabled";
  elements.savedStockTrailingStopValue.textContent = state.credentials.stock_trailing_stop_enabled ? "Enabled" : "Disabled";
  elements.savedStockHeuristicExitValue.textContent = state.credentials.stock_heuristic_early_exit_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyTrailingStopValue.textContent = state.credentials.nifty_trailing_stop_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyHeuristicExitValue.textContent = state.credentials.nifty_heuristic_early_exit_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyCostSlValue.textContent = state.credentials.nifty_cost_sl_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyCostSlPointsValue.textContent = money(state.credentials.nifty_cost_sl_points);
  elements.savedNiftyMinSlPointsValue.textContent = money(state.credentials.nifty_min_sl_points);
  elements.savedNiftyMaxSlPointsValue.textContent = money(state.credentials.nifty_max_sl_points);
  elements.savedNiftyTargetValue.textContent = state.credentials.nifty_target_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyTargetPointsValue.textContent = money(state.credentials.nifty_target_points);
  elements.savedPyramidingValue.textContent = state.credentials.pyramiding_enabled ? "Enabled" : "Disabled";
  elements.savedIntelligentPyramidingValue.textContent = state.credentials.intelligent_pyramiding_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyPointPyramidingValue.textContent = state.credentials.nifty_point_pyramiding_enabled ? "Enabled" : "Disabled";
  elements.savedNiftyPointPyramidingPointsValue.textContent = money(state.credentials.nifty_point_pyramiding_points);
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
  if (elements.marketStructureView) {
    elements.marketStructureView.textContent = state.market_structure || "No market structure loaded yet.";
  }
  const mechanics = state.nifty_market_mechanics || {};
  if (elements.niftyMechanicsBias) {
    elements.niftyMechanicsBias.textContent = `${mechanics.trade_bias || "neutral"} | ${mechanics.expected_behavior || "wait_for_structure"}`;
    elements.niftyMechanicsRisk.textContent = mechanics.risk_mode || "normal";
    elements.niftyMechanicsSummary.textContent = mechanics.summary || "No NIFTY market mechanics profile yet.";
    elements.niftyMechanicsPrevious.textContent = `Previous day ${mechanics.previous_day_profile || "-"} | Last 2h ${mechanics.last_2h_flow || "-"} | Open ${mechanics.open_type || "-"} | Gap ${money(mechanics.gap_points)}`;
  }
  renderStockSearchResults(state);
  renderStockWatchlist(state);

  const liquidityItems = [
    ...(state.liquidity_zones || []).map((zone) => ({ kind: "zone", ...zone })),
    ...(state.liquidity_ledger || [])
      .filter((entry) => entry.status !== "untouched")
      .slice(0, 8)
      .map((entry) => ({ kind: "ledger", ...entry })),
  ];
  renderList(elements.liquidityZones, liquidityItems, (item) => {
    if (item.kind === "ledger") {
      return `
        <div class="list-item">
          <strong>${item.level_label}</strong>
          <span class="pill">${item.status}</span>
          <p>${item.window_label} | ${item.side} | Level ${money(item.level)} | Strength ${Math.round((item.strength || 0) * 100)}%</p>
          <p>${item.notes}</p>
        </div>
      `;
    }
    return `
      <div class="list-item">
        <strong>${item.label}</strong>
        <span class="pill">${item.zone_type}</span>
        <p>Price ${money(item.price)} | Upper ${money(item.upper)} | Lower ${money(item.lower)}</p>
        <p>${item.notes}</p>
      </div>
    `;
  });

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

  renderTradeHistoryList(state.trade_history || []);

  renderList(elements.heuristicNarrative, state.heuristic_narrative, (event) => `
    <div class="list-item">
      <strong>${event.title}</strong>
      <span class="pill">${event.status || event.event_type}</span>
      <p>${formatSignalTime(event.timestamp)} | ${event.direction || "-"}</p>
      <p>Matched ${event.matched_level_label || "-"} | Level ${event.matched_level_price != null ? money(event.matched_level_price) : "-"}</p>
      <p>Price ${money(event.price)} | ${event.detail || "No detail."}</p>
      ${renderHeuristicCandleRefs(event.candle_refs)}
    </div>
  `);

  renderList(elements.heuristicTrace, state.heuristic_trace, (entry) => `
    <div class="list-item">
      <strong>${entry.title}</strong>
      <span class="pill">${entry.status || entry.market_state || entry.event_type}</span>
      <p>${formatSignalTime(entry.timestamp)} | Action ${entry.action || "-"} | Score ${entry.setup_score != null ? entry.setup_score.toFixed(1) : "-"}</p>
      <p>${entry.setup_type || "No setup"} | ${entry.option_type || "-"} | Confidence ${entry.confidence != null ? Math.round(entry.confidence * 100) : "-"}%</p>
      <p>Trigger ${entry.trigger_price != null ? money(entry.trigger_price) : "-"} | Invalidation ${entry.invalidation_level != null ? money(entry.invalidation_level) : "-"}</p>
      <p>Matched ${entry.matched_level_label || "-"} | Level ${entry.matched_level_price != null ? money(entry.matched_level_price) : "-"}</p>
      <p>${entry.block_reason || entry.market_state || "-"}</p>
      <p>${entry.detail || "No detail."}</p>
      ${renderHeuristicCandleRefs(entry.candle_refs)}
    </div>
  `);

  elements.rulebookView.textContent = state.rulebook;

  const liveConnectForm = document.getElementById("liveConnectForm");
  if (liveConnectForm) {
    if (state.credentials.client_id && !liveConnectForm.elements.client_id.value) {
      liveConnectForm.elements.client_id.value = state.credentials.client_id;
    }
    syncLiveCredentialsFromSavedForm();
  }
  const instrumentModeForm = document.getElementById("instrumentModeForm");
  if (instrumentModeForm) {
    instrumentModeForm.elements.instrument_mode.value = state.instrument.mode;
  }

  const credentialSaveForm = document.getElementById("credentialSaveForm");
  if (credentialSaveForm) {
    syncCredentialField(credentialSaveForm, "client_id", state.credentials.client_id || "");
    syncCredentialField(credentialSaveForm, "openai_model", state.credentials.openai_model || "");
    syncCredentialField(credentialSaveForm, "deepseek_model", state.credentials.deepseek_model || "");
    syncCredentialField(credentialSaveForm, "full_ai_provider", state.credentials.full_ai_provider || "");
    syncCredentialField(credentialSaveForm, "operating_mode", state.credentials.operating_mode || "");
    syncCredentialField(credentialSaveForm, "nifty_order_lots", String(state.credentials.nifty_order_lots || 1));
    syncCredentialField(credentialSaveForm, "stock_trade_capital", String(state.credentials.stock_trade_capital || 25000));
    syncCredentialField(credentialSaveForm, "nifty_expiry_preference", state.credentials.nifty_expiry_preference || "current-weekly");
    syncCredentialField(credentialSaveForm, "nifty_option_trade_mode", state.credentials.nifty_option_trade_mode || "selling");
    syncCredentialField(credentialSaveForm, "stock_partial_profit_enabled", state.credentials.stock_partial_profit_enabled !== false);
    syncCredentialField(credentialSaveForm, "stock_trailing_stop_enabled", state.credentials.stock_trailing_stop_enabled !== false);
    syncCredentialField(credentialSaveForm, "stock_heuristic_early_exit_enabled", state.credentials.stock_heuristic_early_exit_enabled !== false);
    syncCredentialField(credentialSaveForm, "nifty_trailing_stop_enabled", state.credentials.nifty_trailing_stop_enabled !== false);
    syncCredentialField(credentialSaveForm, "nifty_heuristic_early_exit_enabled", state.credentials.nifty_heuristic_early_exit_enabled !== false);
    syncCredentialField(credentialSaveForm, "nifty_cost_sl_enabled", state.credentials.nifty_cost_sl_enabled === true);
    syncCredentialField(credentialSaveForm, "nifty_cost_sl_points", String(state.credentials.nifty_cost_sl_points ?? 35));
    syncCredentialField(credentialSaveForm, "nifty_min_sl_points", String(state.credentials.nifty_min_sl_points ?? 40));
    syncCredentialField(credentialSaveForm, "nifty_max_sl_points", String(state.credentials.nifty_max_sl_points ?? 60));
    syncCredentialField(credentialSaveForm, "nifty_target_enabled", state.credentials.nifty_target_enabled === true);
    syncCredentialField(credentialSaveForm, "nifty_target_points", String(state.credentials.nifty_target_points ?? 90));
    syncCredentialField(credentialSaveForm, "pyramiding_enabled", state.credentials.pyramiding_enabled === true);
    syncCredentialField(credentialSaveForm, "intelligent_pyramiding_enabled", state.credentials.intelligent_pyramiding_enabled === true);
    syncCredentialField(credentialSaveForm, "nifty_point_pyramiding_enabled", state.credentials.nifty_point_pyramiding_enabled === true);
    syncCredentialField(credentialSaveForm, "nifty_point_pyramiding_points", String(state.credentials.nifty_point_pyramiding_points ?? 50));
    syncCredentialField(credentialSaveForm, "nifty_trade_bias", state.credentials.nifty_trade_bias || "both");
    if (!settingsUiState.dirtyFields.size && !settingsUiState.saveInFlight) {
      setCredentialSaveStatus("Saved locally. Autosave is active.", "saved");
    }
  }
  persistBrowserSettingsFromForms();

  renderList(elements.learningLog, state.learning_log, (item) => `
    <div class="list-item">
      <strong>Update</strong>
      <p>${item}</p>
    </div>
  `);

  drawChart(state);
}

async function refreshState() {
  if (runtimeUiState.stateRefreshInFlight) {
    return;
  }
  runtimeUiState.stateRefreshInFlight = true;
  try {
    const headers = {};
    if (runtimeUiState.stateRevision) {
      headers["If-None-Match"] = `W/"state-${runtimeUiState.stateRevision}"`;
    }
    const response = await fetch(stateUrl, { headers });
    if (response.status === 304) {
      return;
    }
    const state = await response.json();
    if (!response.ok) {
      throw new Error(state.detail || "Request failed");
    }
    renderState(state);
  } finally {
    runtimeUiState.stateRefreshInFlight = false;
    scheduleStatePoll();
  }
}

async function searchStocks(query) {
  stockUiState.lastQuery = query || "";
  const payload = await fetchJson(`/api/stocks/search?q=${encodeURIComponent(stockUiState.lastQuery)}&limit=20`);
  stockUiState.searchResults = payload.results || [];
  const state = runtimeUiState.dashboard || await fetchJson(stateUrl);
  renderState(state);
}

async function addStock(symbol) {
  const formData = new FormData();
  formData.append("symbol", symbol);
  await postForm("/api/stocks/watchlist/add", formData);
}

async function addBulkStocks(bulkText, tradeBias = "both") {
  const formData = new FormData();
  formData.append("bulk_text", bulkText);
  formData.append("trade_bias", tradeBias);
  await postForm("/api/stocks/watchlist/bulk-add", formData);
}

async function selectStock(symbol) {
  const formData = new FormData();
  formData.append("symbol", symbol);
  await postForm("/api/stocks/watchlist/select", formData);
}

async function removeStock(symbol) {
  const formData = new FormData();
  formData.append("symbol", symbol);
  await postForm("/api/stocks/watchlist/remove", formData);
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
  if (runtimeUiState.aiHealthRefreshInFlight) {
    return;
  }
  runtimeUiState.aiHealthRefreshInFlight = true;
  try {
    const health = await fetchJson("/api/health/ai");
    elements.aiStatusValue.textContent = health.reachable ? `Configured (${health.provider})` : "Not configured";
    elements.aiModelStatusValue.textContent = health.model_available ? `Yes (${health.model})` : `No (${health.model})`;
    elements.aiHealthMessage.textContent = health.message || "No health message.";
  } catch (error) {
    elements.aiStatusValue.textContent = "Unknown";
    elements.aiModelStatusValue.textContent = "Unknown";
    elements.aiHealthMessage.textContent = error.message || "Unable to load AI health.";
  } finally {
    runtimeUiState.aiHealthRefreshInFlight = false;
    scheduleAiHealthPoll();
  }
}

document.getElementById("loadSampleBtn").addEventListener("click", () => runAction(async () => {
  const data = await fetchJson("/api/simulation/load-sample", { method: "POST" });
  renderState(data);
  setToast(`Loaded sample ${data.instrument.label} session.`);
}));

document.getElementById("simulateTodayBtn").addEventListener("click", () => runAction(async () => {
  const replayForm = document.getElementById("historicalReplayForm");
  const credentials = currentDhanCredentials();
  const formData = new FormData();
  formData.append("client_id", credentials.client_id);
  formData.append("access_token", credentials.access_token);
  formData.append("decision_duration_minutes", replayForm.elements.decision_duration_minutes.value || "5");
  formData.append("stock_replay_scope", replayForm.elements.stock_replay_scope?.value || "active");
  await postForm("/api/simulation/today/start", formData);
}));

document.getElementById("historicalReplayForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
    const credentials = currentDhanCredentials();
    const formData = new FormData();
    formData.append("client_id", credentials.client_id);
    formData.append("access_token", credentials.access_token);
    formData.append("replay_date", form.elements.replay_date.value || "");
    formData.append("previous_context_date", form.elements.previous_context_date.value || "");
    formData.append("decision_duration_minutes", form.elements.decision_duration_minutes.value || "5");
    formData.append("stock_replay_scope", form.elements.stock_replay_scope?.value || "active");
    await postForm("/api/simulation/historical/start", formData);
  });
});

document.getElementById("historicalRangeReplayForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const replayForm = document.getElementById("historicalReplayForm");
  runAction(async () => {
    const credentials = currentDhanCredentials();
    const formData = new FormData();
    formData.append("client_id", credentials.client_id);
    formData.append("access_token", credentials.access_token);
    formData.append("replay_start_date", form.elements.replay_start_date.value || "");
    formData.append("replay_end_date", form.elements.replay_end_date.value || "");
    formData.append("decision_duration_minutes", replayForm.elements.decision_duration_minutes.value || "5");
    await postForm("/api/simulation/historical-range/start", formData);
  });
});

document.getElementById("instrumentModeForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
    persistBrowserSettingsFromForms();
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
  runAction(async () => {
  mirrorLiveCredentialsIntoSavedForm();
  persistBrowserSettingsFromForms();
  const credentials = currentDhanCredentials();
  const formData = new FormData();
  formData.append("client_id", credentials.client_id);
  formData.append("access_token", credentials.access_token);
  await postForm("/api/live/connect", formData);
  });
});

document.getElementById("credentialSaveForm").addEventListener("submit", (event) => {
  event.preventDefault();
  runAction(async () => {
    await saveCredentialSettings({ immediate: true, notify: true });
  });
});

document.querySelectorAll("#credentialSaveForm input, #credentialSaveForm select").forEach((field) => {
  const eventName = field.tagName === "SELECT" ? "change" : "input";
  field.addEventListener(eventName, () => {
    scheduleCredentialAutosave(field.name);
  });
});

document.querySelectorAll("#liveConnectForm input").forEach((field) => {
  field.addEventListener("input", () => {
    noteLiveCredentialEdit(field.name);
    mirrorLiveCredentialsIntoSavedForm();
    scheduleCredentialAutosave(field.name);
  });
  field.addEventListener("blur", () => {
    const value = (field.value || "").trim();
    const savedForm = document.getElementById("credentialSaveForm");
    const savedValue = (savedForm?.elements[field.name]?.value || "").trim();
    if (value && value === savedValue) {
      settingsUiState.liveCredentialDirtyFields.delete(field.name);
    }
  });
});

document.getElementById("instrumentModeForm").elements.instrument_mode.addEventListener("change", () => {
  const instrumentModeForm = document.getElementById("instrumentModeForm");
  persistBrowserSettingsFromForms();
  runAction(async () => {
    const formData = new FormData(instrumentModeForm);
    await postForm("/api/instrument-mode", formData);
  });
});

document.getElementById("disconnectLiveBtn").addEventListener("click", () => runAction(async () => {
  const data = await fetchJson("/api/live/disconnect", { method: "POST" });
  renderState(data.state);
  setToast(data.message);
}));

document.getElementById("startTradingBtn").addEventListener("click", () => runAction(async () => {
  const data = await fetchJson("/api/trading/start", { method: "POST" });
  renderState(data.state);
  setToast(data.message);
}));

document.getElementById("squareOffBtn").addEventListener("click", () => runAction(async () => {
  const data = await fetchJson("/api/trading/square-off", { method: "POST" });
  renderState(data.state);
  setToast(data.message);
}));

document.getElementById("syncHistoryBtn").addEventListener("click", () => runAction(async () => {
  const credentials = currentDhanCredentials();
  const formData = new FormData();
  formData.append("client_id", credentials.client_id);
  formData.append("access_token", credentials.access_token);
  await postForm("/api/live/sync-history/start", formData);
}));

document.getElementById("stockSearchForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  runAction(async () => {
    await searchStocks(form.elements.query.value || "");
  });
});

document.querySelectorAll(".stock-bulk-form").forEach((bulkForm) => {
  bulkForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    runAction(async () => {
      const bulkText = (form.elements.bulk_text?.value || "").trim();
      await addBulkStocks(bulkText, form.dataset.tradeBias || "both");
      form.reset();
    });
  });
});

document.addEventListener("click", (event) => {
  const addButton = event.target.closest(".stock-add-btn");
  if (addButton) {
    runAction(async () => {
      await addStock(addButton.dataset.symbol || "");
    });
    return;
  }
  const selectButton = event.target.closest(".stock-select-btn");
  if (selectButton) {
    runAction(async () => {
      await selectStock(selectButton.dataset.symbol || "");
    });
    return;
  }
  const removeButton = event.target.closest(".stock-remove-btn");
  if (removeButton) {
    runAction(async () => {
      await removeStock(removeButton.dataset.symbol || "");
    });
  }
});

restoreBrowserSettings();
syncLiveCredentialsFromSavedForm();
refreshState().catch((error) => setToast(error.message));
refreshAiHealth().catch(() => {});
setHistoricalReplayDefaults();
ensureStateStream();
