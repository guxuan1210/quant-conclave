// --- Safe JSON parse for SSE events ---
function safeJSON(str) {
  if (!str || str.trim() === "") return null;
  try { return JSON.parse(str); } catch(e) { return null; }
}

// --- Debug: catch early errors ---
window.onerror = function(msg, url, line) {
  var el = document.getElementById("js-error-banner");
  if (!el) {
    el = document.createElement("div");
    el.id = "js-error-banner";
    el.style.cssText = "background:#ffebe9;color:#cf222e;padding:12px 16px;margin:8px 0;border-radius:6px;border:1px solid #cf222e;font-size:13px;font-family:monospace;white-space:pre-wrap;";
    var closeBtn = document.createElement("span");
    closeBtn.textContent = "✖";
    closeBtn.style.cssText = "float:right;cursor:pointer;font-weight:bold;font-size:16px;margin-left:8px;";
    closeBtn.onclick = function() { el.style.display = "none"; };
    el.appendChild(closeBtn);
    var msgSpan = document.createElement("span");
    msgSpan.textContent = "JS Error: " + msg + " (line " + line + ")";
    el.appendChild(msgSpan);
    document.body.insertBefore(el, document.body.firstChild);
  } else {
    el.style.display = "block";
    var msgSpan = el.querySelector("span");
    if (msgSpan) msgSpan.textContent = "JS Error: " + msg + " (line " + line + ")";
  }
};
console.log("app.js loaded, starting init...");

// --- State ---
let selectedTicker = null;
let activeStage = null;
let eventSource = null;
let pipelineStart = 0;
let sessionId = null;
let chartKline = null, chartVolume = null, chartMacd = null, chartRsi = null;
let candleSeries = null, volumeSeries = null;
let ma5Series = null, ma20Series = null;
let macdDiffSeries = null, macdDeaSeries = null, macdHistSeries = null;
let rsiSeries = null;
let manipulationRisks = {};
let downloadBlobUrl = null;   // track Blob URL for cleanup

// --- Verdict helpers (UI chrome) ---
// Verdict words are BOTH logical keys and display text: the backend always
// returns the raw Chinese value ("看多"/"看空"/"观望"), so color/icon/text must
// be derived from the RAW value — never from a localized display string, or the
// `.includes("看多")` color checks would silently break in English mode.
var VERDICT_KEYS  = { "看多": "verdict.bull", "看空": "verdict.bear", "观望": "verdict.hold" };
var VERDICT_COLOR = { "看多": "var(--green)", "看空": "var(--red)", "观望": "#888" };
function verdictText(rawV) { return t(VERDICT_KEYS[rawV] || "verdict.hold"); }
function verdictIcon(rawV) { return rawV === "看多" ? "🟢" : (rawV === "看空" ? "🔴" : "🟡"); }
function verdictDisplay(rawV) { return verdictIcon(rawV) + " " + verdictText(rawV); }
// Fallback verdict from analysis text: the prompt mandates the analysis to open
// with "结论：", so judge from that line only. A full-text scan picks up negated
// mentions like "不可将全口径数字等同于主力看多" and flips a 观望 to 看多
// (000933 08-21). Mirrors backend web/watchlist_store.py extract_verdict.
function verdictFromText(txt) {
  if (!txt) return "观望";
  var first = (txt.split("\n")[0] || "");
  var probe = (first.indexOf("结论：") >= 0) ? first : txt;
  if (probe.indexOf("看多") >= 0) return "看多";
  if (probe.indexOf("看空") >= 0) return "看空";
  return "观望";
}

// --- DOM refs ---
const tickerInput = document.getElementById("ticker-input");
const searchDropdown = document.getElementById("search-dropdown");
const selectedCard = document.getElementById("selected-ticker");
const selectedSymbol = document.getElementById("selected-symbol");
const selectedName = document.getElementById("selected-name");
const selectedExchange = document.getElementById("selected-exchange");
const clearTickerBtn = document.getElementById("clear-ticker");
const dateInput = document.getElementById("date-input");
const dateError = document.getElementById("date-error");
const analystToggles = document.getElementById("analyst-toggles");
const runBtn = document.getElementById("run-btn");
const resetBtn = document.getElementById("reset-btn");
const resultsContainer = document.getElementById("results-container");
const progressList = document.getElementById("progress-list");
const languageSelect = document.getElementById("language-select");
const toast = document.getElementById("toast");
const chartContainer = document.getElementById("chart-container");
const chartElKline = document.getElementById("chart-kline");
const chartElVolume = document.getElementById("chart-volume");
const chartElMacd = document.getElementById("chart-macd");
const chartElRsi = document.getElementById("chart-rsi");
const chartTitle = document.getElementById("chart-title");
const periodBtns = document.querySelectorAll(".period-btn");
const cp1Toggle = document.getElementById("checkpoint-1-toggle");
const cp2Toggle = document.getElementById("checkpoint-2-toggle");
const deepProviderSelect = document.getElementById("deep-provider-select");
const quickProviderSelect = document.getElementById("quick-provider-select");
const deepModelSelect = document.getElementById("deep-model-select");
const quickModelSelect = document.getElementById("quick-model-select");
const backendUrlInput = document.getElementById("backend-url-input");
const proxyUrlInput = document.getElementById("proxy-url-input");
const refreshDeepBtn = document.getElementById("refresh-deep-models");
const refreshQuickBtn = document.getElementById("refresh-quick-models");

// --- Model Catalog ---
let modelCatalog = null;  // { providers: [...] }
// Global provider fallback from /api/config. When a role's provider is
// "跟随默认" (empty), that role uses this value.
let defaultProvider = "";

async function loadModelCatalog() {
  try {
    var resp = await fetch("/api/models");
    modelCatalog = await resp.json();
    populateProviders();
  } catch (e) {
    console.error("Failed to load model catalog:", e);
  }
}

function populateProviders() {
  if (!modelCatalog || !modelCatalog.providers || !modelCatalog.providers.length) {
    if (deepProviderSelect) deepProviderSelect.innerHTML = '<option value="">No providers available</option>';
    if (quickProviderSelect) quickProviderSelect.innerHTML = '<option value="">No providers available</option>';
    return;
  }

  // Per-role provider dropdowns: first option = "跟随默认" (empty → .env llm_provider)
  var roleHtml = '<option value="">' + t("common.followDefault") + '</option>';
  modelCatalog.providers.forEach(function(p) {
    roleHtml += '<option value="' + esc(p.key) + '">' + esc(p.label) + '</option>';
  });
  if (deepProviderSelect) deepProviderSelect.innerHTML = roleHtml;
  if (quickProviderSelect) quickProviderSelect.innerHTML = roleHtml;

  // Load config to set initial values
  fetch("/api/config").then(function(r) { return r.json(); }).then(function(cfg) {
    defaultProvider = cfg.llm_provider || "";
    // Per-role provider overrides (raw from server; empty → 跟随默认)
    if (deepProviderSelect && cfg.deep_think_provider) deepProviderSelect.value = cfg.deep_think_provider;
    if (quickProviderSelect && cfg.quick_think_provider) quickProviderSelect.value = cfg.quick_think_provider;
    // Repopulate role model dropdowns per effective provider, then apply stored models
    if (deepProviderSelect) onRoleProviderChange("deep");
    if (quickProviderSelect) onRoleProviderChange("quick");
    if (cfg.deep_think_llm && deepModelSelect.querySelector('option[value="' + cfg.deep_think_llm + '"]')) {
      deepModelSelect.value = cfg.deep_think_llm;
    }
    if (cfg.quick_think_llm && quickModelSelect.querySelector('option[value="' + cfg.quick_think_llm + '"]')) {
      quickModelSelect.value = cfg.quick_think_llm;
    }
    if (cfg.backend_url && !backendUrlInput.value) {
      backendUrlInput.value = cfg.backend_url;
    }
    if (cfg.proxy && proxyUrlInput && !proxyUrlInput.value) {
      proxyUrlInput.value = cfg.proxy;
    }
  }).catch(function() {});
}

// Re-populate a role's model dropdown when its per-role provider changes.
// role in ("deep", "quick"). Empty role provider → use the .env default provider.
function onRoleProviderChange(role) {
  var roleProvider = role === "deep" ? deepProviderSelect.value : quickProviderSelect.value;
  var selectEl = role === "deep" ? deepModelSelect : quickModelSelect;
  var refreshBtn = role === "deep" ? refreshDeepBtn : refreshQuickBtn;
  if (!selectEl || !modelCatalog) return;
  var effective = roleProvider || defaultProvider;
  var p = modelCatalog.providers.find(function(x) { return x.key === effective; });
  if (!p) return;
  populateModelDropdown(selectEl, p[role + "_models"], role);
  var isOllama = effective === "ollama";
  if (refreshBtn) refreshBtn.classList.toggle("hidden", !isOllama);
  if (isOllama) {
    var defOpt = selectEl.querySelector('option[value="qwen3.5:latest"]');
    if (defOpt) selectEl.value = "qwen3.5:latest";
  }
  updateBackendUrlHint();
}

if (deepProviderSelect) deepProviderSelect.addEventListener("change", function() { onRoleProviderChange("deep"); });
if (quickProviderSelect) quickProviderSelect.addEventListener("change", function() { onRoleProviderChange("quick"); });

// backend_url is a single global value shared by both roles (no per-role URLs).
// If either role's effective provider is Ollama, the shared URL points at Ollama.
function updateBackendUrlHint() {
  if (!backendUrlInput) return;
  var deepEffective = deepProviderSelect ? (deepProviderSelect.value || defaultProvider) : defaultProvider;
  var quickEffective = quickProviderSelect ? (quickProviderSelect.value || defaultProvider) : defaultProvider;
  var anyOllama = deepEffective === "ollama" || quickEffective === "ollama";
  if (anyOllama) {
    if (!backendUrlInput.value) {
      fetch("/api/config").then(function(r){return r.json()}).then(function(cfg){
        if (!backendUrlInput.value && cfg.backend_url) {
          backendUrlInput.value = cfg.backend_url;
        }
      });
    }
    if (!backendUrlInput.value) {
      backendUrlInput.placeholder = "http://localhost:11434/v1 (Ollama default)";
    }
  } else {
    // Clear any Ollama URL when all providers are cloud
    backendUrlInput.value = "";
    backendUrlInput.placeholder = "auto-detected from provider";
  }
}

function populateModelDropdown(selectEl, models, mode) {
  selectEl.innerHTML = "";
  if (!models || !models.length) {
    selectEl.innerHTML = '<option value="">-- No models available --</option>';
    return;
  }
  models.forEach(function(m) {
    var opt = document.createElement("option");
    opt.value = m.value;
    opt.textContent = m.label;
    if (m.value === "custom") opt.textContent = m.label;
    selectEl.appendChild(opt);
  });
}

// --- Ollama Model Refresh ---
async function refreshOllamaModels() {
  var baseUrl = backendUrlInput ? (backendUrlInput.value.trim() || "") : "";
  if (!baseUrl) {
    try {
      var cfgResp = await fetch("/api/config");
      var cfg = await cfgResp.json();
      baseUrl = cfg.backend_url || "";
    } catch(e) {}
  }
  if (!baseUrl) baseUrl = "http://localhost:11434/v1";
  if (refreshDeepBtn) { refreshDeepBtn.classList.add("spinning"); refreshDeepBtn.disabled = true; }
  if (refreshQuickBtn) { refreshQuickBtn.classList.add("spinning"); refreshQuickBtn.disabled = true; }

  try {
    var resp = await fetch("/api/ollama/models?base_url=" + encodeURIComponent(baseUrl));
    var data = await resp.json();
    if (data.error) {
      showToast(data.error);
      return;
    }
    if (!data.models || !data.models.length) {
      showToast("No models found on Ollama at " + (data.host || baseUrl) + ". Pull models with 'ollama pull <model>' first.");
      return;
    }
    populateModelDropdown(deepModelSelect, data.models, "deep");
    populateModelDropdown(quickModelSelect, data.models, "quick");
    showToast("Loaded " + data.models.length + " model(s) from Ollama");
  } catch (e) {
    console.error("Failed to refresh Ollama models:", e);
    showToast("Failed to connect to Ollama. Make sure it is running.");
  } finally {
    if (refreshDeepBtn) { refreshDeepBtn.classList.remove("spinning"); refreshDeepBtn.disabled = false; }
    if (refreshQuickBtn) { refreshQuickBtn.classList.remove("spinning"); refreshQuickBtn.disabled = false; }
  }
}

if (refreshDeepBtn) refreshDeepBtn.addEventListener("click", refreshOllamaModels);
if (refreshQuickBtn) refreshQuickBtn.addEventListener("click", refreshOllamaModels);

// --- Stock Search ---
let searchTimer = null;
tickerInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = tickerInput.value.trim();
  if (q.length < 1) { searchDropdown.classList.add("hidden"); return; }
  searchTimer = setTimeout(() => searchStocks(q), 300);
});

async function searchStocks(q) {
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const results = await resp.json();
    renderSearchResults(results);
  } catch {
    searchDropdown.innerHTML = '<div class="dropdown-item"><span class="no-results">Search unavailable</span></div>';
    searchDropdown.classList.remove("hidden");
  }
}

function renderSearchResults(items) {
  if (!items.length) {
    searchDropdown.innerHTML = '<div class="dropdown-item"><span class="no-results">No results found</span></div>';
  } else {
    searchDropdown.innerHTML = items.map((r, i) =>
      `<div class="dropdown-item" data-idx="${i}">
        <span class="sym">${esc(r.symbol)}</span>
        <span class="name">${esc(r.name)}</span>
        <span class="exch">${esc(r.exchange)}</span>
      </div>`
    ).join("");
    searchDropdown.querySelectorAll(".dropdown-item[data-idx]").forEach(el => {
      el.addEventListener("click", () => selectTicker(items[parseInt(el.dataset.idx)]));
    });
  }
  searchDropdown.classList.remove("hidden");
}

function selectTicker(item) {
  selectedTicker = item;
  tickerInput.value = "";
  searchDropdown.classList.add("hidden");
  selectedSymbol.textContent = item.symbol;
  selectedName.textContent = item.name;
  selectedExchange.textContent = item.exchange;
  selectedCard.classList.remove("hidden");
  validateForm();
  loadChart(item.symbol, "max");
}

clearTickerBtn.addEventListener("click", () => {
  selectedTicker = null;
  selectedCard.classList.add("hidden");
  validateForm();
});

document.addEventListener("click", (e) => {
  if (!searchDropdown.contains(e.target) && e.target !== tickerInput) {
    searchDropdown.classList.add("hidden");
  }
});

// --- Analyst Toggles ---
analystToggles.querySelectorAll(".toggle").forEach(btn => {
  btn.addEventListener("click", () => {
    const toggles = analystToggles.querySelectorAll(".toggle.active");
    if (toggles.length === 1 && btn.classList.contains("active")) return;
    btn.classList.toggle("active");
  });
});

function getSelectedAnalysts() {
  return [...analystToggles.querySelectorAll(".toggle.active")]
    .map(b => b.dataset.analyst);
}

// --- Date Validation ---
dateInput.addEventListener("input", validateForm);

function validateForm() {
  var valid = selectedTicker && /^\d{4}-\d{2}-\d{2}$/.test(dateInput.value.trim());
  runBtn.disabled = !valid;
  if (dateInput.value && !/^\d{4}-\d{2}-\d{2}$/.test(dateInput.value.trim())) {
    dateInput.classList.add("invalid");
    dateError.classList.remove("hidden");
  } else {
    dateInput.classList.remove("invalid");
    dateError.classList.add("hidden");
  }
}

// --- Chart (4-pane layout with toggle switches) ---
function _chartOptions(h) {
  return {
    width: (chartElKline && chartElKline.clientWidth) || 800,
    height: h, autoSize: true,
    layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#1f2328" },
    grid: { vertLines: { color: "#e8eaed" }, horzLines: { color: "#e8eaed" } },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: "#d0d7de" },
    timeScale: { borderColor: "#d0d7de", timeVisible: false },
  };
}

function initCharts() {
  if (chartKline) return;
  if (typeof LightweightCharts === "undefined") return;
  try {
    chartKline = LightweightCharts.createChart(chartElKline, _chartOptions(260));
    candleSeries = chartKline.addCandlestickSeries({
      upColor: "#ef5350", downColor: "#26a69a",
      borderUpColor: "#ef5350", borderDownColor: "#26a69a",
      wickUpColor: "#ef5350", wickDownColor: "#26a69a", priceScaleId: "right",
    });
    ma5Series = chartKline.addLineSeries({ color: "#f5a623", lineWidth: 1, priceScaleId: "right", lastValueVisible: false });
    ma20Series = chartKline.addLineSeries({ color: "#8250df", lineWidth: 1, priceScaleId: "right", lastValueVisible: false });

    chartVolume = LightweightCharts.createChart(chartElVolume, _chartOptions(80));
    volumeSeries = chartVolume.addHistogramSeries({ priceFormat: { type: "volume" } });
    chartVolume.timeScale().applyOptions({ visible: false });

    chartMacd = LightweightCharts.createChart(chartElMacd, _chartOptions(100));
    macdHistSeries = chartMacd.addHistogramSeries({ priceFormat: { type: "volume" } });
    macdDiffSeries = chartMacd.addLineSeries({ color: "#0969da", lineWidth: 1, lastValueVisible: false });
    macdDeaSeries = chartMacd.addLineSeries({ color: "#f0883e", lineWidth: 1, lastValueVisible: false });
    chartMacd.timeScale().applyOptions({ visible: false });

    chartRsi = LightweightCharts.createChart(chartElRsi, _chartOptions(80));
    rsiSeries = chartRsi.addLineSeries({ color: "#8250df", lineWidth: 1.5, lastValueVisible: false });
    chartRsi.addLineSeries({ color: "#d0d7de", lineWidth: 1, lineStyle: 2, lastValueVisible: false })
      .setData([{ time: "2000-01-01", value: 70 }, { time: "2099-01-01", value: 70 }]);
    chartRsi.addLineSeries({ color: "#d0d7de", lineWidth: 1, lineStyle: 2, lastValueVisible: false })
      .setData([{ time: "2000-01-01", value: 30 }, { time: "2099-01-01", value: 30 }]);
    chartRsi.timeScale().applyOptions({ visible: false });

    // Sync time scales across ALL charts (bidirectional)
    var _allCharts = [chartKline, chartVolume, chartMacd, chartRsi].filter(Boolean);
    var _syncing = false;
    function _syncAllFrom(sourceChart) {
      if (!sourceChart || _syncing) return;
      _syncing = true;
      // Get visible range as logical bar indices (works across chart types)
      var range = sourceChart.timeScale().getVisibleLogicalRange();
      if (range) {
        for (var ci = 0; ci < _allCharts.length; ci++) {
          if (_allCharts[ci] !== sourceChart) {
            _allCharts[ci].timeScale().setVisibleLogicalRange(range);
          }
        }
      }
      _syncing = false;
    }
    // Hide sub-chart time scales (show only on K-line)
    [chartVolume, chartMacd, chartRsi].forEach(function(c) {
      if (c) c.timeScale().applyOptions({ visible: false });
    });
    // Subscribe K-line time scale changes -> sync to all sub-charts
    chartKline.timeScale().subscribeVisibleLogicalRangeChange(function(r) {
      if (r && !_syncing) _syncAllFrom(chartKline);
    });
    // Also subscribe sub-charts: when user scrolls on them -> sync K-line first, then all
    [chartVolume, chartMacd, chartRsi].forEach(function(sub) {
      if (!sub) return;
      sub.timeScale().subscribeVisibleLogicalRangeChange(function(r) {
        if (r && !_syncing) {
          _syncing = true;
          var sr = sub.timeScale().getVisibleLogicalRange();
          if (sr) {
            chartKline.timeScale().setVisibleLogicalRange(sr);
          }
          // Use microtask to let K-line's setVisibleLogicalRange settle,
          // then sync all sub-charts from the updated K-line position
          Promise.resolve().then(function() {
            _syncing = false;
            _syncAllFrom(chartKline);
          });
        }
      });
    });
    chartKline.timeScale().applyOptions({ timeVisible: true });

    // Toggle buttons
    var header = document.querySelector(".chart-header");
    if (header && !document.getElementById("indicator-toggles")) {
      var d = document.createElement("div");
      d.id = "indicator-toggles";
      d.style.cssText = "display:flex;gap:4px;margin-left:12px;";
      [{id:"vol",label:"VOL",el:chartElVolume},{id:"macd",label:"MACD",el:chartElMacd},{id:"rsi",label:"RSI",el:chartElRsi}].forEach(function(k) {
        var b = document.createElement("button");
        b.className = "indicator-toggle";
        b.style.cssText = "padding:2px 8px;font-size:10px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--accent);color:#fff;";
        b.textContent = k.label;
        b.addEventListener("click", function() {
          k.visible = !k.visible;
          k.el.style.display = k.visible ? "" : "none";
          b.style.background = k.visible ? "var(--accent)" : "var(--bg)";
          b.style.color = k.visible ? "#fff" : "var(--text)";
        });
        k.visible = true;
        d.appendChild(b);
      });
      header.appendChild(d);
    }
  } catch(e) { console.error("Chart init failed:", e); chartKline = null; }
}

periodBtns.forEach(function(btn) {
  btn.addEventListener("click", function() {
    periodBtns.forEach(function(b) { b.classList.remove("active"); });
    btn.classList.add("active");
    if (selectedTicker) loadChart(selectedTicker.symbol, btn.dataset.period);
  });
});

async function loadChart(ticker, period) {
  chartContainer.style.display = "";
  chartContainer.classList.remove("hidden");
  // Delay chart init to allow browser reflow after removing display:none
  if (!chartKline) {
    await new Promise(function(r) { setTimeout(r, 100); });
    initCharts();
  }
  if (!chartKline) { chartTitle.textContent = "Chart init failed — check console"; return; }
  chartTitle.textContent = ticker + " — MA / VOL / MACD / RSI";
  try {
    var resp = await fetch("/api/history/" + encodeURIComponent(ticker) + "/indicators?period=" + period);
    var data = await resp.json();
    if (data.error || !data.candles || !data.candles.length) {
      resp = await fetch("/api/history?ticker=" + encodeURIComponent(ticker) + "&period=" + period);
      data = await resp.json();
    }
    if (data.error || !data.candles || !data.candles.length) return;
    var candles = [], ma5d = [], ma20d = [], vols = [];
    var mh = [], md = [], me = [], rd = [];
    data.candles.forEach(function(c) {
      candles.push({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close });
      ma5d.push({ time: c.time, value: c.ma5 != null ? c.ma5 : null });
      ma20d.push({ time: c.time, value: c.ma20 != null ? c.ma20 : null });
      vols.push({ time: c.time, value: c.volume, color: c.close >= c.open ? "rgba(239,83,80,0.5)" : "rgba(38,166,154,0.5)" });
      mh.push({ time: c.time, value: c.macd != null ? c.macd : null, color: c.macd != null ? (c.macd >= 0 ? "rgba(239,83,80,0.6)" : "rgba(38,166,154,0.6)") : "rgba(38,166,154,0)" });
      md.push({ time: c.time, value: c.dif != null ? c.dif : null });
      me.push({ time: c.time, value: c.dea != null ? c.dea : null });
      rd.push({ time: c.time, value: c.rsi != null ? c.rsi : null });
    });
    candleSeries.setData(candles);
    ma5Series.setData(ma5d);
    ma20Series.setData(ma20d);
    volumeSeries.setData(vols);
    macdHistSeries.setData(mh);
    macdDiffSeries.setData(md);
    macdDeaSeries.setData(me);
    rsiSeries.setData(rd);
    chartKline.timeScale().fitContent();
    // Force resize in case container was hidden during init
    setTimeout(function() {
      if (chartKline) chartKline.resize(chartElKline.clientWidth || 800, 260);
      if (chartVolume) chartVolume.resize(chartElVolume.clientWidth || 800, 80);
      if (chartMacd) chartMacd.resize(chartElMacd.clientWidth || 800, 100);
      if (chartRsi) chartRsi.resize(chartElRsi.clientWidth || 800, 80);
    }, 100);
  } catch (e) { console.error("Chart load failed:", e); }
}
// --- Run Analysis ---
runBtn.addEventListener("click", startAnalysis);
resetBtn.addEventListener("click", resetAll);

function startAnalysis() {
  if (!selectedTicker) return;
  var date = dateInput.value.trim();
  var analysts = getSelectedAnalysts().join(",");
  var language = languageSelect.value;
  var deepProvider = deepProviderSelect ? deepProviderSelect.value : "";
  var quickProvider = quickProviderSelect ? quickProviderSelect.value : "";
  var deepModel = deepModelSelect.value;
  var quickModel = quickModelSelect.value;
  var backendUrl = backendUrlInput.value.trim();
  var proxyUrl = proxyUrlInput ? proxyUrlInput.value.trim() : "";
  // No global "provider" param: roles resolve server-side via deep_provider /
  // quick_provider, falling back to the .env llm_provider when empty.
  var params = new URLSearchParams({
    ticker: selectedTicker.symbol, date: date, analysts: analysts,
    deep_provider: deepProvider, quick_provider: quickProvider,
    deep_model: deepModel, quick_model: quickModel,
    language: language, checkpoint_1: cp1Toggle.checked, checkpoint_2: cp2Toggle.checked,
  });
  if (backendUrl) params.set("backend_url", backendUrl);
  if (proxyUrl) params.set("proxy", proxyUrl);
  var url = `/api/analyze?${params.toString()}`;

  // Abort any in-progress analysis before starting a new one
  if (eventSource) { eventSource.close(); eventSource = null; }
  runBtn.disabled = true;
  resetProgress();
  clearResults();
  pipelineStart = Date.now();

  eventSource = new EventSource(url);

  eventSource.addEventListener("pipeline-start", function(e) {
    var d = safeJSON(e.data) || {};
    sessionId = (typeof d.session_id === 'string' && d.session_id) ? d.session_id : null;
    addResultCard("system", t("analyze.analyzingLine", {ticker: d.ticker, date: d.date, provider: d.provider, deep: d.deep_model, quick: d.quick_model}), "");
  });

  eventSource.addEventListener("stage-start", function(e) {
    var d = safeJSON(e.data) || {};
    activateStage(d.stage, d.elapsed);
  });

  eventSource.addEventListener("stage-complete", function(e) {
    var d = safeJSON(e.data) || {};
    completeStage(d.stage, d.elapsed);
  });

  eventSource.addEventListener("analyst-report", function(e) {
    var d = safeJSON(e.data) || {};
    var risk = d.manipulation_risk || "";
    if (risk) {
      manipulationRisks[d.analyst] = risk;
      updateManipulationSummary();
    }
    addResultCard(d.analyst, d.report, d.elapsed_ms, d.color, risk);
    updateAnalyzeNav();
    updateStageProgress("analysts", d.elapsed_ms);
  });

  if (!eventSource) return;
  eventSource.addEventListener("analyst-progress", function(e) {
      var d = safeJSON(e.data) || {};
      var item = progressList.querySelector('[data-analyst="' + d.analyst + '"]');
      if (!item) return;
      if (d.status === "in_progress") {
        item.querySelector(".stage-icon").textContent = "●";
        item.querySelector(".stage-icon").className = "stage-icon active";
        item.querySelector(".stage-time").textContent = fmtMs(d.elapsed_ms);
        item.style.opacity = "1";
      } else {
        item.querySelector(".stage-icon").textContent = "✓";
        item.querySelector(".stage-icon").className = "stage-icon done";
        item.querySelector(".stage-time").textContent = fmtMs(d.elapsed_ms);
        item.style.opacity = "1";
      }
    });

  eventSource.addEventListener("stage-progress", function(e) {
      var d = safeJSON(e.data) || {};
      if (d.stage === "analysts") {
        var item = progressList.querySelector('[data-stage="analysts"]');
        if (!item) return;
        item.querySelector(".stage-time").textContent = fmtMs(d.elapsed_ms);
        var fill = item.querySelector(".progress-bar-fill");
        if (fill) {
          fill.style.width = d.percent + "%";
        }
      }
    });

  eventSource.addEventListener("debate-round", function(e) {
    var d = safeJSON(e.data) || {};
    addDebateRound(d.stage, d.side, d.round, d.content);
    updateAnalyzeNav();
  });

  eventSource.addEventListener("debate-decision", function(e) {
    var d = safeJSON(e.data) || {};
    addResultCard("decision-" + d.stage, d.decision, null, "#888");
    updateAnalyzeNav();
  });

  eventSource.addEventListener("trader-proposal", function(e) {
    var d = safeJSON(e.data) || {};
    addResultCard("trader", d.proposal, null, "#f0883e");
    updateAnalyzeNav();
  });

  eventSource.addEventListener("final-decision", function(e) {
    var d = safeJSON(e.data) || {};
    addFinalDecision(d.decision, d.rating, d.manipulation_risk);
    updateAnalyzeNav();
  });

  eventSource.addEventListener("interaction-required", function(e) {
    var d = safeJSON(e.data) || {};
    showInteractionDialog(d.checkpoint, d.label, d.question, d.session_id);
  });

  eventSource.addEventListener("pipeline-done", function(e) {
    var d = safeJSON(e.data) || {};
    var errPanel = document.getElementById("pipeline-error-panel");
    if (errPanel) errPanel.style.display = "none";
    completeAllStages(d.total_elapsed_ms);
    if (d.report_md) {
      addDownloadButton(d.report_md);
    }
    runBtn.disabled = false;
    eventSource.close();
    eventSource = null;
  });

  eventSource.addEventListener("chat-ready", function(e) {
    var d = safeJSON(e.data) || {};
    showChatPanel(d.message);
  });

  eventSource.addEventListener("chat-typing", function(e) {
    showChatThinking();
  });

  eventSource.addEventListener("chat-done", function(e) {
    var d = safeJSON(e.data) || {};
    appendChatMessage("pm", d.full_response || "");
  });

  eventSource.addEventListener("chat-timeout", function(e) {
    showToast(t("analyze.chatTimeout"));
    closeChatPanel();
    if (eventSource) { eventSource.close(); eventSource = null; }
  });

  eventSource.addEventListener("pipeline-error", function(e) {
    var d = safeJSON(e.data) || {};
    var errPanel = document.getElementById("pipeline-error-panel");
    if (!errPanel) {
      errPanel = document.createElement("div");
      errPanel.id = "pipeline-error-panel";
      errPanel.style.cssText = "background:#fff0f0;color:#cf222e;padding:14px 16px;margin:8px 0;border-radius:6px;border:2px solid #cf222e;font-size:13px;white-space:pre-wrap;word-break:break-all;";
      var progressSection = document.getElementById("progress-list");
      if (progressSection && progressSection.parentNode) {
        progressSection.parentNode.insertBefore(errPanel, progressSection.nextSibling);
      } else {
        document.body.insertBefore(errPanel, document.body.firstChild);
      }
    }
    errPanel.textContent = t("analyze.pipelineErrorPanel", {stage: d.stage, msg: d.message});
    errPanel.style.display = "block";
    showToast(t("analyze.pipelineErrorToast", {stage: d.stage, msg: d.message}));
    markStageError(d.stage);
    runBtn.disabled = false;
    closeChatPanel();
    if (eventSource) { eventSource.close(); eventSource = null; }
  });

  eventSource.onerror = function() {
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      runBtn.disabled = false;
      closeChatPanel();
    }
  };
}

// --- Progress Helpers ---
function resetProgress() {
  var errPanel = document.getElementById("pipeline-error-panel");
  if (errPanel) errPanel.style.display = "none";
  activeStage = null;
  // Reset stage-level items (with data-stage attribute)
  progressList.querySelectorAll("[data-stage]").forEach(function(item) {
    item.classList.add("dimmed");
    item.querySelector(".stage-icon").textContent = "◌";
    item.querySelector(".stage-icon").className = "stage-icon";
    item.querySelector(".stage-time").textContent = "";
    var bar = item.querySelector(".progress-bar");
    if (bar) bar.remove();
  });
  // Reset analyst sub-items (with data-analyst attribute)
  progressList.querySelectorAll("[data-analyst]").forEach(function(item) {
    item.classList.remove("dimmed");
    item.style.opacity = "0.5";
    item.querySelector(".stage-icon").textContent = "◌";
    item.querySelector(".stage-icon").className = "stage-icon";
    item.querySelector(".stage-time").textContent = "";
  });
}

function activateStage(stageKey, elapsed) {
  var item = progressList.querySelector('[data-stage="' + stageKey + '"]');
  if (!item) return;
  item.classList.remove("dimmed");
  item.querySelector(".stage-icon").textContent = "◉";
  item.querySelector(".stage-icon").className = "stage-icon active";
  item.querySelector(".stage-time").textContent = fmtMs(elapsed);
  if (!item.querySelector(".progress-bar")) {
    var bar = document.createElement("div");
    bar.className = "progress-bar";
    bar.innerHTML = '<div class="progress-bar-fill" style="width:5%"></div>';
    item.appendChild(bar);
  }
  activeStage = stageKey;
}

function updateStageProgress(stageKey, elapsed) {
  var item = progressList.querySelector('[data-stage="' + stageKey + '"]');
  if (!item) return;
  item.querySelector(".stage-time").textContent = fmtMs(elapsed);
  // Progress is now driven by stage-progress events from backend (analyst completion count)
  // This function is kept as a fallback to show elapsed time
}

function completeStage(stageKey, elapsed) {
  var item = progressList.querySelector('[data-stage="' + stageKey + '"]');
  if (!item) return;
  item.querySelector(".stage-icon").textContent = "✓";
  item.querySelector(".stage-icon").className = "stage-icon done";
  item.querySelector(".stage-time").textContent = fmtMs(elapsed);
  var fill = item.querySelector(".progress-bar-fill");
  if (fill) fill.style.width = "100%";
}

function completeAllStages(totalElapsed) {
  progressList.querySelectorAll(".progress-item").forEach(function(item) {
    if (item.querySelector(".stage-icon").textContent === "◌") {
      item.querySelector(".stage-icon").textContent = "✓";
      item.querySelector(".stage-icon").className = "stage-icon done";
    }
    item.querySelector(".stage-time").textContent = fmtMs(totalElapsed);
    item.classList.remove("dimmed");
  });
  var fill = document.querySelector('[data-stage="' + activeStage + '"] .progress-bar-fill');
  if (fill) fill.style.width = "100%";
}

function markStageError(stageKey) {
  var item = progressList.querySelector('[data-stage="' + stageKey + '"]');
  if (!item) return;
  item.querySelector(".stage-icon").textContent = "✗";
  item.querySelector(".stage-icon").className = "stage-icon error";
}

// --- Results Helpers ---
function clearResults() {
  _analyzeNavItems = [];
  clearRightPanel();
}

function getPlaceholder() {
  return document.getElementById("placeholder-hint");
}

var _analyzeNavItems = [];
function updateAnalyzeNav() {
  var existing = document.getElementById("analyze-nav");
  if (existing) existing.remove();
  // Collect all cards: result-card, debate-round, final-decision
  var cards = resultsContainer.querySelectorAll(".result-card, .debate-round, .final-decision");
  var items = [];
  cards.forEach(function(card) {
    var titleEl = card.querySelector(".card-title");
    var id = card.id;
    // For final-decision without id, use its position
    if (!id && card.classList.contains("final-decision")) {
      id = "card-final-decision";
      card.id = id;
    }
    if (titleEl) items.push({ id: id, label: titleEl.textContent });
  });
  if (items.length < 2) return;
  _analyzeNavItems = items;

  var nav = document.createElement("div");
  nav.id = "analyze-nav";
  nav.style.cssText = "display:flex;gap:4px;flex-wrap:wrap;padding:8px 12px;background:var(--panel-bg);border:1px solid var(--border);border-radius:8px;margin-bottom:12px;position:sticky;top:0;z-index:50;";
  items.forEach(function(it) {
    var btn = document.createElement("button");
    btn.className = "analyze-nav-btn";
    btn.style.cssText = "padding:3px 8px;font-size:11px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--bg);color:var(--text);white-space:nowrap;";
    btn.textContent = it.label;
    btn.onclick = function() {
      var el = document.getElementById(it.id);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    nav.appendChild(btn);
  });
  resultsContainer.insertBefore(nav, resultsContainer.firstChild);
}

function addResultCard(id, content, elapsedMs, color, manipulationRisk) {
  var ph = getPlaceholder();
  if (ph) ph.remove();
  var existing = document.getElementById("card-" + id);
  if (existing) existing.remove();
  var elapsedHtml = elapsedMs != null ? '<span class="card-time">' + fmtMs(elapsedMs) + '</span>' : "";
  var riskBadge = "";
  if (manipulationRisk) {
    var riskLower = manipulationRisk.toLowerCase();
    riskBadge = '<span class="manipulation-badge ' + riskLower + '" title="' + esc(t("analyze.manipRiskTitle") + manipulationRisk) + '">' +
      '&#9888; ' + manipulationRisk + '</span>';
  }
  var card = document.createElement("div");
  card.id = "card-" + id;
  card.className = "result-card";
  card.style.borderLeftColor = color || "#888";
  card.innerHTML = '<div class="card-header"><span class="card-title">' + idLabel(id) + '</span>' + riskBadge + elapsedHtml + '</div>' +
    '<div class="card-body markdown-body">' + renderMarkdown(content) + '</div>';
  resultsContainer.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function updateManipulationSummary() {
  var existing = document.getElementById("manipulation-summary");
  if (existing) existing.remove();

  var analystOrder = ["capital_flow", "market", "social", "news", "fundamentals", "competitor", "partner"];
  var hasAny = analystOrder.some(function(k) { return manipulationRisks[k]; });
  if (!hasAny) return;

  var panel = document.createElement("div");
  panel.id = "manipulation-summary";
  panel.className = "manipulation-summary";

  var riskCount = { HIGH: 0, MEDIUM: 0, LOW: 0 };
  analystOrder.forEach(function(k) {
    var r = manipulationRisks[k];
    if (r) riskCount[r] = (riskCount[r] || 0) + 1;
  });

  var overallRisk = "LOW";
  if (riskCount.HIGH >= 3 || (riskCount.HIGH >= 2 && riskCount.MEDIUM >= 2)) {
    overallRisk = "HIGH";
  } else if (riskCount.HIGH >= 1 || riskCount.MEDIUM >= 3) {
    overallRisk = "MEDIUM";
  }

  var html = '<div class="manipulation-summary-header">' +
    '<span class="manipulation-icon">&#9888;</span>' +
    '<span>' + esc(t("analyze.manipHeader")) + '</span>' +
    '<span class="manipulation-overall ' + overallRisk.toLowerCase() + '">' + overallRisk + '</span>' +
    '</div>' +
    '<div class="manipulation-summary-grid">';

  var labels = {
    capital_flow: t("analyze.manipCapitalFlow"), market: t("analyze.manipMarket"), social: t("analyze.manipSocial"), news: t("analyze.manipNews"),
    fundamentals: t("analyze.manipFundamentals"), competitor: t("analyze.manipCompetitor"), partner: t("analyze.manipPartner")
  };

  analystOrder.forEach(function(k) {
    var risk = manipulationRisks[k];
    var riskClass = risk ? risk.toLowerCase() : "unknown";
    var riskText = risk || "—";
    html += '<div class="manipulation-item">' +
      '<span class="manipulation-label">' + labels[k] + '</span>' +
      '<span class="manipulation-risk ' + riskClass + '">' + riskText + '</span>' +
      '</div>';
  });

  html += '</div>';
  panel.innerHTML = html;

  // Insert at the top of results container
  var firstChild = resultsContainer.firstChild;
  if (firstChild) {
    resultsContainer.insertBefore(panel, firstChild);
  } else {
    resultsContainer.appendChild(panel);
  }
}

var _debateRoundCounter = {};
function addDebateRound(stage, side, round, content) {
  var ph = getPlaceholder();
  if (ph) ph.remove();
  var key = stage + "-" + side;
  _debateRoundCounter[key] = (_debateRoundCounter[key] || 0) + 1;
  var el = document.createElement("div");
  el.id = "card-debate-" + key + "-" + _debateRoundCounter[key];
  el.className = "debate-round " + side;
  var sideLabels = {
    bull: t("debate.bull"), bear: t("debate.bear"),
    aggressive: t("debate.aggressive"), conservative: t("debate.conservative"), neutral: t("debate.neutral"),
  };
  var label = (sideLabels[side] || side) + t("debate.round", {n: round});
  el.innerHTML = '<div class="card-header"><span class="card-title">' + label + '</span></div>' +
    '<div class="card-body markdown-body">' + renderMarkdown(content) + '</div>';
  resultsContainer.appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function addFinalDecision(content, rating, manipulationRisk) {
  var ph = getPlaceholder();
  if (ph) ph.remove();
  var ratingClass = (rating || "hold").toLowerCase();
  var riskBadge = "";
  if (manipulationRisk) {
    var riskLower = manipulationRisk.toLowerCase();
    riskBadge = '<span class="manipulation-badge ' + riskLower + '" style="margin-left:8px" title="' + esc(t("analyze.overallRiskTitle") + manipulationRisk) + '">' +
      '&#9888; ' + esc(t("analyze.manipBadge")) + manipulationRisk + '</span>';
  }
  var pmLabel = t("report.decision-portfolio");
  var el = document.createElement("div");
  el.className = "final-decision";
  var downloadHtml = sessionId ? (
    '<div style="margin-top:12px;display:flex;gap:8px;align-items:center;">' +
    '<span style="font-size:11px;color:var(--text-muted);">' + esc(t("report.download")) + '</span>' +
    '<a href="/api/download/' + sessionId + '?format=md" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">MD</a>' +
    '<a href="/api/download/' + sessionId + '?format=pdf" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">PDF</a>' +
    '<a href="/api/download/' + sessionId + '?format=docx" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">DOCX</a>' +
    '</div>') : '';
  el.innerHTML = '<div class="card-header"><span class="card-title">' + esc(pmLabel) + '</span></div>' +
    '<span class="rating-badge ' + ratingClass + '">' + esc(rating || "N/A") + '</span>' + riskBadge +
    '<div class="card-body" style="margin-top:12px">' + renderMarkdown(content) + '</div>' +
    downloadHtml +
    '<button id="discuss-with-pm-btn" class="btn primary" style="margin-top:12px;padding:8px 16px;font-size:13px;">' + esc(t("report.discussPm")) + '</button>';
  resultsContainer.appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });

  // Wire the discuss button
  var discussBtn = document.getElementById("discuss-with-pm-btn");
  if (discussBtn && sessionId) {
    discussBtn.addEventListener("click", function() { discussWithPM(); });
  }

  // Add decision markers to the K-line chart
  addChartMarkers(rating);
}

function addDownloadButton(reportMd) {
  var existing = document.getElementById("download-btn");
  if (existing) existing.remove();
  existing = document.getElementById("download-btn-docx");
  if (existing) existing.remove();
  if (downloadBlobUrl) { URL.revokeObjectURL(downloadBlobUrl); downloadBlobUrl = null; }
  if (!reportMd) return;
  var ticker = selectedTicker ? selectedTicker.symbol : "report";
  var date = dateInput ? dateInput.value.trim() : "";
  var filenameMd = "CapitalRadar_" + ticker + "_" + date + ".md";
  var filenameDocx = "CapitalRadar_" + ticker + "_" + date + ".docx";
  var blob = new Blob([reportMd], { type: "text/markdown;charset=utf-8" });
  downloadBlobUrl = URL.createObjectURL(blob);
  
  // MD button (existing behavior, uses local Blob)
  var btnMd = document.createElement("a");
  btnMd.id = "download-btn";
  btnMd.className = "btn primary";
  btnMd.href = downloadBlobUrl;
  btnMd.download = filenameMd;
  btnMd.textContent = t("common.downloadMd");
  btnMd.style.cssText = "display:inline-block;margin-top:16px;text-decoration:none;text-align:center;background:#0969da;";
  resultsContainer.appendChild(btnMd);
  
  // DOCX button (uses API endpoint)
  var btnDocx = document.createElement("a");
  btnDocx.id = "download-btn-docx";
  btnDocx.className = "btn primary";
  btnDocx.href = "/api/download/" + (sessionId || "") + "?format=docx";
  btnDocx.download = filenameDocx;
  btnDocx.textContent = t("common.downloadDocx");
  btnDocx.style.cssText = "display:inline-block;margin-top:16px;margin-left:8px;text-decoration:none;text-align:center;background:#2da44e;";
  resultsContainer.appendChild(btnDocx);
}

// --- Chart Markers for Final Decision ---
var MARKER_STYLES = {
  buy:        { color: "#1a7f37", shape: "arrowUp",   position: "belowBar", text: "BUY" },
  overweight: { color: "#0969da", shape: "arrowUp",   position: "belowBar", text: "OVERWEIGHT" },
  hold:       { color: "#9a6700", shape: "circle",    position: "inBar",    text: "HOLD" },
  underweight:{ color: "#bc4c00", shape: "arrowDown", position: "aboveBar", text: "UNDERWEIGHT" },
  sell:       { color: "#cf222e", shape: "arrowDown", position: "aboveBar", text: "SELL" },
};

function addChartMarkers(rating) {
  if (!candleSeries) return;
  var normalized = (rating || "hold").toLowerCase();
  var style = MARKER_STYLES[normalized] || MARKER_STYLES["hold"];
  var tradeDate = dateInput.value.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(tradeDate)) return;

  // Remove existing markers and add the new one
  candleSeries.setMarkers([
    {
      time: tradeDate,
      position: style.position,
      color: style.color,
      shape: style.shape,
      text: style.text,
      size: 3,
    }
  ]);

  // Ensure the chart is visible and scrolled to show the marker
  if (chartContainer.classList.contains("hidden")) {
    chartContainer.classList.remove("hidden");
  }
  if (chartKline) chartKline.timeScale().fitContent();
}

function showToast(msg) {
  toast.textContent = msg;
  toast.classList.remove("hidden");
  setTimeout(function() { toast.classList.add("hidden"); }, 6000);
}

function showInteractionDialog(checkpoint, label, question, sessionId) {
  var overlay = document.createElement("div");
  overlay.className = "interaction-overlay";
  overlay.id = "interaction-dialog";

  var box = document.createElement("div");
  box.className = "interaction-box";
  box.innerHTML =
    '<h3 class="interaction-title">' + esc(t("analyze.cpTitle", {n: checkpoint}) + label) + '</h3>' +
    '<p class="interaction-question">' + esc(question) + '</p>' +
    '<textarea id="interaction-answer" class="interaction-textarea" ' +
    'placeholder="' + esc(t("analyze.cpAnswerPh")) + '" rows="4"></textarea>' +
    '<div class="interaction-actions">' +
    '<button id="interaction-skip" class="btn secondary">' + esc(t("analyze.cpSkip")) + '</button>' +
    '<button id="interaction-submit" class="btn primary">' + esc(t("analyze.cpSubmit")) + '</button>' +
    '</div>';

  overlay.appendChild(box);
  document.body.appendChild(overlay);

  function respond(answer) {
    fetch("/api/respond/" + sessionId, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answer: answer }),
    }).catch(function() {});
    overlay.remove();
  }

  document.getElementById("interaction-submit").addEventListener("click", function() {
    var answer = document.getElementById("interaction-answer").value.trim();
    respond(answer || t("analyze.noInput"));
  });

  document.getElementById("interaction-skip").addEventListener("click", function() {
    respond("");
  });
}

function resetAll() {
  if (eventSource) { eventSource.close(); eventSource = null; }
  clearResults();
  resetProgress();
  showRightContent("placeholder");
  runBtn.disabled = !selectedTicker || !/^\d{4}-\d{2}-\d{2}$/.test(dateInput.value.trim());
  cp1Toggle.checked = false;
  cp2Toggle.checked = false;
  closeChatPanel();
  manipulationRisks = {};
  var dialog = document.getElementById("interaction-dialog");
  if (dialog) dialog.remove();
  if (downloadBlobUrl) { URL.revokeObjectURL(downloadBlobUrl); downloadBlobUrl = null; }
  var summaryPanel = document.getElementById("manipulation-summary");
  if (summaryPanel) summaryPanel.remove();
  sessionId = null;
}

// --- Utils ---
function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
function fmtMs(ms) { return ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms"; }

function idLabel(id) {
  var label = t("report." + id);
  return label === ("report." + id) ? id : label;
}

// --- Markdown to HTML Renderer ---
function renderMarkdown(text) {
  if (!text) return "";

  // ---- Step 0: extract fenced code blocks so nothing inside them is processed ----
  var codeBlocks = [];
  var html = esc(text);
  html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, function(m, lang, code) {
    var idx = codeBlocks.length;
    codeBlocks.push('<pre><code>' + code.replace(/\n$/, '') + '</code></pre>');
    return '\x00CODE' + idx + '\x00';
  });

  // ---- Step 1: block-level processing (line by line) ----
  var lines = html.split('\n');
  var output = [];
  var i = 0;

  // State machines for multi-line blocks
  var tableRows = null;       // accumulating table rows
  var inList = null;          // 'ul' or 'ol'
  var listItems = [];         // accumulated <li> strings
  var quoteLines = [];        // accumulated blockquote lines
  var paraLines = [];         // accumulating paragraph text

  function flushTable() {
    if (!tableRows || tableRows.length === 0) return;
    var tbl = '<table>';
    for (var r = 0; r < tableRows.length; r++) {
      var cells = tableRows[r];
      var tag = (r === 0) ? 'th' : 'td';
      tbl += '<tr>';
      for (var c = 0; c < cells.length; c++) {
        tbl += '<' + tag + '>' + _renderInline(cells[c].trim()) + '</' + tag + '>';
      }
      tbl += '</tr>';
    }
    tbl += '</table>';
    output.push(tbl);
    tableRows = null;
  }

  function flushList() {
    if (!inList || listItems.length === 0) return;
    var wrap = inList === 'ol' ? 'ol' : 'ul';
    output.push('<' + wrap + '>' + listItems.join('') + '</' + wrap + '>');
    inList = null;
    listItems = [];
  }

  function flushQuote() {
    if (quoteLines.length === 0) return;
    var body = _renderInline(quoteLines.join('\n'));
    output.push('<blockquote>' + body + '</blockquote>');
    quoteLines = [];
  }

  function flushParagraph() {
    if (paraLines.length === 0) return;
    var body = _renderInline(paraLines.join('\n'));
    output.push('<p>' + body + '</p>');
    paraLines = [];
  }

  function commitBlock() {
    flushTable();
    flushList();
    flushQuote();
    flushParagraph();
  }

  for (i = 0; i < lines.length; i++) {
    var raw = lines[i];
    var line = raw.trim();

    // --- blank line: close all open blocks ---
    if (line === '') {
      commitBlock();
      output.push('');
      continue;
    }

    // --- fenced code block placeholder ---
    if (/\x00CODE\d+\x00/.test(line)) {
      commitBlock();
      output.push(line);
      continue;
    }

    // --- horizontal rule ---
    if (/^---$/m.test(line) || /^\*\*\*$/m.test(line)) {
      commitBlock();
      output.push('<hr>');
      continue;
    }

    // --- atx headers ---
    var hMatch = line.match(/^(#{1,4}) (.+)$/);
    if (hMatch) {
      commitBlock();
      var level = hMatch[1].length;
      var hContent = _renderInline(hMatch[2]);
      output.push('<h' + level + '>' + hContent + '</h' + level + '>');
      continue;
    }

    // --- blockquote ---
    var qMatch = line.match(/^> (.*)$/);
    if (qMatch) {
      flushTable();
      flushList();
      flushParagraph();
      quoteLines.push(qMatch[1]);
      continue;
    }
    // continue blockquote on non-empty line if we're in one
    if (quoteLines.length > 0 && line !== '') {
      quoteLines.push(line);
      continue;
    }

    // --- table row (starts or ends with |) ---
    if (/^\|.*\|$/.test(line) || /^\|.*[^|]$/.test(line) || /^[^|].*\|$/.test(line)) {
      // Normalize: strip leading/trailing pipes, then split
      var cleanLine = line.replace(/^\|/, '').replace(/\|$/, '');
      if (/^[\s\-:|]+$/.test(cleanLine)) {
        // This is a separator row — skip it, but keep collecting
        if (tableRows === null) tableRows = [];
        continue;
      }
      flushList();
      flushQuote();
      flushParagraph();
      if (tableRows === null) tableRows = [];
      var cells = cleanLine.split('|');
      tableRows.push(cells);
      continue;
    }

    // --- unordered list ---
    var ulMatch = line.match(/^[\-*] (.+)$/);
    if (ulMatch) {
      flushTable();
      flushQuote();
      flushParagraph();
      if (inList !== 'ul') { flushList(); inList = 'ul'; }
      listItems.push('<li>' + _renderInline(ulMatch[1]) + '</li>');
      continue;
    }

    // --- ordered list ---
    var olMatch = line.match(/^(\d+)\. (.+)$/);
    if (olMatch) {
      flushTable();
      flushQuote();
      flushParagraph();
      if (inList !== 'ol') { flushList(); inList = 'ol'; }
      listItems.push('<li>' + _renderInline(olMatch[2]) + '</li>');
      continue;
    }

    // --- continuation of previous list item (indented line) ---
    if (inList && line !== '' && !/^[-*\d]/.test(line)) {
      // Append as <br> continuation to the last list item
      var lastIdx = listItems.length - 1;
      if (lastIdx >= 0) {
        listItems[lastIdx] = listItems[lastIdx].replace('</li>', '<br>' + _renderInline(line) + '</li>');
      }
      continue;
    }

    // --- plain paragraph text ---
    flushTable();
    flushList();
    flushQuote();
    paraLines.push(line);
  }

  // End of input: flush any open blocks
  commitBlock();

  // ---- Step 2: join output, restore code blocks ----
  html = output.join('\n');

  // Restore protected code blocks
  html = html.replace(/\x00CODE(\d+)\x00/g, function(m, idx) {
    return codeBlocks[parseInt(idx)] || m;
  });

  // ---- Step 3: post-process decorations ----
  // Highlight "Major Fund Movement Assessment" sections
  html = html.replace(
    /(<h[234]>Major Fund Movement Assessment[\s\S]*?)(?=<hr>|<h[234]>|$)/g,
    '<div class="md-highlight-box">$1</div>'
  );
  // Highlight "Major Fund Trap Assessment" sections (competitor/partner analysts)
  html = html.replace(
    /(<h[234]>Major Fund Trap Assessment[\s\S]*?)(?=<hr>|<h[234]>|$)/g,
    '<div class="md-highlight-box">$1</div>'
  );

  // Highlight "FINAL TRANSACTION PROPOSAL" lines
  html = html.replace(
    /(FINAL TRANSACTION PROPOSAL:\s*<strong>[^<]+<\/strong>)/g,
    '<div class="md-decision-box">$1</div>'
  );

  // **BUY** / **SELL** / **HOLD** / **OVERWEIGHT** / **UNDERWEIGHT** badges
  html = html.replace(/<strong>(BUY|SELL|HOLD|OVERWEIGHT|UNDERWEIGHT)<\/strong>/g,
    '<span class="md-badge $1">$1</span>');

  // ---- Step 4: clean up empty paragraphs ----
  html = html.replace(/<p>\s*<\/p>/g, '');
  html = html.replace(/\n{3,}/g, '\n\n');

  return html;
}

// ---- Inline formatter (bold, italic, code, links, strikethrough) ----
function _renderInline(text) {
  if (!text) return '';
  var out = text;

  // Strikethrough ~~text~~
  out = out.replace(/~~(.+?)~~/g, '<del>$1</del>');

  // Bold+italic ***text***
  out = out.replace(/\*\*\*(.+?)\*\*\*/g, '<strong><em>$1</em></strong>');

  // Bold **text**
  out = out.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');

  // Italic *text* (but not inside words with multiple asterisks)
  out = out.replace(/\*(.+?)\*/g, '<em>$1</em>');

  // Inline code `text`
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Links [text](url)
  out = out.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

  return out;
}

// --- Init ---
function initApp() {
  console.log("initApp running, loading model catalog...");
  loadModelCatalog();
  // Fallback: only surface an error if the catalog genuinely failed to load.
  // /api/models probes up to 4 Ollama hosts server-side (each can take
  // seconds), so a slow-but-successful load must NOT be clobbered by a false
  // "Failed to load providers". Guard on `modelCatalog` (set only on success)
  // plus the placeholder-only check so a late-arriving catalog is never wiped.
  setTimeout(function() {
    if (modelCatalog) return; // slow but succeeded — dropdowns already filled
    if (deepProviderSelect && deepProviderSelect.options.length === 1 && deepProviderSelect.options[0].value === "") {
      deepProviderSelect.innerHTML = '<option value="">Failed to load providers - check server</option>';
      if (deepModelSelect) deepModelSelect.innerHTML = '<option value="">-- Select provider first --</option>';
    }
    if (quickProviderSelect && quickProviderSelect.options.length === 1 && quickProviderSelect.options[0].value === "") {
      quickProviderSelect.innerHTML = '<option value="">Failed to load providers - check server</option>';
      if (quickModelSelect) quickModelSelect.innerHTML = '<option value="">-- Select provider first --</option>';
    }
  }, 15000);
  initPanelResizer();
}

// ---- Panel Resizer ----
function initPanelResizer() {
  var resizer = document.getElementById("panel-resizer");
  var leftPanel = document.getElementById("left-panel");
  if (!resizer || !leftPanel) return;

  var startX = 0;
  var startWidth = 0;

  function onMouseDown(e) {
    e.preventDefault();
    startX = e.clientX;
    startWidth = leftPanel.offsetWidth;
    resizer.classList.add("active");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }

  function onMouseMove(e) {
    var dx = e.clientX - startX;
    var newWidth = startWidth + dx;
    if (newWidth < 180) newWidth = 180;
    if (newWidth > 1200) newWidth = 1200;
    leftPanel.style.width = newWidth + "px";
  }

  function onMouseUp() {
    resizer.classList.remove("active");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
    // Trigger chart resize if visible
    window.dispatchEvent(new Event("resize"));
  }

  resizer.addEventListener("mousedown", onMouseDown);
}

// ---- Tab Switching ----
var currentTab = "analyze";
var _savedResultsHtml = "";  // preserve analysis results across tab switches
document.querySelectorAll(".top-tab-btn").forEach(function(btn) {
  btn.addEventListener("click", function() {
    var tabName = btn.dataset.tab;
    if (tabName === currentTab) return;
    // Stop the scheduled-tab auto-refresh poll whenever we leave the tab.
    stopSchedPoll();
    _schedLiveJobId = null;
    document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
    document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
    
    // Advisory/AI Pick mode: expand to right panel (mutually exclusive)
    var app = document.getElementById("app");
    if (app) app.classList.remove("advisory-mode", "aipick-mode", "prediction-mode", "strategy-mode", "backtest-mode");

      // Remove all mode classes
      if (app) {
        app.classList.remove("advisory-mode", "aipick-mode", "prediction-mode", "strategy-mode", "backtest-mode", "historyagent-mode", "sched-mode");
      }
      if (tabName === "advisory") {
      if (app) app.classList.add("advisory-mode");
    } else if (tabName === "aipick") {
      if (app) app.classList.add("aipick-mode");
    } else if (tabName === "prediction") {
      if (app) app.classList.add("prediction-mode");
    } else if (tabName === "strategy") {
      if (app) app.classList.add("strategy-mode");
    } else if (tabName === "backtest") {
      if (app) app.classList.add("backtest-mode");
    } else if (tabName === "history") {
      console.log("[Tab] Switching to history tab");
      if (app) app.classList.add("historyagent-mode");
      console.log("[Tab] app class after add:", app ? app.className : "null");
      // Auto-show the History Agent chat panel via history_agent.js
      if (typeof window.showHistoryAgentPanel === "function") {
        console.log("[Tab] calling showHistoryAgentPanel...");
        window.showHistoryAgentPanel();
        console.log("[Tab] showHistoryAgentPanel returned");
      } else {
        console.log("[Tab] WARNING: showHistoryAgentPanel NOT FOUND");
      }
    }
    btn.classList.add("active");
    document.getElementById("tab-" + tabName).classList.add("active");

    // Save results before switching away from analyze
    if (currentTab === "analyze") {
      _savedResultsHtml = resultsContainer.innerHTML || "";
    }

    // Always clean up history chat panel when switching tabs
    closeHistoryChatPanel();

    currentTab = tabName;

    // ── Right-panel management ──
    // Chat-mode tabs (advisory/aipick/prediction/strategy/backtest): #app.{mode} CSS
    // already hides right-panel content.
    // history tab: shows calibration panel & detail views in right panel.
    // analyze tab: shows analysis results / chart in right panel.
    // ALL other tabs (shortlist/scheduled/rotation/stockpick) hide right panel.
    var rightPanel = document.getElementById("right-panel");
    var _nonChatTabs = ["shortlist", "scheduled", "rotation", "stockpick"];
    if (rightPanel) {
      if (_nonChatTabs.indexOf(tabName) >= 0) {
        rightPanel.classList.add("hide-all");
      } else {
        rightPanel.classList.remove("hide-all");
      }
    }

    // Show/hide calibration panel: only visible on history tab
    var calPanel = document.getElementById("calibration-panel");
    if (calPanel) {
      calPanel.style.display = (tabName === "history") ? "" : "none";
    }

    // Show/hide skill panel: only visible on history tab
    var skillPanel = document.getElementById("skill-panel");
    if (skillPanel) {
      skillPanel.style.display = (tabName === "history") ? "" : "none";
    }

    // Hide chart/results containers on watchlist & index tabs (they use emwl-detail-panel instead)
    var chartCtn = document.getElementById("chart-container");
    var resultsCtn = document.getElementById("results-container");
    var isWatchlistTab = (tabName === "eastmoney-wl" || tabName === "index-stocks");
    if (chartCtn) chartCtn.classList.toggle("hidden", isWatchlistTab);
    if (resultsCtn) resultsCtn.classList.toggle("hidden", isWatchlistTab);

    // Show/hide EMWL detail panel: visible on eastmoney-wl and index-stocks tabs
    var emwlDetail = document.getElementById("emwl-detail-panel");
    if (emwlDetail) {
      if (tabName === "eastmoney-wl" || tabName === "index-stocks") {
        if (emwlDetail._wasVisible) {
          emwlDetail.classList.remove("hidden");
          emwlDetail.style.display = "flex";
        }
      } else {
        emwlDetail._wasVisible = !emwlDetail.classList.contains("hidden");
        emwlDetail.classList.add("hidden");
        emwlDetail.style.display = "none";
      }
    }

    // Clean up ha-expanded inline styles when leaving history tab
    // (showPanel() sets inline display:flex; no other tab clears it)
    var _haExpanded = document.getElementById("ha-expanded");
    if (_haExpanded && tabName !== "history") {
      _haExpanded.style.display = "";
      _haExpanded.classList.add("hidden");
    }

    // Reset sched-expanded inline display (openSchedReport never touches it;
    // visibility is driven entirely by #app.sched-mode CSS, so switching away
    // from the scheduled tab cleanly hides the report again via the element's
    // inline display:none attribute).
    var _schedExpanded = document.getElementById("sched-expanded");
    if (_schedExpanded && tabName !== "scheduled") {
      _schedExpanded.style.display = "";
    }

    // Hide analyze results when entering history tab
    var _rc = document.getElementById("results-container");
    if (_rc) _rc.classList.add("hidden");
    var _haApp = document.getElementById("app");
    if (_haApp && tabName === "history") {
      _haApp.classList.remove("advisory-mode","aipick-mode","prediction-mode","strategy-mode","backtest-mode","sched-mode");
      _haApp.classList.add("historyagent-mode");
      // Force HA dialog visible
      var _he = document.getElementById("ha-expanded");
      if (_he) _he.style.display = "flex";
      var _rp = document.getElementById("right-panel");
      if (_rp) { _rp.style.display = "flex"; _rp.style.flexDirection = "column"; _rp.style.height = "100%"; _rp.style.minHeight = "0"; _rp.style.overflow = "hidden"; }
    }
    if (tabName === "history") loadHistory();
    if (tabName === "shortlist") loadShortlist();
    if (tabName === "scheduled") {
      loadScheduledTasks();
      // Re-open the last-viewed task report so it persists across tab switches
      // (the switch handler above removed sched-mode + added hide-all).
      if (currentSchedJobId) openSchedReport(currentSchedJobId);
      // Auto-refresh: poll for finished runs and auto-show their output.
      startSchedPoll();
    }
    if (tabName === "aipick") { loadAipickThreads(); loadAipickRankings(); }
    if (tabName === "rotation") {
      try { loadRRG(); } catch(e) { console.error(e); }
      return;
    }
    if (tabName === "stockpick") {
      initStockPick();
      return;
    }
    // Restore results when switching to analyze tab
    if (tabName === "analyze") {
      // ═══ CLEANUP: remove inline styles left by other tabs (history, etc) ═══
      var rpCleanup = document.getElementById("right-panel");
      if (rpCleanup) {
        rpCleanup.style.display = "";
        rpCleanup.style.flexDirection = "";
        rpCleanup.style.height = "";
        rpCleanup.style.overflow = "";
      }
      // showPanel() (history_agent.js) sets inline display:none on these containers
      // when entering history tab — always reset them when switching back to analyze
      resultsContainer.style.display = "";
      chartContainer.style.display = "";
      var cc = document.getElementById("comparison-container");
      if (cc) cc.style.display = "";
      var cal = document.getElementById("calibration-panel");
      if (cal) cal.style.display = "";
      // showPanel() also sets inline display:flex on ha-expanded — reset it
      var _haEx = document.getElementById("ha-expanded");
      if (_haEx) { _haEx.style.display = ""; _haEx.classList.add("hidden"); }

      // Restore chart visibility (may have been hidden by hide-all CSS or mode rules)
      if (selectedTicker) {
        chartContainer.classList.remove("hidden");
      }
      if (_savedResultsHtml && _savedResultsHtml.trim()) {
        resultsContainer.innerHTML = _savedResultsHtml;
        resultsContainer.classList.remove("hidden");
        var hd = document.getElementById("history-detail-container");
        if (hd) { hd.innerHTML = ""; hd.classList.add("hidden"); }
        // Rebuild navigation because innerHTML assignment kills live onclick handlers
        updateAnalyzeNav();
        // Re-wire "Discuss with PM" button
        var discussBtn = document.getElementById("discuss-with-pm-btn");
        if (discussBtn && sessionId) {
          discussBtn.addEventListener("click", function() { discussWithPM(); });
        }

        // ═══ Force chart resize after display becomes visible ═══
        setTimeout(function() {
          if (chartKline) {
            chartKline.resize(chartElKline.clientWidth || 800, 260);
            chartKline.timeScale().fitContent();
          }
          if (chartVolume) chartVolume.resize(chartElVolume.clientWidth || 800, 80);
          if (chartMacd) chartMacd.resize(chartElMacd.clientWidth || 800, 100);
          if (chartRsi) chartRsi.resize(chartElRsi.clientWidth || 800, 80);
        }, 150);
      } else {
        showRightContent("placeholder");
      }
    }
  });
});

// ---- History Module ----
var selectedHistoryIds = [];
var _selectedTickers = {};  // runId -> ticker for same-stock validation
var _selectedRunMeta = {};  // runId -> {ticker, date} for chat context display
var _histViewMode = "time"; // "time" | "company"
var _histSortKey = "date";    // current sort column
var _histSortAsc = false;     // ascending?

function loadHistory() {
  var ticker = document.getElementById("hist-ticker").value.trim();
  var dateFrom = document.getElementById("hist-date-from").value.trim();
  var dateTo = document.getElementById("hist-date-to").value.trim();
  var rating = document.getElementById("hist-rating").value;
  var params = new URLSearchParams({ limit: "50", offset: "0" });
  if (ticker) params.set("ticker", ticker);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (rating) params.set("rating", rating);

  fetch("/api/results?" + params.toString())
    .then(function(r) { return r.json(); })
    .then(renderHistoryList)
    .catch(function() {
      document.getElementById("history-list").innerHTML = '<span class="no-results">' + tText("hist.loadFailed") + '</span>';
    });
}

function renderHistoryList(items) {
  var list = document.getElementById("history-list");
  // Update summary stats
  var summaryEl = document.getElementById("history-summary");
  if (summaryEl) {
    if (items && items.length) {
      var buys = 0, sells = 0, holds = 0;
      items.forEach(function(it) {
        var r = (it.rating || "").toLowerCase();
        if (r === "buy" || r === "overweight") buys++;
        else if (r === "sell" || r === "underweight") sells++;
        else holds++;
      });
      summaryEl.innerHTML =
        '<div style="background:var(--panel-bg);border:1px solid var(--border);border-radius:6px;padding:8px 12px;text-align:center;flex:1;">' +
          '<div style="font-size:18px;font-weight:700;color:var(--accent);">' + items.length + '</div>' +
          '<div style="font-size:10px;color:var(--text-muted);">' + tText("hist.total") + '</div>' +
        '</div>' +
        '<div style="background:#dafbe1;border:1px solid var(--green);border-radius:6px;padding:8px 12px;text-align:center;flex:1;">' +
          '<div style="font-size:18px;font-weight:700;color:var(--green);">' + buys + '</div>' +
          '<div style="font-size:10px;color:var(--green);">' + tText("hist.buys") + '</div>' +
        '</div>' +
        '<div style="background:#ffebe9;border:1px solid var(--red);border-radius:6px;padding:8px 12px;text-align:center;flex:1;">' +
          '<div style="font-size:18px;font-weight:700;color:var(--red);">' + sells + '</div>' +
          '<div style="font-size:10px;color:var(--red);">' + tText("hist.sells") + '</div>' +
        '</div>' +
        '<div style="background:#fff8c5;border:1px solid var(--gold);border-radius:6px;padding:8px 12px;text-align:center;flex:1;">' +
          '<div style="font-size:18px;font-weight:700;color:var(--gold);">' + holds + '</div>' +
          '<div style="font-size:10px;color:var(--gold);">' + tText("hist.hold") + '</div>' +
        '</div>';
    } else {
      summaryEl.innerHTML = "";
    }
  }
  if (!items || !items.length) {
    list.innerHTML = '<span class="no-results">' + tText("hist.empty") + '</span>';
    updateCompareBar();
    return;
  }
  var savedIds = selectedHistoryIds.slice();
  var savedTickers = Object.assign({}, _selectedTickers);
  var savedMeta = Object.assign({}, _selectedRunMeta);
  selectedHistoryIds = [];
  _selectedTickers = {};
  _selectedRunMeta = {};
  var html = "";

  // Sort header
  var sortArrows = { date: "", ticker: "", rating: "" };
  sortArrows[_histSortKey] = _histSortAsc ? " ▲" : " ▼";
  html += '<div class="history-sort-header">' +
    '<span class="hi-sort" onclick="_doSortHistory(\'date\')">' + tText("hist.date") + sortArrows.date + '</span>' +
    '<span class="hi-sort" onclick="_doSortHistory(\'ticker\')">' + tText("hist.ticker") + sortArrows.ticker + '</span>' +
    '<span class="hi-sort" onclick="_doSortHistory(\'rating\')">' + tText("hist.rating") + sortArrows.rating + '</span>' +
    '<span style="flex:1;"></span>' +
    '</div>';

  // Sort items
  var sorted = items.slice();
  if (_histViewMode === "time") {
    sorted.sort(function(a, b) {
      var va = (a[_histSortKey] || ""), vb = (b[_histSortKey] || "");
      if (_histSortKey === "date") { va = a.date; vb = b.date; }
      if (_histSortKey === "rating") {
        var order = {Buy:5,Overweight:4,Hold:3,Underweight:2,Sell:1};
        va = order[a.rating] || 3; vb = order[b.rating] || 3;
      }
      var cmp = va < vb ? -1 : va > vb ? 1 : 0;
      return _histSortAsc ? cmp : -cmp;
    });
  }

  // Company-grouped view
  if (_histViewMode === "company") {
    var groups = {};
    sorted.forEach(function(item) {
      var key = item.ticker.toUpperCase();
      if (!groups[key]) groups[key] = [];
      groups[key].push(item);
    });
    var tickers = Object.keys(groups).sort();
    tickers.forEach(function(tk) {
      var grp = groups[tk];
      var lastName = grp[0].company_name || "";
      grp.sort(function(a, b) { return b.date.localeCompare(a.date); });
      html += '<div class="history-group-header">' +
        '<span class="hi-ticker" style="font-size:13px;">' + esc(tk) + '</span>' +
        '<span class="hi-name" style="font-size:11px;margin-left:6px;">' + esc(lastName.substring(0, 12) || "") + '</span>' +
        '<span style="font-size:10px;color:var(--text-muted);margin-left:auto;">' + t("hist.analysesCount", {n: grp.length}) + '</span>' +
        '</div>';
      grp.forEach(function(item) {
        html += _renderOneItem(item);
      });
    });
  } else {
    // Time-sorted view
    sorted.forEach(function(item) {
      html += _renderOneItem(item);
    });
  }

  function _renderOneItem(it) {
    var typeBadge = it.run_type === "scheduled" ? '<span class="hi-type">' + tText("hist.scheduled") + '</span>' : '<span class="hi-type">' + tText("hist.manual") + '</span>';
    var ratingClass = (it.rating || "Hold").toLowerCase();
    var nextDate = it.next_analysis_date || "";
    var nextBadge = nextDate ? '<span class="hi-next">' + t("hist.next", {date: esc(nextDate)}) + '</span>' : "";
    var reanalyzeBtn = '<button class="hi-reanalyze" data-ticker="' + esc(it.ticker) + '" data-date="' + esc(it.date) + '" data-analysts="' + esc(it.analysts || '') + '" title="' + t("hist.reanalyzeTitle") + '">&#x21bb;</button>';
    var deleteBtn = '<button class="hi-delete" data-run-id="' + esc(it.run_id) + '" title="' + t("hist.deleteTitle") + '">&times;</button>';
    var companyName = it.company_name || "";
    return '<div class="history-item" data-run-id="' + esc(it.run_id) + '"' +
      (it.analysts ? ' data-analysts="' + esc(it.analysts) + '"' : '') + '>' +
      '<input type="checkbox" class="hi-check" data-run-id="' + esc(it.run_id) + '">' +
      '<span class="hi-date">' + esc(it.date) + '</span>' +
      '<span class="hi-ticker" style="cursor:pointer;" title="' + t("hist.tickerTitle") + '">' + esc(it.ticker) + '</span>' +
      '<span class="hi-name" title="' + esc(companyName) + '">' + esc(companyName.substring(0, 8) || "") + '</span>' +
      '<span class="rating-badge ' + ratingClass + '">' + esc(it.rating || "Hold") + '</span>' +
      typeBadge + nextBadge + reanalyzeBtn + deleteBtn +
      '</div>';
  }

  list.innerHTML = html;

  list.querySelectorAll(".history-item").forEach(function(el) {
    el.addEventListener("click", function(e) {
      if (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON") return;
      var runId = el.dataset.runId;
      showHistoryDetail(runId);
    });
  });
  list.querySelectorAll(".hi-check").forEach(function(cb) {
    cb.addEventListener("change", function(e) {
      e.stopPropagation();
      var runId = cb.dataset.runId;
      if (cb.checked) {
        if (selectedHistoryIds.length >= 3) { cb.checked = false; return; }
        selectedHistoryIds.push(runId);
        cb.closest(".history-item").classList.add("selected");
        var row = cb.closest(".history-item");
        var tickerSpan = row.querySelector(".hi-ticker");
        var dateSpan = row.querySelector(".hi-date");
        if (tickerSpan) _selectedTickers[runId] = tickerSpan.textContent.trim();
        if (tickerSpan && dateSpan) {
          _selectedRunMeta[runId] = {
            ticker: tickerSpan.textContent.trim(),
            date: dateSpan.textContent.trim()
          };
        }
      } else {
        selectedHistoryIds = selectedHistoryIds.filter(function(id) { return id !== runId; });
        cb.closest(".history-item").classList.remove("selected");
        delete _selectedTickers[runId];
        delete _selectedRunMeta[runId];
      }
      updateCompareBar();
    });
  });
  // Delete buttons
  list.querySelectorAll(".hi-delete").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      var runId = btn.dataset.runId;
      if (!confirm(t("hist.deleteConfirm"))) return;
      fetch("/api/results/" + runId, { method: "DELETE" })
        .then(function(r) {
          if (r.ok) { loadHistory(); }
          else { alert(t("hist.deleteFailed")); }
        })
        .catch(function() { alert(t("hist.deleteFailed")); });
    });
  });
  // Ticker click: switch to analyze tab with pre-filled values (no auto-run)
  list.querySelectorAll(".hi-ticker").forEach(function(el) {
    el.addEventListener("click", function(e) {
      e.stopPropagation();
      var item = el.closest(".history-item");
      if (!item) return;
      var ticker = el.textContent.trim();
      var date = item.querySelector(".hi-date").textContent.trim();
      var analysts = item.dataset.analysts || "";
      var runId = item.dataset.runId || "";
      switchToAnalyzeAndRun(ticker, date, analysts, false, runId);
    });
  });
  // Re-analyze buttons
  list.querySelectorAll(".hi-reanalyze").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      var ticker = btn.dataset.ticker;
      var analysts = btn.dataset.analysts;
      // Switch to analyze tab, pre-fill with today's date, and run
      switchToAnalyzeAndRun(ticker, new Date().toISOString().slice(0, 10), analysts);
    });
  });
  // Restore selections that still exist in the re-rendered list
  savedIds.forEach(function(id) {
    var cb = list.querySelector('.hi-check[data-run-id="' + id + '"]');
    if (cb) {
      cb.checked = true;
      cb.closest(".history-item").classList.add("selected");
      if (selectedHistoryIds.indexOf(id) === -1) {
        selectedHistoryIds.push(id);
        if (savedTickers[id]) _selectedTickers[id] = savedTickers[id];
        if (savedMeta[id]) _selectedRunMeta[id] = savedMeta[id];
      }
    }
  });
  updateCompareBar();
}

function switchToAnalyzeAndRun(ticker, date, analysts, autoRun, runId) {
  if (autoRun === undefined) autoRun = true;
  // Clean up any expanded modes
  var app = document.getElementById("app");
  if (app) app.classList.remove("advisory-mode", "aipick-mode", "prediction-mode", "strategy-mode", "backtest-mode", "historyagent-mode", "sched-mode");
  currentTab = "analyze";
  document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
  var analyzeTab = document.querySelector('[data-tab="analyze"]');
  if (analyzeTab) analyzeTab.classList.add("active");
  var analyzeContent = document.getElementById("tab-analyze");
  if (analyzeContent) analyzeContent.classList.add("active");

  // When autoRun=false (ticker click from history), preserve existing results
  if (autoRun) {
    showRightContent("results");
  } else {
    // Still ensure the right panel is visible and chart/result containers show
    var rp = document.getElementById("right-panel");
    if (rp) {
      rp.style.display = "";
      rp.style.flexDirection = "";
      rp.style.height = "";
      rp.style.overflow = "";
    }
    var cc = document.getElementById("comparison-container");
    var hd = document.getElementById("history-detail-container");
    if (cc) { cc.innerHTML = ""; cc.classList.add("hidden"); }
    if (hd) { hd.innerHTML = ""; hd.classList.add("hidden"); }
    resultsContainer.classList.remove("hidden");
    // ═══ CLEANUP: clear resultsContainer inline styles ═══
    if (resultsContainer) {
      resultsContainer.style.display = "";
      resultsContainer.style.flexDirection = "";
      resultsContainer.style.height = "";
      resultsContainer.style.overflow = "";
    }
    // Clear chartContainer inline style set by showPanel()
    chartContainer.style.display = "";
    var cal = document.getElementById("calibration-panel");
    if (cal) cal.style.display = "";
    // Clear ha-expanded inline display set by showPanel()
    var _haEx = document.getElementById("ha-expanded");
    if (_haEx) { _haEx.style.display = ""; _haEx.classList.add("hidden"); }
    if (_savedResultsHtml && _savedResultsHtml.trim()) {
      resultsContainer.innerHTML = _savedResultsHtml;
      // Rebuild nav and re-wire discuss button
      updateAnalyzeNav();
      var discussBtn = document.getElementById("discuss-with-pm-btn");
      if (discussBtn && sessionId) {
        discussBtn.addEventListener("click", function() { discussWithPM(); });
      }
    } else {
      // No saved results — show placeholder
      resultsContainer.innerHTML = '<div id="placeholder-hint" class="placeholder-hint">' + tText("placeholder.hint") + '</div>';
    }
  }

  tickerInput.value = ticker;
  // When viewing a historical record (runId provided), use today's date
  // so chart shows current data and Run analyzes current conditions.
  dateInput.value = (runId ? new Date().toISOString().slice(0, 10) : (date || new Date().toISOString().slice(0, 10)));

  if (analysts) {
    var analystList = analysts.split(",");
    analystToggles.querySelectorAll(".toggle").forEach(function(t) {
      var key = t.dataset.analyst;
      if (analystList.indexOf(key) >= 0) { t.classList.add("active"); }
      else { t.classList.remove("active"); }
    });
  }

  // Set ticker directly and load chart
  selectedTicker = { symbol: ticker, name: ticker, exchange: "" };
  selectedSymbol.textContent = ticker;
  selectedName.textContent = ticker;
  selectedExchange.textContent = "";
  selectedCard.classList.remove("hidden");
  runBtn.disabled = false;
  loadChart(ticker, "max");
  // Load historical record's analysis data into the right panel
  if (!autoRun && runId) {
    fetchAndDisplayRecordResults(runId);
  }
  // Auto-click Run only when autoRun=true
  if (autoRun) {
    setTimeout(function() { runBtn.click(); }, 500);
  }
}

function renderRecordResultsHtml(meta, state, runId) {
  var html = '<div class="result-card" style="border-left-color:#888;">';
  html += '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">';
  html += '<span class="card-title">' + esc(meta.ticker) + ' — ' + esc(meta.date) + '</span>';
  html += '<div style="display:flex;gap:8px;align-items:center;">';
  var rtg = (meta.rating || "Hold").toLowerCase();
  html += '<span class="rating-badge ' + rtg + '">' + esc(meta.rating || "Hold") + '</span>';
  html += '<button class="btn primary" style="padding:4px 12px;font-size:12px;" id="detail-reanalyze-btn" data-ticker="' + esc(meta.ticker) + '" data-date="' + esc(meta.date) + '" data-analysts="' + esc(meta.analysts || '') + '">' + tText("hist.reanalyze") + '</button>';
  html += '<a href="/api/results/' + esc(runId) + '/download?format=md" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">MD</a>';
  html += '<a href="/api/results/' + esc(runId) + '/download?format=json" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">JSON</a>';
  html += '</div></div>';

  // Meta row
  html += '<div class="card-body" style="font-size:12px;color:var(--text-muted);padding-bottom:0;">';
  html += 'Provider: ' + esc(meta.provider || "N/A") + ' | ';
  html += 'Deep: ' + esc(meta.deep_model || "N/A") + ' | ';
  html += 'Quick: ' + esc(meta.quick_model || "N/A") + ' | ';
  html += 'Elapsed: ' + ((meta.total_elapsed_ms || 0) / 1000).toFixed(1) + 's | ';
  html += 'Risk: ' + esc(meta.risk_level || "N/A");
  if (meta.next_analysis_date) {
    html += ' | <b>Next Analysis: ' + esc(meta.next_analysis_date) + '</b>';
  }
  html += '</div>';

  // Key metrics
  if (state && state.key_metrics) {
    html += '<div class="card-body" style="padding-top:0;">' + renderKeyMetricsCards(state.key_metrics) + '</div>';
  }
  if (state && state.key_metrics && state.key_metrics.retracement) {
    html += '<div class="card-body" style="padding-top:0;">' + renderRetracementCard(state.key_metrics.retracement) + '</div>';
  }

  // Full decision
  html += '<div class="card-body markdown-body">';
  if (state && state.final_trade_decision) {
    html += renderMarkdown(String(state.final_trade_decision).substring(0, 12000));
  } else {
    html += "<p>" + tText("hist.noDetailed") + "</p>";
  }
  html += '</div>';

  // Tool-call traces
  if (state && state.analyst_tool_traces) {
    html += '<div class="card-body" style="padding-top:0;">' + renderToolTraces(state.analyst_tool_traces) + '</div>';
  }

  // Analyst reports (collapsible)
  if (state) {
    var reportKeys = [
      {key: "capital_flow_report", label: t("report.capital_flow")},
      {key: "market_report", label: t("report.market")},
      {key: "sentiment_report", label: t("report.social")},
      {key: "news_report", label: t("report.news")},
      {key: "fundamentals_report", label: t("report.fundamentals")},
      {key: "competitor_report", label: t("report.competitor")},
      {key: "partner_report", label: t("report.partner")},
    ];
    var hasReports = false;
    html += '<div class="card-body" style="padding-top:0;">';
    html += '<details style="margin-top:12px;"><summary style="cursor:pointer;font-weight:600;font-size:13px;">' + tText("hist.analystReports") + '</summary>';
    html += '<div class="markdown-body" style="margin-top:8px;">';
    reportKeys.forEach(function(rk) {
      var report = state[rk.key];
      if (report && String(report).trim()) {
        hasReports = true;
        html += '<h4>' + rk.label + '</h4>';
        html += renderMarkdown(String(report).substring(0, 5000));
        html += '<hr>';
      }
    });
    if (!hasReports) html += '<p>No analyst reports stored</p>';
    html += '</div></details></div>';
  }

  // ── Investment Debate ──
  var invDebate = state ? state.investment_debate_state : null;
  if (invDebate && (invDebate.bull_history || invDebate.bear_history)) {
    html += '<div class="card-body" style="padding-top:0;">';
    html += '<details style="margin-top:12px;"><summary style="cursor:pointer;font-weight:600;font-size:13px;">' + tText("hist.bullBearDebate") + '</summary>';
    html += '<div class="markdown-body" style="margin-top:8px;">';
    if (invDebate.bull_history) {
      html += '<h4>' + tText("debate.bull") + '</h4>';
      html += renderMarkdown(String(invDebate.bull_history).substring(0, 6000));
      html += '<hr>';
    }
    if (invDebate.bear_history) {
      html += '<h4>' + tText("debate.bear") + '</h4>';
      html += renderMarkdown(String(invDebate.bear_history).substring(0, 6000));
      html += '<hr>';
    }
    if (invDebate.judge_decision) {
      html += '<h4>' + tText("hist.researchManager") + '</h4>';
      html += renderMarkdown(String(invDebate.judge_decision).substring(0, 4000));
    }
    html += '</div></details></div>';
  }

  // ── Trader Proposal ──
  var traderText = state ? (state.trader_investment_plan || state.trader_investment_decision) : null;
  if (traderText && String(traderText).trim()) {
    html += '<div class="card-body" style="padding-top:0;">';
    html += '<details style="margin-top:12px;"><summary style="cursor:pointer;font-weight:600;font-size:13px;">' + tText("report.trader") + '</summary>';
    html += '<div class="markdown-body" style="margin-top:8px;">';
    html += renderMarkdown(String(traderText).substring(0, 5000));
    html += '</div></details></div>';
  }

  // ── Risk Debate ──
  var riskDebate = state ? state.risk_debate_state : null;
  if (riskDebate && (riskDebate.aggressive_history || riskDebate.conservative_history || riskDebate.neutral_history)) {
    html += '<div class="card-body" style="padding-top:0;">';
    html += '<details style="margin-top:12px;"><summary style="cursor:pointer;font-weight:600;font-size:13px;">' + tText("hist.riskDebate") + '</summary>';
    html += '<div class="markdown-body" style="margin-top:8px;">';
    if (riskDebate.aggressive_history) {
      html += '<h4>' + tText("debate.aggressive") + '</h4>';
      html += renderMarkdown(String(riskDebate.aggressive_history).substring(0, 6000));
      html += '<hr>';
    }
    if (riskDebate.conservative_history) {
      html += '<h4>' + tText("debate.conservative") + '</h4>';
      html += renderMarkdown(String(riskDebate.conservative_history).substring(0, 6000));
      html += '<hr>';
    }
    if (riskDebate.neutral_history) {
      html += '<h4>' + tText("debate.neutral") + '</h4>';
      html += renderMarkdown(String(riskDebate.neutral_history).substring(0, 6000));
    }
    if (riskDebate.judge_decision) {
      html += '<details style="margin-top:8px;"><summary style="cursor:pointer;font-weight:600;">' + tText("report.decision-risk") + '</summary>';
      html += '<div class="markdown-body" style="margin-top:4px;">';
      html += renderMarkdown(String(riskDebate.judge_decision).substring(0, 4000));
      html += '</div></details>';
    }
    html += '</div></details></div>';
  }

  // Chat with PM button
  html += '<div class="card-body" style="padding-top:8px;border-top:1px solid var(--border);margin-top:12px;">';
  html += '<button class="btn primary" id="history-chat-btn" data-run-id="' + esc(runId) + '" style="padding:8px 16px;font-size:13px;">' + tText("hist.chatPm") + '</button>';
  html += '</div>';
  html += '</div>';

  return html;
}

function fetchAndDisplayRecordResults(runId) {
  resultsContainer.innerHTML = '<div class="result-card" style="border-left-color:#888;padding:16px;"><div style="text-align:center;color:var(--text-muted);">' + tText("hist.loadingRecord") + '</div></div>';
  Promise.all([
    fetch("/api/results/" + runId).then(function(r) { return r.json(); }),
    fetch("/api/results/" + runId + "/full").then(function(r) { return r.json(); }).catch(function() { return null; }),
  ]).then(function(results) {
    var meta = results[0];
    var state = results[1];
    if (!meta) {
      resultsContainer.innerHTML = '<div class="placeholder-hint">' + tText("hist.recordNotFound") + '</div>';
      return;
    }
    var html = renderRecordResultsHtml(meta, state, runId);
    resultsContainer.innerHTML = html;
    // Wire the Re-Analyze button
    var reBtn = document.getElementById("detail-reanalyze-btn");
    if (reBtn) {
      reBtn.addEventListener("click", function() {
        switchToAnalyzeAndRun(reBtn.dataset.ticker, new Date().toISOString().slice(0, 10), reBtn.dataset.analysts);
      });
    }
    // Wire Chat with PM button
    var chatBtn = document.getElementById("history-chat-btn");
    if (chatBtn) {
      chatBtn.addEventListener("click", function() {
        _selectedTickers[runId] = meta.ticker;
        _selectedRunMeta[runId] = { ticker: meta.ticker, date: meta.date };
        showHistoryChatPanel(runId);
      });
    }
  }).catch(function() {
    resultsContainer.innerHTML = '<div class="placeholder-hint">' + tText("hist.loadFailedRecord") + '</div>';
  });
}

function updateCompareBar() {
  var bar = document.getElementById("compare-bar");
  var countEl = document.getElementById("compare-count");
  var compareBtn = document.getElementById("compare-btn");
  var chatBtn = document.getElementById("chat-pm-btn");
  if (!bar || !countEl) return;

  // Always show bar in history tab
  bar.classList.remove("hidden");

  if (selectedHistoryIds.length >= 1) {
    countEl.textContent = t("hist.selected", {n: selectedHistoryIds.length});
    if (compareBtn) compareBtn.style.display = selectedHistoryIds.length >= 2 ? "" : "none";

    // Chat button: only enabled when all selected items share the same ticker
    if (chatBtn) {
      chatBtn.style.display = "";
      var tickers = selectedHistoryIds.map(function(id) { return _selectedTickers[id]; }).filter(Boolean);
      var unique = {};
      tickers.forEach(function(t) { unique[t] = true; });
      if (Object.keys(unique).length <= 1) {
        chatBtn.disabled = false;
        chatBtn.style.opacity = "";
        chatBtn.title = "";
      } else {
        chatBtn.disabled = true;
        chatBtn.style.opacity = "0.4";
        chatBtn.title = t("hist.sameStock");
      }
    }
  } else {
    countEl.textContent = "";
    if (compareBtn) compareBtn.style.display = "none";
    // Chat button always visible — opens analysis picker
    if (chatBtn) {
      chatBtn.style.display = "";
      chatBtn.disabled = false;
      chatBtn.style.opacity = "";
      chatBtn.title = t("hist.chooseToChat");
    }
  }
  // Wire chat button
  if (chatBtn) {
    chatBtn.onclick = function() {
      startMultiRunChat();
    };
  }
}

function startMultiRunChat() {
  var runIds = selectedHistoryIds.slice();
  if (!runIds.length) {
    showToast(t("hist.selectOne"));
    return;
  }
  if (runIds.length > 3) {
    showToast(t("hist.selectUp3"));
    return;
  }
  var tickers = runIds.map(function(id) { return _selectedTickers[id]; }).filter(Boolean);
  var unique = {};
  tickers.forEach(function(t) { unique[t] = true; });
  if (Object.keys(unique).length > 1) {
    showToast(t("hist.sameStock"));
    return;
  }
  showHistoryChatPanel(runIds[0]);
}

// Unified right panel content management
// Chart is owned by chartContainer (never hidden once ticker selected).
// Content below chart is managed here.
function showRightContent(mode, opts) {
  var cc = document.getElementById("comparison-container");
  var hd = document.getElementById("history-detail-container");
  // Hide all content containers first
  resultsContainer.innerHTML = "";
  if (cc) { cc.innerHTML = ""; cc.classList.add("hidden"); }
  if (hd) { hd.innerHTML = ""; hd.classList.add("hidden"); }

  opts = opts || {};
  if (mode === "results") {
    resultsContainer.classList.remove("hidden");
    if (opts.html) resultsContainer.innerHTML = opts.html;
  } else if (mode === "comparison") {
    if (cc) cc.classList.remove("hidden");
    if (opts.html && cc) cc.innerHTML = opts.html;
  } else if (mode === "history") {
    resultsContainer.classList.add("hidden");
    if (hd) { hd.classList.remove("hidden"); hd.innerHTML = opts.html || ""; }
  } else if (mode === "placeholder") {
    resultsContainer.classList.remove("hidden");
    resultsContainer.innerHTML = '<div id="placeholder-hint" class="placeholder-hint">Configure and run an analysis to see results here.</div>';
  }
}

function clearRightPanel() {
  showRightContent("results");
}

function showHistoryDetail(runId) {
  // Load both metadata and full state
  Promise.all([
    fetch("/api/results/" + runId).then(function(r) { return r.json(); }),
    fetch("/api/results/" + runId + "/full").then(function(r) { return r.json(); }).catch(function() { return null; }),
  ]).then(function(results) {
    var meta = results[0];
    var state = results[1];

    var html = '<div class="result-card" style="border-left-color:#888;">';
    html += '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">';
    html += '<span class="card-title">' + esc(meta.ticker) + ' — ' + esc(meta.date) + '</span>';
    html += '<div style="display:flex;gap:8px;align-items:center;">';
    var rtg = (meta.rating || "Hold").toLowerCase();
    html += '<span class="rating-badge ' + rtg + '">' + esc(meta.rating || "Hold") + '</span>';
    html += '<button class="btn primary" style="padding:4px 12px;font-size:12px;" id="detail-reanalyze-btn" data-ticker="' + esc(meta.ticker) + '" data-date="' + esc(meta.date) + '" data-analysts="' + esc(meta.analysts || '') + '">' + tText("hist.reanalyze") + '</button>';
    html += '<a href="/api/results/' + esc(runId) + '/download?format=md" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">MD</a>';
    html += '<a href="/api/results/' + esc(runId) + '/download?format=json" class="btn secondary" style="padding:4px 10px;font-size:11px;text-decoration:none;">JSON</a>';
    html += '<button class="btn secondary" id="detail-back-btn" style="padding:4px 10px;font-size:11px;">' + tText("hist.backToList") + '</button>';
    html += '</div></div>';

    // Meta row
    html += '<div class="card-body" style="font-size:12px;color:var(--text-muted);padding-bottom:0;">';
    html += t("hist.provider") + ' ' + esc(meta.provider || "N/A") + ' | ';
    html += t("hist.deep") + ' ' + esc(meta.deep_model || "N/A") + ' | ';
    html += t("hist.quick") + ' ' + esc(meta.quick_model || "N/A") + ' | ';
    html += t("hist.elapsed") + ' ' + ((meta.total_elapsed_ms || 0) / 1000).toFixed(1) + 's | ';
    html += t("hist.risk") + ' ' + esc(meta.risk_level || "N/A");
    if (meta.next_analysis_date) {
      html += ' | <b>' + t("hist.nextAnalysis") + ' ' + esc(meta.next_analysis_date) + '</b>';
    }
    html += '</div>';

    // Key metrics cards
    if (state && state.key_metrics) {
      html += '<div class="card-body" style="padding-top:0;">';
      html += renderKeyMetricsCards(state.key_metrics);
      html += '</div>';
    }

    // Retracement signal card
    if (state && state.key_metrics && state.key_metrics.retracement) {
      html += '<div class="card-body" style="padding-top:0;">';
      html += renderRetracementCard(state.key_metrics.retracement);
      html += '</div>';
    }

    // Full decision
    html += '<div class="card-body markdown-body">';
    if (state && state.final_trade_decision) {
      html += renderMarkdown(String(state.final_trade_decision).substring(0, 12000));
    } else {
      html += "<p>" + tText("hist.noDetailed") + "</p>";
    }
    html += '</div>';

    // Tool-call traces
    if (state && state.analyst_tool_traces) {
      html += '<div class="card-body" style="padding-top:0;">';
      html += renderToolTraces(state.analyst_tool_traces);
      html += '</div>';
    }

    // Analyst reports (collapsible)
    if (state) {
      var reportKeys = [
        {key: "capital_flow_report", label: t("report.capital_flow")},
        {key: "market_report", label: t("report.market")},
        {key: "sentiment_report", label: t("report.social")},
        {key: "news_report", label: t("report.news")},
        {key: "fundamentals_report", label: t("report.fundamentals")},
        {key: "competitor_report", label: t("report.competitor")},
        {key: "partner_report", label: t("report.partner")},
      ];
      var hasReports = false;
      html += '<div class="card-body" style="padding-top:0;">';
      html += '<details style="margin-top:12px;"><summary style="cursor:pointer;font-weight:600;font-size:13px;">' + tText("hist.analystReports") + '</summary>';
      html += '<div class="markdown-body" style="margin-top:8px;">';
      reportKeys.forEach(function(rk) {
        var report = state[rk.key];
        if (report && String(report).trim()) {
          hasReports = true;
          html += '<h4>' + rk.label + '</h4>';
          html += renderMarkdown(String(report).substring(0, 5000));
          html += '<hr>';
        }
      });
      if (!hasReports) html += '<p>' + tText("hist.noReports") + '</p>';
      html += '</div></details></div>';
    }

    // Chat with PM button
    html += '<div class="card-body" style="padding-top:8px;border-top:1px solid var(--border);margin-top:12px;">';
    html += '<button class="btn primary" id="history-chat-btn" data-run-id="' + esc(runId) + '" style="padding:8px 16px;font-size:13px;">' + tText("hist.chatPm") + '</button>';
    html += '</div>';

    html += '</div>';
    showRightContent("history", {html: html});

    // Bind re-analyze button
    var reBtn = document.getElementById("detail-reanalyze-btn");
    if (reBtn) {
      reBtn.addEventListener("click", function() {
        switchToAnalyzeAndRun(reBtn.dataset.ticker, new Date().toISOString().slice(0, 10), reBtn.dataset.analysts);
      });
    }

    // Bind Chat with PM button
    var chatBtn = document.getElementById("history-chat-btn");
    if (chatBtn) {
      chatBtn.addEventListener("click", function() {
        // Populate metadata maps so chat header shows ticker+date
        _selectedTickers[runId] = meta.ticker;
        _selectedRunMeta[runId] = { ticker: meta.ticker, date: meta.date };
        showHistoryChatPanel(runId);
      });
    }

    // Bind Back to List button
    var backBtn2 = document.getElementById("detail-back-btn");
    if (backBtn2) {
      backBtn2.addEventListener("click", function() {
        showRightContent("placeholder");
      });
    }
  }).catch(function() {
    showRightContent("history", {html: '<div class="placeholder-hint">' + tText("hist.loadFailedDetail") + '</div>'});
  });
}

document.getElementById("hist-refresh").addEventListener("click", loadHistory);
window._doSortHistory = function(key) {
  if (_histSortKey === key) { _histSortAsc = !_histSortAsc; }
  else { _histSortKey = key; _histSortAsc = false; }
  loadHistory();
};

window.switchHistoryView = function(mode) {
  _histViewMode = mode;
  document.getElementById("hist-view-time").style.background = mode === "time" ? "var(--accent)" : "";
  document.getElementById("hist-view-time").style.color = mode === "time" ? "#fff" : "";
  document.getElementById("hist-view-company").style.background = mode === "company" ? "var(--accent)" : "";
  document.getElementById("hist-view-company").style.color = mode === "company" ? "#fff" : "";
  loadHistory();
};
document.getElementById("hist-view-time").addEventListener("click", function() { switchHistoryView("time"); });
document.getElementById("hist-view-company").addEventListener("click", function() { switchHistoryView("company"); });
document.getElementById("compare-btn").addEventListener("click", function() {
  if (selectedHistoryIds.length < 2) return;
  fetch("/api/results/compare?ids=" + selectedHistoryIds.join(","))
    .then(function(r) { return r.json(); })
    .then(renderComparison);
});

function renderComparison(rows) {
  var cc = document.getElementById("comparison-container");
  if (!rows || rows.length < 2) {
    cc.innerHTML = '<div class="placeholder-hint">Not enough data to compare</div>';
    return;
  }
  var headers = rows.map(function(r) {
    return '<div class="comparison-cell header">' + esc(r.date) + '<br>' + esc(r.ticker) + '</div>';
  }).join("");

  var fields = [
    { key: "rating", label: "Rating" },
    { key: "risk_level", label: "Risk Level" },
    { key: "next_analysis_date", label: "Next Analysis" },
    { key: "provider", label: "Provider" },
    { key: "deep_model", label: "Deep Model" },
    { key: "total_elapsed_ms", label: "Elapsed (s)", fmt: function(v) { return ((parseInt(v)||0)/1000).toFixed(1) + "s"; } },
    { key: "analysts", label: "Analysts" },
  ];

  var bodyHtml = "";
  fields.forEach(function(f) {
    bodyHtml += '<div class="comparison-cell label">' + f.label + '</div>';
    rows.forEach(function(r, i) {
      var raw = r[f.key];
      var val = (raw === null || raw === undefined || raw === "") ? "-" : String(raw);
      if (f.fmt && raw) val = f.fmt(raw);
      var extraClass = "";
      if (f.key === "rating") {
        var v = val.toLowerCase();
        if (v === "buy" || v === "overweight") extraClass = " bullish";
        else if (v === "sell" || v === "underweight") extraClass = " bearish";
        else extraClass = " neutral";
      }
      bodyHtml += '<div class="comparison-cell' + extraClass + '">' + esc(val) + '</div>';
    });
  });

  var compHtml =
    '<h3 style="margin-bottom:12px;">Comparison</h3>' +
    '<div class="comparison-grid">' +
    '<div class="comparison-cell label"></div>' + headers +
    bodyHtml +
    '</div>' +
    '<button class="btn secondary" id="close-compare" style="margin-top:12px;">Back to Results</button>';
  showRightContent("comparison", {html: compHtml});

  document.getElementById("close-compare").addEventListener("click", function() {
    showRightContent("placeholder");
  });
}

var METRIC_LABELS = {
  "main_net_inflow": "Main Net Inflow",
  "hsgt_net_inflow": "Northbound Flow",
  "margin_balance": "Margin Balance",
  "institutional_holding_pct": "Institutional Holding %",
  "current_price": "Current Price",
  "ma5": "MA5",
  "ma20": "MA20",
  "macd_signal": "MACD Signal",
  "rsi_14": "RSI(14)",
  "sentiment_score": "Sentiment Score",
  "pos_neg_ratio": "Positive/Negative",
  "article_count": "Articles Analyzed",
  "key_topics": "Key Topics",
  "sentiment_bias": "Sentiment Bias",
  "pe": "PE Ratio",
  "pb": "PB Ratio",
  "roe": "ROE",
  "revenue_growth": "Revenue Growth",
  "eps": "EPS",
  "competitor_count": "Competitors Analyzed",
  "relative_position": "Relative Position",
  "key_competitor": "Key Competitor",
  "partner_count": "Partners Analyzed",
  "supply_chain_risk": "Supply Chain Risk",
  "key_partner": "Key Partner",
  "manipulation_risk": "Manipulation Risk",
};

var ANALYST_CARD_ORDER = [
  "capital_flow", "market", "social", "news",
  "fundamentals", "competitor", "partner"
];

var ANALYST_CARD_NAMES = {
  "capital_flow": "Capital Flow",
  "market": "Market",
  "social": "Sentiment",
  "news": "News",
  "fundamentals": "Fundamentals",
  "competitor": "Competitor",
  "partner": "Partner",
};

var ANALYST_CARD_COLORS = {
  "capital_flow": "#f0883e",
  "market": "#3fb950",
  "social": "#a371f7",
  "news": "#ffd700",
  "fundamentals": "#53d8fb",
  "competitor": "#ff6b6b",
  "partner": "#48dbfb",
};

function renderRetracementCard(retrace) {
  if (!retrace || !retrace.signal_type || retrace.signal_type === "NONE") {
    return '<div class="retracement-card" style="border-left-color:#888;"><div class="retracement-header">No Active Retracement Signal</div></div>';
  }

  var typeLabel = retrace.signal_type === "REBOUND" ? t("retrace.rebound") : t("retrace.pullback");
  var tierLabel = retrace.tier || "WEAK";
  var tierColor = tierLabel === "STRONG" ? "var(--green)" : tierLabel === "MODERATE" ? "#f0ad4e" : "var(--red)";

  var html = '<div class="retracement-card">';
  html += '<div class="retracement-header">Retracement Signal</div>';
  html += '<div class="retracement-type">Type: ' + esc(typeLabel) + '</div>';
  html += '<div class="retracement-total">Total Score: ' + esc(retrace.total_score || "0/14") + ' <span style="color:' + tierColor + ';font-weight:700;">' + esc(tierLabel) + '</span></div>';

  // Score grid: 7 dimensions in a responsive grid
  var dims = [
    {key: "technical", label: "Technical"},
    {key: "capital_flow", label: "Cap.Flow"},
    {key: "sentiment", label: "Sentiment"},
    {key: "news", label: "News"},
    {key: "fundamentals", label: "Fundam."},
    {key: "competitor", label: "Competitor"},
    {key: "partner", label: "Partner"},
  ];

  html += '<div class="retracement-grid">';
  dims.forEach(function(d) {
    var score = retrace[d.key];
    var scoreNum = score ? parseInt(score, 10) : 0;
    var dotColor = scoreNum === 2 ? "var(--green)" : scoreNum === 1 ? "#f0ad4e" : "var(--red)";
    var dot = scoreNum === 2 ? "🟢" : scoreNum === 1 ? "🟡" : "🔴";
    html += '<div class="retracement-dim">';
    html += '<div class="retracement-dim-label">' + esc(d.label) + '</div>';
    html += '<div class="retracement-dim-score" style="color:' + dotColor + ';">' + dot + ' ' + esc(String(scoreNum)) + '/2</div>';
    html += '</div>';
  });
  html += '</div>';

  // Supporting details
  if (retrace.retracement_depth || retrace.retracement_duration || retrace.volume_pattern || retrace.support_level) {
    html += '<div class="retracement-details">';
    if (retrace.retracement_depth) html += '<span>Depth: ' + esc(retrace.retracement_depth) + '</span> ';
    if (retrace.retracement_duration) html += '<span>Duration: ' + esc(retrace.retracement_duration) + ' days</span> ';
    if (retrace.volume_pattern) html += '<span>Volume: ' + esc(retrace.volume_pattern) + '</span> ';
    if (retrace.support_level) html += '<span>Support: ' + esc(retrace.support_level) + '</span>';
    html += '</div>';
  }

  // Suggestion text based on tier
  var suggestion = "";
  if (tierLabel === "STRONG") {
    suggestion = "Strong buy-the-dip signal. Multiple dimensions confirm the retracement is a genuine opportunity.";
  } else if (tierLabel === "MODERATE") {
    suggestion = "Mixed signals — requires human judgment. Some dimensions confirm but others are neutral or conflicting.";
  } else {
    suggestion = "Weak or conflicting signals. Retracement may be a trap or trend break. Not recommended without further confirmation.";
  }
  html += '<div class="retracement-suggestion">' + esc(suggestion) + '</div>';

  html += '</div>';
  return html;
}

function renderKeyMetricsCards(metrics) {
  if (!metrics || typeof metrics !== "object") {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No key metrics available for this analysis.</div>';
  }

  var html = '<div class="metric-grid">';
  var hasAny = false;

  ANALYST_CARD_ORDER.forEach(function(analystKey) {
    var kv = metrics[analystKey];
    if (!kv || typeof kv !== "object" || Object.keys(kv).length === 0) return;
    hasAny = true;
    var color = ANALYST_CARD_COLORS[analystKey] || "#888";
    var name = ANALYST_CARD_NAMES[analystKey] || analystKey;
    html += '<div class="metric-card" style="border-left: 3px solid ' + color + ';">';
    html += '<div class="metric-card-header">' + esc(name) + '</div>';
    html += '<div class="metric-card-body">';
    Object.keys(kv).forEach(function(k) {
      var label = METRIC_LABELS[k] || k;
      var val = kv[k];
      html += '<div class="metric-row">';
      html += '<span class="metric-label">' + esc(label) + '</span>';
      html += '<span class="metric-value">' + esc(String(val)) + '</span>';
      html += '</div>';
    });
    html += '</div></div>';
  });

  if (!hasAny) {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No key metrics available for this analysis.</div>';
  }

  html += '</div>';
  return html;
}

function renderToolTraces(traces) {
  if (!traces || typeof traces !== "object") {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No tool-call traces recorded.</div>';
  }

  var hasAny = false;
  var html = '<div style="padding-top:0;">';
  html += '<details class="tool-traces-details">';
  html += '<summary style="cursor:pointer;font-weight:600;font-size:13px;">Tool-Call Traces</summary>';
  html += '<div class="tool-traces-body" style="margin-top:8px;">';

  ANALYST_CARD_ORDER.forEach(function(analystKey) {
    var records = traces[analystKey];
    if (!records || !Array.isArray(records) || records.length === 0) return;
    hasAny = true;
    var name = ANALYST_CARD_NAMES[analystKey] || analystKey;
    var color = ANALYST_CARD_COLORS[analystKey] || "#888";
    html += '<details class="tool-analyst-details" style="margin-bottom:6px;">';
    html += '<summary style="cursor:pointer;font-size:12px;color:' + color + ';font-weight:600;">';
    html += esc(name) + ' &#8212; ' + records.length + ' tool call(s)';
    html += '</summary>';
    html += '<div style="padding-left:14px;margin-top:4px;">';
    records.forEach(function(rec) {
      var argsStr = "";
      if (rec.tool_args && typeof rec.tool_args === "object") {
        var parts = [];
        Object.keys(rec.tool_args).forEach(function(k) {
          var v = rec.tool_args[k];
          if (typeof v === "string" && v.length > 80) v = v.substring(0, 80) + "...";
          parts.push(k + "=" + String(v));
        });
        argsStr = parts.join(", ");
      }
      html += '<details class="tool-item-details" style="margin-bottom:4px;">';
      html += '<summary style="cursor:pointer;font-size:11px;color:var(--text-muted);font-family:monospace;">';
      html += esc(rec.tool_name || "unknown") + '(' + esc(argsStr) + ')';
      if (rec.result_snippet) {
        var snippetLen = rec.result_snippet.length;
        html += ' <span style="opacity:0.6;">(' + snippetLen + ' chars)</span>';
      }
      html += '</summary>';
      if (rec.result_snippet) {
        html += '<pre class="tool-result-pre">' + esc(rec.result_snippet) + '</pre>';
      }
      html += '</details>';
    });
    html += '</div></details>';
  });

  html += '</div></details></div>';

  if (!hasAny) {
    return '<div class="placeholder-hint" style="padding:8px;font-size:12px;">No tool-call traces recorded.</div>';
  }
  return html;
}

// ---- Scheduled Tasks Module ----
var CRON_PRESETS = [
  { label: "Weekdays at 9:30 AM",    value: "30 9 * * 1-5" },
  { label: "Weekdays at 4:00 PM",    value: "0 16 * * 1-5" },
  { label: "Daily at 8:00 AM",       value: "0 8 * * *" },
  { label: "Every Monday at 8:00 AM", value: "0 8 * * 1" },
  { label: "Every Monday at 9:30 AM", value: "30 9 * * 1" },
  { label: "Every 4 hours (weekdays)", value: "0 */4 * * 1-5" },
  { label: "Custom...",               value: "" },
];

function cronToHuman(cron) {
  var map = {};
  CRON_PRESETS.forEach(function(p) { if (p.value) map[p.value] = p.label; });
  return map[cron] || cron;
}

// ── Scheduled-tab auto-refresh ──
// While the user stays on the 定时任务 tab, poll for finished runs every few
// seconds and auto-open the freshly-run task's report in the right panel
// ("运行完自动显示"). The last_run snapshot is seeded on every explicit
// refresh (loadScheduledTasks) so the poll only fires on NEW runs, never on
// initial load. Leaving the tab stops the poll (and the poll self-stops if
// the tab is switched by code paths that skip the tab handler).
var _schedPollTimer = null;
var _schedLastRunAt = {};    // job_id -> last_run.run_at (completed-run marker)
var _schedProgressSig = {};  // job_id -> "phase|analyzed|total|elapsed" (in-flight marker)
var _schedLiveJobId = null;  // job_id whose live-progress panel is in the right panel

// job_id -> latest task snapshot, so openSchedReport(jobId) without a task
// (e.g. re-opening the last report after a tab switch) still knows whether
// the task is currently running.
var _schedTaskCache = {};

// Matches the backend's BOT_SECRET_MASK — the auto-filled placeholder for the
// 智能机器人 secret. Saving it (or an empty value) means "use the global .env
// secret", so the real secret never has to appear in the page.
var _BOT_SECRET_MASK = "••••••";

function _schedSig(task) {
  // Elapsed is included so an in-flight run keeps re-rendering every poll —
  // otherwise the elapsed counter would freeze while the first stock is still
  // being analyzed (phase/analyzed/total unchanged). Idle tasks stay "" → no
  // re-render.
  return task.running && task.progress
    ? (task.progress.phase + "|" + (task.progress.analyzed || 0) + "|" + (task.progress.total || 0) + "|" + (task.progress.elapsed_sec || 0))
    : "";
}

function startSchedPoll() {
  if (_schedPollTimer) return;
  _schedPollTimer = setInterval(pollScheduledTasks, 6000);
}

function stopSchedPoll() {
  if (_schedPollTimer) { clearInterval(_schedPollTimer); _schedPollTimer = null; }
}

function pollScheduledTasks() {
  if (currentTab !== "scheduled") { stopSchedPoll(); return; }
  fetch("/api/scheduler/tasks")
    .then(function(r) { return r.json(); })
    .then(function(tasks) {
      var changed = false;
      var autoOpen = null;
      var autoOpenTask = null;
      tasks.forEach(function(task) {
        var at = (task.last_run && task.last_run.run_at) || "";
        if (at === _schedLastRunAt[task.job_id]) {
          // No new completed run — but an in-flight run may have advanced.
          var sig = _schedSig(task);
          if (sig !== (_schedProgressSig[task.job_id] || "")) {
            _schedProgressSig[task.job_id] = sig;
            changed = true;
          }
          return;
        }
        changed = true;
        var prev = _schedLastRunAt[task.job_id] || "";
        _schedLastRunAt[task.job_id] = at;
        // A previously-empty (or older) run_at just became a real run_at →
        // that task finished. Auto-show its output.
        if (at && at !== prev) { autoOpen = task.job_id; autoOpenTask = task; }
      });
      if (!changed) return;
      renderTaskList(tasks);
      if (autoOpen) { openSchedReport(autoOpen, autoOpenTask); return; }
      // Keep the live-progress right panel in sync while a run is in flight —
      // only when the scheduled view is actually showing (guard against the
      // tab having been switched away while a run was open).
      if (_schedLiveJobId && document.getElementById("app").classList.contains("sched-mode")) {
        var live = null;
        tasks.forEach(function(tt) { if (tt.job_id === _schedLiveJobId) live = tt; });
        if (live) {
          if (live.running) renderSchedRunning(_schedLiveJobId, live);
          else if (live.last_run) {
            var jid = _schedLiveJobId;
            _schedLiveJobId = null;
            openSchedReport(jid, live);
          }
        }
      }
    })
    .catch(function() { /* transient poll failure — try again next tick */ });
}

function loadScheduledTasks() {
  fetch("/api/scheduler/tasks")
    .then(function(r) { return r.json(); })
    .then(function(tasks) {
      renderTaskList(tasks);
      // Seed the poll snapshots (no auto-open on explicit refresh).
      _schedLastRunAt = {};
      _schedProgressSig = {};
      tasks.forEach(function(t) {
        _schedLastRunAt[t.job_id] = (t.last_run && t.last_run.run_at) || "";
        _schedProgressSig[t.job_id] = _schedSig(t);
      });
    })
    .catch(function() {
      document.getElementById("task-list").innerHTML = '<span class="no-results">Failed to load tasks</span>';
    });
  loadTaskAudit();
}

function taskTypeLabel(task) {
  var m = { deep: "sched.typeDeep", emwl_batch: "sched.typeEmwl", idx_batch: "sched.typeIdx" };
  return t(m[task.task_type] || "sched.typeDeep");
}

function taskLastRunHtml(task) {
  if (!task.last_run) {
    return '<span style="color:var(--text-muted);font-size:11px;">' + t("sched.neverRun") + '</span>';
  }
  var s = task.last_run.summary || {};
  var parts = [];
  if (s.analyzed != null) parts.push(t("sched.summaryAnalyzed", { n: s.analyzed }));
  if (s.bullish != null) parts.push(t("sched.summaryBullish", { n: s.bullish }));
  if (s.twopass_record_id != null) parts.push(t("sched.summaryTwopass"));
  if (s.stage3_record_id != null) parts.push(t("sched.summaryStage3"));
  var sum = parts.join(" · ");
  return '<span style="font-size:11px;">' +
    esc(task.last_run.run_at || "") + (sum ? " — " + esc(sum) : "") +
    '</span>';
}

function fmtDuration(sec) {
  sec = Math.max(0, Math.floor(sec || 0));
  var zh = window.I18N && I18N.currentLang() === "zh";
  var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  if (h) return h + (zh ? "小时" : "h ") + m + (zh ? "分" : "m");
  if (m) return m + (zh ? "分" : "m ") + s + (zh ? "秒" : "s");
  return s + (zh ? "秒" : "s");
}

function schedPhaseLabel(phase) {
  var m = { analyzing: "sched.phaseAnalyzing", twopass: "sched.phaseTwopass", stage3: "sched.phaseStage3", deep: "sched.phaseDeep" };
  return t(m[phase] || "sched.phaseDeep");
}

// Live in-flight progress for a task row — empty string when the task is idle.
function taskProgressHtml(task) {
  if (!task.running || !task.progress) return "";
  var p = task.progress;
  var text = t("sched.running") + " · " + schedPhaseLabel(p.phase);
  var bar = "";
  if (p.total > 0) {
    var pct = Math.min(100, Math.round(((p.analyzed || 0) * 100) / p.total));
    text += " · " + t("sched.progressFrac", { done: p.analyzed, total: p.total });
    bar = '<div class="task-progress-bar"><div class="task-progress-fill" style="width:' + pct + '%;"></div></div>';
  }
  if (p.current) text += ' · <span style="color:var(--text-muted);">' + t("sched.current", { name: esc(p.current) }) + '</span>';
  text += " · " + t("sched.elapsed", { t: fmtDuration(p.elapsed_sec || 0) });
  return '<div style="display:flex;flex-direction:column;gap:3px;padding:2px 0;">' +
    '<span class="task-progress-text">' + text + '</span>' + bar + '</div>';
}

function renderTaskList(tasks) {
  var list = document.getElementById("task-list");
  if (!tasks || !tasks.length) {
    list.innerHTML = '<span class="no-results">' + t("sched.empty") + '</span>';
    return;
  }
  var html = "";
  tasks.forEach(function(task) {
    _schedTaskCache[task.job_id] = task;
    var nextRun = task.next_run
      ? new Date(/[+-]\d\d:\d\d|Z$/.test(task.next_run) ? task.next_run : task.next_run + "Z").toLocaleString()
      : t("sched.paused");
    var statusHtml = task.enabled
      ? '<span class="task-next">' + t("sched.next", { time: esc(nextRun) }) + '</span>'
      : '<span class="task-paused">' + t("sched.paused") + '</span>';
    var progHtml = taskProgressHtml(task);
    html +=
      '<div class="task-item" data-job-id="' + esc(task.job_id) + '">' +
      '<div class="task-row">' +
      '<span class="task-name">' + esc(task.name) + '</span>' +
      '<span style="display:inline-block;padding:1px 6px;border-radius:10px;background:var(--accent);color:#fff;font-size:10px;font-weight:600;">' + esc(taskTypeLabel(task)) + '</span>' +
      '<span class="task-ticker">' + esc(task.ticker || "") + '</span>' +
      '<span class="task-cron">' + esc(cronToHuman(task.trigger || "")) + '</span>' +
      '</div>' +
      '<div class="task-row">' +
      statusHtml +
      '<span class="task-cron">' + taskLastRunHtml(task) + '</span>' +
      '<div class="task-actions">' +
      '<button data-action="run" data-job-id="' + esc(task.job_id) + '">' + t("sched.runNow") + '</button>' +
      '<button data-action="report" data-job-id="' + esc(task.job_id) + '">' + t("sched.viewReport") + '</button>' +
      '<button data-action="edit" data-job-id="' + esc(task.job_id) + '">' + t("sched.edit") + '</button>' +
      (task.enabled
        ? '<button data-action="pause" data-job-id="' + esc(task.job_id) + '">' + t("sched.pause") + '</button>'
        : '<button data-action="resume" data-job-id="' + esc(task.job_id) + '">' + t("sched.resume") + '</button>') +
      '<button class="danger" data-action="delete" data-job-id="' + esc(task.job_id) + '">' + t("sched.delete") + '</button>' +
      '</div>' +
      '</div>' +
      progHtml +
      '</div>';
  });
  list.innerHTML = html;

  // Re-apply highlight to the previously selected task (list was re-rendered).
  if (currentSchedJobId) {
    var active = list.querySelector('.task-item[data-job-id="' + currentSchedJobId + '"]');
    if (active) active.style.outline = "2px solid var(--accent)";
  }

  list.querySelectorAll("button[data-action]").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var action = btn.dataset.action;
      var jobId = btn.dataset.jobId;
      if (action === "pause") { toggleTask(jobId, false); }
      else if (action === "resume") { toggleTask(jobId, true); }
      else if (action === "delete") { deleteTask(jobId); }
      else if (action === "run") { runTaskNow(jobId); }
      else if (action === "report") {
        // Resolve the task from the render's tasks array (the button listener
        // lives outside the per-task forEach, so `task` is out of scope here).
        var t = null;
        for (var ti = 0; ti < tasks.length; ti++) {
          if (tasks[ti].job_id === jobId) { t = tasks[ti]; break; }
        }
        openSchedReport(jobId, t);
      }
      else if (action === "edit") { editTask(jobId); }
    });
  });
}

function runTaskNow(jobId) {
  fetch("/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/run", { method: "POST" })
    .then(function(r) {
      if (!r.ok) { return r.json().then(function(d) { throw new Error(d.detail || ("HTTP " + r.status)); }); }
      return r.json();
    })
    .then(function() { loadScheduledTasks(); })
    .catch(function(e) { alert(t("sched.runNowFailed", { msg: e.message })); });
}

function toggleTask(jobId, enabled) {
  fetch("/api/scheduler/tasks/" + jobId, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled: enabled }),
  }).then(function() { loadScheduledTasks(); });
}

function deleteTask(jobId) {
  if (!confirm(t("sched.deleteConfirm"))) return;
  fetch("/api/scheduler/tasks/" + jobId, { method: "DELETE" })
    .then(function() { loadScheduledTasks(); });
}

var currentSchedJobId = null;

function openSchedReport(jobId, task, runId) {
  if (!task && _schedTaskCache[jobId]) task = _schedTaskCache[jobId];
  currentSchedJobId = jobId;
  // Highlight the selected row in the left task list.
  var items = document.querySelectorAll("#task-list .task-item");
  for (var i = 0; i < items.length; i++) { items[i].style.outline = ""; }
  var el = document.querySelector('#task-list .task-item[data-job-id="' + jobId + '"]');
  if (el) el.style.outline = "2px solid var(--accent)";

  // Show the report in the dashboard's right panel (to the right of the
  // scheduler task list), following the advisory/aipick {mode} pattern.
  var app = document.getElementById("app");
  if (app) app.classList.add("sched-mode");
  var rightPanel = document.getElementById("right-panel");
  if (rightPanel) rightPanel.classList.remove("hide-all");

  var panel = document.getElementById("sched-expanded");
  if (!panel) return;

  // A run that is still in flight has no report yet — show its live progress
  // instead of the "尚未运行" empty panel. (Only for the live/latest view;
  // explicitly selecting a past run shows that run's report directly.)
  if (task && task.running && !runId) { renderSchedRunning(jobId, task); return; }
  _schedLiveJobId = null;

  panel.innerHTML = '<div class="label" style="padding:8px 0;">' + t("sched.reportLoading") + '</div>';

  var q = "/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/report?";
  if (runId) q += "run_id=" + runId + "&";
  q += "format=md";
  var dl = "/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/report?";
  if (runId) dl += "run_id=" + runId + "&";
  dl += "format=";

  fetch(q)
    .then(function(r) {
      if (r.status === 404) { return null; } // no run yet
      if (!r.ok) { return r.text().then(function(d) { throw new Error(d.detail || ("HTTP " + r.status)); }); }
      return r.text();
    })
    .then(function(md) {
      if (md === null) {
        panel.innerHTML =
          '<div style="display:flex;align-items:center;justify-content:space-between;padding:0 0 8px 0;flex-shrink:0;">' +
          '<span style="font-size:16px;font-weight:700;">' + t("sched.reportTitle") + '</span></div>' +
          '<div class="no-results" style="padding:24px 0;text-align:center;background:var(--bg);border-radius:8px;border:1px solid var(--border);">' + t("sched.reportEmpty") + '</div>';
        return;
      }
      panel.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:space-between;padding:0 0 8px 0;flex-shrink:0;flex-wrap:wrap;gap:6px;">' +
        '<span style="font-size:16px;font-weight:700;">' + t("sched.reportTitle") + '</span>' +
        '<span style="font-size:11px;color:var(--text-muted);">' + t("sched.reportDownload") +
        ' <a href="' + dl + 'docx" download>' + t("sched.reportDocx") + '</a>' +
        ' · <a href="' + dl + 'md" download>' + t("sched.reportMd") + '</a>' +
        ' · <a href="' + dl + 'json" download>' + t("sched.reportJson") + '</a>' +
        ' &nbsp;·&nbsp; <button type="button" id="sched-push-wechat" style="padding:2px 8px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;color:var(--text);">' + t("sched.pushWechat") + '</button>' +
        '</span></div>' +
        '<div id="sched-runs" style="flex-shrink:0;"></div>' +
        '<div class="markdown-body" style="flex:1;overflow-y:auto;padding:16px;background:var(--bg);border-radius:8px;border:1px solid var(--border);min-height:300px;font-size:12px;line-height:1.7;">' +
        renderMarkdown(md) + '</div>';
      renderSchedRuns(jobId, runId);

      // 「推送微信」— manually re-push the current run's WeChat card through
      // the same push_run_result the automatic scheduled hook uses.
      var pushBtn = document.getElementById("sched-push-wechat");
      if (pushBtn) {
        pushBtn.addEventListener("click", function() {
          if (pushBtn.disabled) return;
          pushBtn.disabled = true;
          var pushUrl = "/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/push";
          if (runId) pushUrl += "?run_id=" + runId;
          fetch(pushUrl, { method: "POST" })
            .then(function(r) {
              return r.json().catch(function() { return null; }).then(function(d) {
                return { status: r.status, body: d };
              });
            })
            .then(function(res) {
              var d = res.body;
              if (res.status === 404) {
                alert((d && d.detail) || t("sched.pushWechatNoRun"));
              } else if (d && d.ok) {
                alert(t("sched.pushWechatOk"));
              } else {
                alert((d && d.errmsg) || t("sched.pushWechatFail"));
              }
            })
            .catch(function(e) {
              alert(t("sched.createFailed", { msg: e.message }));
            })
            .finally(function() { pushBtn.disabled = false; });
        });
      }
    })
    .catch(function(e) {
      panel.innerHTML =
        '<div style="display:flex;align-items:center;justify-content:space-between;padding:0 0 8px 0;flex-shrink:0;">' +
        '<span style="font-size:16px;font-weight:700;">' + t("sched.reportTitle") + '</span></div>' +
        '<div class="no-results" style="padding:24px 0;text-align:center;">' + esc(e.message) + '</div>';
    });
}

// Run-history selector shown above the report body. Every run is stored in
// scheduled_run_log; picking one reloads the report for that specific run via
// ?run_id=, so past results stay queryable after the latest one scrolls away.
function renderSchedRuns(jobId, activeRunId) {
  var holder = document.getElementById("sched-runs");
  if (!holder) return;
  fetch("/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/runs")
    .then(function(r) { return r.json(); })
    .then(function(runs) {
      if (!runs || !runs.length) { holder.innerHTML = ""; return; }
      var opts = '<option value="">' + t("sched.runLatest") + '</option>';
      runs.forEach(function(run) {
        var s = run.summary || {};
        var label = esc(run.run_at || "");
        if (run.task_type === "deep") {
          if (s.rating) label += " — " + esc(s.rating) + (s.signal ? " · " + esc(s.signal) : "");
        } else {
          var parts = [];
          if (s.analyzed != null) parts.push(t("sched.summaryAnalyzed", { n: s.analyzed }));
          if (s.bullish != null) parts.push(t("sched.summaryBullish", { n: s.bullish }));
          if (s.twopass_record_id != null) parts.push(t("sched.summaryTwopass"));
          if (s.stage3_record_id != null) parts.push(t("sched.summaryStage3"));
          if (parts.length) label += " — " + esc(parts.join(" · "));
        }
        opts += '<option value="' + run.id + '"' + (String(run.id) === String(activeRunId) ? " selected" : "") + '>' + label + '</option>';
      });
      holder.innerHTML =
        '<div style="display:flex;align-items:center;gap:6px;padding:0 0 8px 0;flex-wrap:wrap;">' +
        '<span style="font-size:11px;color:var(--text-muted);white-space:nowrap;">' + t("sched.runHistory") + '</span>' +
        '<select style="font-size:11px;max-width:100%;flex:1;min-width:140px;">' + opts + '</select>' +
        '</div>';
      var sel = holder.querySelector("select");
      sel.addEventListener("change", function() {
        var rid = sel.value;
        openSchedReport(jobId, _schedTaskCache[jobId], rid ? parseInt(rid, 10) : null);
      });
    })
    .catch(function() { holder.innerHTML = ""; });
}

// Live in-flight progress shown in the right panel while a task is running.
// The 6s poll re-renders this each tick; when the run completes, the poll
// auto-opens the full report in its place.
function renderSchedRunning(jobId, task) {
  _schedLiveJobId = jobId;
  var panel = document.getElementById("sched-expanded");
  if (!panel) return;
  var p = task.progress || {};
  var text = t("sched.running") + " · " + schedPhaseLabel(p.phase);
  var bar = "";
  if (p.total > 0) {
    var pct = Math.min(100, Math.round(((p.analyzed || 0) * 100) / p.total));
    text += " · " + t("sched.progressFrac", { done: p.analyzed, total: p.total });
    bar = '<div style="width:100%;height:8px;background:var(--border);border-radius:4px;overflow:hidden;margin:10px 0;">' +
      '<div style="width:' + pct + '%;height:100%;background:linear-gradient(90deg,var(--accent),#0550ae);border-radius:4px;transition:width .5s;"></div></div>';
  }
  if (p.current) text += ' · <span style="color:var(--text-muted);">' + t("sched.current", { name: esc(p.current) }) + '</span>';
  text += " · " + t("sched.elapsed", { t: fmtDuration(p.elapsed_sec || 0) });
  panel.innerHTML =
    '<div style="display:flex;align-items:center;justify-content:space-between;padding:0 0 8px 0;flex-shrink:0;flex-wrap:wrap;gap:6px;">' +
    '<span style="font-size:16px;font-weight:700;">' + t("sched.runTitle") + '</span>' +
    '<span style="font-size:12px;color:var(--text-muted);">' + esc(task.name || "") + '</span></div>' +
    '<div style="background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:16px;">' +
    '<span class="task-progress-text" style="font-size:13px;">' + text + '</span>' + bar +
    '<div style="font-size:11px;color:var(--text-muted);margin-top:6px;">' + t("sched.runDesc") + '</div>' +
    '</div>';
}

function loadTaskAudit() {
  fetch("/api/scheduler/audit?limit=200")
    .then(function(r) { return r.json(); })
    .then(renderTaskAudit)
    .catch(function() {
      var list = document.getElementById("task-audit-list");
      if (list) list.innerHTML = '<span class="no-results">Failed to load audit log</span>';
    });
}

function renderTaskAudit(entries) {
  var list = document.getElementById("task-audit-list");
  if (!list) return;
  if (!entries || !entries.length) {
    list.innerHTML = '<span class="no-results">' + t("sched.auditEmpty") + '</span>';
    return;
  }
  var actionKeys = {
    create: "sched.auditCreate",
    delete: "sched.auditDelete",
    pause: "sched.auditPause",
    resume: "sched.auditResume",
    run: "sched.auditRun",
    update: "sched.auditUpdate"
  };
  var actionColors = {
    create: "var(--accent)",
    delete: "#e05d5d",
    pause: "#d99a2b",
    resume: "#4caf50",
    run: "#5b8ff9",
    update: "#8a6de9"
  };
  var html = entries.map(function(e) {
    var key = actionKeys[e.action] || e.action;
    var badge = '<span style="display:inline-block;padding:0 6px;border-radius:8px;background:' +
      (actionColors[e.action] || "var(--accent)") + ';color:#fff;font-size:10px;font-weight:600;">' +
      esc(t(key)) + '</span>';
    return '<div style="display:flex;gap:8px;align-items:center;padding:2px 0;flex-wrap:wrap;">' +
      '<span style="color:var(--text-muted);font-size:11px;white-space:nowrap;">' + esc(e.created_at || "") + '</span>' +
      badge +
      '<span style="font-size:12px;">' + esc(e.task_name || "") + '</span>' +
      '<span style="color:var(--text-muted);font-size:11px;">(' + esc(taskTypeLabel({ task_type: e.task_type })) + ')</span>' +
      '</div>';
  }).join("");
  list.innerHTML = html;
}

function postTask(body, container) {
  fetch("/api/scheduler/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  .then(function(r) {
    if (!r.ok) { return r.json().then(function(d) { throw new Error(d.detail || ("HTTP " + r.status)); }); }
    return r.json();
  })
  .then(function() {
    schedEditJobId = null;
    container.innerHTML = "";
    loadScheduledTasks();
  })
  .catch(function(e) { alert(t("sched.createFailed", { msg: e.message })); });
}

// job_id being edited (null = the form is creating a new task).
var schedEditJobId = null;

function updateTaskConfig(jobId, body, container) {
  fetch("/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  .then(function(r) {
    if (!r.ok) { return r.json().then(function(d) { throw new Error(d.detail || ("HTTP " + r.status)); }); }
    return r.json();
  })
  .then(function() {
    schedEditJobId = null;
    container.innerHTML = "";
    loadScheduledTasks();
  })
  .catch(function(e) { alert(t("sched.editFailed", { msg: e.message })); });
}

function editTask(jobId) {
  fetch("/api/scheduler/tasks/" + encodeURIComponent(jobId) + "/config")
    .then(function(r) {
      if (!r.ok) { return r.json().then(function(d) { throw new Error(d.detail || ("HTTP " + r.status)); }); }
      return r.json();
    })
    .then(function(cfg) {
      var container = document.getElementById("task-form-container");
      openTaskForm(container, cfg);   // replaces whatever the form is showing
    })
    .catch(function(e) { alert(t("sched.editFailed", { msg: e.message })); });
}

// Parse a stored Python cron expression into the form's daily/weekly/custom
// shape so an existing task's schedule can be prefilled. Python cron Sunday=0
// is mapped to the UI's Sunday=7.
function pad2(n) { return ("0" + n).slice(-2); }
function cronToFormParts(expr) {
  var parts = (expr || "").trim().split(/\s+/);
  if (parts.length === 5) {
    var hm = pad2(parts[1]) + ":" + pad2(parts[0]);
    if (parts[2] === "*" && parts[3] === "*" && parts[4] === "*") {
      return { mode: "daily", hm: hm };
    }
    if (parts[2] === "*" && parts[3] === "*") {
      var days = [];
      parts[4].split(",").forEach(function(x) {
        var v = parseInt(x, 10);
        if (!isNaN(v)) days.push(v === 0 ? 7 : v);
      });
      return { mode: "weekly", hm: hm, days: days };
    }
  }
  return { mode: "custom", expr: expr };
}

// ── Scheduled-task form (task-type switching) ──
var SCHED_INDEXES = [
  { key: "csi300",   i18n: "idx.name.csi300" },
  { key: "csi500",   i18n: "idx.name.csi500" },
  { key: "csi1000",  i18n: "idx.name.csi1000" },
  { key: "gem",      i18n: "idx.name.gem" },
  { key: "star50",   i18n: "idx.name.star50" },
  { key: "sse50",    i18n: "idx.name.sse50" },
  { key: "highdiv",  i18n: "idx.name.highdiv" },
  { key: "shanghai", i18n: "idx.name.shanghai" },
  { key: "cycle",    i18n: "idx.name.cycle" },
  { key: "microcap", i18n: "idx.name.microcap" },
];
var SCHED_BOARDS = [
  { key: "cyb", i18n: "sched.boardCyb" },
  { key: "kcb", i18n: "sched.boardKcb" },
  { key: "bjs", i18n: "sched.boardBjs" },
];
var SCHED_WEEKDAYS = [
  { v: 1, i18n: "sched.mon" }, { v: 2, i18n: "sched.tue" }, { v: 3, i18n: "sched.wed" },
  { v: 4, i18n: "sched.thu" }, { v: 5, i18n: "sched.fri" }, { v: 6, i18n: "sched.sat" },
  { v: 7, i18n: "sched.sun" },
];
var schedModelCatalog = null;  // cached /api/models for worker rows

function schedProvidersHtml() {
  var h = '<option value="">' + t("common.defaultProvider") + '</option>';
  ((schedModelCatalog && schedModelCatalog.providers) || []).forEach(function(p) {
    h += '<option value="' + esc(p.key) + '">' + esc(p.label) + '</option>';
  });
  return h;
}

function schedWorkerRowHTML(idx) {
  return '<div class="sched-worker-row" style="display:flex;gap:6px;align-items:center;flex:1 1 100%;">' +
    '<span style="font-size:11px;font-weight:600;color:var(--text-muted);min-width:14px;">' + (idx + 1) + '</span>' +
    '<select class="sched-worker-provider" style="flex:1;min-width:0;">' + schedProvidersHtml() + '</select>' +
    '<select class="sched-worker-model" style="flex:1.4;min-width:0;"><option value="">' + t("common.defaultModel") + '</option></select>' +
    '<button type="button" class="sched-worker-refresh" title="' + t("sched.refreshModels") + '" style="padding:3px 6px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;">🔄</button>' +
    '<button type="button" class="sched-worker-remove" style="padding:3px 8px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;">&times;</button>' +
    '</div>';
}

function schedOnWorkerProviderChange(sel) {
  var row = sel.closest(".sched-worker-row");
  if (!row) return;
  var modelSel = row.querySelector(".sched-worker-model");
  var prov = sel.value;
  if (!prov || !schedModelCatalog) {
    modelSel.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>';
    return;
  }
  var pd = (schedModelCatalog.providers || []).find(function(p) { return p.key === prov; });
  if (!pd) { modelSel.innerHTML = '<option value="">--</option>'; return; }
  modelSel.innerHTML = pd.deep_models.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
}

function schedBindWorkerRow(row, workersBox) {
  var provSel = row.querySelector(".sched-worker-provider");
  var removeBtn = row.querySelector(".sched-worker-remove");
  var refreshBtn = row.querySelector(".sched-worker-refresh");
  if (provSel) provSel.addEventListener("change", function() { schedOnWorkerProviderChange(provSel); });
  if (removeBtn) removeBtn.addEventListener("click", function() {
    if (workersBox.querySelectorAll(".sched-worker-row").length <= 1) return;
    row.parentNode.removeChild(row);
  });
  if (refreshBtn) refreshBtn.addEventListener("click", schedRefreshModels);
}

// Re-populate every model dropdown (worker rows + the stage-3 row) from a
// freshly fetched /api/models catalog, preserving each selected provider.
// Mirrors the 东方自选 refreshEmwlModels semantics so scheduled-task models
// can be refreshed / selected live.
// Preserve the previously selected value if the new options still contain it.
function schedRestoreSelection(sel, prevVal) {
  if (!sel || !prevVal) return;
  var still = Array.prototype.some.call(sel.options, function(o) { return o.value === prevVal; });
  if (still) sel.value = prevVal;
}

function schedPopulateModelSelects(d) {
  schedModelCatalog = d;
  document.querySelectorAll(".sched-worker-row").forEach(function(row) {
    var pv = row.querySelector(".sched-worker-provider");
    var ms = row.querySelector(".sched-worker-model");
    if (!pv) return;
    var prevProv = pv.value;
    var prevModel = ms ? ms.value : "";
    pv.innerHTML = schedProvidersHtml();
    if (prevProv) pv.value = prevProv;
    schedOnWorkerProviderChange(pv);
    schedRestoreSelection(row.querySelector(".sched-worker-model"), prevModel);
  });
  var sp = document.getElementById("tf-stage3-provider");
  if (sp) {
    var prevProv2 = sp.value;
    var prevModel2 = document.getElementById("tf-stage3-model").value;
    sp.innerHTML = schedProvidersHtml();
    if (prevProv2) sp.value = prevProv2;
    schedOnStage3ProviderChange();
    schedRestoreSelection(document.getElementById("tf-stage3-model"), prevModel2);
  }
  var dp = document.getElementById("tf-deep-provider");
  if (dp) {
    var prevDp = dp.value;
    dp.innerHTML = schedProvidersHtml();
    if (prevDp) dp.value = prevDp;
    schedOnDeepProviderChange();
  }
  var qp = document.getElementById("tf-quick-provider");
  if (qp) {
    var prevQp = qp.value;
    qp.innerHTML = schedProvidersHtml();
    if (prevQp) qp.value = prevQp;
    schedOnQuickProviderChange();
  }
}

// Re-fetch /api/models then re-populate all model dropdowns.
function schedRefreshModels() {
  fetch("/api/models")
    .then(function(r) { return r.json(); })
    .then(schedPopulateModelSelects)
    .catch(function() {});
}

// Populate the stage-3 model select from the selected provider's deep_models.
function schedOnStage3ProviderChange() {
  var prov = document.getElementById("tf-stage3-provider");
  var model = document.getElementById("tf-stage3-model");
  if (!prov || !model) return;
  var p = prov.value;
  if (!p || !schedModelCatalog) {
    model.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>';
    return;
  }
  var pd = (schedModelCatalog.providers || []).find(function(x) { return x.key === p; });
  if (!pd) { model.innerHTML = '<option value="">--</option>'; return; }
  model.innerHTML = pd.deep_models.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
}

// Populate a deep-task role model select (deep or quick) from the chosen
// provider's model list — deep_models for the deep role, quick_models for
// the quick role. Local picks default to qwen3.8:27b when the selected
// server actually hosts it (mirrors presetModelDefault for emwl/idx).
function schedPopulateRoleModel(provSel, modelSel, role) {
  if (!modelSel) return;
  var p = provSel ? provSel.value : "";
  if (!p || !schedModelCatalog) {
    modelSel.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>';
    return;
  }
  var pd = (schedModelCatalog.providers || []).find(function(x) { return x.key === p; });
  if (!pd) { modelSel.innerHTML = '<option value="">--</option>'; return; }
  var list = (role === "quick") ? (pd.quick_models || []) : (pd.deep_models || []);
  modelSel.innerHTML = list.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
  // Local picks default to qwen3.8:27b when the selected server actually
  // hosts it — leave the natural first option otherwise (remote providers
  // keep their own defaults).
  if (list.some(function(m) { return m.value === "qwen3.8:27b"; })) {
    modelSel.value = "qwen3.8:27b";
  }
}

function schedOnDeepProviderChange() {
  schedPopulateRoleModel(
    document.getElementById("tf-deep-provider"),
    document.getElementById("tf-deep-model"), "deep");
}

function schedOnQuickProviderChange() {
  schedPopulateRoleModel(
    document.getElementById("tf-quick-provider"),
    document.getElementById("tf-quick-model"), "quick");
}

function openTaskForm(container, config) {
  var editing = !!config;
  schedEditJobId = editing ? (config.job_id || null) : null;

  // Warm the model catalog for worker-row / stage-3 dropdowns. The fetch is
  // async and the form DOM is built synchronously right after, so the
  // population callback runs once the selects exist. In edit mode the catalog
  // fetch is owned by applyPrefill() (below) to avoid racing its value-set.
  if (!editing) {
    schedModelCatalog = null;
    schedRefreshModels();
  }

  var boardChecks = SCHED_BOARDS.map(function(b) {
    return '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;margin-right:8px;">' +
      '<input type="checkbox" class="sched-board" data-board="' + esc(b.key) + '" checked> ' + t(b.i18n) + '</label>';
  }).join("");
  var idxChecks = SCHED_INDEXES.map(function(i) {
    return '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;margin-right:8px;">' +
      '<input type="checkbox" class="sched-index" value="' + esc(i.key) + '"> ' + t(i.i18n) + '</label>';
  }).join("");
  var weekChecks = SCHED_WEEKDAYS.map(function(d) {
    return '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;margin-right:8px;">' +
      '<input type="checkbox" class="sched-weekday" value="' + d.v + '"> ' + t(d.i18n) + '</label>';
  }).join("");
  var workerRows = schedWorkerRowHTML(0);

  var editHeader = editing
    ? '<div class="label" style="padding:6px 0 10px 0;">' + esc(t("sched.edit") + "：") + esc(config.name || "") + '</div>'
    : "";
  container.innerHTML =
    editHeader +
    '<div class="task-form">' +
    '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.type") + '</label>' +
      '<select id="tf-type" style="flex:1;">' +
        '<option value="deep">' + t("sched.typeDeep") + '</option>' +
        '<option value="emwl_batch">' + t("sched.typeEmwl") + '</option>' +
        '<option value="idx_batch">' + t("sched.typeIdx") + '</option>' +
      '</select></div>' +
    '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.name") + '</label>' +
      '<input id="tf-name" placeholder="' + t("sched.namePh") + '" style="flex:1;"></div>' +
    '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.webhookUrl") + '</label>' +
      '<input id="tf-webhook" placeholder="' + t("sched.webhookPlaceholder") + '" style="flex:1;min-width:200px;" title="' + t("sched.webhookHint") + '">' +
      '<button type="button" id="tf-webhook-test" style="padding:3px 8px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;">' + t("sched.webhookTest") + '</button></div>' +
    '<div class="form-hint" style="font-size:11px;color:var(--text-muted);padding-left:55px;margin-top:-4px;margin-bottom:2px;">' + t("sched.webhookHint") + '</div>' +
    '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.botId") + '</label>' +
      '<input id="tf-bot-id" placeholder="' + t("sched.botIdPh") + '" style="flex:1;min-width:160px;" title="' + t("sched.botHint") + '">' +
      '<button type="button" id="tf-bot-test" style="padding:3px 8px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;">' + t("sched.botTest") + '</button></div>' +
    '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.botSecret") + '</label>' +
      '<input id="tf-bot-secret" type="password" placeholder="' + t("sched.botSecretPh") + '" style="flex:1;min-width:160px;" title="' + t("sched.botSecretHint") + '"></div>' +
    '<div class="form-row" style="flex-wrap:wrap;align-items:flex-start;"><label>' + t("sched.botUser") + '</label>' +
      '<div id="tf-bot-users" style="display:flex;flex-wrap:wrap;gap:3px 14px;flex:1;min-height:20px;padding-top:3px;">' +
        '<span style="font-size:11px;color:var(--text-muted);align-self:center;">' + t("sched.botUserLoading") + '</span>' +
      '</div>' +
      '<button type="button" id="tf-bot-users-manage" title="' + t("adv.usersPanelTitle") + '" style="padding:2px 8px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text);cursor:pointer;white-space:nowrap;">👥 ' + t("sched.botUserManage") + '</button></div>' +
    '<div class="form-hint" style="font-size:11px;color:var(--text-muted);padding-left:55px;margin-top:-4px;margin-bottom:2px;">' + t("sched.botUsersHint") + '</div>' +
    '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.schedule") + '</label>' +
      '<select id="tf-sched-mode">' +
        '<option value="daily">' + t("sched.schedDaily") + '</option>' +
        '<option value="weekly">' + t("sched.schedWeekly") + '</option>' +
        '<option value="custom">' + t("sched.schedCustom") + '</option>' +
      '</select>' +
      '<input type="time" id="tf-time" value="09:30" style="width:110px;">' +
      '<input id="tf-cron-custom" placeholder="30 9 * * 1-5" style="width:130px;display:none;">' +
      '</div>' +
    '<div class="form-row" id="tf-weekdays-row" style="flex-wrap:wrap;display:none;"><label></label>' + weekChecks + '</div>' +
    '<div class="form-row" id="tf-deep-fields" style="flex-wrap:wrap;"><label>' + t("sched.ticker") + '</label>' +
      '<input id="tf-ticker" placeholder="600036" style="flex:1;"></div>' +
    '<div class="form-row" id="tf-deep-analysts" style="flex-wrap:wrap;"><label>' + t("sched.analysts") + '</label>' +
      '<select id="tf-analysts" multiple style="height:80px;flex:1;">' +
        '<option value="capital_flow" selected>' + t("sched.analystCapitalFlow") + '</option>' +
        '<option value="market" selected>' + t("sched.analystMarket") + '</option>' +
        '<option value="social" selected>' + t("sched.analystSentiment") + '</option>' +
        '<option value="news" selected>' + t("sched.analystNews") + '</option>' +
        '<option value="fundamentals" selected>' + t("sched.analystFundamentals") + '</option>' +
        '<option value="competitor">' + t("sched.analystCompetitor") + '</option>' +
        '<option value="partner">' + t("sched.analystPartner") + '</option>' +
      '</select></div>' +
    '<div class="form-row" id="tf-deep-language" style="flex-wrap:wrap;"><label>' + t("sched.language") + '</label>' +
      '<select id="tf-language"><option value="Chinese">中文</option><option value="English">English</option></select></div>' +
    '<div class="form-row" id="tf-deep-models" style="flex-wrap:wrap;display:none;">' +
      '<span style="font-size:11px;color:var(--text-muted);align-self:center;min-width:44px;">' + t("analyze.deepProvider") + '</span>' +
      '<select id="tf-deep-provider" style="flex:1;min-width:90px;"></select>' +
      '<select id="tf-deep-model" style="flex:1.4;min-width:110px;"><option value="">' + t("common.defaultModel") + '</option></select>' +
      '<span style="font-size:11px;color:var(--text-muted);align-self:center;min-width:44px;">' + t("analyze.quickProvider") + '</span>' +
      '<select id="tf-quick-provider" style="flex:1;min-width:90px;"></select>' +
      '<select id="tf-quick-model" style="flex:1.4;min-width:110px;"><option value="">' + t("common.defaultModel") + '</option></select>' +
    '</div>' +
    '<div id="tf-batch-fields" style="display:none;">' +
      '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.boards") + '</label>' + boardChecks + '</div>' +
      '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.changeOnly") + '</label>' +
        '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;"><input type="checkbox" id="tf-change-toggle"> ' + t("sched.changeHint") + '</label>' +
        '<input type="number" id="tf-change-value" value="0" step="0.1" style="width:70px;display:none;">' +
      '</div>' +
      '<div class="form-row" id="tf-indexes-row" style="flex-wrap:wrap;display:none;"><label>' + t("sched.indexes") + '</label>' + idxChecks + '</div>' +
      '<div class="form-row" style="flex-wrap:wrap;align-items:flex-start;"><label>' + t("sched.models") + '</label>' +
        '<div id="tf-workers" style="display:flex;flex-wrap:wrap;gap:6px;flex:1;">' + workerRows + '</div>' +
        '<button type="button" id="tf-add-worker" style="padding:4px 10px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;">+ ' + t("sched.addModel") + '</button>' +
        '<button type="button" id="tf-refresh-models" style="padding:4px 10px;font-size:11px;border:1px solid var(--border);border-radius:4px;background:var(--bg);cursor:pointer;">🔄 ' + t("sched.refreshModels") + '</button>' +
      '</div>' +
      '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.twoPass") + '</label>' +
        '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;"><input type="checkbox" id="tf-twopass"> ' + t("sched.twoPassHint") + '</label>' +
        '<select id="tf-twopass-mode" style="display:none;">' +
          '<option value="reanalyze">' + t("sched.twoPassReanalyze") + '</option>' +
          '<option value="pack">' + t("sched.twoPassPack") + '</option>' +
        '</select>' +
        '<span id="tf-twopass-note" style="font-size:11px;color:var(--text-muted);display:none;">' + t("sched.twoPassCostNote") + '</span>' +
      '</div>' +
      '<div class="form-row" style="flex-wrap:wrap;"><label>' + t("sched.stage3") + '</label>' +
        '<label style="display:inline-flex;align-items:center;gap:4px;font-size:12px;"><input type="checkbox" id="tf-stage3"> ' + t("sched.stage3Hint") + '</label>' +
        '<div id="tf-stage3-row" style="display:none;flex:1 1 100%;flex-wrap:wrap;gap:6px;">' +
          '<span style="font-size:11px;color:var(--text-muted);align-self:center;">' + t("sched.stage3Model") + '</span>' +
          '<select id="tf-stage3-provider" style="flex:1;min-width:100px;">' + schedProvidersHtml() + '</select>' +
          '<select id="tf-stage3-model" style="flex:1.4;min-width:120px;"><option value="">' + t("common.defaultModel") + '</option></select>' +
        '</div>' +
      '</div>' +
    '</div>' +
    '<div class="form-row"><label></label>' +
      '<button id="tf-submit" class="btn primary" style="padding:6px 12px;font-size:12px;">' + (editing ? t("sched.save") : t("sched.create")) + '</button>' +
      '<button id="tf-cancel" class="btn secondary" style="padding:6px 12px;font-size:12px;">' + t("common.cancel") + '</button>' +
    '</div>' +
    '</div>';

  var typeSel = document.getElementById("tf-type");
  var schedMode = document.getElementById("tf-sched-mode");
  var batchFields = document.getElementById("tf-batch-fields");
  var deepFields = document.getElementById("tf-deep-fields");
  var deepAnalysts = document.getElementById("tf-deep-analysts");
  var deepLanguage = document.getElementById("tf-deep-language");
  var deepModels = document.getElementById("tf-deep-models");
  var weekdaysRow = document.getElementById("tf-weekdays-row");
  var timeInput = document.getElementById("tf-time");
  var cronCustom = document.getElementById("tf-cron-custom");
  var indexesRow = document.getElementById("tf-indexes-row");
  var workersBox = document.getElementById("tf-workers");
  var twopass = document.getElementById("tf-twopass");
  var twopassMode = document.getElementById("tf-twopass-mode");
  var twopassNote = document.getElementById("tf-twopass-note");
  var changeToggle = document.getElementById("tf-change-toggle");
  var changeValue = document.getElementById("tf-change-value");

  function applyType() {
    var isDeep = typeSel.value === "deep";
    deepFields.style.display = isDeep ? "flex" : "none";
    deepAnalysts.style.display = isDeep ? "flex" : "none";
    deepLanguage.style.display = isDeep ? "flex" : "none";
    deepModels.style.display = isDeep ? "flex" : "none";
    batchFields.style.display = isDeep ? "none" : "block";
    indexesRow.style.display = (typeSel.value === "idx_batch") ? "flex" : "none";
  }
  applyType();
  typeSel.addEventListener("change", applyType);

  function applySchedMode() {
    var m = schedMode.value;
    timeInput.style.display = (m === "custom") ? "none" : "inline-block";
    cronCustom.style.display = (m === "custom") ? "inline-block" : "none";
    weekdaysRow.style.display = (m === "weekly") ? "flex" : "none";
  }
  applySchedMode();
  schedMode.addEventListener("change", applySchedMode);

  // Build a Python-cron (北京时间) string from the schedule UI.
  function buildCron() {
    var m = schedMode.value;
    if (m === "custom") return cronCustom.value.trim();
    var hm = (timeInput.value || "09:30").split(":");
    var H = parseInt(hm[0], 10), M = parseInt(hm[1], 10);
    if (isNaN(H)) H = 9; if (isNaN(M)) M = 30;
    if (m === "daily") return M + " " + H + " * * *";
    var days = [];
    document.querySelectorAll(".sched-weekday:checked").forEach(function(cb) {
      var v = parseInt(cb.value, 10);
      if (v === 7) v = 0; // UI Sunday=7 → Python cron Sunday=0
      days.push(v);
    });
    if (!days.length) days = [1];
    return M + " " + H + " * * " + days.sort(function(a, b) { return a - b; }).join(",");
  }

  function collectWorkers() {
    var out = [];
    workersBox.querySelectorAll(".sched-worker-row").forEach(function(row) {
      out.push({
        provider: row.querySelector(".sched-worker-provider").value,
        model: row.querySelector(".sched-worker-model").value,
      });
    });
    if (!out.length) out = [{ provider: "", model: "" }];
    return out;
  }

  // Create vs edit: same body, different endpoint.
  function submitTask(body) {
    if (editing && schedEditJobId) { updateTaskConfig(schedEditJobId, body, container); }
    else { postTask(body, container); }
  }

  // Edit mode: prefill every form control from the stored task config.
  function applyPrefill(cfg) {
    if (typeSel) { typeSel.value = cfg.task_type || "deep"; applyType(); }
    var nameInput = document.getElementById("tf-name");
    if (nameInput) nameInput.value = cfg.name || "";
    var webhookInput = document.getElementById("tf-webhook");
    if (webhookInput) webhookInput.value = cfg.webhook_url || "";
    var botIdInput = document.getElementById("tf-bot-id");
    if (botIdInput) botIdInput.value = cfg.bot_id || "";
    var botSecretInput = document.getElementById("tf-bot-secret");
    if (botSecretInput) botSecretInput.value = cfg.bot_secret || "";

    var sched = cronToFormParts(cfg.cron_expression || "");
    if (sched.mode === "daily" || sched.mode === "weekly") {
      schedMode.value = sched.mode;
      timeInput.value = sched.hm;
      if (sched.mode === "weekly" && sched.days) {
        document.querySelectorAll(".sched-weekday").forEach(function(cb) {
          cb.checked = sched.days.indexOf(parseInt(cb.value, 10)) !== -1;
        });
      }
    } else {
      schedMode.value = "custom";
      cronCustom.value = cfg.cron_expression || "";
    }
    applySchedMode();

    var tickerInput = document.getElementById("tf-ticker");
    if (tickerInput) tickerInput.value = cfg.ticker || "";
    var analystsSel = document.getElementById("tf-analysts");
    if (analystsSel && cfg.analysts && cfg.analysts.length) {
      for (var i = 0; i < analystsSel.options.length; i++) {
        analystsSel.options[i].selected = cfg.analysts.indexOf(analystsSel.options[i].value) !== -1;
      }
    }
    var langSel = document.getElementById("tf-language");
    if (langSel) langSel.value = cfg.language || "Chinese";

    var boards = cfg.excluded_boards || [];
    document.querySelectorAll(".sched-board").forEach(function(cb) {
      cb.checked = boards.indexOf(cb.dataset.board) === -1;
    });
    if (cfg.change_pct_min != null) {
      changeToggle.checked = true;
      changeValue.value = cfg.change_pct_min;
      changeValue.style.display = "inline-block";
    }
    if (cfg.indexes && cfg.indexes.length) {
      // A stored config may carry a legacy index key (an old "周期股100" task
      // saved "period" before the canonical key became "cycle"). Normalize so
      // the prefilled checkbox matches; the next save writes the canonical key.
      var idxAliases = { period: "cycle" };
      var idxNorm = cfg.indexes.map(function(k) { return idxAliases[k] || k; });
      document.querySelectorAll(".sched-index").forEach(function(cb) {
        cb.checked = idxNorm.indexOf(cb.value) !== -1;
      });
    }
    twopass.checked = !!cfg.two_pass;
    twopassMode.value = cfg.two_pass_mode || "reanalyze";
    twopassMode.style.display = twopass.checked ? "inline-block" : "none";
    twopassNote.style.display = twopass.checked ? "inline" : "none";
    stage3.checked = !!cfg.stage3;
    stage3Row.style.display = stage3.checked ? "flex" : "none";

    // Grow worker rows to match the stored config (the form starts with one).
    var workers = cfg.workers || [];
    var curRows = workersBox.querySelectorAll(".sched-worker-row").length;
    for (var w = curRows; w < workers.length; w++) {
      if (w >= 8) break;
      var wrap = document.createElement("div");
      wrap.innerHTML = schedWorkerRowHTML(w);
      var row = wrap.firstChild;
      workersBox.appendChild(row);
      schedBindWorkerRow(row, workersBox);
    }

    // Model dropdowns need the /api/models catalog. Fetch it here (edit mode
    // does not pre-warm the catalog), then set provider→model per row/role.
    fetch("/api/models")
      .then(function(r) { return r.json(); })
      .then(function(d) {
        schedPopulateModelSelects(d);
        var rows = workersBox.querySelectorAll(".sched-worker-row");
        workers.forEach(function(worker, i) {
          if (i >= rows.length) return;
          var pv = rows[i].querySelector(".sched-worker-provider");
          var ms = rows[i].querySelector(".sched-worker-model");
          if (pv && worker.provider) pv.value = worker.provider;
          if (pv) schedOnWorkerProviderChange(pv);
          if (ms && worker.model) ms.value = worker.model;
        });
        var sp = document.getElementById("tf-stage3-provider");
        if (sp && cfg.stage3_provider) { sp.value = cfg.stage3_provider; schedOnStage3ProviderChange(); }
        var sm = document.getElementById("tf-stage3-model");
        if (sm && cfg.stage3_model) sm.value = cfg.stage3_model;
        var dp = document.getElementById("tf-deep-provider");
        if (dp && cfg.deep_provider) { dp.value = cfg.deep_provider; schedOnDeepProviderChange(); }
        var dm = document.getElementById("tf-deep-model");
        if (dm && cfg.deep_model) dm.value = cfg.deep_model;
        var qp = document.getElementById("tf-quick-provider");
        if (qp && cfg.quick_provider) { qp.value = cfg.quick_provider; schedOnQuickProviderChange(); }
        var qm = document.getElementById("tf-quick-model");
        if (qm && cfg.quick_model) qm.value = cfg.quick_model;
      })
      .catch(function() {});
  }

  workersBox.querySelectorAll(".sched-worker-row").forEach(function(row) { schedBindWorkerRow(row, workersBox); });

  document.getElementById("tf-add-worker").addEventListener("click", function() {
    var rows = workersBox.querySelectorAll(".sched-worker-row");
    if (rows.length >= 8) { alert(t("sched.maxModels")); return; }
    var wrap = document.createElement("div");
    wrap.innerHTML = schedWorkerRowHTML(rows.length);
    var row = wrap.firstChild;
    workersBox.appendChild(row);
    schedBindWorkerRow(row, workersBox);
  });

  changeToggle.addEventListener("change", function() {
    changeValue.style.display = changeToggle.checked ? "inline-block" : "none";
  });
  twopass.addEventListener("change", function() {
    twopassMode.style.display = twopass.checked ? "inline-block" : "none";
    twopassNote.style.display = twopass.checked ? "inline" : "none";
  });
  var stage3 = document.getElementById("tf-stage3");
  var stage3Row = document.getElementById("tf-stage3-row");
  var stage3Prov = document.getElementById("tf-stage3-provider");
  stage3.addEventListener("change", function() {
    stage3Row.style.display = stage3.checked ? "flex" : "none";
  });
  if (stage3Prov) stage3Prov.addEventListener("change", schedOnStage3ProviderChange);
  var deepProvSel = document.getElementById("tf-deep-provider");
  var quickProvSel = document.getElementById("tf-quick-provider");
  if (deepProvSel) deepProvSel.addEventListener("change", schedOnDeepProviderChange);
  if (quickProvSel) quickProvSel.addEventListener("change", schedOnQuickProviderChange);
  var refreshBtn = document.getElementById("tf-refresh-models");
  if (refreshBtn) refreshBtn.addEventListener("click", schedRefreshModels);

  document.getElementById("tf-cancel").addEventListener("click", function() {
    schedEditJobId = null;
    container.innerHTML = "";
  });

  document.getElementById("tf-submit").addEventListener("click", function() {
    var name = document.getElementById("tf-name").value.trim();
    var cronExpr = buildCron();
    var ttype = typeSel.value;
    var webhookUrl = ((document.getElementById("tf-webhook") || {}).value || "").trim();
    if (webhookUrl.indexOf(_BOT_SECRET_MASK) !== -1) webhookUrl = ""; // redacted ⇒ keep global .env webhook
    var botId = ((document.getElementById("tf-bot-id") || {}).value || "").trim();
    var botSecret = ((document.getElementById("tf-bot-secret") || {}).value || "").trim();
    if (botSecret === _BOT_SECRET_MASK) botSecret = ""; // masked ⇒ keep global .env secret
    var pushUser = [];
    document.querySelectorAll(".sched-botuser:checked").forEach(function(cb) { pushUser.push(cb.value); });
    if (!name) { alert(t("sched.requiredName")); return; }
    if (!cronExpr) { alert(t("sched.requiredCron")); return; }

    if (ttype === "deep") {
      var ticker = document.getElementById("tf-ticker").value.trim();
      if (!ticker) { alert(t("sched.requiredTicker")); return; }
      var analystsSel = document.getElementById("tf-analysts");
      var analysts = [];
      for (var i = 0; i < analystsSel.options.length; i++) {
        if (analystsSel.options[i].selected) analysts.push(analystsSel.options[i].value);
      }
      submitTask({
        name: name, task_type: "deep", cron_expression: cronExpr,
        ticker: ticker, analysts: analysts, use_current_date: true,
        language: document.getElementById("tf-language").value || "Chinese",
        deep_provider: document.getElementById("tf-deep-provider").value,
        deep_model: document.getElementById("tf-deep-model").value,
        quick_provider: document.getElementById("tf-quick-provider").value,
        quick_model: document.getElementById("tf-quick-model").value,
        webhook_url: webhookUrl,
        bot_id: botId, bot_secret: botSecret, push_user: pushUser,
      });
      return;
    }

    var body = {
      name: name, task_type: ttype, cron_expression: cronExpr,
      workers: collectWorkers(),
      excluded_boards: [],
      webhook_url: webhookUrl,
      bot_id: botId, bot_secret: botSecret, push_user: pushUser,
    };
    document.querySelectorAll(".sched-board").forEach(function(cb) {
      if (!cb.checked) body.excluded_boards.push(cb.dataset.board);
    });
    if (changeToggle.checked) {
      var th = parseFloat(changeValue.value);
      if (!isNaN(th)) body.change_pct_min = th;
    }
    if (ttype === "idx_batch") {
      var idxs = [];
      document.querySelectorAll(".sched-index:checked").forEach(function(cb) { idxs.push(cb.value); });
      if (!idxs.length) { alert(t("sched.requiredIdx")); return; }
      body.indexes = idxs;
    }
    if (twopass.checked) {
      body.two_pass = true;
      body.two_pass_mode = twopassMode.value;
      body.two_pass_workers = collectWorkers();
    }
    if (stage3.checked) {
      body.stage3 = true;
      body.stage3_provider = document.getElementById("tf-stage3-provider").value;
      body.stage3_model = document.getElementById("tf-stage3-model").value;
    }
    submitTask(body);
  });

  var webhookTestBtn = document.getElementById("tf-webhook-test");
  if (webhookTestBtn) {
    webhookTestBtn.addEventListener("click", function() {
      var url = ((document.getElementById("tf-webhook") || {}).value || "").trim();
      if (!url) { alert(t("sched.webhookTestNeed")); return; }
      var taskName = ((document.getElementById("tf-name") || {}).value || "").trim();
      webhookTestBtn.disabled = true;
      fetch("/api/scheduler/webhook/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ webhook_url: url, name: taskName })
      }).then(function(r) { return r.json(); }).then(function(data) {
        if (data && data.errcode === 0) alert(t("sched.webhookTestOk"));
        else alert((data && data.errmsg) || t("sched.webhookTestNeed"));
      }).catch(function(e) {
        alert(t("sched.createFailed", { msg: e.message }));
      }).finally(function() { webhookTestBtn.disabled = false; });
    });
  }

  var botTestBtn = document.getElementById("tf-bot-test");
  if (botTestBtn) {
    botTestBtn.addEventListener("click", function() {
      var bid = ((document.getElementById("tf-bot-id") || {}).value || "").trim();
      var bsec = ((document.getElementById("tf-bot-secret") || {}).value || "").trim();
      if (!bid || !bsec) { alert(t("sched.botTestNeed")); return; }
      var taskName = ((document.getElementById("tf-name") || {}).value || "").trim();
      botTestBtn.disabled = true;
      fetch("/api/scheduler/bot/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bot_id: bid, bot_secret: bsec, name: taskName })
      }).then(function(r) { return r.json(); }).then(function(data) {
        if (data && data.ok && data.errcode === 0) alert(t("sched.botTestOk"));
        else if (data && data.errmsg) alert(data.errmsg);
        else alert(t("sched.botTestNeed"));
      }).catch(function(e) {
        alert(t("sched.createFailed", { msg: e.message }));
      }).finally(function() { botTestBtn.disabled = false; });
    });
  }

  if (editing) applyPrefill(config);

  // Auto-fill the 智能机器人 credentials from the global .env config when the
  // form fields are still empty (new tasks, or old tasks with no per-task
  // creds) — these are exactly the values the backend falls back to anyway.
  // The secret arrives MASKED (never the real value); an empty/masked secret
  // saves as "use the global .env secret".
  fetch("/api/scheduler/bot/status")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!d) return;
      var botIdAuto = document.getElementById("tf-bot-id");
      var botSecAuto = document.getElementById("tf-bot-secret");
      if (d.bot_id) {
        if (botIdAuto && !botIdAuto.value) botIdAuto.value = d.bot_id;
        if (botSecAuto && !botSecAuto.value && d.secret_configured) botSecAuto.value = d.bot_secret;
      }
    })
    .catch(function() {});

  // 推送用户 checkboxes: restore the saved targets (list, or a legacy scalar
  // string → single-element list), and open the 👥 users manager on demand.
  var savedPushUsers = [];
  if (config) {
    savedPushUsers = Array.isArray(config.push_user) ? config.push_user.slice()
      : (config.push_user ? [config.push_user] : []);
  }
  var manageBtn = document.getElementById("tf-bot-users-manage");
  if (manageBtn) manageBtn.addEventListener("click", function() {
    if (window.openWecomUsersManager) window.openWecomUsersManager();
  });
  renderTaskBotUsers(savedPushUsers);
}

document.getElementById("new-task-btn").addEventListener("click", function() {
  var container = document.getElementById("task-form-container");
  if (container.innerHTML) { container.innerHTML = ""; schedEditJobId = null; return; }
  openTaskForm(container, null);
});



// ---- WeCom push-user multi-select helpers (scheduler task form) -------------
// The task-form 推送用户 row is a checkbox group fed by the per-user registry
// (GET /api/advisory/wecom_users). Only ACTIVE users are listed — the bot can
// only push to people who messaged it; pre-registered placeholders (grey, not
// yet active) must be activated first. wecomUserLabel renders 姓名(userid).
function wecomUserLabel(u) {
  var nm = (u && u.name) || "";
  var uid = (u && u.userid) || "";
  if (nm && nm !== uid) return nm + " (" + uid + ")";
  return uid;
}

function renderTaskBotUsers(saved) {
  var box = document.getElementById("tf-bot-users");
  if (!box) return;
  box.innerHTML = '<span style="font-size:11px;color:var(--text-muted);align-self:center;">' +
    esc(t("sched.botUserLoading")) + '</span>';
  var wanted = saved || [];
  fetch("/api/advisory/wecom_users")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!box) return;
      var rows = ((d && d.users) || []).filter(function(u) { return !!u.active; });
      if (!rows.length) {
        box.innerHTML = '<span style="font-size:11px;color:var(--text-muted);align-self:center;">' +
          esc(t("sched.botUserEmpty")) + '</span>';
        return;
      }
      box.innerHTML = "";
      rows.forEach(function(u) {
        var lb = document.createElement("label");
        lb.style.cssText = "display:inline-flex;align-items:center;gap:4px;font-size:12px;cursor:pointer;margin-right:4px;";
        var cb = document.createElement("input");
        cb.type = "checkbox";
        cb.className = "sched-botuser";
        cb.value = u.userid;
        cb.style.cssText = "accent-color:var(--accent);cursor:pointer;margin:0;vertical-align:middle;";
        if (wanted.indexOf(u.userid) !== -1) cb.checked = true;
        lb.appendChild(cb);
        var sp = document.createElement("span");
        sp.textContent = wecomUserLabel(u);
        lb.appendChild(sp);
        box.appendChild(lb);
      });
    })
    .catch(function() {
      if (!box) return;
      box.innerHTML = '<span style="font-size:11px;color:var(--text-muted);align-self:center;">' +
        esc(t("sched.botUserEmpty")) + '</span>';
    });
}

// Called after a registry mutation elsewhere (e.g. the 👥 users manager) while
// the task form is open: re-render, keeping the current checkbox selection.
window.refreshWecomUserOptions = function() {
  var box = document.getElementById("tf-bot-users");
  if (!box) return;
  var cur = [];
  box.querySelectorAll(".sched-botuser:checked").forEach(function(cb) { cur.push(cb.value); });
  renderTaskBotUsers(cur);
};

// ---- Smart Scan ----
var _smartScanRunning = false;
var _smartScanLastResults = null;
var _smartScanLastData = null;
var _smartScanLastReport = null;

// ---- Stock Pick Tab (sub-tabs) ----
var _stockPickInitialized = false;
var _currentSubTab = "stockpick-smart";

function initStockPick() {
  if (_stockPickInitialized) return;
  _stockPickInitialized = true;

  // Sub-tab switching
  document.querySelectorAll("#tab-stockpick .sub-tab-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var subName = btn.dataset.subtab;
      if (subName === _currentSubTab) return;
      document.querySelectorAll("#tab-stockpick .sub-tab-btn").forEach(function(b) { b.classList.remove("active"); });
      document.querySelectorAll("#tab-stockpick .sub-tab-content").forEach(function(c) { c.classList.remove("active"); });
      btn.classList.add("active");
      document.getElementById("subtab-" + subName).classList.add("active");
      _currentSubTab = subName;

      if (subName === "stockpick-smart") initSmartScan();
      if (subName === "stockpick-quick") initManualScan();
      if (subName === "stockpick-sector") initManualScan();
    });
  });

  // Init default sub-tab (Smart Scan)
  initSmartScan();
}

var _manualScanInitialized = false;
function initManualScan() {
  if (_manualScanInitialized) return;
  _manualScanInitialized = true;
  if (typeof initSectorScan === "function") {
    try { initSectorScan(); } catch(e) { console.error(e); }
  }
}

function initSmartScan() {
  var scanBtn = document.getElementById("smart-scan-btn");
  var resultsEl = document.getElementById("smart-scan-results");
  var progressEl = document.getElementById("smart-scan-progress");
  var spinner = document.getElementById("smart-scan-spinner");
  if (!scanBtn) return;

  // If we have cached results, re-render them
  if (_smartScanLastResults && _smartScanLastResults.length > 0 && _smartScanLastData) {
    renderSmartResults(_smartScanLastResults, _smartScanLastData);
    progressEl.classList.remove("hidden");
    progressEl.textContent = "Cached — " + _smartScanLastResults.length + " results";
  }
  // Restore LLM report in right panel
  if (_smartScanLastReport) showPrelimResults(_smartScanLastReport);

  scanBtn.addEventListener("click", function() {
    if (_smartScanRunning) return;
    _smartScanRunning = true;
    scanBtn.disabled = true;
    spinner.classList.remove("hidden");
    progressEl.classList.remove("hidden");
    resultsEl.innerHTML = '<div style="padding:20px;text-align:center;">' +
      '<div style="font-size:14px;font-weight:600;margin-bottom:12px;">Smart Scan Running</div>' +
      '<div id="smart-progress-bar" style="width:100%;height:8px;background:var(--border);border-radius:4px;overflow:hidden;margin-bottom:4px;">' +
      '<div id="smart-progress-fill" style="width:5%;height:100%;background:linear-gradient(90deg,var(--accent),#0550ae);border-radius:4px;transition:width 0.5s;"></div></div>' +
      '<div id="smart-progress-text" style="font-size:11px;color:var(--text-muted);">Step 1/3: Loading RRG rotation data...</div></div>';

    // Animate progress
    var fillEl2 = document.getElementById("smart-progress-fill");
    var textEl2 = document.getElementById("smart-progress-text");
    var w2 = 5;
    var anim2 = setInterval(function() {
      w2 = Math.min(w2 + 2, 85);
      if (fillEl2) fillEl2.style.width = w2 + "%";
      if (textEl2 && w2 < 40) textEl2.textContent = "Step 1/3: Loading rotation data...";
      else if (textEl2 && w2 < 70) textEl2.textContent = "Step 2/3: Scoring stocks across industries...";
      else if (textEl2) textEl2.textContent = "Step 3/3: Analyzing results...";
    }, 800);

    var topN = parseInt(document.getElementById("smart-top-n").value) || 5;
    var autoAnalyze = document.getElementById("smart-auto-analyze").checked;
    var useRps = document.getElementById("smart-rps-filter").checked;
    var quadrantFilter = document.getElementById("smart-quadrant-filter").value;
    var langSelect = document.getElementById("prelim-language-select");
    var lang = langSelect ? langSelect.value : "Chinese";

    fetch("/api/smart-scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        top_n: topN,
        auto_analyze: autoAnalyze,
        use_rps: useRps,
        quadrant_filter: quadrantFilter,
        quick_provider: quickProviderSelect ? quickProviderSelect.value : "",
        quick_model: quickModelSelect ? quickModelSelect.value : "",
        language: lang,
      }),
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      spinner.classList.add("hidden");
      _smartScanRunning = false;
      scanBtn.disabled = false;

      var results = data.results || [];
      if (results.length === 0) {
        resultsEl.innerHTML = '<span class="no-results">No stocks found</span>';
        progressEl.textContent = "Complete — 0 results";
        return;
      }

      // Cache results
      _smartScanLastResults = results;
      _smartScanLastData = data;
      _smartScanLastReport = data.report || null;
      clearInterval(anim2);
      renderSmartResults(results, data);
      // If auto-analyze returned a report, show it
      if (data.report) showPrelimResults(data.report);
    })
    .catch(function(err) {
      clearInterval(anim2);
      spinner.classList.add("hidden");
      _smartScanRunning = false;
      scanBtn.disabled = false;
      resultsEl.innerHTML = '<span class="no-results" style="color:var(--red);">Error: ' + (err.message || "Scan failed") + '</span>';
      progressEl.textContent = "Failed";
    });
  });
}

function renderSmartResults(results, data) {
  var resultsEl = document.getElementById("smart-scan-results");
  var progressEl = document.getElementById("smart-scan-progress");
  if (!resultsEl) return;

  progressEl.textContent = "Complete! " + results.length + " stocks from " + (data.industries_scanned || "?") + " industries. Leading: " + (data.by_quadrant?.leading||0) + ", Improving: " + (data.by_quadrant?.improving||0);

  var html = '<div class="scan-summary">';
  html += '<b>' + results.length + '</b> stocks scored across <b>' + (data.industries_scanned || "?") + '</b> industries';
  html += ' | <span style="color:#1a7f37;">Leading: ' + (data.by_quadrant?.leading||0) + '</span>';
  html += ' | <span style="color:#0969da;">Improving: ' + (data.by_quadrant?.improving||0) + '</span>';
  html += '</div>';

  // Batch buttons
  html += '<div class="batch-actions">';
  html += '<label class="select-all-label"><input type="checkbox" id="smart-select-all"> Select All</label>';
  html += '<button id="smart-prelim-btn" class="btn prelim-start-btn" disabled>Start Preliminary Analysis</button>';
  html += '<button id="smart-save-all-btn" class="btn secondary" style="padding:4px 8px;font-size:11px;">Save All to ★</button>';
  html += '<div class="prelim-count-hint" id="smart-prelim-hint">0 of ' + results.length + ' selected</div>';
  html += '</div>';

  results.forEach(function(r, idx) {
    var qLabel = r.quadrant === "leading" ? "L" : "I";
    var qColor = r.quadrant === "leading" ? "#1a7f37" : "#0969da";
    html += '<div class="candidate-card">';
    html += '<div class="candidate-row">';
    html += '<input type="checkbox" class="smart-check" data-idx="' + idx + '">';
    html += '<span class="candidate-code">' + esc(r.code) + '</span>';
    html += '<span class="candidate-name">' + esc(r.name) + '</span>';
    html += '<span style="color:' + qColor + ';font-weight:700;font-size:10px;">' + qLabel + '</span>';
    html += '<span class="candidate-score high">' + r.total_score.toFixed(0) + '</span>';
    html += '</div>';
    html += '<div class="candidate-meta">';
    html += '<span>Momentum: ' + (r.momentum||0).toFixed(0) + '</span>';
    html += '<span>Volume: ' + (r.volume_score||0).toFixed(0) + '</span>';
    html += '<span>Capital: ' + (r.capital_score||0).toFixed(0) + '</span>';
    html += '<span>' + t("smart.inflow") + (r.net_inflow||0).toFixed(1) + '万</span>';
    if (r.rps != null && r.rps !== undefined) {
      var rpsColor = r.rps >= 90 ? "#1a7f37" : r.rps >= 80 ? "#9a6700" : "#656d76";
      html += '<span style="color:' + rpsColor + ';font-weight:700;">RPS ' + r.rps.toFixed(0) + '</span>';
    }
    if (r.has_golden_cross) html += '<span style="color:#1a7f37;">' + t("smart.goldenCross") + '</span>';
    html += '<span>RSI: ' + (r.rsi_14||"-") + '</span>';
    html += '<span>Close: ¥' + (r.close||0).toFixed(2) + '</span>';
    html += '</div>';
    html += '<div class="candidate-actions">';
    html += '<button class="analyze-btn" data-ticker="' + esc(r.ticker) + '" data-name="' + esc(r.name) + '">Analyze</button>';
    html += '<button class="shortlist-add-btn" data-ticker="' + esc(r.ticker) + '" data-name="' + esc(r.name) + '" data-score="' + r.total_score + '">☆ Save</button>';
    html += '</div>';
    html += '</div>';
  });

  resultsEl.innerHTML = html;

  // Bind checkboxes
  function updateSmartCount() {
    var checked = resultsEl.querySelectorAll(".smart-check:checked").length;
    var btn = document.getElementById("smart-prelim-btn");
    var hint = document.getElementById("smart-prelim-hint");
    if (btn) btn.disabled = checked === 0;
    if (hint) hint.textContent = checked + " of " + results.length + " selected";
    var allCb = document.getElementById("smart-select-all");
    if (allCb) { allCb.checked = checked === results.length; allCb.indeterminate = checked > 0 && checked < results.length; }
  }
  resultsEl.querySelectorAll(".smart-check").forEach(function(cb) { cb.addEventListener("change", updateSmartCount); });
  var allCb2 = document.getElementById("smart-select-all");
  if (allCb2) allCb2.addEventListener("change", function() {
    resultsEl.querySelectorAll(".smart-check").forEach(function(cb) { cb.checked = allCb2.checked; });
    updateSmartCount();
  });

  // Preliminary analysis
  var prelimBtn2 = document.getElementById("smart-prelim-btn");
  if (prelimBtn2) prelimBtn2.addEventListener("click", function() {
    var selected = [];
    resultsEl.querySelectorAll(".smart-check:checked").forEach(function(cb) {
      var i = parseInt(cb.dataset.idx);
      if (i >= 0 && i < results.length) selected.push(results[i]);
    });
    if (selected.length > 0) {
      var candidates = selected.map(function(r) {
        return { ts_code: r.ts_code, code: r.code, name: r.name, score: r.total_score, close: r.close, market_cap: r.market_cap };
      });
      runPreliminaryAnalysis(candidates);
    }
  });

  // Save All to Shortlist
  var saveAllBtn = document.getElementById("smart-save-all-btn");
  if (saveAllBtn) saveAllBtn.addEventListener("click", function() {
    results.forEach(function(r) {
      addToShortlist(r.ticker, r.name, "Smart Scan", r.total_score, null);
    });
    saveAllBtn.textContent = "Saved " + results.length + " ★";
    saveAllBtn.disabled = true;
  });

  // Individual buttons
  resultsEl.querySelectorAll(".analyze-btn").forEach(function(btn) {
    btn.addEventListener("click", function() { switchToAnalyzeTab(this.dataset.ticker, this.dataset.name); });
  });
  resultsEl.querySelectorAll(".shortlist-add-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      addToShortlist(this.dataset.ticker, this.dataset.name, "Smart Scan", parseFloat(this.dataset.score)||0, this);
    });
  });
}

function runPreliminaryAnalysis(selected, containerEl, buttonEl, context) {
  context = context || "default";
  var scanResultsEl = containerEl || document.getElementById("scan-results") || document.getElementById("smart-scan-results");
  if (buttonEl) buttonEl.disabled = true;
  else { var pb = document.getElementById("prelim-btn"); if (pb) pb.disabled = true; }

  var oldPc = document.getElementById("prelim-progress-container");
  if (oldPc) oldPc.remove();

  var progressDiv = document.createElement("div");
  progressDiv.id = "prelim-progress-container";
  progressDiv.style.cssText = "padding:12px 16px;margin-bottom:12px;background:var(--panel-bg);border:1px solid var(--accent);border-radius:8px;";
  progressDiv.innerHTML = '<div style="font-size:14px;font-weight:600;margin-bottom:8px;">Running Preliminary Analysis</div>' +
    '<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">' + selected.length + ' stocks — 1-2 min</div>' +
    '<div id="prelim-progress-bar" style="width:100%;height:8px;background:var(--border);border-radius:4px;overflow:hidden;margin-bottom:4px;">' +
    '<div id="prelim-progress-fill" style="width:10%;height:100%;background:linear-gradient(90deg,var(--accent),#0550ae);border-radius:4px;transition:width 0.5s;"></div></div>' +
    '<div id="prelim-progress-text" style="font-size:11px;color:var(--text-muted);">Sending request...</div>';
  scanResultsEl.insertBefore(progressDiv, scanResultsEl.firstChild);

  var fillEl = document.getElementById("prelim-progress-fill");
  var textEl = document.getElementById("prelim-progress-text");
  if (!fillEl || !textEl) return;

  var width = 10;
  var animTimer = setInterval(function() {
    width = Math.min(width + 3, 85);
    if (fillEl) fillEl.style.width = width + "%";
    if (textEl) textEl.textContent = "Analyzing " + selected.length + " stocks...";
  }, 600);

  var langSelect = document.getElementById("prelim-language-select");
  var lang = langSelect ? langSelect.value : "Chinese";

  fetch("/api/sector/batch-analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidates: selected,
      quick_provider: quickProviderSelect ? quickProviderSelect.value : "",
      quick_model: quickModelSelect ? quickModelSelect.value : "",
      language: lang,
      context: context,
    }),
  })
  .then(function(r) { return r.json(); })
  .then(function(data) {
    clearInterval(animTimer);
    if (fillEl) fillEl.style.width = "100%";
    if (textEl) textEl.textContent = "Done! " + (data.stocks_analyzed || 0) + " stocks.";
    if (buttonEl) buttonEl.disabled = false;
    else { var pb2 = document.getElementById("prelim-btn"); if (pb2) pb2.disabled = false; }
    setTimeout(function() { var pc = document.getElementById("prelim-progress-container"); if (pc) pc.remove(); }, 1500);
    showPrelimResults(data.report || "");
  })
  .catch(function(err) {
    clearInterval(animTimer);
    if (fillEl) { fillEl.style.width = "100%"; fillEl.style.background = "var(--red)"; }
    if (textEl) textEl.textContent = "Error: " + (err.message || "Analysis failed");
    if (buttonEl) buttonEl.disabled = false;
    else { var pb3 = document.getElementById("prelim-btn"); if (pb3) pb3.disabled = false; }
    setTimeout(function() { var pc = document.getElementById("prelim-progress-container"); if (pc) pc.remove(); }, 3000);
  });
}

function runPreliminaryAnalysisSmart(selected) {
  // Reuse the existing runPreliminaryAnalysis logic
  var candidates = selected.map(function(r) {
    return { ts_code: r.ts_code, code: r.code, name: r.name, score: r.total_score, close: r.close, market_cap: r.market_cap };
  });
  runPreliminaryAnalysis(candidates);
}

function switchToAnalyzeTab(ticker, name) {
  currentTab = "analyze";
  document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
  var analyzeTab = document.querySelector('[data-tab="analyze"]');
  if (analyzeTab) analyzeTab.classList.add("active");
  var analyzeContent = document.getElementById("tab-analyze");
  if (analyzeContent) analyzeContent.classList.add("active");
  showRightContent("results");
  tickerInput.value = ticker;
  tickerInput.dispatchEvent(new Event("input"));
  selectedTicker = { symbol: ticker, name: name || ticker, exchange: "" };
  selectedSymbol.textContent = ticker;
  selectedName.textContent = name || ticker;
  selectedExchange.textContent = "";
  selectedCard.classList.remove("hidden");
  runBtn.disabled = false;
  loadChart(ticker, "max");
}

// ---- Shared: show preliminary analysis results in right panel ----
function showPrelimResults(reportMarkdown) {
  var html = '<div class="result-card" style="border-left-color:var(--accent);">' +
    '<div class="card-header"><span class="card-title">Batch Preliminary Analysis</span>' +
    '<button id="close-prelim" class="btn secondary" style="padding:4px 10px;font-size:11px;">Close</button></div>' +
    '<div class="card-body markdown-body">' + renderMarkdown(reportMarkdown) + '</div></div>';
  showRightContent("results", {html: html});
  var closeBtn = document.getElementById("close-prelim");
  if (closeBtn) closeBtn.addEventListener("click", function() {
    showRightContent("placeholder");
  });
}

// ---- Sector Scan ----
var _sectorScanLoaded = false;
function initSectorScan() {
  var scanBtn = document.getElementById("scan-btn");
  var scanResults = document.getElementById("scan-results");
  var checkboxList = document.getElementById("sector-checkbox-list");
  var searchInput = document.getElementById("sector-search-input");
  var countHint = document.getElementById("sector-count-hint");
  var selectAllCb = document.getElementById("sector-select-all");
  var filterCountEl = document.getElementById("sector-filter-count");

  if (!checkboxList) { console.error("sector-checkbox-list not found"); return; }

  var sectorsData = [];

  function getSelectedIndustries() {
    var selected = [];
    checkboxList.querySelectorAll("input:checked").forEach(function(cb) {
      selected.push(cb.value);
    });
    return selected;
  }

  function updateSelectionCount() {
    var checked = checkboxList.querySelectorAll("input:checked").length;
    var total = checkboxList.querySelectorAll("input").length;
    if (countHint) countHint.textContent = "(" + checked + " selected)";
    scanBtn.disabled = checked === 0;
    if (selectAllCb) {
      selectAllCb.checked = (checked === total && total > 0);
      selectAllCb.indeterminate = (checked > 0 && checked < total);
    }
  }

  function renderCheckboxList(items) {
    var html = "";
    items.forEach(function(s) {
      html += '<label class="sector-cb-item">';
      html += '<input type="checkbox" value="' + esc(s.name) + '" data-stocks="' + s.stock_count + '">';
      html += '<span class="sector-cb-name">' + esc(s.name) + '</span>';
      html += '<span class="sector-cb-count">' + s.stock_count + '</span>';
      html += '</label>';
    });
    checkboxList.innerHTML = html;

    checkboxList.querySelectorAll("input").forEach(function(cb) {
      cb.addEventListener("change", updateSelectionCount);
    });
    updateSelectionCount();
    if (filterCountEl) filterCountEl.textContent = items.length + " industries";
  }

  // Search filter
  if (searchInput) {
    searchInput.addEventListener("input", function() {
      var q = searchInput.value.trim().toLowerCase();
      var filtered = q ? sectorsData.filter(function(s) { return s.name.toLowerCase().indexOf(q) >= 0; }) : sectorsData;
      renderCheckboxList(filtered);
    });
  }
  var clearSearchBtn = document.getElementById("sector-clear-search");
  if (clearSearchBtn) {
    clearSearchBtn.addEventListener("click", function() {
      searchInput.value = "";
      searchInput.dispatchEvent(new Event("input"));
    });
  }

  // Select All
  if (selectAllCb) {
    selectAllCb.addEventListener("change", function() {
      checkboxList.querySelectorAll("input").forEach(function(cb) {
        cb.checked = selectAllCb.checked;
      });
      updateSelectionCount();
    });
  }

  // Select Improving button
  var selImproveBtn = document.getElementById("sector-select-improving");
  if (selImproveBtn) {
    selImproveBtn.addEventListener("click", function() {
      selImproveBtn.disabled = true;
      selImproveBtn.textContent = "Loading...";
      fetch("/api/rotation/rrg?lookback=10&mode=price")
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var improving = new Set();
          (data.industries || []).forEach(function(ind) {
            if (ind.quadrant === "improving") improving.add(ind.name);
          });
          checkboxList.querySelectorAll("input").forEach(function(cb) {
            cb.checked = improving.has(cb.value);
            cb.dispatchEvent(new Event("change"));
          });
          updateSelectionCount();
          selImproveBtn.textContent = "Improving (" + improving.size + ")";
          selImproveBtn.disabled = false;
        })
        .catch(function() { selImproveBtn.textContent = "Failed"; selImproveBtn.disabled = false; });
    });
  }

  // Load sector list
  function loadSectors() {
    checkboxList.innerHTML = '<span class="no-results" style="padding:10px;">Loading industries...</span>';
    scanBtn.disabled = true;
    fetch("/api/sector/list")
      .then(function(r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function(data) {
        if (!Array.isArray(data)) throw new Error("Invalid response format");
        sectorsData = data;
        renderCheckboxList(data);
        scanBtn.disabled = false;
        _sectorScanLoaded = true;
      })
      .catch(function(e) {
        console.error("Failed to load industries:", e);
        checkboxList.innerHTML = '<span class="no-results" style="color:var(--red);">' + tText("scan.loadIndFailed") + '</span>';
      });
  }

  // ---- Strategy Group Management ----

  var STRATEGY_OPTIONS = [
    {name: "macd_golden_cross", label: t("sc.macdGoldenCross"), hasParam: false, category: t("sc.catTechnical")},
    {name: "macd_convergence", label: t("sc.macdConvergence"), hasParam: false, category: t("sc.catTechnical")},
    {name: "ma_cross", label: t("sc.maCross"), hasParam: false, category: t("sc.catTechnical")},
    {name: "volume_breakout", label: t("sc.volumeBreakout"), hasParam: true, paramLabel: t("sc.paramMultiple"), paramDefault: 2.0, category: t("sc.catTechnical")},
    {name: "rsi_oversold", label: t("sc.rsiOversold"), hasParam: true, paramLabel: t("sc.paramThreshold"), paramDefault: 30, category: t("sc.catTechnical")},
    {name: "kdj_cross", label: t("sc.kdjCross"), hasParam: false, category: t("sc.catTechnical")},
    {name: "bollinger_bottom", label: t("sc.bollingerBottom"), hasParam: false, category: t("sc.catTechnical")},
    {name: "main_net_inflow", label: t("sc.mainNetInflow"), hasParam: true, paramLabel: t("sc.paramAmountWan"), paramDefault: 5000, category: t("sc.catFund")},
    {name: "consecutive_inflow", label: t("sc.consecutiveInflow"), hasParam: true, paramLabel: t("sc.paramDays"), paramDefault: 3, category: t("sc.catFund")},
    {name: "institutional_increase", label: t("sc.institutionalIncrease"), hasParam: false, category: t("sc.catInstitution")},
    {name: "no_insider_selling", label: t("sc.noInsiderSelling"), hasParam: false, category: t("sc.catInstitution")},
    {name: "low_valuation", label: t("sc.lowValuation"), hasParam: false, category: t("sc.catValue")},
    {name: "fund_turnaround", label: t("sc.fundTurnaround"), hasParam: true, paramLabel: t("sc.paramDays"), paramDefault: 5, category: t("sc.catValue")},
    {name: "bottom_breakout", label: t("sc.bottomBreakout"), hasParam: true, paramLabel: t("sc.paramVolRatio"), paramDefault: 1.3, category: t("sc.catValue")},
    {name: "macd_divergence", label: t("sc.macdDivergence"), hasParam: false, category: t("sc.catDivergence")},
  ];

  // Strategy presets
  var STRATEGY_PRESETS = [
    {name: t("sc.presetMacdDefault"), groups: [{logic: "AND", conditions: [{name: "macd_golden_cross", must: true, param: null}]}]},
    {name: t("sc.presetAboutToCross"), groups: [{logic: "AND", conditions: [
      {name: "macd_convergence", must: true, param: null},
      {name: "main_net_inflow", must: true, param: {min_amount: 3000}},
      {name: "volume_breakout", must: false, param: {multiple: 1.5}},
    ]}]},
    {name: t("sc.presetValue"), groups: [{logic: "AND", conditions: [
      {name: "low_valuation", must: true, param: null},
      {name: "fund_turnaround", must: true, param: {days: 5}},
      {name: "bottom_breakout", must: false, param: {volume_mult: 1.3}},
      {name: "macd_golden_cross", must: false, param: null},
    ]}]},
    {name: t("sc.presetVolumeBreakoutCross"), groups: [{logic: "AND", conditions: [
      {name: "volume_breakout", must: true, param: {multiple: 2.0}},
      {name: "macd_golden_cross", must: true, param: null},
    ]}]},
    {name: t("sc.presetOversoldRebound"), groups: [{logic: "AND", conditions: [
      {name: "rsi_oversold", must: true, param: {threshold: 30}},
      {name: "bollinger_bottom", must: true, param: null},
    ]}]},
    {name: t("sc.presetMainInflow"), groups: [{logic: "AND", conditions: [
      {name: "consecutive_inflow", must: true, param: {days: 3}},
      {name: "main_net_inflow", must: true, param: {min_amount: 5000}},
    ]}]},
    {name: t("sc.presetDivergenceAbsorb"), groups: [{logic: "AND", conditions: [
      {name: "macd_divergence", must: true, param: null},
      {name: "main_net_inflow", must: true, param: {min_amount: 3000}},
      {name: "low_valuation", must: false, param: null},
      {name: "rsi_oversold", must: false, param: {threshold: 45}},
    ]}]},
  ];

  var strategyGroups = [
    {logic: "AND", conditions: [{name: "macd_golden_cross", must: true, param: null}]},
  ];

  function renderStrategyGroups() {
    var container = document.getElementById("strategy-groups-container");
    if (!container) return;
    var html = "";
    strategyGroups.forEach(function(grp, gi) {
      html += '<div class="strategy-group">';
      html += '<div class="strategy-group-header">';
      html += '<span class="strategy-group-title">' + t("strategy.group", {n: gi+1}) + '</span>';
      html += '<select class="strategy-logic-select" data-gi="' + gi + '">';
      html += '<option value="AND"' + (grp.logic==="AND"?" selected":"") + '>' + tText("strategy.allAnd") + '</option>';
      html += '<option value="OR"' + (grp.logic==="OR"?" selected":"") + '>' + tText("strategy.anyOr") + '</option>';
      html += '</select>';
      if (strategyGroups.length > 1) {
        html += '<button class="btn secondary strategy-del-btn" data-gi="' + gi + '" style="padding:2px 6px;font-size:11px;">✕</button>';
      }
      html += '</div>';
      html += '<div class="strategy-conditions">';
      STRATEGY_OPTIONS.forEach(function(opt) {
        var cond = grp.conditions.find(function(c) { return c.name === opt.name; });
        var checked = !!cond;
        var isMust = cond ? cond.must : true;
        var paramVal = cond && cond.param ? (cond.param.paramDefault || cond.param) : (opt.paramDefault || "");
        html += '<label class="strategy-cond-row">';
        html += '<input type="checkbox" class="strategy-check" data-gi="' + gi + '" data-name="' + opt.name + '"' + (checked?" checked":"") + '>';
        html += '<span class="strategy-cond-label">' + tText("strategy.opt_" + opt.name) + '</span>';
        if (opt.hasParam) {
          html += '<input type="number" class="strategy-param" data-gi="' + gi + '" data-name="' + opt.name + '" value="' + paramVal + '" style="width:55px;padding:1px 4px;font-size:11px;border:1px solid var(--border);border-radius:3px;"' + (checked?"":" disabled") + '>';
        }
        html += '<select class="strategy-must" data-gi="' + gi + '" data-name="' + opt.name + '" style="font-size:10px;padding:1px 2px;"' + (checked?"":" disabled") + '>';
        html += '<option value="1"' + (isMust?" selected":"") + '>MUST</option>';
        html += '<option value="0"' + (isMust?"":" selected") + '>ANY</option>';
        html += '</select>';
        html += '</label>';
      });
      html += '</div></div>';
    });
    container.innerHTML = html;

    // Bind events
    container.querySelectorAll(".strategy-check").forEach(function(cb) {
      cb.addEventListener("change", function() {
        var gi = parseInt(cb.dataset.gi);
        var name = cb.dataset.name;
        if (cb.checked) {
          var opt = STRATEGY_OPTIONS.find(function(o) { return o.name === name; });
          strategyGroups[gi].conditions.push({name: name, must: true, param: opt && opt.hasParam ? opt.paramDefault : null});
        } else {
          strategyGroups[gi].conditions = strategyGroups[gi].conditions.filter(function(c) { return c.name !== name; });
        }
        renderStrategyGroups();
      });
    });
    container.querySelectorAll(".strategy-must").forEach(function(sel) {
      sel.addEventListener("change", function() {
        var gi = parseInt(sel.dataset.gi);
        var name = sel.dataset.name;
        var cond = strategyGroups[gi].conditions.find(function(c) { return c.name === name; });
        if (cond) cond.must = sel.value === "1";
      });
    });
    container.querySelectorAll(".strategy-param").forEach(function(inp) {
      inp.addEventListener("change", function() {
        var gi = parseInt(inp.dataset.gi);
        var name = inp.dataset.name;
        var cond = strategyGroups[gi].conditions.find(function(c) { return c.name === name; });
        if (cond) cond.param = parseFloat(inp.value) || 0;
      });
    });
    container.querySelectorAll(".strategy-logic-select").forEach(function(sel) {
      sel.addEventListener("change", function() {
        strategyGroups[parseInt(sel.dataset.gi)].logic = sel.value;
      });
    });
    container.querySelectorAll(".strategy-del-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        strategyGroups.splice(parseInt(btn.dataset.gi), 1);
        renderStrategyGroups();
      });
    });
  }

  document.getElementById("add-strategy-group-btn").addEventListener("click", function() {
    strategyGroups.push({logic: "AND", conditions: []});
    renderStrategyGroups();
  });

  function getStrategyGroupsJSON() {
    return JSON.stringify(strategyGroups.filter(function(g) { return g.conditions.length > 0; }));
  }

  // Populate preset dropdown
  var presetSelect = document.getElementById("strategy-preset-select");
  if (presetSelect) {
    STRATEGY_PRESETS.forEach(function(p, i) {
      var opt = document.createElement("option");
      opt.value = p.name;
      opt.textContent = t("strategy.preset" + i);
      presetSelect.appendChild(opt);
    });
    presetSelect.addEventListener("change", function() {
      var found = STRATEGY_PRESETS.find(function(p) { return p.name === presetSelect.value; });
      if (found) {
        strategyGroups = JSON.parse(JSON.stringify(found.groups));
        presetSelect.value = "";
        renderStrategyGroups();
      }
    });
  }

  // ---- Shared Quick Scan Engine ----

  function updateIndustryStatus(name, quadrant, status, found, listEl) {
    var id = "scan-item-" + name.replace(/[^a-zA-Z一-鿿]/g, "_");
    var el = document.getElementById(id);
    if (!el) return;
    var badge = quadrant === "leading" ? '<span style="color:#16a34a;">●</span>' : '<span style="color:#2563eb;">●</span>';
    var icon, statusText, color;
    if (status === "pending") {
      icon = '<span style="color:var(--text-muted);">⏳</span>';
      statusText = t("scan.stPending");
      color = "var(--text-muted)";
    } else if (status === "scanning") {
      icon = '<span style="animation:pulse 1s ease-in-out infinite;">🔍</span>';
      statusText = t("scan.stScanning");
      color = "var(--text-muted)";
    } else if (status === "done") {
      icon = '✅';
      statusText = found > 0 ? t("scan.found", {n: found}) : t("scan.zeroFound");
      color = found > 0 ? "#16a34a" : "var(--text-muted)";
    } else {
      icon = '❌';
      statusText = t("scan.stFailed");
      color = "#dc2626";
    }
    el.innerHTML = '<span style="width:14px;text-align:center;font-size:10px;">' + icon + '</span>' +
      '<span style="flex:1;">' + badge + ' ' + name + '</span>' +
      '<span style="color:' + color + ';font-size:10px;min-width:55px;text-align:right;">' + statusText + '</span>';
  }

  function buildIndustryList(industries, listEl) {
    var html = "";
    industries.forEach(function(ind) {
      html += '<div id="scan-item-' + ind.name.replace(/[^a-zA-Z一-鿿]/g, "_") + '" style="display:flex;align-items:center;gap:6px;padding:3px 0;border-bottom:1px solid var(--border);"></div>';
    });
    listEl.innerHTML = html;
    industries.forEach(function(ind) {
      updateIndustryStatus(ind.name, ind.quadrant, "pending", 0, listEl);
    });
  }

  function runQuickRotationScan(opts) {
    // opts: { strategyName, strategyGroups, topNInputId, leadersOnlyCbId,
    //         progressContainerId, barFillId, stepTextId, industryListId, button,
    //         quadrantFilterId }
    var btn = opts.button;
    btn.disabled = true;
    var progressEl = document.getElementById(opts.progressContainerId);
    var barFill = document.getElementById(opts.barFillId);
    var stepText = document.getElementById(opts.stepTextId);
    var industryList = document.getElementById(opts.industryListId);
    progressEl.classList.remove("hidden");
    if (barFill) barFill.style.width = "0%";
    stepText.textContent = t("scan.loadingRotation");
    industryList.innerHTML = "";
    scanResults.innerHTML = "";

    // Read quadrant filter if provided
    var qfVal = "all";
    if (opts.quadrantFilterId) {
      var qfEl = document.getElementById(opts.quadrantFilterId);
      if (qfEl) qfVal = qfEl.value;
    }

    fetch("/api/rotation/rrg?lookback=10&mode=capital")
      .then(function(r) { return r.json(); })
      .then(function(data) {
        var leaders = [];
        (data.industries || []).forEach(function(ind) {
          if (qfVal === "leading" && ind.quadrant !== "leading") return;
          if (qfVal === "improving" && ind.quadrant !== "improving") return;
          if (ind.quadrant === "leading" || ind.quadrant === "improving") {
            leaders.push(ind);
          }
        });
        var topN = parseInt(document.getElementById(opts.topNInputId).value) || 3;
        leaders = leaders.slice(0, topN);
        if (leaders.length === 0) {
          scanResults.innerHTML = '<span class="no-results">' + tText("scan.noLeading") + '</span>';
          btn.disabled = false;
          progressEl.classList.add("hidden");
          return;
        }

        stepText.textContent = t("scan.scanningWith", {n: leaders.length, name: opts.strategyName});

        // Build initial industry status list
        buildIndustryList(leaders, industryList);

        var groupsJson = JSON.stringify(opts.strategyGroups);
        var capPct = parseInt(document.getElementById("filter-cap-percent").value) || 50;
        var allCandidates = [];
        var completed = 0;
        var total = leaders.length;
        var errors = 0;

        function scanNext(idx) {
          if (idx >= total) {
            btn.disabled = false;
            if (barFill) barFill.style.width = "100%";
            stepText.textContent = t("scan.complete", {n: allCandidates.length, m: total - errors});
            if (allCandidates.length === 0) {
              scanResults.innerHTML = '<span class="no-results">' + tText("scan.noSignals", {name: opts.strategyName}) + '</span>';
              return;
            }
            var seen = {};
            var unique = [];
            allCandidates.forEach(function(c) {
              if (!seen[c.ts_code]) { seen[c.ts_code] = true; unique.push(c); }
            });

            // Industry leaders filter
            var leadersOnlyCb = document.getElementById(opts.leadersOnlyCbId);
            var leadersOnly = leadersOnlyCb && leadersOnlyCb.checked;
            if (leadersOnly) {
              var bySector = {};
              unique.forEach(function(c) {
                var sector = c._sector || "unknown";
                if (!bySector[sector]) bySector[sector] = [];
                bySector[sector].push(c);
              });
              unique = [];
              Object.keys(bySector).forEach(function(sector) {
                bySector[sector].sort(function(a, b) { return (b.market_cap || 0) - (a.market_cap || 0); });
                unique = unique.concat(bySector[sector].slice(0, 3));
              });
            }

            unique.sort(function(a, b) { return (b.score || 0) - (a.score || 0); });
            var desc = leadersOnly ? t("scan.descLeaders", {name: opts.strategyName}) : t("scan.descRotation", {name: opts.strategyName});
            renderCandidates({
              sector_name: desc,
              sector_code: t("scan.rotationIndustries", {n: total}),
              candidates: unique,
              total_candidates: unique.length,
            });
            return;
          }

          var ind = leaders[idx];
          var name = ind.name;
          updateIndustryStatus(name, ind.quadrant, "scanning", 0, industryList);

          var params = new URLSearchParams({ sector_name: name, strategies: groupsJson, cap_percent: capPct, require_inflow: false });
          fetch("/api/sector/scan/" + encodeURIComponent(name) + "?" + params.toString())
            .then(function(r) { return r.json(); })
            .then(function(data) {
              var list = (data.candidates && data.candidates.length > 0) ? data.candidates : (data.fallback_all || []);
              list.forEach(function(c) { c._sector = name; });
              if (list.length > 0) allCandidates = allCandidates.concat(list);
              updateIndustryStatus(name, ind.quadrant, "done", list.length, industryList);
            })
            .catch(function() { errors++; updateIndustryStatus(name, ind.quadrant, "error", 0, industryList); })
            .finally(function() {
              completed++;
              if (barFill) barFill.style.width = Math.round(5 + (completed / total) * 90) + "%";
              stepText.textContent = t("scan.progress", {done: completed, total: total, n: allCandidates.length});
              scanNext(idx + 1);
            });
        }

        scanNext(0);
      })
      .catch(function(e) {
        btn.disabled = false;
        stepText.textContent = t("scan.errorPrefix") + (e.message || t("scan.loadRotationFailed"));
      });
  }

  // Quick: Value Discovery
  var quickValueBtn = document.getElementById("quick-value-rotation-btn");
  if (quickValueBtn) {
    quickValueBtn.addEventListener("click", function() {
      runQuickRotationScan({
        strategyName: t("scan.valueDiscovery"),
        strategyGroups: [{logic: "AND", conditions: [
          {name: "low_valuation", must: true, param: null},
          {name: "fund_turnaround", must: true, param: {days: 5}},
          {name: "bottom_breakout", must: false, param: {volume_mult: 1.3}},
          {name: "macd_golden_cross", must: false, param: null},
        ]}],
        topNInputId: "quick-value-top-n",
        leadersOnlyCbId: "quick-value-leaders-only",
        quadrantFilterId: "quick-value-quadrant",
        progressContainerId: "quick-value-progress",
        barFillId: "quick-value-bar-fill",
        stepTextId: "quick-value-step-text",
        industryListId: "quick-value-industry-list",
        button: quickValueBtn,
      });
    });
  }

  // Quick: MACD Convergence
  var quickConvergenceBtn = document.getElementById("quick-convergence-rotation-btn");
  if (quickConvergenceBtn) {
    quickConvergenceBtn.addEventListener("click", function() {
      runQuickRotationScan({
        strategyName: t("scan.macdConvergence"),
        strategyGroups: [{logic: "AND", conditions: [
          {name: "macd_convergence", must: true, param: null},
          {name: "main_net_inflow", must: true, param: {min_amount: 3000}},
          {name: "volume_breakout", must: false, param: {multiple: 1.5}},
        ]}],
        topNInputId: "quick-convergence-top-n",
        leadersOnlyCbId: "quick-convergence-leaders-only",
        quadrantFilterId: "quick-convergence-quadrant",
        progressContainerId: "quick-convergence-progress",
        barFillId: "quick-convergence-bar-fill",
        stepTextId: "quick-convergence-step-text",
        industryListId: "quick-convergence-industry-list",
        button: quickConvergenceBtn,
      });
    });
  }

  // Expose for onclick
  window._runQuickRotationScan = runQuickRotationScan;

  // Quick: Divergence Hunter
  var quickDivergenceBtn = document.getElementById("quick-divergence-rotation-btn");
  if (quickDivergenceBtn) {
    quickDivergenceBtn.onclick = function() {
      runQuickRotationScan({
        strategyName: t("scan.divergenceHunter"),
        strategyGroups: [{logic: "AND", conditions: [
          {name: "macd_divergence", must: true, param: null},
          {name: "main_net_inflow", must: true, param: {min_amount: 3000}},
          {name: "low_valuation", must: false, param: null},
          {name: "rsi_oversold", must: false, param: {threshold: 45}},
        ]}],
        topNInputId: "quick-divergence-top-n",
        leadersOnlyCbId: "quick-divergence-leaders-only",
        quadrantFilterId: "quick-divergence-quadrant",
        progressContainerId: "quick-divergence-progress",
        barFillId: "quick-divergence-bar-fill",
        stepTextId: "quick-divergence-step-text",
        industryListId: "quick-divergence-industry-list",
        button: quickDivergenceBtn,
      });
    };
  }

  // Initial render
  renderStrategyGroups();

  // Scan button — scan multiple industries and merge results
  scanBtn.addEventListener("click", function() {
    var selected = getSelectedIndustries();
    if (selected.length === 0) return;

    var groupsJson = getStrategyGroupsJSON();
    var capPct = parseInt(document.getElementById("filter-cap-percent").value) || 50;
    var requireInflow = document.getElementById("filter-fund-flow").checked;

    scanBtn.disabled = true;
    scanResults.innerHTML = "";

    // Show progress UI
    var progressContainer = document.getElementById("scan-progress-container");
    var barFill = document.getElementById("scan-progress-bar-fill");
    var stepText = document.getElementById("scan-progress-step-text");
    var industryList = document.getElementById("scan-progress-industry-list");
    progressContainer.classList.remove("hidden");
    barFill.style.width = "0%";
    stepText.textContent = t("scan.scanningInd", {n: selected.length});
    industryList.innerHTML = "";

    // Build industry list (manual scan shows name+count as pseudo-industry objects)
    var industries = selected.map(function(name) {
      return {name: name, quadrant: ""};
    });
    buildIndustryList(industries, industryList);

    var allCandidates = [];
    var completed = 0;
    var total = selected.length;
    var errors = 0;

    function scanNext(idx) {
      if (idx >= total) {
        scanBtn.disabled = false;
        barFill.style.width = "100%";
        stepText.textContent = "Complete! " + allCandidates.length + " candidates from " + (total - errors) + " industries.";
        if (allCandidates.length === 0) {
          scanResults.innerHTML = '<span class="no-results">' + tText("scan.noCandSelected") + '</span>';
          return;
        }
        var seen = {};
        var unique = [];
        allCandidates.forEach(function(c) {
          if (!seen[c.ts_code]) { seen[c.ts_code] = true; unique.push(c); }
        });
        unique.sort(function(a, b) { return (b.score || 0) - (a.score || 0); });
        renderCandidates({
          sector_name: t("scan.nIndustries", {n: selected.length}),
          sector_code: selected.join(", "),
          candidates: unique,
          total_candidates: unique.length,
        });
        return;
      }

      var name = selected[idx];
      updateIndustryStatus(name, "", "scanning", 0, industryList);

      var params = new URLSearchParams({
        sector_name: name,
        strategies: groupsJson,
        cap_percent: capPct,
        require_inflow: requireInflow,
      });

      fetch("/api/sector/scan/" + encodeURIComponent(name) + "?" + params.toString())
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var list = (data.candidates && data.candidates.length > 0) ? data.candidates : (data.fallback_all || []);
          if (list.length > 0) allCandidates = allCandidates.concat(list);
          updateIndustryStatus(name, "", "done", list.length, industryList);
        })
        .catch(function() { errors++; updateIndustryStatus(name, "", "error", 0, industryList); })
        .finally(function() {
          completed++;
          barFill.style.width = Math.round((completed / total) * 100) + "%";
          stepText.textContent = completed + "/" + total + " industries done. " + allCandidates.length + " candidates so far.";
          scanNext(idx + 1);
        });
    }

    scanNext(0);
  });

  function renderCandidates(data) {
    var candidates = data.candidates || [];
    var fallback = data.fallback_all || [];

    if (candidates.length === 0 && fallback.length === 0) {
      scanResults.innerHTML = '<span class="no-results">' + tText("scan.noStocks") + '</span>';
      return;
    }

    // Show fallback (all stocks) when no strategy matches
    if (candidates.length === 0 && fallback.length > 0) {
      candidates = fallback;
      var fallbackHtml = '<div class="scan-summary" style="background:#fff8c5;border:1px solid #d4a72c;">';
      fallbackHtml += t("scan.fallbackMsg", {n: fallback.length, name: esc(data.sector_name || data.sector_code)});
      fallbackHtml += '</div>';
      scanResults.innerHTML = fallbackHtml;
      renderCandidateList(candidates, true);
      return;
    }

    var html = '<div class="scan-summary">';
    html += t("scan.industryLabel", {name: esc(data.sector_name || data.sector_code)});
    html += t("scan.candMatch", {n: candidates.length});
    html += '</div>';
    renderCandidateList(candidates, false);
  }

  function renderCandidateList(candidates, isFallback) {
    var html = isFallback ? "" : ""; // fallback already has summary above
    // Batch buttons
    html += '<div class="batch-actions">';
    html += '<label class="select-all-label">';
    html += '<input type="checkbox" id="candidate-select-all">' + t("scan.selectAllN", {n: candidates.length}) + '</label>';
    html += '<button id="prelim-btn" class="btn prelim-start-btn" disabled>';
    html += '<span class="prelim-btn-icon">&#x1F4CA;</span> ';
    html += tText("scan.startPrelim") + '</button>';
    html += '<div class="prelim-count-hint" id="prelim-count-hint">' + t("scan.countOf", {done: 0, total: candidates.length}) + '</div>';
    html += '</div>';
    html += '<div class="batch-actions secondary-row">';
    html += '<button id="batch-analyze-btn" class="btn secondary" style="padding:6px 12px;font-size:12px;">';
    html += tText("scan.runFullAll", {n: candidates.length}) + '</button>';
    html += '<span id="batch-progress" class="batch-progress hidden"></span>';
    html += '</div>';

    candidates.forEach(function(c, idx) {
      var scoreClass = c.score >= 0.7 ? "high" : (c.score >= 0.4 ? "medium" : "low");
      var capYi = (c.market_cap / 1e4).toFixed(0);
      var inflowWan = (c.main_net_inflow / 1e4).toFixed(1);
      var ticker = c.ticker || c.ts_code;

      html += '<div class="candidate-card">';
      html += '<div class="candidate-row">';
      html += '<input type="checkbox" class="candidate-check" data-idx="' + idx + '" style="margin-right:6px;flex-shrink:0;">';
      html += '<span class="candidate-code">' + c.code + '</span>';
      html += '<span class="candidate-name">' + c.name + '</span>';
      if (!isFallback) {
        html += '<span class="candidate-score ' + scoreClass + '">' + c.score.toFixed(2) + '</span>';
      }
      html += '</div>';
      html += '<div class="candidate-meta">';
      html += '<span>' + t("scan.mcap", {v: capYi}) + '</span>';
      if (!isFallback) {
        html += '<span>' + t("scan.netInflow", {v: inflowWan}) + '</span>';
        html += '<span>' + t("scan.crossStr", {v: (c.cross_strength || 0).toFixed(4)}) + '</span>';
      }
      html += '<span>' + t("scan.close", {v: c.close.toFixed(2)}) + '</span>';
      html += '</div>';
      html += '<div class="candidate-actions">';
      html += '<button class="analyze-btn" data-ticker="' + ticker + '" data-name="' + c.name + '">' + tText("scan.analyze") + '</button>';
      html += '<button class="shortlist-add-btn" data-ticker="' + ticker + '" data-name="' + c.name + '" data-score="' + c.score + '" title="' + t("scan.saveTitle") + '">' + tText("scan.save") + '</button>';
      html += '</div>';
      html += '</div>';
    });

    scanResults.innerHTML = (scanResults.innerHTML || "") + html;

    // Bind buttons
    scanResults.querySelectorAll(".shortlist-add-btn").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        addToShortlist(this.dataset.ticker, this.dataset.name, "Sector Scan", parseFloat(this.dataset.score) || 0, this);
      });
    });
    scanResults.querySelectorAll(".analyze-btn").forEach(function(btn) {
      btn.addEventListener("click", function() {
        switchToAnalyzeTab(this.dataset.ticker, this.dataset.name);
      });
    });

    var prelimBtn = document.getElementById("prelim-btn");
    var countHint = document.getElementById("prelim-count-hint");
    function updatePrelimCount() {
      var checked = scanResults.querySelectorAll(".candidate-check:checked").length;
      var allCb = document.getElementById("candidate-select-all");
      if (prelimBtn) prelimBtn.disabled = checked === 0;
      if (countHint) countHint.textContent = checked + " of " + candidates.length + " selected";
      if (allCb) {
        allCb.checked = (checked === candidates.length);
        allCb.indeterminate = (checked > 0 && checked < candidates.length);
      }
    }
    scanResults.querySelectorAll(".candidate-check").forEach(function(cb) {
      cb.addEventListener("change", updatePrelimCount);
    });
    var selectAllCb = document.getElementById("candidate-select-all");
    if (selectAllCb) {
      selectAllCb.addEventListener("change", function() {
        scanResults.querySelectorAll(".candidate-check").forEach(function(cb) { cb.checked = selectAllCb.checked; });
        updatePrelimCount();
      });
    }
    if (prelimBtn) {
      prelimBtn.disabled = true;
      prelimBtn.addEventListener("click", function() {
        var selected = [];
        scanResults.querySelectorAll(".candidate-check:checked").forEach(function(cb) {
          var i = parseInt(cb.dataset.idx);
          if (i >= 0 && i < candidates.length) selected.push(candidates[i]);
        });
        if (selected.length > 0) runPreliminaryAnalysis(selected);
      });
    }
    var batchBtn = document.getElementById("batch-analyze-btn");
    if (batchBtn) {
      batchBtn.addEventListener("click", function() {
        batchAnalyzeAll(candidates);
      });
    }
  }

  function batchAnalyzeAll(candidates) {
    var batchBtn = document.getElementById("batch-analyze-btn");
    var progressEl = document.getElementById("batch-progress");
    var total = candidates.length;
    var current = 0;

    batchBtn.disabled = true;
    progressEl.classList.remove("hidden");

    function runNext() {
      if (current >= total) {
        progressEl.textContent = "All " + total + " analyses complete! Check History tab for results.";
        batchBtn.disabled = false;
        return;
      }

      var c = candidates[current];
      var ticker = c.ticker || c.ts_code;
      current++;
      progressEl.textContent = "Analyzing " + current + "/" + total + ": " + c.name + " (" + ticker + ") ...";

      // Fire SSE analysis request
      var analysts = getSelectedAnalysts().join(",");
      var params = new URLSearchParams({
        ticker: ticker,
        date: dateInput.value,
        analysts: analysts,
        deep_provider: deepProviderSelect ? deepProviderSelect.value : "",
        quick_provider: quickProviderSelect ? quickProviderSelect.value : "",
        deep_model: deepModelSelect.value,
        quick_model: quickModelSelect.value,
        language: languageSelect.value,
        proxy: document.getElementById("proxy-url-input").value || "",
        checkpoint_1: cp1Toggle.checked,
        checkpoint_2: cp2Toggle.checked,
      });

      var evtSource = new EventSource("/api/analyze?" + params.toString());
      evtSource.addEventListener("done", function() {
        evtSource.close();
        // Brief delay before next to avoid rate limiting
        setTimeout(runNext, 1000);
      });
      evtSource.addEventListener("error", function() {
        evtSource.close();
        setTimeout(runNext, 1000);
      });
      // Also listen for generic message events
      evtSource.onmessage = function(e) {
        if (e.data === "[DONE]") {
          evtSource.close();
          setTimeout(runNext, 1000);
        }
      };
    }

    runNext();
  }

  function getSelectedAnalysts() {
    var analysts = [];
    var toggles = analystToggles.querySelectorAll(".toggle.active");
    toggles.forEach(function(t) { analysts.push(t.dataset.analyst); });
    return analysts;
  }

  loadSectors();
}

// ---- RRG Rotation Monitor ----
function loadRRG() {
  var modeSelect = document.getElementById("rrg-mode-select");
  var mode = modeSelect ? modeSelect.value : "capital";
  var canvas = document.getElementById("rrg-canvas");
  if (!canvas) return;

  // Show progress bar while loading
  var dateEl = document.getElementById("rrg-date");
  if (!dateEl) return;
  dateEl.innerHTML = '<span id="rrg-progress-bar-wrap" style="display:inline-block;width:140px;height:8px;background:var(--border);border-radius:4px;overflow:hidden;vertical-align:middle;margin-right:6px;border:1px solid var(--accent);"><span id="rrg-progress-fill" style="display:block;width:10%;height:100%;background:linear-gradient(90deg,var(--accent),#0550ae);border-radius:3px;"></span></span> <span id="rrg-loading-text" style="font-size:11px;color:var(--text-muted);">loading...</span>';
  var fillEl2 = document.getElementById("rrg-progress-fill");
  var w = 20;
  var anim = setInterval(function() { w = Math.min(w + 15, 90); if (fillEl2) fillEl2.style.width = w + "%"; }, 500);

  var startTime = Date.now();
  fetch("/api/rotation/rrg?lookback=10&mode=" + mode)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      // Ensure progress bar shows at least 1 second
      var elapsed = Date.now() - startTime;
      var remaining = Math.max(0, 1000 - elapsed);
      setTimeout(function() {
        clearInterval(anim);
        if (fillEl2) fillEl2.style.width = "100%";
        var lt = document.getElementById("rrg-loading-text");
        if (lt) lt.textContent = "done";
        setTimeout(function() {
          var dateEl2 = document.getElementById("rrg-date");
          if (dateEl2) dateEl2.textContent = data.date || "no data";
        }, 300);
      }, remaining);
      if (data.error) { console.error(data.error); /* still update after delay */ }
      var dateEl2 = document.getElementById("rrg-date");
      // Update quadrant counts
      var qc = data.quadrant_counts || {};
      document.getElementById("rrg-count-leading").textContent = qc.leading || 0;
      document.getElementById("rrg-count-improving").textContent = qc.improving || 0;
      document.getElementById("rrg-count-weakening").textContent = qc.weakening || 0;
      document.getElementById("rrg-count-lagging").textContent = qc.lagging || 0;

      var filtered = getFilteredIndustries(data.industries || []);
      drawRRG(canvas, filtered);
      renderRRGTable(data.industries || []);
    })
    .catch(function(e) { console.error("RRG load failed:", e); });
}

function drawRRG(canvas, industries) {
  if (!canvas || !industries.length) return;
  var ctx = canvas.getContext("2d");
  var W = canvas.width, H = canvas.height;
  var cx = W / 2, cy = H / 2;

  ctx.clearRect(0, 0, W, H);

  // Quadrant backgrounds
  var colors = { leading: "#dafbe1", improving: "#ddf4ff", weakening: "#ffebe9", lagging: "#f6f8fa" };
  ctx.fillStyle = colors.improving; ctx.fillRect(0, 0, cx, cy);
  ctx.fillStyle = colors.leading;   ctx.fillRect(cx, 0, cx, cy);
  ctx.fillStyle = colors.lagging;   ctx.fillRect(0, cy, cx, cy);
  ctx.fillStyle = colors.weakening; ctx.fillRect(cx, cy, cx, cy);

  // Axes
  ctx.strokeStyle = "#d0d7de";
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, H); ctx.stroke();
  ctx.beginPath(); ctx.moveTo(0, cy); ctx.lineTo(W, cy); ctx.stroke();

  // Scale
  var maxRatio = 0, maxMom = 0;
  industries.forEach(function(i) {
    maxRatio = Math.max(maxRatio, Math.abs(i.rs_ratio));
    maxMom = Math.max(maxMom, Math.abs(i.rs_momentum));
  });
  maxRatio = maxRatio || 1; maxMom = maxMom || 1;
  var pad = 1.3;
  maxRatio *= pad; maxMom *= pad;
  function toX(ratio) { return cx + (ratio / maxRatio) * (cx - 30); }
  function toY(mom)   { return cy - (mom / maxMom) * (cy - 30); }

  // Store point positions for hover detection
  canvas._rrgPoints = [];

  var quadColors = { leading: "#1a7f37", improving: "#0969da", weakening: "#cf222e", lagging: "#656d76" };
  var top12 = industries.slice(0, 12);
  var top12Names = new Set(top12.map(function(i) { return i.name; }));

  // Draw tails first (behind points)
  industries.forEach(function(ind) {
    var tail = ind.tail || [];
    if (tail.length < 2) return;
    ctx.strokeStyle = quadColors[ind.quadrant] || "#656d76";
    ctx.lineWidth = 1.5;
    ctx.globalAlpha = 0.5;
    ctx.beginPath();
    for (var t = 0; t < tail.length; t++) {
      var tx = Math.max(20, Math.min(W - 20, toX(tail[t].rs_ratio)));
      var ty = Math.max(20, Math.min(H - 20, toY(tail[t].rs_momentum)));
      if (t === 0) ctx.moveTo(tx, ty);
      else ctx.lineTo(tx, ty);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;

    // Arrowhead at the end of tail (latest movement direction)
    if (tail.length >= 2) {
      var last = tail[tail.length - 1], prev = tail[tail.length - 2];
      var lx = toX(last.rs_ratio), ly = toY(last.rs_momentum);
      var px = toX(prev.rs_ratio), py = toY(prev.rs_momentum);
      var angle = Math.atan2(ly - py, lx - px);
      var arrowSize = 6;
      ctx.fillStyle = quadColors[ind.quadrant] || "#656d76";
      ctx.beginPath();
      ctx.moveTo(lx, ly);
      ctx.lineTo(lx - arrowSize * Math.cos(angle - 0.5), ly - arrowSize * Math.sin(angle - 0.5));
      ctx.lineTo(lx - arrowSize * Math.cos(angle + 0.5), ly - arrowSize * Math.sin(angle + 0.5));
      ctx.closePath();
      ctx.fill();
    }
  });

  // Then draw points on top
  industries.forEach(function(ind) {
    var x = Math.max(20, Math.min(W - 20, toX(ind.rs_ratio)));
    var y = Math.max(20, Math.min(H - 20, toY(ind.rs_momentum)));
    var r = Math.min(8, Math.max(3, 3 + Math.abs(ind.fund_flow || 0) * 0.15));

    ctx.fillStyle = quadColors[ind.quadrant] || "#656d76";
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1;
    ctx.stroke();

    canvas._rrgPoints.push({ x: x, y: y, r: r, name: ind.name, ratio: ind.rs_ratio, mom: ind.rs_momentum, quadrant: ind.quadrant });

    if (top12Names.has(ind.name)) {
      ctx.fillStyle = "#1f2328";
      ctx.font = "bold 9px sans-serif";
      ctx.fillText(ind.name, x + 8, y + 3);
    }
  });

  // Quadrant labels
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.font = "bold 12px sans-serif";
  ctx.fillText(t("rot.legendImproving"), 8, 18);
  ctx.fillText(t("rot.legendLeading"), cx + 8, 18);
  ctx.fillText(t("rot.legendLagging"), 8, H - 6);
  ctx.fillText(t("rot.legendWeakening"), cx + 8, H - 6);

  // Axis labels
  ctx.fillStyle = "#656d76";
  ctx.font = "10px sans-serif";
  ctx.fillText("RS-Ratio →", W - 70, cy - 4);
  ctx.fillText("↑ RS-Momentum", cx + 4, 12);

  // Click to expand
  canvas.style.cursor = "pointer";
  canvas.onclick = function() {
    showRRGModal(getFilteredIndustries(industries));
  };

  // Hover tooltip (works even when canvas is CSS-transformed)
  canvas.onmousemove = function(e) {
    var rect = canvas.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    // Adjust for CSS scale/transform: convert screen coords to canvas coords
    var cs = (canvas.style.transform || "").match(/scale\(([\d.]+)\)/);
    var s = cs ? parseFloat(cs[1]) : 1;
    var tx = (canvas.style.transform || "").match(/translate\(([-\d.]+)px,\s*([-\d.]+)px\)/);
    var dx = tx ? parseFloat(tx[1]) : 0, dy = tx ? parseFloat(tx[2]) : 0;
    mx = (mx - dx) / s;
    my = (my - dy) / s;
    var found = null;
    for (var i = canvas._rrgPoints.length - 1; i >= 0; i--) {
      var p = canvas._rrgPoints[i];
      if (Math.hypot(mx - p.x, my - p.y) < p.r + 6) { found = p; break; }
    }
    // Show tooltip as floating label on canvas
    var tooltip = document.getElementById("rrg-tooltip");
    if (!tooltip) {
      tooltip = document.createElement("div");
      tooltip.id = "rrg-tooltip";
      tooltip.style.cssText = "position:fixed;background:rgba(31,35,40,0.92);color:#fff;padding:6px 10px;border-radius:6px;font-size:12px;pointer-events:none;z-index:1001;white-space:nowrap;transition:opacity 0.1s;";
      document.body.appendChild(tooltip);
    }
    if (found) {
      var qLabels = { leading: t("rot.legendLeading"), improving: t("rot.legendImproving"), weakening: t("rot.legendWeakening"), lagging: t("rot.legendLagging") };
      tooltip.innerHTML = "<b>" + found.name + "</b> &nbsp;RS: " + found.ratio.toFixed(3) + " &nbsp;Mom: " + found.mom.toFixed(3) + " &nbsp;<span style=\"color:" + ({leading:"#5d5",improving:"#59f",weakening:"#f55",lagging:"#999"}[found.quadrant]||"#fff") + "\">" + (qLabels[found.quadrant]||found.quadrant) + "</span>";
      tooltip.style.opacity = "1";
      tooltip.style.left = (e.clientX + 14) + "px";
      tooltip.style.top = (e.clientY - 30) + "px";
    } else {
      tooltip.style.opacity = "0";
    }
  };
  canvas.onmouseleave = function() {
    var tooltip = document.getElementById("rrg-tooltip");
    if (tooltip) tooltip.style.opacity = "0";
  };
}

function renderRRGTable(industries) {
  var el = document.getElementById("rrg-table");
  if (!el) return;
  var top = industries.filter(function(i) { return i.quadrant === "leading" || i.quadrant === "improving"; }).slice(0, 20);
  if (!top.length) { el.innerHTML = '<span class="no-results">' + tText("scan.noLeadImproveSectors") + '</span>'; return; }
  var html = '<table style="width:100%;font-size:11px;border-collapse:collapse;">';
  html += '<tr style="background:var(--panel-bg);"><th>' + tText("scan.thIndustry") + '</th><th>RS-Ratio</th><th>RS-Mom</th><th>' + tText("scan.thFlow") + '</th><th>Q</th><th>' + tText("scan.thAction") + '</th></tr>';
  top.forEach(function(i) {
    var qLabel = i.quadrant === "leading" ? "L" : "I";
    var qColor = i.quadrant === "leading" ? "#1a7f37" : "#0969da";
    html += '<tr style="border-bottom:1px solid #f0f0f0;">';
    html += '<td style="padding:3px 4px;">' + esc(i.name) + '</td>';
    html += '<td>' + i.rs_ratio.toFixed(2) + '</td>';
    html += '<td>' + i.rs_momentum.toFixed(2) + '</td>';
    html += '<td>' + (i.fund_flow || 0).toFixed(1) + '</td>';
    html += '<td style="color:' + qColor + ';font-weight:700;">' + qLabel + '</td>';
    html += '<td><button class="btn secondary" style="padding:1px 6px;font-size:10px;" onclick="switchToSectorScan(\'' + esc(i.name) + '\')">' + tText("scan.thScanBtn") + '</button></td>';
    html += '</tr>';
  });
  html += '</table>';
  el.innerHTML = html;
}

function switchToSectorScan(industryName) {
  var cbList = document.getElementById("sector-checkbox-list");
  if (!cbList) return;
  // Select only this industry
  var inputs = cbList.querySelectorAll("input");
  inputs.forEach(function(inp) {
    inp.checked = (inp.value === industryName);
    inp.dispatchEvent(new Event("change"));
  });
  // Update count hint
  var hint = document.getElementById("sector-count-hint");
  if (hint) hint.textContent = t("scan.countOne");
  var scanBtn = document.getElementById("scan-btn");
  if (scanBtn) scanBtn.disabled = false;
  // Trigger scan
  if (scanBtn) scanBtn.click();
  // Scroll results into view
  var scanResults = document.getElementById("scan-results");
  if (scanResults) scanResults.scrollIntoView({ behavior: "smooth" });
}

function showRRGModal(industries) {
  var old = document.getElementById("rrg-modal");
  if (old) old.remove();

  var modal = document.createElement("div");
  modal.id = "rrg-modal";
  modal.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:999;display:flex;align-items:center;justify-content:center;";

  var box = document.createElement("div");
  box.style.cssText = "background:#fff;border-radius:10px;padding:16px;max-width:95vw;max-height:95vh;box-shadow:0 8px 30px rgba(0,0,0,0.3);";
  box.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">' +
    '<b style="font-size:16px;">' + tText("rot.modalTitle") + '</b>' +
    '<span style="font-size:11px;color:var(--text-muted);">' + tText("rot.modalHint") + '</span>' +
    '<button id="rrg-modal-close" style="padding:4px 12px;font-size:13px;border:1px solid var(--border);border-radius:4px;cursor:pointer;background:var(--bg);">' + tText("rot.modalClose") + '</button>' +
    '</div>' +
    '<div id="rrg-zoom-container" style="overflow:hidden;width:800px;height:800px;position:relative;border:1px solid var(--border);">' +
    '<canvas id="rrg-canvas-large" width="2400" height="2400" style="width:800px;height:800px;position:absolute;top:0;left:0;cursor:grab;"></canvas>' +
    '</div>';
  modal.appendChild(box);
  document.body.appendChild(modal);

  // Close handlers
  document.getElementById("rrg-modal-close").onclick = function() { modal.remove(); };
  modal.addEventListener("click", function(e) { if (e.target === modal) modal.remove(); });
  document.addEventListener("keydown", function esc(e) { if (e.key === "Escape") { modal.remove(); document.removeEventListener("keydown", esc); } });

  // Draw on large canvas
  var bigCanvas = document.getElementById("rrg-canvas-large");
  if (!bigCanvas) return;
  drawRRG(bigCanvas, industries);

  // --- Zoom & Pan on large canvas ---
  var container = document.getElementById("rrg-zoom-container");
  var scale = 1, panX = 0, panY = 0;
  var isPanning = false, startX, startY, startPanX, startPanY;

  function applyTransform() {
    bigCanvas.style.transform = "translate(" + panX + "px," + panY + "px) scale(" + scale + ")";
    bigCanvas.style.transformOrigin = "0 0";
  }

  container.addEventListener("wheel", function(e) {
    e.preventDefault();
    var rect = container.getBoundingClientRect();
    var mx = e.clientX - rect.left, my = e.clientY - rect.top;
    var zoom = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    var newScale = Math.min(5, Math.max(0.5, scale * zoom));
    panX = mx - (mx - panX) * (newScale / scale);
    panY = my - (my - panY) * (newScale / scale);
    scale = newScale;
    applyTransform();
  });

  bigCanvas.addEventListener("mousedown", function(e) {
    isPanning = true;
    startX = e.clientX; startY = e.clientY;
    startPanX = panX; startPanY = panY;
    bigCanvas.style.cursor = "grabbing";
    e.preventDefault();
  });
  window.addEventListener("mousemove", function(e) {
    if (!isPanning) return;
    panX = startPanX + (e.clientX - startX);
    panY = startPanY + (e.clientY - startY);
    applyTransform();
  });
  window.addEventListener("mouseup", function() {
    isPanning = false;
    bigCanvas.style.cursor = "grab";
  });

  // Double-click to reset
  bigCanvas.addEventListener("dblclick", function() {
    scale = 1; panX = 0; panY = 0;
    applyTransform();
  });
}

// RRG quadrant filter
var rrgFilter = "leading_improving"; // default: show only leading + improving
function getFilteredIndustries(all) {
  if (rrgFilter === "all") return all;
  if (rrgFilter === "leading") return all.filter(function(i) { return i.quadrant === "leading"; });
  if (rrgFilter === "improving") return all.filter(function(i) { return i.quadrant === "improving"; });
  // leading_improving
  return all.filter(function(i) { return i.quadrant === "leading" || i.quadrant === "improving"; });
}

// Bind RRG refresh
var rrgRefreshBtn = document.getElementById("rrg-refresh-btn");
if (rrgRefreshBtn) rrgRefreshBtn.addEventListener("click", loadRRG);
var rrgModeSelect = document.getElementById("rrg-mode-select");
if (rrgModeSelect) rrgModeSelect.addEventListener("change", loadRRG);
var rrgFilterSelect = document.getElementById("rrg-filter-select");
if (rrgFilterSelect) {
  rrgFilterSelect.addEventListener("change", function() {
    rrgFilter = rrgFilterSelect.value;
    loadRRG();
  });
}

// ---- Shortlist ----
function addToShortlist(ticker, name, source, score, btnEl) {
  if (btnEl) { btnEl.textContent = "..."; btnEl.disabled = true; }
  fetch("/api/shortlist", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ticker: ticker, name: name || ticker, source: source, score: score || 0 }),
  })
    .then(function(r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
    .then(function(data) {
      if (btnEl) { btnEl.textContent = t("shortlist.saved"); btnEl.style.color = "var(--gold)"; btnEl.disabled = true; }
    })
    .catch(function(e) {
      if (btnEl) { btnEl.textContent = t("shortlist.retry"); btnEl.style.color = "var(--red)"; btnEl.disabled = false; }
    });
}

function loadShortlist() {
  var list = document.getElementById("shortlist-list");
  var actions = document.getElementById("shortlist-actions");
  if (!list) return;
  list.innerHTML = '<span class="no-results">' + tText("common.loading") + '</span>';
  if (actions) actions.classList.add("hidden");
  fetch("/api/shortlist")
    .then(function(r) { return r.json(); })
    .then(function(items) {
      if (!items || !items.length) {
        list.innerHTML = '<span class="no-results">' + tText("shortlist.empty") + '</span>';
        if (actions) actions.classList.add("hidden");
        return;
      }
      if (actions) actions.classList.remove("hidden");
      var html = "";
      html += '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px;">';
      html += '<label style="font-size:11px;cursor:pointer;"><input type="checkbox" id="shortlist-select-all" style="margin-right:4px;">' + tText("common.selectAll") + '</label>';
      html += '<button id="shortlist-batch-btn" class="btn primary" style="padding:4px 12px;font-size:11px;" disabled>' + tText("shortlist.batchAnalyze") + '</button>';
      html += '<span id="shortlist-batch-status" style="font-size:10px;color:var(--text-muted);"></span>';
      html += '</div>';
      items.forEach(function(item) {
        var priceStr = item.price_at_add ? ' ¥' + Number(item.price_at_add).toFixed(2) : '';
        var addedStr = item.added_at ? ' ' + String(item.added_at).slice(0,16) : '';
        var scoreStr = item.score ? item.score.toFixed(2) : "-";
        html += '<div class="shortlist-item" data-ticker="' + esc(item.ticker) + '" style="cursor:pointer;">';
        html += '<input type="checkbox" class="shortlist-check" data-ticker="' + esc(item.ticker) + '" style="margin-right:8px;" onclick="event.stopPropagation();">';
        html += '<span class="shortlist-ticker">' + esc(item.ticker) + '</span>';
        html += '<span class="shortlist-name">' + esc(item.name || "") + '</span>';
        html += '<span class="shortlist-price" style="font-size:10px;color:var(--accent);">' + esc(priceStr) + '</span>';
        html += '<span class="shortlist-time" style="font-size:9px;color:var(--text-muted);">' + esc(addedStr) + '</span>';
        html += '<button class="shortlist-analyze" data-ticker="' + esc(item.ticker) + '" data-name="' + esc(item.name || "") + '" title="' + t("shortlist.sendPipeline") + '" onclick="event.stopPropagation();">▶</button>';
        html += '<button class="hi-delete shortlist-del" data-ticker="' + esc(item.ticker) + '" title="' + t("shortlist.remove") + '" onclick="event.stopPropagation();">&times;</button>';
        html += '</div>';
      });
      list.innerHTML = html;
      bindShortlistButtons(list);
      bindShortlistBatch();
    })
    .catch(function() { list.innerHTML = '<span class="no-results">' + tText("shortlist.loadFailed") + '</span>'; });
}

function bindShortlistBatch() {
  var selectAllCb = document.getElementById("shortlist-select-all");
  var batchBtn = document.getElementById("shortlist-batch-btn");
  var statusEl = document.getElementById("shortlist-batch-status");
  var checks = document.querySelectorAll(".shortlist-check");

  function updateBatchBtn() {
    var checked = document.querySelectorAll(".shortlist-check:checked");
    if (batchBtn) batchBtn.disabled = (checked.length === 0);
  }

  if (selectAllCb) {
    selectAllCb.addEventListener("change", function() {
      checks.forEach(function(cb) { cb.checked = selectAllCb.checked; });
      updateBatchBtn();
    });
  }
  checks.forEach(function(cb) { cb.addEventListener("change", updateBatchBtn); });

  if (batchBtn) {
    batchBtn.addEventListener("click", function() {
      if (_batchRunning) { _batchStop = true; return; }
      var checked = document.querySelectorAll(".shortlist-check:checked");
      if (!checked.length) return;
      var tickers = [];
      checked.forEach(function(cb) { tickers.push(cb.dataset.ticker); });
      runBatchAnalysis(tickers, statusEl, batchBtn);
    });
  }
}

var _batchStop = false;
var _batchRunning = false;

function runBatchAnalysis(tickers, statusEl, btnEl) {
  if (!tickers.length || _batchRunning) return;
  _batchStop = false;
  _batchRunning = true;
  var total = tickers.length;
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = t("shortlist.stop"); }
  if (statusEl) statusEl.textContent = "0/" + total;

  // Switch to Analyze tab view
  currentTab = "analyze";
  document.querySelectorAll(".top-tab-btn").forEach(function(b){b.classList.remove("active");});
  document.querySelectorAll(".tab-content").forEach(function(c){c.classList.remove("active");});
  var aTab = document.querySelector('[data-tab="analyze"]');
  if (aTab) aTab.classList.add("active");
  var aCont = document.getElementById("tab-analyze");
  if (aCont) aCont.classList.add("active");
  clearResults();
  var app = document.getElementById("app");
  if (app) app.classList.remove("advisory-mode","aipick-mode","historyagent-mode","sched-mode");

  function runNext(idx) {
    if (_batchStop || idx >= total) {
      _batchRunning = false;
      if (statusEl) statusEl.textContent = _batchStop ? t("shortlist.stoppedAt", {done: idx, total: total}) : t("shortlist.done", {n: total});
      if (btnEl) { btnEl.disabled = false; btnEl.textContent = t("shortlist.batchAnalyze"); }
      return;
    }
    var ticker = tickers[idx];
    if (statusEl) statusEl.textContent = t("shortlist.analyzing", {done: idx+1, total: total, ticker: ticker});

    // Auto-fill ticker
    var tInput = document.getElementById("ticker-input");
    if (tInput) tInput.value = ticker;
    selectedTicker = {symbol: ticker, name: ticker, exchange: ""};
    var selSym = document.getElementById("selected-symbol");
    var selName = document.getElementById("selected-name");
    var selCard = document.getElementById("selected-card");
    if (selSym) selSym.textContent = ticker;
    if (selName) selName.textContent = ticker;
    if (selCard) selCard.classList.remove("hidden");
    var cc = document.getElementById("chart-container");
    if (cc) cc.classList.remove("hidden");
    if (typeof loadChart === "function") loadChart(ticker, "max");

    // Fire SSE analysis
    var deepP = deepProviderSelect ? deepProviderSelect.value : "";
    var quickP = quickProviderSelect ? quickProviderSelect.value : "";
    var deepM = deepModelSelect ? deepModelSelect.value : "";
    var quickM = quickModelSelect ? quickModelSelect.value : "";
    var lang = languageSelect ? languageSelect.value : "Chinese";
    var date = new Date().toISOString().slice(0,10);
    var analysts = "capital_flow,market,social,news,fundamentals,competitor,partner";
    var url = "/api/analyze?ticker=" + encodeURIComponent(ticker) + "&date=" + date + "&analysts=" + analysts + "&deep_provider=" + encodeURIComponent(deepP) + "&quick_provider=" + encodeURIComponent(quickP) + "&deep_model=" + encodeURIComponent(deepM) + "&quick_model=" + encodeURIComponent(quickM) + "&language=" + lang + "&checkpoint_1=false&checkpoint_2=false";

    var es = new EventSource(url);
    var done = false;
    es.addEventListener("pipeline-done", function() { if (!done) { done = true; es.close(); runNext(idx + 1); } });
    es.addEventListener("pipeline-error", function() { if (!done) { done = true; es.close(); runNext(idx + 1); } });
    es.onerror = function() {
      if (!done) { done = true; es.close(); setTimeout(function(){ runNext(idx + 1); }, 2000); }
    };
  }

  // Update button to show "Stop" mode
  if (btnEl) {
    btnEl.textContent = t("shortlist.stop");
    btnEl.disabled = false;
  }
  runNext(0);
}

function bindShortlistButtons(list) {
  list.querySelectorAll(".shortlist-analyze").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      switchToAnalyzeAndRun(btn.dataset.ticker, "", "");
    });
  });
  list.querySelectorAll(".shortlist-del").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      fetch("/api/shortlist/" + encodeURIComponent(btn.dataset.ticker), { method: "DELETE" })
        .then(function() { loadShortlist(); });
    });
  });
  list.querySelectorAll(".shortlist-item").forEach(function(el) {
    el.addEventListener("click", function() {
      var ticker = el.querySelector(".shortlist-ticker").textContent;
      validateShortlistItem(ticker);
    });
  });
}

// ── Shortlist manual add ──
var shortlistAddBtn = document.getElementById("shortlist-add-btn");
var shortlistAddInput = document.getElementById("shortlist-add-input");
var shortlistAddStatus = document.getElementById("shortlist-add-status");
if (shortlistAddBtn && shortlistAddInput) {
  shortlistAddBtn.addEventListener("click", function() {
    var ticker = shortlistAddInput.value.trim();
    if (!ticker) return;
    shortlistAddBtn.disabled = true;
    shortlistAddStatus.textContent = t("shortlist.adding");
    fetch("/api/shortlist/add-manual", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ticker: ticker})
    })
    .then(function(r) {
      if (!r.ok) return r.json().then(function(e) { throw new Error(e.detail || t("shortlist.failed")); });
      return r.json();
    })
    .then(function() {
      shortlistAddInput.value = "";
      shortlistAddStatus.textContent = t("shortlist.added");
      loadShortlist();
    })
    .catch(function(e) {
      shortlistAddStatus.textContent = t("shortlist.failedPrefix") + e.message;
    })
    .finally(function() {
      shortlistAddBtn.disabled = false;
      setTimeout(function() { shortlistAddStatus.textContent = ""; }, 3000);
    });
  });
  shortlistAddInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") shortlistAddBtn.click();
  });
}

// ── Shortlist validation panel ──
function validateShortlistItem(ticker) {
  var panel = document.getElementById("shortlist-validate-panel");
  var title = document.getElementById("shortlist-validate-title");
  var body = document.getElementById("shortlist-validate-body");
  if (!panel) return;

  panel.classList.remove("hidden");
  title.textContent = t("shortlist.validating");
  body.textContent = "";

  fetch("/api/shortlist/validate/" + encodeURIComponent(ticker))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      title.textContent = d.ticker + " " + (d.name || "");
      var changeColor = d.change_pct >= 0 ? "var(--green)" : "var(--red)";
      var changeSign = d.change_pct >= 0 ? "+" : "";
      body.innerHTML =
        '<div style="display:grid;grid-template-columns:auto 1fr;gap:4px 12px;">' +
        '<span style="color:var(--text-muted);">' + tText("shortlist.addedAt") + '</span><span>' + esc(d.added_at || "-") + '</span>' +
        '<span style="color:var(--text-muted);">' + tText("shortlist.addedPrice") + '</span><span>' + (d.price_at_add ? d.price_at_add.toFixed(2) : "-") + '</span>' +
        '<span style="color:var(--text-muted);">' + tText("shortlist.currentPrice") + '</span><span>' + (d.current_price ? d.current_price.toFixed(2) : "-") + '</span>' +
        '<span style="color:var(--text-muted);">' + tText("shortlist.changePct") + '</span><span style="color:' + changeColor + ';font-weight:600;">' + changeSign + d.change_pct.toFixed(2) + '%</span>' +
        '<span style="color:var(--text-muted);">' + tText("shortlist.source") + '</span><span>' + esc(d.source || "-") + '</span>' +
        '</div>' +
        '<div style="margin-top:10px;display:flex;gap:6px;">' +
        '<button onclick="switchToAnalyzeAndRun(\'' + esc(d.ticker) + '\',\'\',\'\')" class="btn primary" style="font-size:11px;padding:4px 10px;">' + tText("tab.analyze") + '</button>' +
        '<button onclick="var p=document.getElementById(\'shortlist-validate-panel\');if(p)p.classList.add(\'hidden\');" class="btn secondary" style="font-size:11px;padding:4px 10px;">' + tText("shortlist.close") + '</button>' +
        '</div>';
    })
    .catch(function(e) {
      title.textContent = t("shortlist.validateFailed");
      body.textContent = e.message;
    });
}

var shortlistValidateClose = document.getElementById("shortlist-validate-close");
if (shortlistValidateClose) {
  shortlistValidateClose.addEventListener("click", function() {
    var p = document.getElementById("shortlist-validate-panel");
    if (p) p.classList.add("hidden");
  });
}

// Clear all shortlist
var clearShortlistBtn = document.getElementById("clear-shortlist-btn");
if (clearShortlistBtn) {
  clearShortlistBtn.addEventListener("click", function() {
    if (!confirm(t("shortlist.clearConfirm"))) return;
    fetch("/api/shortlist", { method: "DELETE" })
      .then(function() { loadShortlist(); });
  });
}

// Analyze All in shortlist
var shortlistAnalyzeAll = document.getElementById("shortlist-analyze-all");
if (shortlistAnalyzeAll) {
  shortlistAnalyzeAll.addEventListener("click", function() {
    fetch("/api/shortlist").then(function(r) { return r.json(); })
      .then(function(items) {
        if (!items || !items.length) return;
        var candidates = items.map(function(i) { return { ticker: i.ticker, ts_code: i.ticker, name: i.name, score: i.score }; });
        batchAnalyzeAll(candidates);
      });
  });
}

// ---- Eastmoney Watchlist ----
var emwlRefreshBtn = document.getElementById("emwl-refresh-btn");

// ---- EMWL LLM Model Selector ----
var emwlProviderSelect = document.getElementById("emwl-provider-select");
var emwlModelSelect = document.getElementById("emwl-model-select");
var emwlModelCatalog = null;  // cached model catalog from /api/models

function initEmwlModelSelector() {
  if (!emwlProviderSelect || !emwlModelSelect) return;
  fetch("/api/models")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      emwlModelCatalog = data;
      var providers = data.providers || [];
      // Default to Ollama (local) if available, otherwise first provider
      emwlProviderSelect.innerHTML = "";
      var defaultProvider = "";
      providers.forEach(function(p) {
        var sel = p.key === "ollama" ? ' selected' : '';
        if (p.key === "ollama") defaultProvider = "ollama";
        emwlProviderSelect.innerHTML += '<option value="' + esc(p.key) + '"' + sel + '>' + esc(p.label) + '</option>';
      });
      if (defaultProvider) {
        onEmwlProviderChange();
        // Default model: prefer non-thinking models (qwen3:32b, gemma4:12b)
        // Avoid models with "thinking" capability — they put answers in
        // reasoning field, leaving content empty via OpenAI-compatible API.
        setTimeout(function() {
          var opts = emwlModelSelect.options;
          var preferred = ["qwen3.8:27b", "qwen3:32b", "qwen3.5:latest", "gemma4:12b"];
          for (var p = 0; p < preferred.length; p++) {
            for (var i = 0; i < opts.length; i++) {
              if (opts[i].value === preferred[p]) {
                emwlModelSelect.value = opts[i].value;
                return;
              }
            }
          }
          // Fallback: first available model
          if (opts.length > 0) emwlModelSelect.value = opts[0].value;
        }, 200);
      }
    })
    .catch(function() { /* silent — model catalog optional */ });
}

function onEmwlProviderChange() {
  if (!emwlModelSelect || !emwlModelCatalog) return;
  var prov = emwlProviderSelect.value;
  if (!prov) {
    emwlModelSelect.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>';
    return;
  }
  var providerData = (emwlModelCatalog.providers || []).find(function(p) { return p.key === prov; });
  if (!providerData) { emwlModelSelect.innerHTML = '<option value="">--</option>'; return; }
  var deepModels = providerData.deep_models || [];
  if (!deepModels.length) {
    emwlModelSelect.innerHTML = '<option value="">' + t("common.noModels") + '</option>';
    return;
  }
  emwlModelSelect.innerHTML = deepModels.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
}

// Get model1 params (single-stock analysis uses model1 only)
function getEmwlModelParams() {
  var p = emwlProviderSelect ? emwlProviderSelect.value : "";
  var m = emwlModelSelect ? emwlModelSelect.value : "";
  var parts = [];
  if (p) parts.push("provider=" + encodeURIComponent(p));
  if (m) parts.push("model=" + encodeURIComponent(m));
  return parts.join("&");
}
// Get model2 params (for parallel batch — second stock uses model2)
function getEmwlModel2Params() {
  var p = emwlProvider2Select ? emwlProvider2Select.value : "";
  var m = emwlModel2Select ? emwlModel2Select.value : "";
  var parts = [];
  if (p) parts.push("provider=" + encodeURIComponent(p));
  if (m) parts.push("model=" + encodeURIComponent(m));
  return parts.join("&");
}

if (emwlProviderSelect) {
  emwlProviderSelect.addEventListener("change", onEmwlProviderChange);
}
// Init on page load
initEmwlModelSelector();

// ── Dual-model support (模型2) ──
var emwlProvider2Select = document.getElementById("emwl-provider2-select");
var emwlModel2Select = document.getElementById("emwl-model2-select");
var emwlDualToggle = document.getElementById("emwl-dual-toggle");
var emwlModel2Catalog = null;

function initEmwlModel2Selector() {
  if (!emwlProvider2Select || !emwlModel2Select) return;
  fetch("/api/models").then(function(r) { return r.json(); }).then(function(data) {
    emwlModel2Catalog = data;
    var providers = data.providers || [];
    emwlProvider2Select.innerHTML = '<option value="">' + t("common.defaultProvider") + '</option>';
    providers.forEach(function(p) {
      emwlProvider2Select.innerHTML += '<option value="' + esc(p.key) + '">' + esc(p.label) + '</option>';
    });
    onEmwlProvider2Change();
  }).catch(function() {});
}
function onEmwlProvider2Change() {
  if (!emwlModel2Select || !emwlModel2Catalog) return;
  var prov = emwlProvider2Select.value;
  if (!prov) { emwlModel2Select.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>'; return; }
  var pd = (emwlModel2Catalog.providers || []).find(function(p) { return p.key === prov; });
  if (!pd) { emwlModel2Select.innerHTML = '<option value="">--</option>'; return; }
  var models = pd.deep_models || [];
  emwlModel2Select.innerHTML = models.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
}
function toggleEmwlDualModel() {
  var row = document.getElementById("emwl-model2-row");
  if (row) row.style.display = emwlDualToggle && emwlDualToggle.checked ? "flex" : "none";
}
function getEmwlModel2Params() {
  var p = emwlProvider2Select ? emwlProvider2Select.value : "";
  var m = emwlModel2Select ? emwlModel2Select.value : "";
  var parts = [];
  if (p) parts.push("provider=" + encodeURIComponent(p));
  if (m) parts.push("model=" + encodeURIComponent(m));
  return parts.join("&");
}
function isEmwlDualEnabled() { return emwlDualToggle && emwlDualToggle.checked; }

if (emwlProvider2Select) { emwlProvider2Select.addEventListener("change", onEmwlProvider2Change); }
initEmwlModel2Selector();setTimeout(function(){var ms=document.getElementById("emwl-model2-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);

// ── Model 3/4 support (same pattern as model 2) ──
var emwlProvider3Select = document.getElementById("emwl-provider3-select");
var emwlModel3Select = document.getElementById("emwl-model3-select");
var emwlTriToggle = document.getElementById("emwl-tri-toggle");
var emwlModel3Catalog = null;
function initEmwlModel3Selector(){if(!emwlProvider3Select||!emwlModel3Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){emwlModel3Catalog=d;var p=d.providers||[];emwlProvider3Select.innerHTML='<option value="">' + t("common.defaultProvider") + '</option>';p.forEach(function(x){emwlProvider3Select.innerHTML+='<option value="'+esc(x.key)+'">'+esc(x.label)+'</option>'});onEmwlProvider3Change()}).catch(function(){})}
function onEmwlProvider3Change(){if(!emwlModel3Select||!emwlModel3Catalog)return;var prov=emwlProvider3Select.value;if(!prov){emwlModel3Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(emwlModel3Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd){emwlModel3Select.innerHTML='<option value="">--</option>';return}emwlModel3Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}
function toggleEmwlTriModel(){var row=document.getElementById("emwl-model3-row");if(row)row.style.display=emwlTriToggle&&emwlTriToggle.checked?"flex":"none"}
function getEmwlModel3Params(){var p=emwlProvider3Select?emwlProvider3Select.value:"",m=emwlModel3Select?emwlModel3Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}
function isEmwlTriEnabled(){return emwlTriToggle&&emwlTriToggle.checked}
if(emwlProvider3Select){emwlProvider3Select.addEventListener("change",onEmwlProvider3Change)}initEmwlModel3Selector();setTimeout(function(){var ms=document.getElementById("emwl-model3-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);

var emwlProvider4Select = document.getElementById("emwl-provider4-select");
var emwlModel4Select = document.getElementById("emwl-model4-select");
var emwlQuadToggle = document.getElementById("emwl-quad-toggle");
var emwlModel4Catalog = null;
function initEmwlModel4Selector(){if(!emwlProvider4Select||!emwlModel4Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){emwlModel4Catalog=d;var p=d.providers||[];emwlProvider4Select.innerHTML='<option value="">' + t("common.defaultProvider") + '</option>';p.forEach(function(x){emwlProvider4Select.innerHTML+='<option value="'+esc(x.key)+'">'+esc(x.label)+'</option>'});onEmwlProvider4Change()}).catch(function(){})}
function onEmwlProvider4Change(){if(!emwlModel4Select||!emwlModel4Catalog)return;var prov=emwlProvider4Select.value;if(!prov){emwlModel4Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(emwlModel4Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd){emwlModel4Select.innerHTML='<option value="">--</option>';return}emwlModel4Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}
function toggleEmwlQuadModel(){var row=document.getElementById("emwl-model4-row");if(row)row.style.display=emwlQuadToggle&&emwlQuadToggle.checked?"flex":"none"}
function getEmwlModel4Params(){var p=emwlProvider4Select?emwlProvider4Select.value:"",m=emwlModel4Select?emwlModel4Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}
function isEmwlQuadEnabled(){return emwlQuadToggle&&emwlQuadToggle.checked}
if(emwlProvider4Select){emwlProvider4Select.addEventListener("change",onEmwlProvider4Change)}initEmwlModel4Selector();setTimeout(function(){var ms=document.getElementById("emwl-model4-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);
var emwlProvider5Select=document.getElementById("emwl-provider5-select");var emwlModel5Select=document.getElementById("emwl-model5-select");var emwlPentaToggle=document.getElementById("emwl-penta-toggle");var emwlModel5Catalog=null;function initEmwlModel5Selector(){if(!emwlProvider5Select||!emwlModel5Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){emwlModel5Catalog=d;var p=d.providers||[];emwlProvider5Select.innerHTML='<option value="">' + t("common.defaultProvider") + '</option>';p.forEach(function(x){emwlProvider5Select.innerHTML+='<option value="'+esc(x.key)+'">'+esc(x.label)+'</option>'});onEmwlProvider5Change()}).catch(function(){})}function onEmwlProvider5Change(){if(!emwlModel5Select||!emwlModel5Catalog)return;var prov=emwlProvider5Select.value;if(!prov){emwlModel5Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(emwlModel5Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd){emwlModel5Select.innerHTML='<option value="">--</option>';return}emwlModel5Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleEmwlPentaModel(){var row=document.getElementById("emwl-model5-row");if(row)row.style.display=emwlPentaToggle&&emwlPentaToggle.checked?"flex":"none"}function getEmwlModel5Params(){var p=emwlProvider5Select?emwlProvider5Select.value:"",m=emwlModel5Select?emwlModel5Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isEmwlPentaEnabled(){return emwlPentaToggle&&emwlPentaToggle.checked}if(emwlProvider5Select){emwlProvider5Select.addEventListener("change",onEmwlProvider5Change)}initEmwlModel5Selector();setTimeout(function(){var ms=document.getElementById("emwl-model5-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);
var emwlProvider6Select=document.getElementById("emwl-provider6-select");var emwlModel6Select=document.getElementById("emwl-model6-select");var emwlHexaToggle=document.getElementById("emwl-hexa-toggle");var emwlModel6Catalog=null;function initEmwlModel6Selector(){if(!emwlProvider6Select||!emwlModel6Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){emwlModel6Catalog=d;var p=d.providers||[];emwlProvider6Select.innerHTML='<option value="">' + t("common.defaultProvider") + '</option>';p.forEach(function(x){emwlProvider6Select.innerHTML+='<option value="'+esc(x.key)+'">'+esc(x.label)+'</option>'});onEmwlProvider6Change()}).catch(function(){})}function onEmwlProvider6Change(){if(!emwlModel6Select||!emwlModel6Catalog)return;var prov=emwlProvider6Select.value;if(!prov){emwlModel6Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(emwlModel6Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd){emwlModel6Select.innerHTML='<option value="">--</option>';return}emwlModel6Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleEmwlHexaModel(){var row=document.getElementById("emwl-model6-row");if(row)row.style.display=emwlHexaToggle&&emwlHexaToggle.checked?"flex":"none"}function getEmwlModel6Params(){var p=emwlProvider6Select?emwlProvider6Select.value:"",m=emwlModel6Select?emwlModel6Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isEmwlHexaEnabled(){return emwlHexaToggle&&emwlHexaToggle.checked}if(emwlProvider6Select){emwlProvider6Select.addEventListener("change",onEmwlProvider6Change)}initEmwlModel6Selector();
var emwlProvider7Select=document.getElementById("emwl-provider7-select");var emwlModel7Select=document.getElementById("emwl-model7-select");var emwlHeptaToggle=document.getElementById("emwl-hepta-toggle");var emwlModel7Catalog=null;function initEmwlModel7Selector(){if(!emwlProvider7Select||!emwlModel7Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){emwlModel7Catalog=d;var p=d.providers||[];emwlProvider7Select.innerHTML='<option value="">' + t("common.defaultProvider") + '</option>';p.forEach(function(x){emwlProvider7Select.innerHTML+='<option value="'+esc(x.key)+'">'+esc(x.label)+'</option>'});onEmwlProvider7Change()}).catch(function(){})}function onEmwlProvider7Change(){if(!emwlModel7Select||!emwlModel7Catalog)return;var prov=emwlProvider7Select.value;if(!prov){emwlModel7Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(emwlModel7Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd){emwlModel7Select.innerHTML='<option value="">--</option>';return}emwlModel7Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleEmwlHeptaModel(){var row=document.getElementById("emwl-model7-row");if(row)row.style.display=emwlHeptaToggle&&emwlHeptaToggle.checked?"flex":"none"}function getEmwlModel7Params(){var p=emwlProvider7Select?emwlProvider7Select.value:"",m=emwlModel7Select?emwlModel7Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isEmwlHeptaEnabled(){return emwlHeptaToggle&&emwlHeptaToggle.checked}if(emwlProvider7Select){emwlProvider7Select.addEventListener("change",onEmwlProvider7Change)}initEmwlModel7Selector();
var emwlProvider8Select=document.getElementById("emwl-provider8-select");var emwlModel8Select=document.getElementById("emwl-model8-select");var emwlOctaToggle=document.getElementById("emwl-octa-toggle");var emwlModel8Catalog=null;function initEmwlModel8Selector(){if(!emwlProvider8Select||!emwlModel8Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){emwlModel8Catalog=d;var p=d.providers||[];emwlProvider8Select.innerHTML='<option value="">' + t("common.defaultProvider") + '</option>';p.forEach(function(x){emwlProvider8Select.innerHTML+='<option value="'+esc(x.key)+'">'+esc(x.label)+'</option>'});onEmwlProvider8Change()}).catch(function(){})}function onEmwlProvider8Change(){if(!emwlModel8Select||!emwlModel8Catalog)return;var prov=emwlProvider8Select.value;if(!prov){emwlModel8Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(emwlModel8Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd){emwlModel8Select.innerHTML='<option value="">--</option>';return}emwlModel8Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleEmwlOctaModel(){var row=document.getElementById("emwl-model8-row");if(row)row.style.display=emwlOctaToggle&&emwlOctaToggle.checked?"flex":"none"}function getEmwlModel8Params(){var p=emwlProvider8Select?emwlProvider8Select.value:"",m=emwlModel8Select?emwlModel8Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isEmwlOctaEnabled(){return emwlOctaToggle&&emwlOctaToggle.checked}if(emwlProvider8Select){emwlProvider8Select.addEventListener("change",onEmwlProvider8Change)}initEmwlModel8Selector();setTimeout(function(){var ms=document.getElementById("emwl-model6-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);

// Resizable divider between flow box and chart
(function() {
  var resizer = document.getElementById("emwl-detail-resizer");
  var flowBox = document.getElementById("emwl-flow-box");
  var chartBox = document.getElementById("emwl-chart-box");
  if (!resizer || !flowBox || !chartBox) return;
  var isDragging = false;
  resizer.addEventListener("mousedown", function(e) {
    isDragging = true;
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    e.preventDefault();
  });
  document.addEventListener("mousemove", function(e) {
    if (!isDragging) return;
    var panel = document.getElementById("emwl-detail-panel");
    if (!panel) return;
    var rect = panel.getBoundingClientRect();
    var newTop = e.clientY - rect.top;
    var panelHeight = rect.height;
    var minTop = 80, maxTop = panelHeight - 200;
    newTop = Math.max(minTop, Math.min(maxTop, newTop));
    flowBox.style.height = newTop + "px";
  });
  document.addEventListener("mouseup", function() {
    if (isDragging) {
      isDragging = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    }
  });
})();

function showEmwlRightPanel(code) {
  // Show the detail panel in the main right-panel area
  var detailPanel = document.getElementById("emwl-detail-panel");
  if (detailPanel) {
    detailPanel.classList.remove("hidden");
    detailPanel.style.display = "flex";
  }
  var flowBox = document.getElementById("emwl-flow-box");
  var chartFrame = document.getElementById("emwl-chart-iframe");
  if (flowBox) flowBox.innerHTML = '<span style="color:var(--text-muted);">' + t("common.loading") + '</span>';
  if (chartFrame) {
    // Look up stock name from all loaded data sources for the chart header
    var stockName = "";
    // Try emwlStocks first, then idxStocks, then table DOM
    var allStocks = (emwlStocks && emwlStocks.length ? emwlStocks : (typeof idxStocks !== 'undefined' && idxStocks.length ? idxStocks : []));
    if (allStocks.length) {
      var found = allStocks.find(function(s) { return s.code === code; });
      if (found && found.name) stockName = found.name;
    }
    if (!stockName) {
      var row = document.querySelector('[data-code="' + code + '"]');
      if (row) {
        var cells = row.querySelectorAll("td");
        if (cells.length >= 3) stockName = (cells[2].textContent || "").trim();
      }
    }
    var chartUrl = "http://127.0.0.1:8005/?ticker=" + encodeURIComponent(code);
    if (stockName) chartUrl += "&name=" + encodeURIComponent(stockName);
    chartFrame.src = chartUrl;
  }
  var qsR = getEmwlModelParams();
  qsR = (qsR ? qsR + "&" : "") + "no_llm=true";
  fetch("/api/eastmoney/watchlist/detail/" + encodeURIComponent(code) + "?" + qsR)
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (!flowBox) return;
      var fd = d.flow_detail || [];
      // Real-time snapshot header
      var rtPrice = d.rt_price || "?";
      var rtChg = d.rt_change_pct || "0";
      var rtHigh = d.rt_high || "?";
      var rtLow = d.rt_low || "?";
      var rtVol = d.rt_volume || "?";
      var rtAmt = (parseFloat(d.rt_amount || "0")).toFixed(0);  // already in 万元

      var html = '<div style="font-weight:600;margin-bottom:2px;">' + esc(code) + '</div>';
      html += '<div style="font-size:9px;color:var(--text-muted);margin-bottom:4px;">';
      html += t("rt.live") + esc(rtPrice) + ' | ' + t("rt.chg") + esc(rtChg) + '% | ' + t("rt.high") + esc(rtHigh) + ' | ' + t("rt.low") + esc(rtLow);
      html += ' | ' + t("rt.vol") + esc(rtVol) + '手 | ' + t("rt.amt") + esc(rtAmt) + '万</div>';

      // MX realtime 主力 (DDX/DDY/DDZ) + 暗盘/大宗 summary
      if (d.mx_ddx || d.mx_block_trades) {
        var mxLines = [];
        if (d.mx_ddx) {
          var picks = d.mx_ddx.split("\n").map(function(l){ return l.replace(/^-\s+/,"").trim(); })
            .filter(function(l){ return /DDX|DDY|DDZ|主力净流入|超大单净流入|大单净流入/.test(l); })
            .slice(0, 6);
          if (picks.length) mxLines.push("⚡ " + t("rt.mx") + picks.join(" · "));
        }
        if (d.mx_block_trades) {
          var btRows = d.mx_block_trades.split("\n").filter(function(l){ return l.indexOf("|") === 0 && /\d{4}-/.test(l); });
          var btInst = btRows.filter(function(l){ return l.indexOf("机构") >= 0; }).length;
          if (btRows.length) mxLines.push("🕳 " + t("rt.mxBlock") + btRows.length + (btInst ? " · 机构" + btInst : ""));
        }
        if (mxLines.length) html += '<div style="font-size:9px;margin:3px 0;color:#d4a017;">' + esc(mxLines.join("  ")) + '</div>';
      }

      // Multi-horizon 主力 summary (当日/5/20/60日 + 构成) — values already 万元,
      // pre-computed server-side from the 90-day cache (same anti-hallucination
      // discipline as the LLM prompt block: the model/panel both cite verbatim).
      // Strength is shown as 主力净额占日均成交额% (relative to liquidity/scale),
      // NOT a bare absolute 万 figure — the same 万元 means 强吸筹 for a 27亿
      // micro-cap and 噪音 for a 2000亿 mega-cap, so the % (and 流通市值 when
      // present) is what makes the numbers interpretable.
      var mh = d.flow_multi_horizon || {};
      if (mh["1"] || mh["5"] || mh["20"] || mh["60"]) {
        var mhLabels = { "1": t("rt.mh1d"), "5": t("rt.mh5d"), "20": t("rt.mh20d"), "60": t("rt.mh60d") };
        var mhCtx = [];
        if (mh.circ_mv_wan) mhCtx.push(t("rt.mhCap") + (mh.circ_mv_wan / 1e4).toFixed(1) + t("rt.mhCapUnit"));
        ["60", "20", "5"].some(function(h) {
          var e = mh[h];
          if (e && e.days && e.avg_turnover) { mhCtx.push(t("rt.mhAvgTurn") + e.days + t("rt.mhAvgTurnUnit") + (e.avg_turnover / 1e4).toFixed(2) + t("rt.mhCapUnit")); return true; }
          return false;
        });
        var mhRows = ["1", "5", "20", "60"].map(function(h) {
          var e = mh[h];
          if (!e || !e.days) return '  ' + mhLabels[h] + '  数据不足';
          var sgn = function(v) { return (v >= 0 ? "+" : "") + v; };
          var mc = e.main >= 0 ? "var(--green)" : "var(--red)";
          var rr = (e.ratio >= 0 ? "+" : "") + Math.round(e.ratio * 10) / 10;
          return '  ' + mhLabels[h] + ' <span style="color:' + mc + ';font-weight:600;">' + sgn(e.main) + '万</span>' +
                 '(超大' + sgn(e.elg) + '/大单' + sgn(e.lg) + ') 占成交额<span style="color:' + mc + ';">' + rr + '%</span> | ' + e.pos_days + '/' + e.days + '日净流入';
        }).join('<br>');
        html += '<div style="font-size:9px;margin:4px 0;padding:3px 5px;background:var(--bg-elev);border-radius:3px;color:var(--text-muted);">';
        html += '<div style="font-weight:600;color:var(--text);">' + t("rt.mhTitle") + '（万元，主力=超大单+大单，强度=占成交额%）</div>' +
                (mhCtx.length ? '<div style="margin-bottom:2px;">' + mhCtx.join(' | ') + '</div>' : '') +
                mhRows;
        html += '</div>';
      }

      // Flow detail table (tushare amounts are in 万元)
      html += '<div style="overflow-x:auto;"><table style="width:100%;font-size:9px;border-collapse:collapse;white-space:nowrap;">';
      html += '<tr style="color:var(--text-muted);">';
      html += '<th>' + t("rt.colDate") + '</th><th style="text-align:right;">' + t("rt.colNet") + '</th>';
      html += '<th style="text-align:right;">' + t("rt.colSuperNet") + '</th><th style="text-align:right;">' + t("rt.colLargeNet") + '</th>';
      html += '<th style="text-align:right;">' + t("rt.colSuperBuy") + '</th><th style="text-align:right;">' + t("rt.colSuperSell") + '</th>';
      html += '<th style="text-align:right;">' + t("rt.colLargeBuy") + '</th><th style="text-align:right;">' + t("rt.colLargeSell") + '</th></tr>';
      var tNet = 0, tElgNet = 0, tLgNet = 0, tElgB = 0, tElgS = 0, tLgB = 0, tLgS = 0;
      fd.forEach(function(r) {
        var elgNet = (r.buy_elg||0) - (r.sell_elg||0);
        var lgNet = (r.buy_lg||0) - (r.sell_lg||0);
        var c = r.net >= 0 ? "var(--green)" : "var(--red)";
        var ce = elgNet >= 0 ? "var(--green)" : "var(--red)";
        var cl = lgNet >= 0 ? "var(--green)" : "var(--red)";
        html += '<tr><td>' + esc(r.date) + '</td>';
        html += '<td style="text-align:right;color:' + c + ';">' + (r.net >= 0 ? "+" : "") + r.net + '</td>';
        html += '<td style="text-align:right;color:' + ce + ';">' + (elgNet >= 0 ? "+" : "") + elgNet + '</td>';
        html += '<td style="text-align:right;color:' + cl + ';">' + (lgNet >= 0 ? "+" : "") + lgNet + '</td>';
        html += '<td style="text-align:right;">' + (r.buy_elg||0) + '</td><td style="text-align:right;">' + (r.sell_elg||0) + '</td>';
        html += '<td style="text-align:right;">' + (r.buy_lg||0) + '</td><td style="text-align:right;">' + (r.sell_lg||0) + '</td></tr>';
        tNet += r.net||0; tElgNet += elgNet; tLgNet += lgNet;
        tElgB += r.buy_elg||0; tElgS += r.sell_elg||0;
        tLgB += r.buy_lg||0; tLgS += r.sell_lg||0;
      });
      var tc = tNet >= 0 ? "var(--green)" : "var(--red)";
      var tce = tElgNet >= 0 ? "var(--green)" : "var(--red)";
      var tcl = tLgNet >= 0 ? "var(--green)" : "var(--red)";
      var totalBig = tElgNet + tLgNet;
      html += '<tr style="border-top:1px solid var(--border);font-weight:600;">';
      html += '<td>' + t("rt.total") + '</td><td style="text-align:right;color:' + tc + ';">' + (tNet>=0?'+':'') + tNet + '万</td>';
      html += '<td style="text-align:right;color:' + tce + ';">' + (tElgNet>=0?'+':'') + tElgNet + '</td>';
      html += '<td style="text-align:right;color:' + tcl + ';">' + (tLgNet>=0?'+':'') + tLgNet + '</td>';
      html += '<td style="text-align:right;">' + tElgB + '</td><td style="text-align:right;">' + tElgS + '</td>';
      html += '<td style="text-align:right;">' + tLgB + '</td><td style="text-align:right;">' + tLgS + '</td></tr>';
      html += '<tr><td colspan="8" style="font-size:9px;color:var(--text-muted);">';
      html += t("rt.superNetLabel") + (tElgNet>=0?'+':'') + tElgNet + '万 | ' + t("rt.largeNetLabel") + (tLgNet>=0?'+':'') + tLgNet + '万 | ' + t("rt.mainNetLabel") + (totalBig>=0?'+':'') + totalBig + '万';
      html += '</td></tr></table></div>';
      flowBox.innerHTML = html;

      // Render intraday chart if available
      var intra = d.intraday || [];
      if (intra.length > 0) {
        var chartId = "emwl-intra-" + Date.now();
        flowBox.innerHTML += '<div id="' + chartId + '" style="width:100%;height:140px;margin-top:6px;border:1px solid var(--border);border-radius:3px;"></div>';
        setTimeout(function() {
          renderIntradayChart(chartId, intra);
        }, 50);
      }
    })
    .catch(function() { if (flowBox) flowBox.innerHTML = t("rt.dataUnavailable"); });
}

function renderIntradayChart(containerId, bars) {
  var container = document.getElementById(containerId);
  if (!container) return;
  var canvas = document.createElement("canvas");
  canvas.width = container.clientWidth || 400;
  canvas.height = 140;
  container.appendChild(canvas);
  var ctx = canvas.getContext("2d");
  var w = canvas.width, h = canvas.height;
  var pad = {top: 8, right: 8, bottom: 18, left: 45};
  var pw = w - pad.left - pad.right;
  var ph = h - pad.top - pad.bottom;

  var closes = bars.map(function(b) { return b.c; });
  var min = Math.min.apply(null, closes), max = Math.max.apply(null, closes);
  var range = max - min || 1;
  min -= range * 0.1; max += range * 0.1; range = max - min;

  // Grid
  ctx.strokeStyle = "rgba(128,128,128,0.15)";
  ctx.lineWidth = 0.5;
  for (var i = 0; i <= 4; i++) {
    var y = pad.top + ph * i / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(w - pad.right, y); ctx.stroke();
    ctx.fillStyle = "#888"; ctx.font = "8px sans-serif"; ctx.textAlign = "right";
    ctx.fillText((max - range * i / 4).toFixed(2), pad.left - 4, y + 3);
  }

  // Time labels (show every Nth)
  var step = Math.max(1, Math.floor(bars.length / 4));
  ctx.textAlign = "center";
  for (var j = 0; j < bars.length; j += step) {
    var x = pad.left + pw * j / (bars.length - 1 || 1);
    ctx.fillStyle = "#888"; ctx.fillText(bars[j].t.slice(-4), x, h - 4);
  }

  // Price line
  var up = closes[closes.length - 1] >= closes[0];
  ctx.strokeStyle = up ? "#ef5350" : "#26a69a";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  bars.forEach(function(b, i) {
    var x = pad.left + pw * i / (bars.length - 1 || 1);
    var y = pad.top + ph - ph * (b.c - min) / range;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
}
var emwlList = document.getElementById("emwl-list");
var emwlStatus = document.getElementById("emwl-status");

var emwlVerdictCache = {};

if (emwlRefreshBtn) {
  emwlRefreshBtn.addEventListener("click", function() {
    emwlRefreshBtn.disabled = true;
    emwlStatus.textContent = t("emwl.syncing");
    emwlVerdictCache = {};
    fetch("/api/eastmoney/watchlist")
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (data.error) { emwlStatus.textContent = t("emwl.errorPrefix") + data.error; return; }
        emwlStatus.textContent = t("emwl.syncDone", {n: data.count});
        renderEmwlTable(data.stocks);populateExportModels("emwl");
      })
      .catch(function(e) { emwlStatus.textContent = t("emwl.syncFailedPrefix") + e.message; })
      .finally(function() { emwlRefreshBtn.disabled = false; });
  });
}

var emwlStocks = [];
var emwlSortCol = null;
var emwlSortDir = 1; // 1=asc, -1=desc

function sortEmwlTable(col) {
  if (emwlSortCol === col) { emwlSortDir *= -1; } else { emwlSortCol = col; emwlSortDir = 1; }
  emwlStocks.sort(function(a, b) {
    var va, vb;
    if (col === "收益%") {
      va = (a.recent_analyses && a.recent_analyses[0]) ? a.recent_analyses[0].return_pct : null;
      vb = (b.recent_analyses && b.recent_analyses[0]) ? b.recent_analyses[0].return_pct : null;
      va = (typeof va === "number" && !isNaN(va)) ? va : -Infinity;
      vb = (typeof vb === "number" && !isNaN(vb)) ? vb : -Infinity;
    } else if (col === "结论") {
      va = (a.recent_analyses && a.recent_analyses[0]) ? a.recent_analyses[0].verdict : (a.verdict || emwlVerdictCache[a.code] || "");
      vb = (b.recent_analyses && b.recent_analyses[0]) ? b.recent_analyses[0].verdict : (b.verdict || emwlVerdictCache[b.code] || "");
    } else {
      var keyMap = {"代码":"code","名称":"name","最新价":"price","涨跌幅":"change_pct","换手率":"turnover","量比":"vol_ratio"};
      var key = keyMap[col] || col;
      va = a[key]; vb = b[key];
    }
    if (typeof va === "string") return va.localeCompare(String(vb || "")) * emwlSortDir;
    return ((va || -Infinity) - (vb || -Infinity)) * emwlSortDir;
  });
  renderEmwlTable(emwlStocks);
}

function renderEmwlTable(stocks) {
  if (!stocks || !stocks.length) {
    emwlList.innerHTML = '<span class="no-results">' + t("emwl.noDataList") + '</span>';
    return;
  }
  emwlStocks = stocks;
  var dir = emwlSortDir > 0 ? " ▲" : " ▼";
  var html = '<table style="width:100%;border-collapse:collapse;font-size:10px;">';
  html += '<tr style="border-bottom:1px solid var(--border);color:var(--text-muted);">';
  html += '<th style="width:20px;"><input type="checkbox" id="emwl-select-all" title="' + t("common.selectToggle") + '"></th>';
  var cols = ["代码","名称","最新价","涨跌幅","换手率","量比","收益%","结论"];
  cols.forEach(function(c) {
    var marker = emwlSortCol === c ? dir : "";
    html += '<th style="cursor:pointer;padding:2px 4px;" onclick="sortEmwlTable(\'' + c + '\')">' + c + marker + '</th>';
  });
  html += '<th></th></tr>';
  stocks.forEach(function(s) {
    var color = s.change_pct >= 0 ? "var(--green)" : "var(--red)";
    // Build 3-row return + verdict display from recent_analyses
    var retHtml = "", verdictHtml = "";
    var recent = s.recent_analyses || [];
    if (recent.length > 0) {
      for (var ri = 0; ri < 3; ri++) {
        var ra = recent[ri];
        if (ra && typeof ra.return_pct === "number" && !isNaN(ra.return_pct)) {
          var rc = ra.return_pct >= 0 ? "var(--green)" : "var(--red)";
          var rs = (ra.return_pct >= 0 ? "+" : "") + ra.return_pct.toFixed(2) + "%";
          var rd = ra.verdict || "";
          var dirLabel = rd.includes("看空") ? "S" : (rd.includes("看多") ? "L" : "H");
          retHtml += '<div style="font-size:9px;color:' + rc + ';line-height:1.4;" title="' + esc(ra.analyzed_at || "") + ' ' + t("emwl.buyAt") + (ra.buy_price||"-") + ' → ' + t("emwl.currentAt") + (ra.sell_price||"-") + '">' + rs + ' <span style="font-size:7px;">' + dirLabel + '</span></div>';
        } else {
          retHtml += '<div style="font-size:9px;color:#ccc;line-height:1.4;">—</div>';
        }
        if (ra) {
          var v = ra.verdict || "";
          var vc = VERDICT_COLOR[v] || "#888";
          var vi = verdictText(v);
          var vs = (ra.analyzed_at || "").slice(0, 10);
          verdictHtml += '<div style="font-size:8px;color:' + vc + ';line-height:1.4;" title="' + esc(vs) + ' ' + esc(ra.model_name || "") + '">' + vi + '</div>';
        } else {
          verdictHtml += '<div style="font-size:8px;color:#ccc;line-height:1.4;">—</div>';
        }
      }
    } else {
      var verdict = s.verdict || emwlVerdictCache[s.code] || "";
      var vColor = VERDICT_COLOR[verdict] || "#888";
      var retStr2 = "-", retColor2 = "#888";
      if (typeof s.return_pct === "number" && !isNaN(s.return_pct)) {
        retStr2 = (s.return_pct >= 0 ? "+" : "") + s.return_pct.toFixed(2) + "%";
        retColor2 = s.return_pct >= 0 ? "var(--green)" : "var(--red)";
      }
      retHtml = '<span style="color:' + retColor2 + ';font-weight:600;">' + retStr2 + '</span>';
      verdictHtml = '<span style="font-size:9px;color:' + vColor + ';" title="' + esc(s.setup_type || "") + '">' + esc(verdict ? verdictDisplay(verdict) : "") + '</span>';
    }
    html += '<tr class="emwl-row" data-code="' + esc(s.code) + '" style="border-bottom:1px solid var(--border);cursor:pointer;">';
    html += '<td style="padding:2px 4px;text-align:center;"><input type="checkbox" class="emwl-check" data-code="' + esc(s.code) + '" onclick="event.stopPropagation();"></td>';
    html += '<td style="padding:2px 4px;">' + esc(s.code) + '</td>';
    html += '<td>' + esc(s.name) + '</td>';
    html += '<td style="text-align:right;">' + s.price.toFixed(2) + '</td>';
    html += '<td style="text-align:right;color:' + color + ';">' + (s.change_pct >= 0 ? "+" : "") + s.change_pct.toFixed(2) + '%</td>';
    html += '<td style="text-align:right;">' + (s.turnover ? s.turnover.toFixed(2) + '%' : '-') + '</td>';
    html += '<td style="text-align:right;padding-right:12px;">' + (s.vol_ratio ? s.vol_ratio.toFixed(2) : '-') + '</td>';
    html += '<td style="text-align:right;padding:0 2px;">' + retHtml + '</td>';
    html += '<td style="padding:0 2px;text-align:center;">' + verdictHtml + '</td>';
    html += '<td><button class="btn emwl-analyze-btn" data-code="' + esc(s.code) + '" style="font-size:9px;padding:1px 4px;">' + tText("emwl.analyze") + '</button></td>';
    html += '</tr>';
  });
  html += '</table>';
  emwlList.innerHTML = html;

  // Select-all checkbox handler
  var selectAll = document.getElementById("emwl-select-all");
  if (selectAll) {
    selectAll.addEventListener("click", function(e) {
      e.stopPropagation();
      var checks = emwlList.querySelectorAll(".emwl-check");
      checks.forEach(function(cb) { cb.checked = selectAll.checked; });
      updateBatchButtons();
    });
  }
  // Individual checkbox changes update button states
  emwlList.querySelectorAll(".emwl-check").forEach(function(cb) {
    cb.addEventListener("change", updateBatchButtons);
  });

  // Analyze button click → trigger fresh LLM analysis
  emwlList.querySelectorAll(".emwl-analyze-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      showEmwlRightPanel(btn.dataset.code);
      analyzeEmwlStock(btn.dataset.code);
    });
  });

  // Row click (not on checkbox/button) → show history + right panel data
  emwlList.querySelectorAll(".emwl-row").forEach(function(row) {
    row.addEventListener("click", function(e) {
      // Only handle clicks on the row itself, not child elements
      if (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON") return;
      var code = row.dataset.code;
      showEmwlRightPanel(code);
      showEmwlHistory(code);
    });
  });
  updateBatchButtons();
}

// Batch analyze — track progress
var emwlBatchRunning = false, emwlBatchStop = false, emwlBatchAbort = null;
var emwlBatchBtn = document.getElementById("emwl-batch-btn");
var emwlResumeBtn = document.getElementById("emwl-resume-btn");
var emwlRedoBtn = document.getElementById("emwl-redo-btn");

function updateBatchButtons() {
  var rows = emwlList.querySelectorAll(".emwl-row");
  if (!rows.length) { disableAllBatchButtons(); return; }
  var total = rows.length;
  var done = 0;
  rows.forEach(function(r) { if (emwlVerdictCache[r.dataset.code]) done++; });
  var unchecked = total - done;
  var selBtn = document.getElementById("emwl-batch-sel-btn");
  var checksChecked = emwlList.querySelectorAll(".emwl-check:checked").length;

  if (emwlBatchRunning) {
    // Running: disable action buttons, only status-line stop button works
    emwlBatchBtn.textContent = t("emwl.analyzing"); emwlBatchBtn.disabled = true;
    if (selBtn) { selBtn.disabled = true; selBtn.textContent = t("emwl.analyzing"); }
    emwlResumeBtn.disabled = true;
    emwlRedoBtn.disabled = true;
    return;
  }

  // Not running — enable/disable based on state
  // 分析选中: requires checked boxes
  if (selBtn) {
    selBtn.disabled = (checksChecked === 0);
    selBtn.textContent = selBtn.disabled ? t("emwl.noneChecked") : t("emwl.batchSel");
  }
  // 分析新: requires unanalyzed stocks
  emwlBatchBtn.textContent = unchecked > 0 ? t("emwl.batchNewCount", {n: unchecked}) : t("emwl.allDone");
  emwlBatchBtn.disabled = (unchecked === 0);
  // 继续: requires partial completion
  emwlResumeBtn.disabled = !(done > 0 && unchecked > 0);
  // 全重析: requires at least one analyzed
  emwlRedoBtn.disabled = (done === 0);
}

function disableAllBatchButtons() {
  emwlBatchBtn.textContent = t("emwl.batchNew"); emwlBatchBtn.disabled = true;
  var selBtn = document.getElementById("emwl-batch-sel-btn");
  if (selBtn) { selBtn.disabled = true; selBtn.textContent = t("emwl.noneChecked"); }
  emwlResumeBtn.disabled = true;
  emwlRedoBtn.disabled = true;
}

if (emwlBatchBtn) {
  emwlBatchBtn.addEventListener("click", function() {
    var rows = emwlList.querySelectorAll(".emwl-row");
    if (!rows.length) return;
    var codes = [];
    rows.forEach(function(r) { if (!emwlVerdictCache[r.dataset.code]) codes.push(r.dataset.code); });
    codes = filterByBoard(codes, 'emwl-board-toggle');
    codes = filterByChangePct(codes, 'emwl');
    if (!codes.length) { emwlStatus.textContent = t("emwl.allAnalyzedOrFiltered"); return; }
    if(emwlBatchAbort)emwlBatchAbort.abort();emwlBatchAbort=new AbortController();
    emwlBatchRunning = true; emwlBatchStop = false;
    runBatchEmwlAnalysis(codes, 0);
  });
}

var emwlBatchSelBtn = document.getElementById("emwl-batch-sel-btn");
if (emwlBatchSelBtn) {
  emwlBatchSelBtn.addEventListener("click", function() {
    if (emwlBatchRunning) { emwlBatchStop = true; if(emwlBatchAbort)emwlBatchAbort.abort(); return; }
    var checks = emwlList.querySelectorAll(".emwl-check:checked");
    if (!checks.length) { emwlStatus.textContent = t("emwl.pleaseCheck"); return; }
    var codes = [];
    checks.forEach(function(cb) { codes.push(cb.dataset.code); });
    codes = filterByBoard(codes, 'emwl-board-toggle');
    codes = filterByChangePct(codes, 'emwl');
    if (!codes.length) { emwlStatus.textContent = t("emwl.checkedAllFiltered"); return; }
    emwlBatchRunning = true;
    emwlBatchStop = false;
    emwlStatus.textContent = t("emwl.batchSelected", {n: codes.length});
    runBatchEmwlAnalysis(codes, 0);
  });
}

if (emwlResumeBtn) {
  emwlResumeBtn.addEventListener("click", function() {
    var rows = emwlList.querySelectorAll(".emwl-row");
    var codes = [];
    rows.forEach(function(r) {
      if (!emwlVerdictCache[r.dataset.code]) codes.push(r.dataset.code);
    });
    codes = filterByBoard(codes, 'emwl-board-toggle');
    codes = filterByChangePct(codes, 'emwl');
    if (!codes.length) { emwlStatus.textContent = t("emwl.allAnalyzed"); return; }
    emwlBatchRunning = true;
    emwlBatchStop = false;
    emwlStatus.textContent = t("emwl.continue", {done: emwlVerdictCacheSize() + 1, total: rows.length});
    runBatchEmwlAnalysis(codes, 0);
  });
}

if (emwlRedoBtn) {
  emwlRedoBtn.addEventListener("click", function() {
    // Re-analyze ALL stocks (append new analyses, don't clear existing)
    var rows = emwlList.querySelectorAll(".emwl-row");
    if (!rows.length) return;
    var codes = [];
    rows.forEach(function(r) { codes.push(r.dataset.code); });
    codes = filterByBoard(codes, 'emwl-board-toggle');
    codes = filterByChangePct(codes, 'emwl');
    if (!codes.length) { emwlStatus.textContent = t("emwl.allFiltered"); return; }
    if(emwlBatchAbort)emwlBatchAbort.abort();emwlBatchAbort=new AbortController();
    emwlBatchRunning = true; emwlBatchStop = false;
    runBatchEmwlAnalysis(codes, 0);
  });
}

// Search filter + autocomplete dropdown (local — from loaded watchlist stocks)
var emwlSearchInput = document.getElementById("emwl-search-input");
var emwlSearchDropdown = document.getElementById("emwl-search-dropdown");
var emwlSearchTimer = null;
var emwlSearchSuppress = false;
if (emwlSearchInput) {
  emwlSearchInput.addEventListener("input", function() {
    if (emwlSearchSuppress) return;
    clearTimeout(emwlSearchTimer);
    var query = emwlSearchInput.value.trim().toLowerCase();
    // Filter table rows
    var rows = emwlList.querySelectorAll(".emwl-row");
    var matches = [];
    rows.forEach(function(row) {
      var code = (row.dataset.code || "").toLowerCase();
      var nameCell = row.querySelectorAll("td")[2]; // 名称 is col 2
      var name = (nameCell ? nameCell.textContent : "").toLowerCase();
      if (!query || code.indexOf(query) !== -1 || name.indexOf(query) !== -1) {
        row.style.display = "";
        if (query) matches.push({code: row.dataset.code, name: (nameCell ? nameCell.textContent : "")});
      } else {
        row.style.display = "none";
      }
    });
    // Show autocomplete dropdown from local matches
    emwlSearchTimer = setTimeout(function() { renderEmwlSearchDropdown(matches, query); }, 150);
  });
  emwlSearchInput.addEventListener("focus", function() {
    var q = emwlSearchInput.value.trim().toLowerCase();
    if (q && emwlStocks.length) {
      var m = [];
      emwlStocks.forEach(function(s) {
        if (s.code.toLowerCase().indexOf(q) !== -1 || (s.name || "").toLowerCase().indexOf(q) !== -1) {
          m.push({code: s.code, name: s.name});
        }
      });
      renderEmwlSearchDropdown(m, q);
    }
  });
}

function renderEmwlSearchDropdown(matches, query) {
  if (!emwlSearchDropdown) return;
  if (!query || !matches.length) { emwlSearchDropdown.classList.add("hidden"); return; }
  var shown = matches.slice(0, 8);
  emwlSearchDropdown.innerHTML = shown.map(function(m) {
    return '<div class="dropdown-item emwl-search-item" data-code="' + esc(m.code) + '">' +
      '<span class="sym">' + esc(m.code) + '</span>' +
      '<span class="name">' + esc(m.name) + '</span></div>';
  }).join("");
  emwlSearchDropdown.querySelectorAll(".emwl-search-item").forEach(function(el) {
    el.addEventListener("click", function() {
      var code = el.dataset.code;
      emwlSearchSuppress = true;
      emwlSearchInput.value = code;
      emwlSearchSuppress = false;
      emwlSearchDropdown.classList.add("hidden");
      // Filter to show only this stock
      var rows = emwlList.querySelectorAll(".emwl-row");
      rows.forEach(function(r) { r.style.display = r.dataset.code === code ? "" : "none"; });
      showEmwlRightPanel(code);
      showEmwlHistory(code);
    });
  });
  emwlSearchDropdown.classList.remove("hidden");
}

// Hide search dropdown on outside click
document.addEventListener("click", function(e) {
  if (emwlSearchDropdown && emwlSearchInput && !emwlSearchDropdown.contains(e.target) && e.target !== emwlSearchInput) {
    emwlSearchDropdown.classList.add("hidden");
  }
});

// Manual add with autocomplete (API search — like Deep Analysis ticker search)
var emwlAddBtn = document.getElementById("emwl-add-btn");
var emwlAddInput = document.getElementById("emwl-add-input");
var emwlAddDropdown = document.getElementById("emwl-add-dropdown");
var emwlAddStatus = document.getElementById("emwl-add-status");
var emwlAddTimer = null;
var emwlAddSuppressSearch = false;  // guard to prevent re-search on programmatic value set
if (emwlAddInput) {
  emwlAddInput.addEventListener("input", function() {
    if (emwlAddSuppressSearch) return;
    clearTimeout(emwlAddTimer);
    var q = emwlAddInput.value.trim();
    if (q.length < 1) { if (emwlAddDropdown) emwlAddDropdown.classList.add("hidden"); return; }
    emwlAddTimer = setTimeout(function() { searchEmwlAddStocks(q); }, 300);
  });
  emwlAddInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (emwlAddBtn) emwlAddBtn.click();
    }
  });
}

function searchEmwlAddStocks(q) {
  if (!emwlAddDropdown) return;
  fetch("/api/search?q=" + encodeURIComponent(q))
    .then(function(r) { return r.json(); })
    .then(function(items) {
      if (!items || !items.length) {
        emwlAddDropdown.innerHTML = '<div class="dropdown-item"><span class="no-results">' + tText("emwl.noMatch") + '</span></div>';
        emwlAddDropdown.classList.remove("hidden");
        return;
      }
      emwlAddDropdown.innerHTML = items.slice(0, 8).map(function(r) {
        return '<div class="dropdown-item emwl-add-item" data-symbol="' + esc(r.symbol) + '" data-name="' + esc(r.name || "") + '">' +
          '<span class="sym">' + esc(r.symbol) + '</span>' +
          '<span class="name">' + esc(r.name) + '</span>' +
          '<span class="exch">' + esc(r.exchange) + '</span></div>';
      }).join("");
      emwlAddDropdown.querySelectorAll(".emwl-add-item").forEach(function(el) {
        el.addEventListener("click", function() {
          var sym = el.dataset.symbol;
          var nm = el.dataset.name;
          emwlAddSuppressSearch = true;
          emwlAddInput.value = sym + (nm ? " " + nm : "");
          emwlAddSuppressSearch = false;
          emwlAddDropdown.classList.add("hidden");
          // Auto-add immediately
          if (emwlAddBtn) emwlAddBtn.click();
        });
      });
      emwlAddDropdown.classList.remove("hidden");
    })
    .catch(function() {
      emwlAddDropdown.innerHTML = '<div class="dropdown-item"><span class="no-results">' + tText("emwl.searchUnavailable") + '</span></div>';
      emwlAddDropdown.classList.remove("hidden");
    });
}

// Hide add dropdown on outside click
document.addEventListener("click", function(e) {
  if (emwlAddDropdown && emwlAddInput && !emwlAddDropdown.contains(e.target) && e.target !== emwlAddInput) {
    emwlAddDropdown.classList.add("hidden");
  }
});

if (emwlAddBtn) {
  emwlAddBtn.addEventListener("click", function() {
    var raw = emwlAddInput.value.trim();
    if (!raw) { if (emwlAddStatus) { emwlAddStatus.style.display = "block"; emwlAddStatus.textContent = t("emwl.enterCode"); } return; }
    if (emwlAddStatus) { emwlAddStatus.style.display = "block"; emwlAddStatus.textContent = t("emwl.adding"); }
    emwlAddBtn.disabled = true;
    fetch("/api/eastmoney/watchlist/add-manual", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({code: raw, name: "", push_to_eastmoney: false})
    }).then(function(r) { return r.json(); })
      .then(function(d) {
        emwlAddInput.value = "";
        var msg = t("emwl.added", {ticker: d.ticker, name: d.name || ""});
        if (d.push_result) {
          msg += d.push_result.ok ? t("emwl.synced") : t("emwl.syncFailSuffix") + (d.push_result.message || "");
        }
        if (emwlAddStatus) { emwlAddStatus.textContent = msg; setTimeout(function() { emwlAddStatus.style.display = "none"; }, 4000); }
        // Refresh the table
        renderEmwlTable(d.items || emwlStocks);populateExportModels("emwl");
        emwlStatus.textContent = t("emwl.totalCount", {n: d.items ? d.items.length : emwlStocks.length});
      })
      .catch(function(e) {
        if (emwlAddStatus) { emwlAddStatus.textContent = t("emwl.addFailedPrefix") + e.message; }
      })
      .finally(function() { emwlAddBtn.disabled = false; });
  });
  emwlAddInput.addEventListener("keydown", function(e) {
    if (e.key === "Enter") { emwlAddBtn.click(); }
  });
}

// Upload local additions to Eastmoney
var emwlUploadBtn = document.getElementById("emwl-upload-btn");
if (emwlUploadBtn) {
  emwlUploadBtn.addEventListener("click", function() {
    emwlUploadBtn.disabled = true;
    emwlUploadBtn.textContent = "⏳...";
    fetch("/api/eastmoney/watchlist/sync-up", {method: "POST"})
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var ok = d.results ? d.results.filter(function(r) { return r.ok; }).length : 0;
        alert(t("emwl.uploadDone", {ok: ok, total: d.total_to_push || 0}));
      })
      .catch(function(e) { alert(t("emwl.uploadFailedPrefix") + e.message); })
      .finally(function() { emwlUploadBtn.disabled = false; emwlUploadBtn.textContent = t("emwl.upload"); });
  });
}

function emwlVerdictCacheSize() {
  var count = 0;
  for (var k in emwlVerdictCache) { if (emwlVerdictCache.hasOwnProperty(k)) count++; }
  return count;
}

function runBatchEmwlAnalysis(codes, idx) {
  if (emwlBatchStop || idx >= codes.length) {
    if (!emwlBatchStop && idx >= codes.length) { emwlBatchRunning = false; emwlStatus.textContent = t("emwl.analysisDone"); updateBatchButtons(); }
    return;
  }

  var dual = isEmwlDualEnabled();
  if (!dual) {
    // Single-model mode: sequential
    var code = codes[idx];
    var m1 = (emwlProviderSelect ? emwlProviderSelect.value : "") + "/" + (emwlModelSelect ? emwlModelSelect.value : "");
    emwlStatus.innerHTML = t("emwl.batchAnalyzing", {done: emwlVerdictCacheSize() + 1, total: codes.length, code: esc(code), model: esc(m1)}) + ' <button onclick="emwlBatchStop=true;" style="font-size:10px;padding:1px 6px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;margin-left:4px;">' + t("common.stop") + '</button>';
    showEmwlRightPanel(code);
    analyzeEmwlStockSilent(code, function() {
      setTimeout(function() { runBatchEmwlAnalysis(codes, idx + 1); }, 300);
    });
    return;
  }

  // ── Multi-model mode: N workers from shared queue ──
  if(emwlBatchAbort)emwlBatchAbort.abort();emwlBatchAbort=new AbortController();
  var nextIdx = idx, running = 0, total = codes.length;
  var eworkers = [{label:"1",code:"",providerEl:emwlProviderSelect,modelEl:emwlModelSelect,fn:getEmwlModelParams}];
  if(isEmwlDualEnabled())eworkers.push({label:"2",code:"",providerEl:emwlProvider2Select,modelEl:emwlModel2Select,fn:getEmwlModel2Params});
  if(isEmwlTriEnabled())eworkers.push({label:"3",code:"",providerEl:emwlProvider3Select,modelEl:emwlModel3Select,fn:getEmwlModel3Params});
  if(isEmwlQuadEnabled())eworkers.push({label:"4",code:"",providerEl:emwlProvider4Select,modelEl:emwlModel4Select,fn:getEmwlModel4Params});
  if(isEmwlPentaEnabled())eworkers.push({label:"5",code:"",providerEl:emwlProvider5Select,modelEl:emwlModel5Select,fn:getEmwlModel5Params});
  if(isEmwlHexaEnabled())eworkers.push({label:"6",code:"",providerEl:emwlProvider6Select,modelEl:emwlModel6Select,fn:getEmwlModel6Params});
  if(isEmwlHeptaEnabled())eworkers.push({label:"7",code:"",providerEl:emwlProvider7Select,modelEl:emwlModel7Select,fn:getEmwlModel7Params});
  if(isEmwlOctaEnabled())eworkers.push({label:"8",code:"",providerEl:emwlProvider8Select,modelEl:emwlModel8Select,fn:getEmwlModel8Params});
  function getNext(){if(nextIdx<total){var c=codes[nextIdx];nextIdx++;return c}return null}
  function updateStatus(){
    var done=emwlVerdictCacheSize(),line=t("emwl.parallelAnalyzing",{n:eworkers.length,done:done,total:total});
    eworkers.forEach(function(w,i){if(w.code){var ms=(w.providerEl?w.providerEl.value:"")+"/"+(w.modelEl?w.modelEl.value:"");line+=' 🤖'+(i+1)+' '+esc(w.code)+' ← '+esc(ms)}});
    line+=' <button onclick="emwlBatchStop=true;if(emwlBatchAbort)emwlBatchAbort.abort();" style="font-size:10px;padding:1px 6px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;margin-left:4px;">'+t("common.stop")+'</button>';
    emwlStatus.innerHTML=line;
    var bar=document.getElementById("emwl-worker-bar");if(!bar){bar=document.createElement("div");bar.id="emwl-worker-bar";bar.style.cssText="margin:4px 0;padding:6px 8px;border-radius:4px;background:var(--bg);border:1px solid var(--border);font-size:10px;line-height:1.5;";var le=document.getElementById("emwl-list");if(le&&le.parentNode)le.parentNode.insertBefore(bar,le.nextSibling)}
    if(bar){var h2='<b>'+t("emwl.parallelBarTitle",{n:eworkers.length})+'</b>';eworkers.forEach(function(w,i){var c=w.code?"#2da44e":"#888",icon=w.code?"🟢":"⏳";var ms=(w.providerEl?w.providerEl.value:"")+"/"+(w.modelEl?w.modelEl.value:"");h2+='<div>🤖'+(i+1)+' <b>'+esc(ms)+'</b>: <span style="color:'+c+';">'+icon+' '+(w.code?esc(w.code):t("emwl.waiting"))+'</span></div>'});bar.innerHTML=h2}
    var panel=document.getElementById("emwl-analysis-panel");if(panel){panel.classList.remove("hidden");panel.style.display="block";var title=document.getElementById("emwl-analysis-title");if(title)title.textContent=t("emwl.parallelPanelTitle",{n:eworkers.length})}
  }
  function worker(w){
    function run(){
      if(emwlBatchStop||(emwlBatchAbort&&emwlBatchAbort.signal.aborted)){w.code="";running--;if(running<=0){emwlBatchRunning=false;emwlStatus.textContent=t("emwl.stopped");updateBatchButtons();var b=document.getElementById("emwl-worker-bar");if(b)b.remove()}return}
      var code=getNext();if(!code){w.code="";running--;if(running<=0){emwlBatchRunning=false;emwlStatus.textContent=t("emwl.analysisDone");updateBatchButtons();var b=document.getElementById("emwl-worker-bar");if(b)b.remove()}return}
      w.code=code;updateStatus();
      fetch("/api/eastmoney/watchlist/analyze/"+encodeURIComponent(code)+(w.fn()?"?"+w.fn():""),{signal:emwlBatchAbort.signal})
        .then(function(r){return r.json()}).then(function(d){
          var a=d.analysis||"",rawV=d.verdict||"";
          emwlVerdictCache[code]=rawV;var row=emwlList.querySelector('[data-code="'+code+'"]');if(row){var cells=row.querySelectorAll("td");if(cells.length>=9){cells[8].textContent=verdictDisplay(rawV);cells[8].style.color=VERDICT_COLOR[rawV]||"#888"}}
          updateEmwlAnalysisPanel(code,d);updateBatchButtons();
        }).catch(function(){}).finally(function(){w.code="";if(emwlBatchStop){running--;if(running<=0){emwlBatchRunning=false;emwlStatus.textContent=t("emwl.stopped");updateBatchButtons();var b=document.getElementById("emwl-worker-bar");if(b)b.remove()}}else setTimeout(run,200)});
    }
    run();
  }
  running=eworkers.length;emwlBatchRunning=true;eworkers.forEach(function(w){worker(w)});
}

function analyzeEmwlStockSilent(code, callback) {
  var qs = getEmwlModelParams();  // includes model2 when dual enabled
  fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + (qs ? "?" + qs : ""))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var a = d.analysis || "";
      var rawV = d.verdict || "";
      emwlVerdictCache[code] = rawV;
      var row = emwlList.querySelector('[data-code="' + code + '"]');
      if (row) {
        var cells = row.querySelectorAll("td");
        if (cells.length >= 9) { cells[8].textContent = verdictDisplay(rawV); cells[8].style.color = VERDICT_COLOR[rawV] || "#888"; }
      }
      updateEmwlAnalysisPanel(code, d);
      if (callback) callback();
    })
    .catch(function() { if (callback) callback(); });
}

// Update the analysis panel inline during batch runs (without re-fetching returns)
function updateEmwlAnalysisPanel(code, d) {
  var panel = document.getElementById("emwl-analysis-panel");
  var title = document.getElementById("emwl-analysis-title");
  var body = document.getElementById("emwl-analysis-body");
  if (!panel) return;
  panel.classList.remove("hidden");
  var a = d.analysis || "";
  var rawV = d.verdict || "";
  title.textContent = t("emwl.batchTitle", {code: code, verdict: verdictDisplay(rawV)});
  var modelLabel = d.model_name ? ' <span style="font-size:9px;color:var(--text-muted);">🤖 ' + esc(d.model_name) + '</span>' : '';
  var html = '<div style="margin-bottom:8px;"><b>' + tText("emwl.llmConclusion") + '</b>' + modelLabel + '<br>' + esc(a || t("emwl.analysisUnavailable")) + '</div>';
  html += '<details><summary style="cursor:pointer;font-size:10px;color:var(--text-muted);">' + tText("emwl.rawData") + '</summary>';
  html += '<pre style="font-size:9px;max-height:120px;overflow:auto;margin-top:4px;">' + esc(d.flow_data || t("emwl.noData")) + '</pre></details>';
  body.innerHTML = html;
  // Load return validation history
  loadEmwlReturns(code);
}

// Track currently-analyzing stock for abort + button state
var emwlAnalyzingCode = null;
var emwlAnalyzeAbort = null;

function analyzeEmwlStock(code) {
  var panel = document.getElementById("emwl-analysis-panel");
  var title = document.getElementById("emwl-analysis-title");
  var body = document.getElementById("emwl-analysis-body");
  if (!panel) return;
  panel.classList.remove("hidden");

  // If clicking the same stock that's already analyzing → abort
  if (emwlAnalyzingCode === code && emwlAnalyzeAbort) {
    emwlAnalyzeAbort.abort();
    emwlAnalyzingCode = null;
    emwlAnalyzeAbort = null;
    title.textContent = t("emwl.stoppedTitle", {code: code});
    body.innerHTML = '<span style="color:var(--text-muted);">' + tText("emwl.analysisCancelled") + '</span>';
    updateEmwlAnalyzeBtnState(null);
    return;
  }

  // Abort any previous pending analysis
  if (emwlAnalyzeAbort) { emwlAnalyzeAbort.abort(); }
  emwlAnalyzingCode = code;
  emwlAnalyzeAbort = new AbortController();
  updateEmwlAnalyzeBtnState(code);

  title.textContent = t("emwl.analyzingTitle", {code: code});
  var stopBtn = document.createElement('button');
  stopBtn.textContent = t("common.stop");
  stopBtn.style.cssText = 'font-size:10px;padding:2px 8px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;margin-left:8px;';
  stopBtn.onclick = function(e) { e.preventDefault(); analyzeEmwlStock(code); };
  title.appendChild(stopBtn);

  var m1 = (emwlProviderSelect ? emwlProviderSelect.value : "") + "/" + (emwlModelSelect ? emwlModelSelect.value : "");
  body.innerHTML = '<span style="color:var(--text-muted);">' + t("emwl.analyzingDetail", {code: esc(code), model: esc(m1)}) + '</span>';

  if (emwlVerdictCache[code]) { title.textContent = code + " — " + verdictDisplay(emwlVerdictCache[code]); }

  var qs = getEmwlModelParams();
  fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + (qs ? "?" + qs : ""),
       {signal: emwlAnalyzeAbort.signal})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var a1 = d.analysis || ""; var v1 = d.verdict || "";
      var effV = v1 || verdictFromText(a1);
      var disp1 = verdictDisplay(effV);
      emwlVerdictCache[code] = effV; updateEmwlVerdictCell(code, effV);
      var ml = d.model_name ? ' <span style="font-size:9px;color:var(--text-muted);font-weight:400;">🤖 ' + esc(d.model_name) + '</span>' : '';
      title.innerHTML = esc(code) + " — " + disp1 + ml;
      body.innerHTML = (d.model_name ? '<div style="font-size:9px;color:var(--text-muted);margin-bottom:4px;">' + t("emwl.model", {model: esc(d.model_name)}) + '</div>' : '')
        + (d.setup_type ? '<div style="font-size:11px;color:var(--accent);margin-bottom:4px;">' + tText("emwl.setupType") + '：' + esc(d.setup_type) + '</div>' : '')
        + '<div style="margin-bottom:8px;"><b>' + tText("emwl.llmConclusion") + '</b><br>' + esc(a1 || t("emwl.analysisUnavailable")) + '</div>';
      emwlAnalyzingCode = null; emwlAnalyzeAbort = null; updateEmwlAnalyzeBtnState(null);
      loadEmwlReturns(code);
    })
    .catch(function(e) {
      if (e.name === "AbortError") { body.innerHTML = '<span style="color:var(--text-muted);">' + tText("emwl.analysisCancelled") + '</span>'; }
      else { body.innerHTML = '<span style="color:var(--red);">' + t("emwl.analyzeFailedPrefix") + e.message + '</span>'; }
      emwlAnalyzingCode = null; emwlAnalyzeAbort = null; updateEmwlAnalyzeBtnState(null);
    });
}

// Update "分析" button state — show ⏹ 停止 while analyzing a stock
function updateEmwlVerdictCell(code, verdict) {
  var row = emwlList.querySelector('[data-code="' + code + '"]');
  if (row) { var cells = row.querySelectorAll("td"); if (cells.length >= 9) { cells[8].textContent = verdictDisplay(verdict); cells[8].style.color = VERDICT_COLOR[verdict] || "#888"; } }
}
function updateEmwlAnalyzeBtnState(activeCode) {
  emwlList.querySelectorAll(".emwl-analyze-btn").forEach(function(btn) {
    if (btn.dataset.code === activeCode) {
      btn.textContent = t("common.stop");
      btn.style.background = "var(--red)";
      btn.style.color = "#fff";
      btn.style.fontWeight = "700";
      btn.style.border = "none";
      btn.style.padding = "2px 5px";
      btn.style.borderRadius = "3px";
      btn.title = t("emwl.clickStop");
    } else {
      btn.textContent = t("emwl.analyze");
      btn.style.background = "";
      btn.style.color = "";
      btn.style.fontWeight = "";
      btn.style.border = "";
      btn.style.padding = "";
      btn.style.borderRadius = "";
      btn.title = "";
    }
  });
}

// Load per-analysis return validation (buy at analysis-day close, sell now)
function loadEmwlReturns(code) {
  var panel = document.getElementById("emwl-analysis-panel");
  var body = document.getElementById("emwl-analysis-body");
  fetch("/api/eastmoney/watchlist/returns/" + encodeURIComponent(code))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var returns = (d && d.returns) || [];
      if (!returns.length) {
        // Append a placeholder row so the section still shows up
        var hint = document.createElement("div");
        hint.style.cssText = "margin-top:8px;font-size:10px;color:var(--text-muted);";
        hint.textContent = t("emwl.returnsEmpty");
        body.appendChild(hint);
        return;
      }
      var html = '<div style="margin-top:10px;border-top:1px solid var(--border);padding-top:6px;">';
      html += '<div style="font-weight:700;font-size:11px;margin-bottom:4px;">' + tText("emwl.returnsTitle") + ' <span style="font-weight:400;color:var(--text-muted);">' + tText("emwl.returnsSuffix") + '</span></div>';
      html += '<table style="width:100%;border-collapse:collapse;font-size:10px;">';
      html += '<tr style="color:var(--text-muted);border-bottom:1px solid var(--border);">';
      html += '<th style="text-align:left;padding:2px 4px;">' + tText("emwl.thTime") + '</th><th style="text-align:right;">' + tText("emwl.thBuyPrice") + '</th><th style="text-align:right;">' + tText("emwl.thSellPrice") + '</th><th style="text-align:right;">' + tText("emwl.thReturn") + '</th><th style="text-align:left;">' + tText("emwl.thVerdict") + '</th><th style="text-align:left;">' + tText("emwl.thModel") + '</th><th></th>';
      html += '</tr>';
      returns.forEach(function(r) {
        var rc = r.return_pct >= 0 ? "var(--green)" : "var(--red)";
        var rStr = (r.return_pct >= 0 ? "+" : "") + r.return_pct.toFixed(2) + "%";
        var verdictTxt = r.verdict || "";
        // Direction label (localized in region pass)
        var dirLabel = verdictTxt.includes("看空") ? t("emwl.short") : (verdictTxt.includes("看多") ? t("emwl.long") : "");
        var at = (r.analyzed_at || "").replace("T", " ").slice(0, 16);
        var modelTxt = r.model_name ? r.model_name : "-";
        html += '<tr style="border-bottom:1px solid var(--border);">';
        html += '<td style="padding:2px 4px;">' + esc(at) + '</td>';
        html += '<td style="text-align:right;">' + esc(String(r.buy_price)) + '</td>';
        html += '<td style="text-align:right;">' + esc(String(r.sell_price)) + '</td>';
        html += '<td style="text-align:right;color:' + rc + ';font-weight:700;">' + rStr + '<span style="font-size:8px;">' + dirLabel + '</span></td>';
        html += '<td style="font-size:9px;">' + esc(verdictDisplay(verdictTxt || "观望")) + '</td>';
        html += '<td style="font-size:9px;color:var(--text-muted);">' + esc(modelTxt) + '</td>';
        html += '<td><button class="btn emwl-del-analysis" data-id="' + (r.analysis_id || "") + '" data-code="' + esc(r.code || code) + '" style="font-size:9px;padding:1px 4px;color:var(--red);">✕</button></td>';
        html += '</tr>';
      });
      html += '</table></div>';
      body.insertAdjacentHTML("beforeend", html);
      // Wire delete buttons
      body.querySelectorAll(".emwl-del-analysis").forEach(function(btn) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          var aid = btn.dataset.id;
          if (!aid || !confirm(t("emwl.deleteAnalysisConfirm"))) return;
          fetch("/api/eastmoney/watchlist/analysis/" + encodeURIComponent(aid), {method: "DELETE"})
            .then(function(r) {
              if (r.ok) { loadEmwlReturns(code); updateBatchButtons(); }
              else alert(t("emwl.deleteFailed"));
            })
            .catch(function(e) { alert(t("emwl.deleteFailedPrefix") + e.message); });
        });
      });
    })
    .catch(function() { /* silent — return history is optional */ });
}

var emwlClose = document.getElementById("emwl-analysis-close");
if (emwlClose) {
  emwlClose.addEventListener("click", function() {
    var p = document.getElementById("emwl-analysis-panel");
    if (p) p.classList.add("hidden");
  });
}

// ---- Position / Portfolio Management ----

function loadPositions() {
  var list = document.getElementById("pos-list");
  var summary = document.getElementById("pos-summary");
  fetch("/api/positions")
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var pos = d.positions || [];
      var s = d.summary || {};
      if (summary) {
        summary.style.display = pos.length ? "block" : "none";
        var pnlColor = (s.total_pnl || 0) >= 0 ? "var(--green)" : "var(--red)";
        var pnlPct = (s.total_pnl_pct >= 0 ? "+" : "") + (s.total_pnl_pct || 0).toFixed(2) + "%";
        summary.innerHTML = t("pos.summary", {cost: (s.total_cost || 0).toFixed(0), value: (s.total_value || 0).toFixed(0), color: pnlColor, pnl: (s.total_pnl >= 0 ? "+" : "") + (s.total_pnl || 0).toFixed(0), pct: pnlPct});
      }
      if (!list) return;
      if (!pos.length) {
        list.innerHTML = '<span class="no-results" style="font-size:10px;">' + tText("pos.emptyRecord") + '</span>';
        return;
      }
      var html = '<table style="width:100%;border-collapse:collapse;">';
      html += '<tr style="color:var(--text-muted);border-bottom:1px solid var(--border);">';
      html += '<th style="text-align:left;">' + tText("pos.thCode") + '</th><th>' + tText("pos.thName") + '</th><th style="text-align:right;">' + tText("pos.thShares") + '</th><th style="text-align:right;">' + tText("pos.thCost") + '</th><th style="text-align:right;">' + tText("pos.thPrice") + '</th><th style="text-align:right;">' + tText("pos.thValue") + '</th><th style="text-align:right;">' + tText("pos.thPnl") + '</th><th></th></tr>';
      pos.forEach(function(p) {
        var pc = (p.pnl || 0) >= 0 ? "var(--green)" : "var(--red)";
        var pnlStr = (p.pnl >= 0 ? "+" : "") + p.pnl.toFixed(0);
        var pnlPctStr = (p.pnl_pct >= 0 ? "+" : "") + p.pnl_pct.toFixed(2) + "%";
        html += '<tr style="border-bottom:1px solid var(--border);">';
        html += '<td>' + esc(p.ticker) + '</td><td>' + esc(p.name) + '</td>';
        html += '<td style="text-align:right;">' + p.shares + '</td>';
        html += '<td style="text-align:right;">' + p.cost_price.toFixed(2) + '</td>';
        html += '<td style="text-align:right;">' + (p.current_price > 0 ? p.current_price.toFixed(2) : "-") + '</td>';
        html += '<td style="text-align:right;">' + p.market_value.toFixed(0) + '</td>';
        html += '<td style="text-align:right;color:' + pc + ';font-weight:600;">' + pnlStr + '<br><span style="font-size:9px;">' + pnlPctStr + '</span></td>';
        html += '<td><button class="btn pos-del-btn" data-ticker="' + esc(p.ticker) + '" style="font-size:9px;padding:1px 4px;color:var(--red);">✕</button></td>';
        html += '</tr>';
      });
      html += '</table>';
      list.innerHTML = html;
      // Wire delete buttons
      list.querySelectorAll(".pos-del-btn").forEach(function(btn) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          var t = btn.dataset.ticker;
          fetch("/api/positions/" + encodeURIComponent(t), {method: "DELETE"})
            .then(function() { loadPositions(); })
            .catch(function(e) { alert(t("pos.deleteFailedPrefix") + e.message); });
        });
      });
    })
    .catch(function() {
      if (list) list.innerHTML = '<span style="color:var(--red);">' + tText("pos.loadFailed") + '</span>';
    });
}

// Position form toggle
var posAddBtn = document.getElementById("pos-add-btn");
var posForm = document.getElementById("pos-form");
var posSaveBtn = document.getElementById("pos-save-btn");
var posCancelBtn = document.getElementById("pos-cancel-btn");
if (posAddBtn && posForm) {
  posAddBtn.addEventListener("click", function() {
    posForm.classList.toggle("hidden");
  });
}
if (posCancelBtn) {
  posCancelBtn.addEventListener("click", function() {
    posForm.classList.add("hidden");
  });
}
if (posSaveBtn) {
  posSaveBtn.addEventListener("click", function() {
    var ticker = document.getElementById("pos-ticker-input").value.trim();
    var shares = parseInt(document.getElementById("pos-shares-input").value) || 0;
    var cost = parseFloat(document.getElementById("pos-cost-input").value) || 0;
    var date = document.getElementById("pos-date-input").value.trim();
    if (!ticker || shares <= 0) { alert(t("pos.enterCodeShares")); return; }
    posSaveBtn.disabled = true;
    posSaveBtn.textContent = "...";
    fetch("/api/positions", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ticker: ticker, shares: shares, cost_price: cost, buy_date: date})
    }).then(function(r) { return r.json(); })
      .then(function() {
        posForm.classList.add("hidden");
        document.getElementById("pos-ticker-input").value = "";
        document.getElementById("pos-shares-input").value = "";
        document.getElementById("pos-cost-input").value = "";
        document.getElementById("pos-date-input").value = "";
        loadPositions();
      })
      .catch(function(e) { alert(t("pos.saveFailedPrefix") + e.message); })
      .finally(function() { posSaveBtn.disabled = false; posSaveBtn.textContent = t("pos.save"); });
  });
}

// Sync positions from Eastmoney MXAPI
var posSyncBtn = document.getElementById("pos-sync-btn");
if (posSyncBtn) {
  posSyncBtn.addEventListener("click", function() {
    posSyncBtn.disabled = true;
    posSyncBtn.textContent = "⏳...";
    fetch("/api/positions/sync-from-eastmoney", {method: "POST"})
      .then(function(r) {
        if (!r.ok) return r.json().then(function(d) { throw new Error(d.detail || t("pos.syncFailed")); });
        return r.json();
      })
      .then(function(d) {
        alert(t("pos.syncDone", {n: d.imported || 0}));
        loadPositions();
      })
      .catch(function(e) { alert(t("pos.syncFailedPrefix") + e.message); })
      .finally(function() { posSyncBtn.disabled = false; posSyncBtn.textContent = t("pos.sync"); });
  });
}

// Auto-load positions when eastmoney tab is activated
(function() {
  var origTabHandler = null;
  var emwlTab = document.querySelector('[data-tab="eastmoney-wl"]');
  if (emwlTab) {
    // Load positions on first tab switch to eastmoney-wl
    var observer = new MutationObserver(function(mutations) {
      mutations.forEach(function(m) {
        if (m.target.id === "tab-eastmoney-wl" && m.target.classList.contains("active")) {
          loadPositions();
        }
      });
    });
    var tabContent = document.getElementById("tab-eastmoney-wl");
    if (tabContent) {
      observer.observe(tabContent, {attributes: true, attributeFilter: ["class"]});
    }
    // Also load initially if tab is already active
    if (tabContent && tabContent.classList.contains("active")) {
      setTimeout(loadPositions, 500);
    }
  }
})();

// ══════════════════════════════════════════════════════════════════════
// Index Stocks Tab (CSI 300 — 沪深300 成分股)
// Shares analysis infrastructure with eastmoney watchlist but uses
// tushare index_weight for constituent list instead of MXAPI sync.
// ══════════════════════════════════════════════════════════════════════

var idxStocks = [];
var idxVerdictCache = {};
var idxSortCol = null, idxSortDir = 1;
var idxBatchRunning = false, idxBatchStop = false, idxBatchAbort = null;
var idxAnalyzingCode = null, idxAnalyzeAbort = null;

// ── Model Selector ──
var idxProviderSelect = document.getElementById("idx-provider-select");
var idxModelSelect = document.getElementById("idx-model-select");
var idxModelCatalog = null;

function initIdxModelSelector() {
  if (!idxProviderSelect || !idxModelSelect) return;
  fetch("/api/models").then(function(r) { return r.json(); }).then(function(data) {
    idxModelCatalog = data;
    var providers = data.providers || [];
    idxProviderSelect.innerHTML = "";
    providers.forEach(function(p) {
      var sel = p.key === "ollama" ? " selected" : "";
      idxProviderSelect.innerHTML += '<option value="' + esc(p.key) + '"' + sel + '>' + esc(p.label) + '</option>';
    });
    onIdxProviderChange();
    setTimeout(function() {
      var opts = idxModelSelect.options;
      var preferred = ["qwen3.8:27b", "qwen3:32b", "qwen3.5:latest", "gemma4:12b"];
      for (var pi = 0; pi < preferred.length; pi++) {
        for (var i = 0; i < opts.length; i++) {
          if (opts[i].value === preferred[pi]) { idxModelSelect.value = opts[i].value; return; }
        }
      }
      if (opts.length > 0) idxModelSelect.value = opts[0].value;
    }, 200);
  }).catch(function() {});
}
function onIdxProviderChange() {
  if (!idxModelSelect || !idxModelCatalog) return;
  var prov = idxProviderSelect.value;
  if (!prov) { idxModelSelect.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>'; return; }
  var pd = (idxModelCatalog.providers || []).find(function(p) { return p.key === prov; });
  if (!pd) return;
  var deepModels = pd.deep_models || [];
  idxModelSelect.innerHTML = deepModels.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
}
function getIdxModelParams() {
  var p = idxProviderSelect ? idxProviderSelect.value : "";
  var m = idxModelSelect ? idxModelSelect.value : "";
  var parts = [];
  if (p) parts.push("provider=" + encodeURIComponent(p));
  if (m) parts.push("model=" + encodeURIComponent(m));
  return parts.join("&");
}
function getIdxModel2Params() {
  var p = idxProvider2Select ? idxProvider2Select.value : "";
  var m = idxModel2Select ? idxModel2Select.value : "";
  var parts = [];
  if (p) parts.push("provider=" + encodeURIComponent(p));
  if (m) parts.push("model=" + encodeURIComponent(m));
  return parts.join("&");
}
if (idxProviderSelect) { idxProviderSelect.addEventListener("change", onIdxProviderChange); }
initIdxModelSelector();

// ── Dual-model support for idx tab (模型2) ──
var idxProvider2Select = document.getElementById("idx-provider2-select");
var idxModel2Select = document.getElementById("idx-model2-select");
var idxDualToggle = document.getElementById("idx-dual-toggle");
var idxModel2Catalog = null;

function initIdxModel2Selector() {
  if (!idxProvider2Select || !idxModel2Select) return;
  fetch("/api/models").then(function(r) { return r.json(); }).then(function(data) {
    idxModel2Catalog = data;
    (data.providers || []).forEach(function(p) {
      idxProvider2Select.innerHTML += '<option value="' + esc(p.key) + '">' + esc(p.label) + '</option>';
    });
    onIdxProvider2Change();
  }).catch(function() {});
}
function onIdxProvider2Change() {
  if (!idxModel2Select || !idxModel2Catalog) return;
  var prov = idxProvider2Select.value;
  if (!prov) { idxModel2Select.innerHTML = '<option value="">' + t("common.defaultModel") + '</option>'; return; }
  var pd = (idxModel2Catalog.providers || []).find(function(p) { return p.key === prov; });
  if (!pd) return;
  idxModel2Select.innerHTML = pd.deep_models.map(function(m) {
    return '<option value="' + esc(m.value) + '">' + esc(m.label) + '</option>';
  }).join("");
}
function toggleIdxDualModel() {
  var row = document.getElementById("idx-model2-row");
  if (row) row.style.display = idxDualToggle && idxDualToggle.checked ? "flex" : "none";
}
function getIdxModel2Params() {
  var p = idxProvider2Select ? idxProvider2Select.value : "";
  var m = idxModel2Select ? idxModel2Select.value : "";
  var parts = [];
  if (p) parts.push("provider2=" + encodeURIComponent(p));
  if (m) parts.push("model2=" + encodeURIComponent(m));
  return parts.join("&");
}
function isIdxDualEnabled() { return idxDualToggle && idxDualToggle.checked; }

if (idxProvider2Select) { idxProvider2Select.addEventListener("change", onIdxProvider2Change); }
initIdxModel2Selector();setTimeout(function(){var ms=document.getElementById("idx-model2-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);

// ── Model 3/4 for idx ──
var idxProvider3Select = document.getElementById("idx-provider3-select");
var idxModel3Select = document.getElementById("idx-model3-select");
var idxTriToggle = document.getElementById("idx-tri-toggle");
var idxModel3Catalog = null;
function initIdxModel3Selector(){if(!idxProvider3Select||!idxModel3Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){idxModel3Catalog=d;(d.providers||[]).forEach(function(p){idxProvider3Select.innerHTML+='<option value="'+esc(p.key)+'">'+esc(p.label)+'</option>'});onIdxProvider3Change()}).catch(function(){})}
function onIdxProvider3Change(){if(!idxModel3Select||!idxModel3Catalog)return;var prov=idxProvider3Select.value;if(!prov){idxModel3Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(idxModel3Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd)return;idxModel3Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}
function toggleIdxTriModel(){var row=document.getElementById("idx-model3-row");if(row)row.style.display=idxTriToggle&&idxTriToggle.checked?"flex":"none"}
function getIdxModel3Params(){var p=idxProvider3Select?idxProvider3Select.value:"",m=idxModel3Select?idxModel3Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}
function isIdxTriEnabled(){return idxTriToggle&&idxTriToggle.checked}
if(idxProvider3Select){idxProvider3Select.addEventListener("change",onIdxProvider3Change)}initIdxModel3Selector();setTimeout(function(){var ms=document.getElementById("idx-model3-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);

var idxProvider4Select = document.getElementById("idx-provider4-select");
var idxModel4Select = document.getElementById("idx-model4-select");
var idxQuadToggle = document.getElementById("idx-quad-toggle");
var idxModel4Catalog = null;
function initIdxModel4Selector(){if(!idxProvider4Select||!idxModel4Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){idxModel4Catalog=d;(d.providers||[]).forEach(function(p){idxProvider4Select.innerHTML+='<option value="'+esc(p.key)+'">'+esc(p.label)+'</option>'});onIdxProvider4Change()}).catch(function(){})}
function onIdxProvider4Change(){if(!idxModel4Select||!idxModel4Catalog)return;var prov=idxProvider4Select.value;if(!prov){idxModel4Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(idxModel4Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd)return;idxModel4Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}
function toggleIdxQuadModel(){var row=document.getElementById("idx-model4-row");if(row)row.style.display=idxQuadToggle&&idxQuadToggle.checked?"flex":"none"}
function getIdxModel4Params(){var p=idxProvider4Select?idxProvider4Select.value:"",m=idxModel4Select?idxModel4Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}
function isIdxQuadEnabled(){return idxQuadToggle&&idxQuadToggle.checked}
if(idxProvider4Select){idxProvider4Select.addEventListener("change",onIdxProvider4Change)}initIdxModel4Selector();setTimeout(function(){var ms=document.getElementById("idx-model4-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);
var idxProvider5Select=document.getElementById("idx-provider5-select");var idxModel5Select=document.getElementById("idx-model5-select");var idxPentaToggle=document.getElementById("idx-penta-toggle");var idxModel5Catalog=null;function initIdxModel5Selector(){if(!idxProvider5Select||!idxModel5Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){idxModel5Catalog=d;(d.providers||[]).forEach(function(p){idxProvider5Select.innerHTML+='<option value="'+esc(p.key)+'">'+esc(p.label)+'</option>'});onIdxProvider5Change()}).catch(function(){})}function onIdxProvider5Change(){if(!idxModel5Select||!idxModel5Catalog)return;var prov=idxProvider5Select.value;if(!prov){idxModel5Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(idxModel5Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd)return;idxModel5Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleIdxPentaModel(){var row=document.getElementById("idx-model5-row");if(row)row.style.display=idxPentaToggle&&idxPentaToggle.checked?"flex":"none"}function getIdxModel5Params(){var p=idxProvider5Select?idxProvider5Select.value:"",m=idxModel5Select?idxModel5Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isIdxPentaEnabled(){return idxPentaToggle&&idxPentaToggle.checked}if(idxProvider5Select){idxProvider5Select.addEventListener("change",onIdxProvider5Change)}initIdxModel5Selector();setTimeout(function(){var ms=document.getElementById("idx-model5-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);
var idxProvider6Select=document.getElementById("idx-provider6-select");var idxModel6Select=document.getElementById("idx-model6-select");var idxHexaToggle=document.getElementById("idx-hexa-toggle");var idxModel6Catalog=null;function initIdxModel6Selector(){if(!idxProvider6Select||!idxModel6Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){idxModel6Catalog=d;(d.providers||[]).forEach(function(p){idxProvider6Select.innerHTML+='<option value="'+esc(p.key)+'">'+esc(p.label)+'</option>'});onIdxProvider6Change()}).catch(function(){})}function onIdxProvider6Change(){if(!idxModel6Select||!idxModel6Catalog)return;var prov=idxProvider6Select.value;if(!prov){idxModel6Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(idxModel6Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd)return;idxModel6Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleIdxHexaModel(){var row=document.getElementById("idx-model6-row");if(row)row.style.display=idxHexaToggle&&idxHexaToggle.checked?"flex":"none"}function getIdxModel6Params(){var p=idxProvider6Select?idxProvider6Select.value:"",m=idxModel6Select?idxModel6Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isIdxHexaEnabled(){return idxHexaToggle&&idxHexaToggle.checked}if(idxProvider6Select){idxProvider6Select.addEventListener("change",onIdxProvider6Change)}initIdxModel6Selector();
var idxProvider7Select=document.getElementById("idx-provider7-select");var idxModel7Select=document.getElementById("idx-model7-select");var idxHeptaToggle=document.getElementById("idx-hepta-toggle");var idxModel7Catalog=null;function initIdxModel7Selector(){if(!idxProvider7Select||!idxModel7Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){idxModel7Catalog=d;(d.providers||[]).forEach(function(p){idxProvider7Select.innerHTML+='<option value="'+esc(p.key)+'">'+esc(p.label)+'</option>'});onIdxProvider7Change()}).catch(function(){})}function onIdxProvider7Change(){if(!idxModel7Select||!idxModel7Catalog)return;var prov=idxProvider7Select.value;if(!prov){idxModel7Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(idxModel7Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd)return;idxModel7Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleIdxHeptaModel(){var row=document.getElementById("idx-model7-row");if(row)row.style.display=idxHeptaToggle&&idxHeptaToggle.checked?"flex":"none"}function getIdxModel7Params(){var p=idxProvider7Select?idxProvider7Select.value:"",m=idxModel7Select?idxModel7Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isIdxHeptaEnabled(){return idxHeptaToggle&&idxHeptaToggle.checked}if(idxProvider7Select){idxProvider7Select.addEventListener("change",onIdxProvider7Change)}initIdxModel7Selector();
var idxProvider8Select=document.getElementById("idx-provider8-select");var idxModel8Select=document.getElementById("idx-model8-select");var idxOctaToggle=document.getElementById("idx-octa-toggle");var idxModel8Catalog=null;function initIdxModel8Selector(){if(!idxProvider8Select||!idxModel8Select)return;fetch("/api/models").then(function(r){return r.json()}).then(function(d){idxModel8Catalog=d;(d.providers||[]).forEach(function(p){idxProvider8Select.innerHTML+='<option value="'+esc(p.key)+'">'+esc(p.label)+'</option>'});onIdxProvider8Change()}).catch(function(){})}function onIdxProvider8Change(){if(!idxModel8Select||!idxModel8Catalog)return;var prov=idxProvider8Select.value;if(!prov){idxModel8Select.innerHTML='<option value="">' + t("common.defaultModel") + '</option>';return}var pd=(idxModel8Catalog.providers||[]).find(function(p){return p.key===prov});if(!pd)return;idxModel8Select.innerHTML=pd.deep_models.map(function(m){return'<option value="'+esc(m.value)+'">'+esc(m.label)+'</option>'}).join("")}function toggleIdxOctaModel(){var row=document.getElementById("idx-model8-row");if(row)row.style.display=idxOctaToggle&&idxOctaToggle.checked?"flex":"none"}function getIdxModel8Params(){var p=idxProvider8Select?idxProvider8Select.value:"",m=idxModel8Select?idxModel8Select.value:"";var parts=[];if(p)parts.push("provider="+encodeURIComponent(p));if(m)parts.push("model="+encodeURIComponent(m));return parts.join("&")}function isIdxOctaEnabled(){return idxOctaToggle&&idxOctaToggle.checked}if(idxProvider8Select){idxProvider8Select.addEventListener("change",onIdxProvider8Change)}initIdxModel8Selector();setTimeout(function(){var ms=document.getElementById("idx-model6-select");if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}},500);

// Refresh buttons for model lists — re-init EVERY model slot so a freshly
// pulled model shows up in all of them. Lookup by window["initEmwlModelN..."]
// keeps this in sync as slots grow without one giant copy-pasted chain.
function presetModelDefault(selId){var ms=document.getElementById(selId);if(ms&&ms.options.length>1){var found=-1;for(var i=0;i<ms.options.length;i++){if(ms.options[i].value==="qwen3.8:27b"){found=i;break}}ms.selectedIndex=found>=0?found:1}}
function refreshEmwlModels(){initEmwlModelSelector();initEmwlModel2Selector();[3,4,5,6,7,8].forEach(function(n){var f=window["initEmwlModel"+n+"Selector"];if(f){f();setTimeout(function(){presetModelDefault("emwl-model"+n+"-select")},500)}})}
function refreshIdxModels(){initIdxModelSelector();initIdxModel2Selector();[3,4,5,6,7,8].forEach(function(n){var f=window["initIdxModel"+n+"Selector"];if(f){f();setTimeout(function(){presetModelDefault("idx-model"+n+"-select")},500)}})}
var emwlRefreshModels = document.getElementById("emwl-refresh-models");
if (emwlRefreshModels) { emwlRefreshModels.addEventListener("click", refreshEmwlModels); }
var idxRefreshModels = document.getElementById("idx-refresh-models");
if (idxRefreshModels) { idxRefreshModels.addEventListener("click", refreshIdxModels); }

// ── Data Loading ──
var idxSelect = document.getElementById("idx-select");
var idxRefreshBtn = document.getElementById("idx-refresh-btn");
var idxFullRefreshBtn = document.getElementById("idx-full-refresh-btn");
var idxList = document.getElementById("idx-list");
var idxStatus = document.getElementById("idx-status");

var idxAbortController = null;  // cancel in-flight index refresh on rapid switching

function loadIdxStocks(fullRefresh) {
  fullRefresh = !!fullRefresh;  // default false = quick (prices only)
  var idxKey = idxSelect ? idxSelect.value : "csi300";
  // Cancel any in-flight request to prevent stale-data races
  if (idxAbortController) { idxAbortController.abort(); }
  idxAbortController = new AbortController();
  idxRefreshBtn.disabled = true;
  if (idxFullRefreshBtn) idxFullRefreshBtn.disabled = true;
  var label = fullRefresh ? t("idx.refreshingFull") : t("idx.refreshingQuick");
  idxStatus.textContent = label;
  idxVerdictCache = {};
  var timeoutId = setTimeout(function() {
    if (idxAbortController) { idxAbortController.abort(); }
    idxStatus.textContent = t("idx.timeout");
  }, 90000);
  var url = "/api/index/constituents?index=" + encodeURIComponent(idxKey);
  if (fullRefresh) url += "&full_refresh=true";
  fetch(url, {signal: idxAbortController.signal})
    .then(function(r) { return r.json(); })
    .then(function(data) {
      clearTimeout(timeoutId);
      if (data.error) { idxStatus.textContent = t("idx.errorPrefix") + data.error; return; }
      var modeLabel = (data.mode === "full") ? t("idx.modeFull") : (data.mode === "quick" ? t("idx.modeQuick") : "");
      idxStatus.textContent = t("idx.statusLine", {index: data.index, count: data.count, mode: modeLabel, updated: (data.updated_at || "").slice(0, 16)});
      renderIdxTable(data.stocks);populateExportModels("idx");
    })
    .catch(function(e) {
      clearTimeout(timeoutId);
      if (e.name !== "AbortError") { idxStatus.textContent = t("idx.loadFailedPrefix") + e.message; }
    })
    .finally(function() {
      clearTimeout(timeoutId);
      idxRefreshBtn.disabled = false;
      if (idxFullRefreshBtn) idxFullRefreshBtn.disabled = false;
      idxAbortController = null;
    });
}

if (idxRefreshBtn) { idxRefreshBtn.addEventListener("click", function() { loadIdxStocks(false); }); }
if (idxFullRefreshBtn) { idxFullRefreshBtn.addEventListener("click", function() { loadIdxStocks(true); }); }
if (idxSelect) { idxSelect.addEventListener("change", function() { loadIdxStocks(false); }); }

function sortIdxTable(col) {
  if (idxSortCol === col) { idxSortDir *= -1; } else { idxSortCol = col; idxSortDir = 1; }
  idxStocks.sort(function(a, b) {
    var va, vb;
    if (col === "收益%") {
      va = (a.recent_analyses && a.recent_analyses[0]) ? a.recent_analyses[0].return_pct : null;
      vb = (b.recent_analyses && b.recent_analyses[0]) ? b.recent_analyses[0].return_pct : null;
      va = (typeof va === "number" && !isNaN(va)) ? va : -Infinity;
      vb = (typeof vb === "number" && !isNaN(vb)) ? vb : -Infinity;
    } else if (col === "结论") {
      va = (a.recent_analyses && a.recent_analyses[0]) ? a.recent_analyses[0].verdict : (a.verdict || "");
      vb = (b.recent_analyses && b.recent_analyses[0]) ? b.recent_analyses[0].verdict : (b.verdict || "");
    } else {
      var km = {"代码":"code","名称":"name","最新价":"price","涨跌幅":"change_pct","换手率":"turnover","量比":"vol_ratio"};
      var key = km[col] || col;
      va = a[key]; vb = b[key];
    }
    if (typeof va === "string") return va.localeCompare(String(vb || "")) * idxSortDir;
    return ((va || -Infinity) - (vb || -Infinity)) * idxSortDir;
  });
  renderIdxTable(idxStocks);
}

function renderIdxTable(stocks) {
  if (!stocks || !stocks.length) {
    idxList.innerHTML = '<span class="no-results">' + t("idx.empty") + '</span>';
    return;
  }
  idxStocks = stocks;
  var dir = idxSortDir > 0 ? " ▲" : " ▼";
  var html = '<table style="width:100%;border-collapse:collapse;font-size:10px;">';
  html += '<tr style="border-bottom:1px solid var(--border);color:var(--text-muted);">';
  html += '<th style="width:20px;"><input type="checkbox" id="idx-select-all" title="' + t("common.selectAll") + '"></th>';
  var idxColLabels = {"代码":"idx.colCode","名称":"idx.colName","最新价":"idx.colPrice","涨跌幅":"idx.colChange","换手率":"idx.colTurnover","量比":"idx.colVolRatio","收益%":"idx.colReturn","结论":"idx.colVerdict"};
  ["代码","名称","最新价","涨跌幅","换手率","量比","收益%","结论"].forEach(function(c) {
    var marker = idxSortCol === c ? dir : "";
    html += '<th style="cursor:pointer;padding:2px 4px;" onclick="sortIdxTable(\'' + c + '\')">' + t(idxColLabels[c] || c) + marker + '</th>';
  });
  html += '<th></th></tr>';
  stocks.forEach(function(s) {
    var color = s.change_pct >= 0 ? "var(--green)" : "var(--red)";
    var retHtml = "", verdictHtml = "";
    var recent = s.recent_analyses || [];
    if (recent.length > 0) {
      for (var ri = 0; ri < 3; ri++) {
        var ra = recent[ri];
        if (ra && typeof ra.return_pct === "number" && !isNaN(ra.return_pct)) {
          var rc = ra.return_pct >= 0 ? "var(--green)" : "var(--red)";
          var rs = (ra.return_pct >= 0 ? "+" : "") + ra.return_pct.toFixed(2) + "%";
          var rd = ra.verdict || "";
          var dl = rd.includes("看空") ? "S" : (rd.includes("看多") ? "L" : "H");
          retHtml += '<div style="font-size:9px;color:' + rc + ';line-height:1.4;" title="' + esc(ra.analyzed_at||"") + '">' + rs + ' <span style="font-size:7px;">' + dl + '</span></div>';
        } else { retHtml += '<div style="font-size:9px;color:#ccc;line-height:1.4;">—</div>'; }
        if (ra) {
          var v = ra.verdict || "";
          var vc = VERDICT_COLOR[v] || "#888";
          var vi = verdictText(v);
          verdictHtml += '<div style="font-size:8px;color:' + vc + ';line-height:1.4;" title="' + esc((ra.analyzed_at||"").slice(0,10)) + ' ' + esc(ra.model_name||"") + '">' + vi + '</div>';
        } else { verdictHtml += '<div style="font-size:8px;color:#ccc;line-height:1.4;">—</div>'; }
      }
    } else {
      var retStr2 = "-", retColor2 = "#888";
      if (typeof s.return_pct === "number" && !isNaN(s.return_pct)) {
        retStr2 = (s.return_pct >= 0 ? "+" : "") + s.return_pct.toFixed(2) + "%";
        retColor2 = s.return_pct >= 0 ? "var(--green)" : "var(--red)";
      }
      retHtml = '<span style="color:' + retColor2 + ';font-weight:600;">' + retStr2 + '</span>';
      var rv2 = s.verdict || idxVerdictCache[s.code] || "";
      var rvColor2 = VERDICT_COLOR[rv2] || "#888";
      verdictHtml = '<span style="font-size:9px;color:' + rvColor2 + ';">' + (rv2 ? esc(verdictDisplay(rv2)) : "—") + '</span>';
    }
    html += '<tr class="idx-row" data-code="' + esc(s.code) + '" style="border-bottom:1px solid var(--border);cursor:pointer;">';
    html += '<td style="padding:2px 4px;text-align:center;"><input type="checkbox" class="idx-check" data-code="' + esc(s.code) + '" onclick="event.stopPropagation();"></td>';
    html += '<td style="padding:2px 4px;">' + esc(s.code) + '</td>';
    html += '<td>' + esc(s.name) + '</td>';
    html += '<td style="text-align:right;">' + (s.price ? s.price.toFixed(2) : "-") + '</td>';
    html += '<td style="text-align:right;color:' + color + ';">' + (s.change_pct >= 0 ? "+" : "") + (s.change_pct || 0).toFixed(2) + '%</td>';
    html += '<td style="text-align:right;">' + (s.turnover ? s.turnover.toFixed(2) + '%' : '-') + '</td>';
    html += '<td style="text-align:right;padding-right:12px;">' + (s.vol_ratio ? s.vol_ratio.toFixed(2) : '-') + '</td>';
    html += '<td style="text-align:right;padding:0 2px;">' + retHtml + '</td>';
    html += '<td style="padding:0 2px;text-align:center;">' + verdictHtml + '</td>';
    html += '<td><button class="btn idx-analyze-btn" data-code="' + esc(s.code) + '" style="font-size:9px;padding:1px 4px;">' + t("idx.analyze") + '</button></td>';
    html += '</tr>';
  });
  html += '</table>';
  idxList.innerHTML = html;
  // Select-all
  var sa = document.getElementById("idx-select-all");
  if (sa) sa.addEventListener("click", function(e) { e.stopPropagation(); idxList.querySelectorAll(".idx-check").forEach(function(cb) { cb.checked = sa.checked; }); updateIdxBatchButtons(); });
  idxList.querySelectorAll(".idx-check").forEach(function(cb) { cb.addEventListener("change", updateIdxBatchButtons); });
  // Analyze button
  idxList.querySelectorAll(".idx-analyze-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) { e.stopPropagation(); showEmwlRightPanel(btn.dataset.code); analyzeIdxStock(btn.dataset.code); });
  });
  // Row click → history + right panel
  idxList.querySelectorAll(".idx-row").forEach(function(row) {
    row.addEventListener("click", function(e) {
      if (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON") return;
      var code = row.dataset.code;
      showEmwlRightPanel(code);
      showIdxHistory(code);
    });
  });
  updateIdxBatchButtons();
}

// ── Search ──
var idxSearchInput = document.getElementById("idx-search-input");
var idxSearchDropdown = document.getElementById("idx-search-dropdown");
if (idxSearchInput) {
  idxSearchInput.addEventListener("input", function() {
    var q = idxSearchInput.value.trim().toLowerCase();
    idxList.querySelectorAll(".idx-row").forEach(function(row) {
      var code = (row.dataset.code || "").toLowerCase();
      var nameCell = row.querySelectorAll("td")[2];
      var name = (nameCell ? nameCell.textContent : "").toLowerCase();
      row.style.display = (!q || code.indexOf(q) !== -1 || name.indexOf(q) !== -1) ? "" : "none";
    });
  });
}

// ── Single Analysis ──
function analyzeIdxStock(code) {
  var panel = document.getElementById("idx-analysis-panel");
  var title = document.getElementById("idx-analysis-title");
  var body = document.getElementById("idx-analysis-body");
  if (!panel) return;
  panel.classList.remove("hidden");
  if (idxAnalyzingCode === code && idxAnalyzeAbort) { idxAnalyzeAbort.abort(); idxAnalyzingCode = null; idxAnalyzeAbort = null; title.textContent = t("idx.stoppedTitle", {code: code}); body.innerHTML = t("idx.analysisCancelled"); updateIdxAnalyzeBtnState(null); return; }
  if (idxAnalyzeAbort) idxAnalyzeAbort.abort();
  idxAnalyzingCode = code;
  idxAnalyzeAbort = new AbortController();
  updateIdxAnalyzeBtnState(code);
  title.textContent = t("idx.analyzingTitle", {code: code});
  var stopBtn2 = document.createElement('button');
  stopBtn2.textContent = t("common.stop");
  stopBtn2.style.cssText = 'font-size:10px;padding:2px 8px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;margin-left:8px;';
  stopBtn2.onclick = function(e) { e.preventDefault(); analyzeIdxStock(code); };
  title.appendChild(stopBtn2);

  var mi1 = (idxProviderSelect ? idxProviderSelect.value : "") + "/" + (idxModelSelect ? idxModelSelect.value : "");
  body.innerHTML = '<span style="color:var(--text-muted);">' + t("idx.analyzingDetail", {code: esc(code), model: esc(mi1)}) + '</span>';
  var qs = getIdxModelParams();
  fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + (qs ? "?" + qs : ""), {signal: idxAnalyzeAbort.signal})
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var a1 = d.analysis || ""; var v1 = d.verdict || "";
      var verdict = v1 === "看多" ? "看多" : (v1 === "看空" ? "看空" : "观望");
      idxVerdictCache[code] = verdict; updateIdxVerdictCell(code, verdict);
      title.innerHTML = esc(code) + " — " + esc(verdictText(verdict)) + (d.model_name ? ' <span style="font-size:9px;color:var(--text-muted);font-weight:400;">🤖 ' + esc(d.model_name) + '</span>' : '');
      body.innerHTML = (d.model_name ? '<div style="font-size:9px;color:var(--text-muted);margin-bottom:4px;">' + t("idx.model", {model: esc(d.model_name)}) + '</div>' : '')
        + '<div style="margin-bottom:8px;"><b>' + t("idx.llmConclusion") + '</b><br>' + esc(a1 || t("idx.analysisUnavailable")) + '</div>';
      idxAnalyzingCode = null; idxAnalyzeAbort = null; updateIdxAnalyzeBtnState(null); loadIdxReturns(code);
    })
    .catch(function(e) { if (e.name !== "AbortError") body.innerHTML = '<span style="color:var(--red);">' + t("idx.analyzeFailedPrefix") + e.message + '</span>'; idxAnalyzingCode = null; idxAnalyzeAbort = null; updateIdxAnalyzeBtnState(null); });
}
function updateIdxAnalyzeBtnState(activeCode) {
  idxList.querySelectorAll(".idx-analyze-btn").forEach(function(btn) {
    if (btn.dataset.code === activeCode) { btn.textContent = t("common.stop"); btn.style.background = "var(--red)"; btn.style.color = "#fff"; btn.style.fontWeight = "700"; }
    else { btn.textContent = t("idx.analyze"); btn.style.background = ""; btn.style.color = ""; btn.style.fontWeight = ""; }
  });
}
function updateIdxVerdictCell(code, verdict) {
  var row = idxList.querySelector('[data-code="' + code + '"]');
  if (row) { var cells = row.querySelectorAll("td"); if (cells.length >= 9) {
    var color = VERDICT_COLOR[verdict] || "#888";
    cells[8].innerHTML = '<span style="color:' + color + ';font-weight:600;">' + esc(verdictDisplay(verdict)) + '</span>';
  } }
}

// ── History ──
function showIdxHistory(code) {
  var panel = document.getElementById("idx-analysis-panel");
  var title = document.getElementById("idx-analysis-title");
  var body = document.getElementById("idx-analysis-body");
  if (!panel) return;
  panel.classList.remove("hidden");
  title.textContent = t("idx.historyTitle", {code: code});
  body.innerHTML = '<span style="color:var(--text-muted);">' + t("common.loading") + '</span>';
  fetch("/api/eastmoney/watchlist/returns/" + encodeURIComponent(code))
    .then(function(r) { return r.json(); }).then(function(d) {
      var returns = (d && d.returns) || [];
      if (!returns.length) { body.innerHTML = t("idx.noHistory"); return; }
      var html = '<div style="font-size:10px;">';
      returns.forEach(function(r, i) {
        var at = (r.analyzed_at || "").replace("T"," ").slice(0,16);
        var vc = VERDICT_COLOR[r.verdict||""] || "#888";
        var rc = (r.return_pct||0) >= 0 ? "var(--green)" : "var(--red)";
        var rs = ((r.return_pct||0) >= 0 ? "+":"") + (r.return_pct||0).toFixed(2) + "%";
        html += '<div style="margin-bottom:4px;padding:4px;border:1px solid var(--border);border-radius:3px;">';
        html += '<span style="color:' + vc + ';font-weight:600;">' + esc(r.verdict ? verdictDisplay(r.verdict) : "") + '</span> ' + esc(at) + ' | ' + t("emwl.returnLabel") + ' <span style="color:' + rc + ';">' + rs + '</span>';
        if (r.model_name) html += ' | 🤖 ' + esc(r.model_name);
        html += '<details style="margin-top:2px;"><summary style="cursor:pointer;font-size:9px;">' + t("idx.details") + '</summary><div style="white-space:pre-wrap;font-size:9px;">' + esc(r.analysis||"") + '</div></details></div>';
      });
      html += '</div>';
      body.innerHTML = html;
    }).catch(function() { body.innerHTML = t("idx.historyFailed"); });
}

// ── Returns ──
function loadIdxReturns(code) {
  var body = document.getElementById("idx-analysis-body");
  fetch("/api/eastmoney/watchlist/returns/" + encodeURIComponent(code))
    .then(function(r) { return r.json(); }).then(function(d) {
      var returns = (d && d.returns) || [];
      if (!returns.length) { body.insertAdjacentHTML("beforeend", '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">' + t("idx.noReturns") + '</div>'); return; }
      var html = '<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:4px;font-size:10px;"><b>' + t("idx.returnsTitle") + '</b><table style="width:100%;font-size:9px;">';
      html += '<tr><th>' + t("idx.thTime") + '</th><th>' + t("idx.thBuy") + '</th><th>' + t("idx.thCurrent") + '</th><th>' + t("idx.thReturn") + '</th><th>' + t("idx.thVerdict") + '</th><th>' + t("idx.thModel") + '</th></tr>';
      returns.forEach(function(r) {
        var rc = (r.return_pct||0) >= 0 ? "var(--green)" : "var(--red)";
        html += '<tr><td>' + esc((r.analyzed_at||"").replace("T"," ").slice(0,16)) + '</td><td>' + esc(String(r.buy_price||"-")) + '</td><td>' + esc(String(r.sell_price||"-")) + '</td><td style="color:' + rc + ';">' + ((r.return_pct>=0?"+":"") + r.return_pct.toFixed(2) + "%") + '</td><td>' + esc(r.verdict||"") + '</td><td style="font-size:8px;">' + esc(r.model_name||"-") + '</td></tr>';
      });
      html += '</table></div>';
      body.insertAdjacentHTML("beforeend", html);
    }).catch(function() {});
}

// ── Batch Analysis ──
var idxBatchBtn = document.getElementById("idx-batch-btn");
var idxBatchSelBtn = document.getElementById("idx-batch-sel-btn");
var idxResumeBtn = document.getElementById("idx-resume-btn");
var idxRedoBtn = document.getElementById("idx-redo-btn");

function updateIdxBatchButtons() {
  var rows = idxList.querySelectorAll(".idx-row");
  if (!rows.length) return;
  var total = rows.length, done = 0;
  rows.forEach(function(r) { if (idxVerdictCache[r.dataset.code]) done++; });
  var unchecked = total - done;
  var checked = idxList.querySelectorAll(".idx-check:checked").length;
  if (idxBatchRunning) {
    idxBatchBtn.textContent = t("idx.analyzing"); idxBatchBtn.disabled = true;
    if (idxBatchSelBtn) { idxBatchSelBtn.disabled = true; idxBatchSelBtn.textContent = t("idx.analyzing"); }
    idxResumeBtn.disabled = true; idxRedoBtn.disabled = true;
    return;
  }
  if (idxBatchSelBtn) { idxBatchSelBtn.disabled = (checked === 0); idxBatchSelBtn.textContent = idxBatchSelBtn.disabled ? t("idx.noneChecked") : t("idx.batchSel"); }
  idxBatchBtn.textContent = unchecked > 0 ? t("idx.batchNewCount", {n: unchecked}) : t("idx.allDone");
  idxBatchBtn.disabled = (unchecked === 0);
  idxResumeBtn.disabled = !(done > 0 && unchecked > 0);
  idxRedoBtn.disabled = (done === 0);
}
if (idxBatchBtn) idxBatchBtn.addEventListener("click", function() {
  var rows = idxList.querySelectorAll(".idx-row"); var codes = [];
  rows.forEach(function(r) { if (!idxVerdictCache[r.dataset.code]) codes.push(r.dataset.code); });
  codes = filterByBoard(codes, 'idx-board-toggle');
  codes = filterByChangePct(codes, 'idx');
  if (!codes.length) { idxStatus.textContent = t("idx.allAnalyzedOrFiltered"); return; }
  idxBatchRunning = true; idxBatchStop = false; runIdxBatch(codes, 0);
});
if (idxBatchSelBtn) idxBatchSelBtn.addEventListener("click", function() {
  var checks = idxList.querySelectorAll(".idx-check:checked"); var codes = [];
  checks.forEach(function(cb) { codes.push(cb.dataset.code); });
  codes = filterByBoard(codes, 'idx-board-toggle');
  codes = filterByChangePct(codes, 'idx');
  if (!codes.length) { idxStatus.textContent = t("idx.checkedAllFiltered"); return; }
  idxBatchRunning = true; idxBatchStop = false; idxStatus.textContent = t("idx.batchSelected", {n: codes.length});
  runIdxBatch(codes, 0);
});
if (idxResumeBtn) idxResumeBtn.addEventListener("click", function() {
  var rows = idxList.querySelectorAll(".idx-row"); var codes = [];
  rows.forEach(function(r) { if (!idxVerdictCache[r.dataset.code]) codes.push(r.dataset.code); });
  codes = filterByBoard(codes, 'idx-board-toggle');
  codes = filterByChangePct(codes, 'idx');
  if (!codes.length) { idxStatus.textContent = t("idx.allAnalyzedOrFiltered"); return; }
  idxBatchRunning = true; idxBatchStop = false; runIdxBatch(codes, 0);
});
if (idxRedoBtn) idxRedoBtn.addEventListener("click", function() {
  // Re-analyze ALL stocks (append new analyses, don't clear existing)
  var rows = idxList.querySelectorAll(".idx-row");
  if (!rows.length) return;
  var codes = [];
  rows.forEach(function(r) { codes.push(r.dataset.code); });
  codes = filterByBoard(codes, 'idx-board-toggle');
  codes = filterByChangePct(codes, 'idx');
  if (!codes.length) { idxStatus.textContent = t("idx.allFiltered"); return; }
  idxBatchRunning = true; idxBatchStop = false;
  runIdxBatch(codes, 0);
});
function runIdxBatch(codes, idx) {
  if (idxBatchStop || idx >= codes.length) {
    if (!idxBatchStop && idx >= codes.length) { idxBatchRunning = false; idxStatus.textContent = t("idx.analysisDone"); updateIdxBatchButtons(); }
    if (idxBatchAbort) { idxBatchAbort.abort(); idxBatchAbort = null; }
    return;
  }

  // Stop button helper
  function stopBtn(){return ' <button onclick="idxBatchStop=true;if(idxBatchAbort)idxBatchAbort.abort();" style="font-size:10px;padding:1px 6px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;">'+t("common.stop")+'</button>';}

  var dual = isIdxDualEnabled();
  if (!dual) {
    // Abort previous
    if (idxBatchAbort) idxBatchAbort.abort();
    idxBatchAbort = new AbortController();
    var code = codes[idx];
    var m1 = (idxProviderSelect ? idxProviderSelect.value : "") + "/" + (idxModelSelect ? idxModelSelect.value : "");
    idxStatus.innerHTML = t("idx.batchAnalyzing", {done: (Object.keys(idxVerdictCache).length + 1), total: codes.length, code: esc(code), model: esc(m1)}) + stopBtn();
    showEmwlRightPanel(code);
    var panel = document.getElementById("idx-analysis-panel");
    var title = document.getElementById("idx-analysis-title");
    var body = document.getElementById("idx-analysis-body");
    if (panel) { panel.classList.remove("hidden"); if (title) title.textContent = t("idx.analyzingTitle", {code: code}); if (body) body.innerHTML = '<span style="color:var(--text-muted);">' + t("idx.analyzingDetail", {code: esc(code), model: esc(m1)}) + '</span>'; }
    function next(){if(!idxBatchStop)setTimeout(function(){runIdxBatch(codes,idx+1)},300);else{idxBatchRunning=false;idxStatus.textContent=t("idx.stopped");updateIdxBatchButtons();idxBatchAbort=null;}}
    fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + "?" + getIdxModelParams(), {signal: idxBatchAbort.signal})
      .then(function(r) { return r.json(); }).then(function(d) {
        var rawV = d.verdict || ""; var verdict = rawV === "看多" ? "看多" : (rawV === "看空" ? "看空" : "观望");
        idxVerdictCache[code] = verdict; updateIdxVerdictCell(code, verdict);
        if (title) title.textContent = code + " — " + verdictText(verdict) + (d.model_name ? " 🤖 " + d.model_name : "");
        if (body) body.innerHTML = '<div style="margin-bottom:8px;"><b>' + t("idx.llmConclusion") + '</b><br>' + esc(d.analysis || t("idx.analysisUnavailable")) + '</div>';
        updateIdxBatchButtons();
      }).catch(function(e){if(e.name!=="AbortError")body.innerHTML='<span style="color:var(--red);">'+t("idx.failedPrefix")+e.message+'</span>'}).finally(next);
    return;
  }

  // ── Multi-model: N workers from shared queue ──
  if (idxBatchAbort) idxBatchAbort.abort();
  idxBatchAbort = new AbortController();
  var nextIdx = idx, running = 0, total = codes.length;
  var iworkers = [{code:"",pEl:idxProviderSelect,mEl:idxModelSelect,fn:getIdxModelParams}];
  if(isIdxDualEnabled())iworkers.push({code:"",pEl:idxProvider2Select,mEl:idxModel2Select,fn:getIdxModel2Params});
  if(isIdxTriEnabled())iworkers.push({code:"",pEl:idxProvider3Select,mEl:idxModel3Select,fn:getIdxModel3Params});
  if(isIdxQuadEnabled())iworkers.push({code:"",pEl:idxProvider4Select,mEl:idxModel4Select,fn:getIdxModel4Params});
  if(isIdxPentaEnabled())iworkers.push({code:"",pEl:idxProvider5Select,mEl:idxModel5Select,fn:getIdxModel5Params});
  if(isIdxHexaEnabled())iworkers.push({code:"",pEl:idxProvider6Select,mEl:idxModel6Select,fn:getIdxModel6Params});
  if(isIdxHeptaEnabled())iworkers.push({code:"",pEl:idxProvider7Select,mEl:idxModel7Select,fn:getIdxModel7Params});
  if(isIdxOctaEnabled())iworkers.push({code:"",pEl:idxProvider8Select,mEl:idxModel8Select,fn:getIdxModel8Params});
  function getNext(){if(nextIdx<total){var c=codes[nextIdx];nextIdx++;return c}return null}
  function cleanup(){idxBatchRunning=false;idxBatchAbort=null;updateIdxBatchButtons();var ib=document.getElementById("idx-worker-bar");if(ib)ib.remove()}
  function updateStatus(){
    var done=Object.keys(idxVerdictCache).length;
    var line=t("idx.parallelAnalyzing",{n:iworkers.length,done:done,total:total});
    iworkers.forEach(function(w,i){if(w.code){var ms=(w.pEl?w.pEl.value:"")+"/"+(w.mEl?w.mEl.value:"");line+=' 🤖'+(i+1)+' '+esc(w.code)+' ← '+esc(ms)}});
    line+=stopBtn();idxStatus.innerHTML=line;
    var bar=document.getElementById("idx-worker-bar");
    if(!bar){bar=document.createElement("div");bar.id="idx-worker-bar";bar.style.cssText="margin:4px 0;padding:6px 8px;border-radius:4px;background:var(--bg);border:1px solid var(--border);font-size:10px;line-height:1.5;";var le=document.getElementById("idx-list");if(le&&le.parentNode)le.parentNode.insertBefore(bar,le.nextSibling)}
    if(bar){var h2='<b>'+t("idx.parallelBarTitle",{n:iworkers.length})+'</b>';iworkers.forEach(function(w,i){var c=w.code?"#2da44e":"#888",icon=w.code?"🟢":"⏳";var ms=(w.pEl?w.pEl.value:"")+"/"+(w.mEl?w.mEl.value:"");h2+='<div>🤖'+(i+1)+' <b>'+esc(ms)+'</b>: <span style="color:'+c+';">'+icon+' '+(w.code?esc(w.code):t("idx.waiting"))+'</span></div>'});bar.innerHTML=h2}
  }
  function worker(w) {
    function run() {
      if (idxBatchStop || (idxBatchAbort && idxBatchAbort.signal.aborted)) { w.code = ""; running--; if (running <= 0) { idxStatus.textContent = t("idx.stopped"); cleanup(); } return; }
      var code = getNext();
      if (!code) { w.code = ""; running--; if (running <= 0) { idxStatus.textContent = t("idx.analysisDone"); cleanup(); } return; }
      w.code = code; updateStatus();
      fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + "?" + w.fn(), {signal: idxBatchAbort.signal})
        .then(function(r) { return r.json(); }).then(function(d) {
          var rawV = d.verdict || ""; var verdict = rawV === "看多" ? "看多" : (rawV === "看空" ? "看空" : "观望");
          idxVerdictCache[code] = verdict; updateIdxVerdictCell(code, verdict);
          var p2 = document.getElementById("idx-analysis-panel");
          if (p2) { p2.classList.remove("hidden"); var t2 = document.getElementById("idx-analysis-title"); if (t2) t2.textContent = code + " — " + verdictText(verdict) + (d.model_name ? " 🤖 " + d.model_name : ""); var b2 = document.getElementById("idx-analysis-body"); if (b2) b2.innerHTML = '<div style="margin-bottom:8px;"><b>' + t("idx.llmConclusion") + '</b><br>' + esc(d.analysis || t("idx.analysisUnavailable")) + '</div>'; }
          updateIdxBatchButtons();
        }).catch(function(e) { if (e.name !== "AbortError" && body) body.innerHTML = '<span style="color:var(--red);">' + t("idx.failedPrefix") + e.message + '</span>'; })
        .finally(function(){w.code="";if(idxBatchStop){running--;if(running<=0){idxStatus.textContent=t("idx.stopped");cleanup()}}else setTimeout(run,200)});
    }
    run();
  }
  running = iworkers.length; idxBatchRunning = true;
  iworkers.forEach(function(w){worker(w)});
}

// ── Board filter helper ──
function getExcludedBoards(filterClass) {
  // filterClass: 'emwl-board-toggle' or 'idx-board-toggle'
  var excluded = [];
  document.querySelectorAll('.' + filterClass).forEach(function(cb) {
    if (!cb.checked) excluded.push(cb.dataset.board);
  });
  return excluded; // ['cyb', 'kcb', 'bjs']
}
function filterByBoard(codes, filterClass) {
  // Skip stocks from excluded boards. 创业板=300/301, 科创板=688, 北交所=8/9
  var excluded = getExcludedBoards(filterClass);
  if (!excluded.length) return codes;
  return codes.filter(function(code) {
    var c = code.toUpperCase().replace(/\.(SH|SZ|BJ)$/, '');
    if (excluded.indexOf('cyb') >= 0 && (c.startsWith('300') || c.startsWith('301'))) return false;
    if (excluded.indexOf('kcb') >= 0 && c.startsWith('688')) return false;
    if (excluded.indexOf('bjs') >= 0 && (c.startsWith('8') || c.startsWith('9'))) return false;
    return true;
  });
}

// ── 涨跌幅 filter helpers (batch only) ──
// prefix: 'emwl' or 'idx' — reads #<prefix>-change-toggle + #<prefix>-change-value.
// getChangeFilterState returns {threshold} when the constraint is enabled, else null.
// Used by both the code-list filter and the two-pass (二次分析) object filter.
function getChangeFilterState(prefix) {
  var toggle = document.getElementById(prefix + '-change-toggle');
  if (!toggle || !toggle.checked) return null; // not enabled → no constraint
  var valEl = document.getElementById(prefix + '-change-value');
  var threshold = valEl && valEl.value !== '' ? parseFloat(valEl.value) : 0;
  if (isNaN(threshold)) threshold = 0;
  return {threshold: threshold};
}

// Filter an array of stock codes to only those with change_pct > threshold.
function filterByChangePct(codes, prefix) {
  var state = getChangeFilterState(prefix);
  if (!state) return codes;

  var stocks = prefix === 'idx' ? idxStocks : emwlStocks;
  var map = {};
  (stocks || []).forEach(function(s) {
    if (s.code) map[String(s.code).toUpperCase()] = parseFloat(s.change_pct) || 0;
  });

  var kept = codes.filter(function(code) {
    var chg = map[String(code).toUpperCase()];
    if (chg === undefined) chg = 0; // no change data → treat as 0
    return chg > state.threshold;
  });

  if (kept.length > 0 && kept.length < codes.length) {
    var st = document.getElementById(prefix + '-status');
    if (st) st.textContent = t("common.changeFiltered", {t: state.threshold, kept: kept.length, total: codes.length});
  }
  return kept;
}

// ── Two-Pass buttons (fetch fresh data from DB) ──
var emwlTwoPassBtn = document.getElementById("emwl-twopass-btn");
if (emwlTwoPassBtn) {
  emwlTwoPassBtn.addEventListener("click", function() {
    var tvEl=document.getElementById("emwl-twopass-verdict");var targetVerdict=tvEl?tvEl.value:"看多";
    emwlTwoPassBtn.disabled=true;emwlStatus.textContent=t("twopass.fetching");
    fetch("/api/eastmoney/watchlist").then(function(r){return r.json()}).then(function(data){
      emwlStocks=data.stocks||[];renderEmwlTable(emwlStocks);
      var bullishCodes=[];
      emwlStocks.forEach(function(s){
        if(s.recent_analyses&&s.recent_analyses.length>0&&s.recent_analyses[0].verdict===targetVerdict){
          bullishCodes.push({code:s.code,name:s.name||"",price:s.price||0,change_pct:s.change_pct||0,prevModel:s.recent_analyses[0].model_name||"?",prevVerdict:targetVerdict});
        }
      });
      emwlTwoPassBtn.disabled=false;
      if(!bullishCodes.length){emwlStatus.textContent=t("twopass.noMatch",{verdict:targetVerdict});return}
      bullishCodes=filterByBoard(bullishCodes.map(function(b){return b.code}),"emwl-board-toggle").map(function(c){return bullishCodes.find(function(b){return b.code===c})});
      if(!bullishCodes.length){emwlStatus.textContent=t("twopass.boardExcluded",{verdict:targetVerdict});return}
      var chgState=getChangeFilterState("emwl");
      if(chgState){
        bullishCodes=bullishCodes.filter(function(b){return (parseFloat(b.change_pct)||0)>chgState.threshold});
        if(!bullishCodes.length){emwlStatus.textContent=t("twopass.chgFiltered",{t:chgState.threshold});return}
      }
      runTwoPassV2(bullishCodes,emwlList,emwlStatus,getEmwlModelParams,renderEmwlTable,updateBatchButtons,emwlStocks);
    }).catch(function(e){emwlStatus.textContent=t("twopass.fetchFailedPrefix")+e.message;emwlTwoPassBtn.disabled=false});
  });
}
var idxTwoPassBtn = document.getElementById("idx-twopass-btn");
if (idxTwoPassBtn) {
  idxTwoPassBtn.addEventListener("click", function() {
    var tvEl=document.getElementById("idx-twopass-verdict");var targetVerdict=tvEl?tvEl.value:"看多";
    idxTwoPassBtn.disabled=true;idxStatus.textContent=t("twopass.fetching");
    var idxKey=idxSelect?idxSelect.value:"csi300";
    fetch("/api/index/constituents?index="+encodeURIComponent(idxKey)).then(function(r){return r.json()}).then(function(data){
      idxStocks=data.stocks||[];renderIdxTable(idxStocks);
      var bullishCodes=[];
      idxStocks.forEach(function(s){
        if(s.recent_analyses&&s.recent_analyses.length>0&&s.recent_analyses[0].verdict===targetVerdict){
          bullishCodes.push({code:s.code,name:s.name||"",price:s.price||0,change_pct:s.change_pct||0,prevModel:s.recent_analyses[0].model_name||"?",prevVerdict:targetVerdict});
        }
      });
      idxTwoPassBtn.disabled=false;
      if(!bullishCodes.length){idxStatus.textContent=t("twopass.noMatch",{verdict:targetVerdict});return}
      bullishCodes=filterByBoard(bullishCodes.map(function(b){return b.code}),"idx-board-toggle").map(function(c){return bullishCodes.find(function(b){return b.code===c})});
      if(!bullishCodes.length){idxStatus.textContent=t("twopass.boardExcluded",{verdict:targetVerdict});return}
      var chgState=getChangeFilterState("idx");
      if(chgState){
        bullishCodes=bullishCodes.filter(function(b){return (parseFloat(b.change_pct)||0)>chgState.threshold});
        if(!bullishCodes.length){idxStatus.textContent=t("twopass.chgFiltered",{t:chgState.threshold});return}
      }
      runTwoPassV2(bullishCodes,idxList,idxStatus,getIdxModelParams,renderIdxTable,updateIdxBatchButtons,idxStocks);
    }).catch(function(e){idxStatus.textContent=t("twopass.fetchFailedPrefix")+e.message;idxTwoPassBtn.disabled=false});
  });
}
// ── Export latest batch analysis results ──
function exportBatchVerdict(prefix){
  var verdict=document.getElementById(prefix+"-twopass-verdict").value;
  var source=prefix==="idx"?(idxSelect?idxSelect.value:"csi300"):"mxapi";
  var modelEl=document.getElementById(prefix+"-export-model");
  var model=modelEl?modelEl.value:"";
  var body={verdict:verdict,source:source};if(model)body.model=model;
  // 仅分析涨幅 constraint applies to export too (same as batch analysis)
  var chgState=getChangeFilterState(prefix);
  if(chgState){
    body.change_threshold=chgState.threshold;
    var st=document.getElementById(prefix+"-status");
    if(st)st.textContent=t("twopass.exportChgApplied",{t:chgState.threshold});
  }
  fetch("/api/eastmoney/watchlist/export?format=docx",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify(body)
  }).then(function(r){if(!r.ok)throw new Error("HTTP "+r.status);return r.blob()}).then(function(blob){
    var url=URL.createObjectURL(blob);var a=document.createElement("a");a.href=url;
    a.download="batch-"+source+"-"+verdict+"-"+new Date().toISOString().slice(0,10)+".docx";a.click();URL.revokeObjectURL(url);
  }).catch(function(e){console.error("Export failed:",e)});
}
// Populate export model dropdown from recent data
function populateExportModels(prefix){
  var sel=document.getElementById(prefix+"-export-model");if(!sel)return;
  var stocks=prefix==="idx"?idxStocks:(typeof emwlStocks!=='undefined'?emwlStocks:[]);
  var models=new Set();stocks.forEach(function(s){(s.recent_analyses||[]).forEach(function(r){if(r.model_name)models.add(r.model_name)})});
  sel.innerHTML='<option value="">'+t("emwl.allModels")+'</option>';Array.from(models).sort().forEach(function(m){sel.innerHTML+='<option value="'+esc(m)+'">'+esc(m)+'</option>'});
}
var emwlExportBtn=document.getElementById("emwl-export-btn");
if(emwlExportBtn){emwlExportBtn.addEventListener("click",function(){exportBatchVerdict("emwl")});populateExportModels("emwl")}
var idxExportBtn=document.getElementById("idx-export-btn");
if(idxExportBtn){idxExportBtn.addEventListener("click",function(){exportBatchVerdict("idx")});populateExportModels("idx")}

// ── Tab-aware panel resolution ──
function getTabPanel(listEl) {
  var isIdx = (listEl === idxList);
  return {
    panel:   document.getElementById(isIdx ? "idx-analysis-panel" : "emwl-analysis-panel"),
    title:   document.getElementById(isIdx ? "idx-analysis-title" : "emwl-analysis-title"),
    body:    document.getElementById(isIdx ? "idx-analysis-body" : "emwl-analysis-body"),
    prefix:  isIdx ? "idx" : "emwl",
    rightFn: isIdx ? function(code) { showEmwlRightPanel(code); } : showEmwlRightPanel,  // shared right-panel for now
  };
}

// V2: Analyze previously-bullish stocks with ALL enabled models (multi-model)
function runTwoPassV2(bullishCodes, listEl, statusEl, modelParamsFn, renderFn, updateBtnsFn, stocksArray) {
  if (twoPassRunning) { twoPassStop = true; return; }
  twoPassRunning = true; twoPassStop = false;
  var newResults = {}; // code → [{verdict, model_name, analysis}, ...]
  var tab = getTabPanel(listEl);

  // Collect all enabled model param sets
  var modelQSList = [];
  var isIdx = listEl === idxList;
  if (isIdx) {
    modelQSList.push({label:t("twopass.modelN",{n:1}),qs:getIdxModelParams()});
    if(isIdxDualEnabled())modelQSList.push({label:t("twopass.modelN",{n:2}),qs:getIdxModel2Params()});
    if(isIdxTriEnabled())modelQSList.push({label:t("twopass.modelN",{n:3}),qs:getIdxModel3Params()});
    if(isIdxQuadEnabled())modelQSList.push({label:t("twopass.modelN",{n:4}),qs:getIdxModel4Params()});
    if(isIdxPentaEnabled())modelQSList.push({label:t("twopass.modelN",{n:5}),qs:getIdxModel5Params()});
    if(isIdxHexaEnabled())modelQSList.push({label:t("twopass.modelN",{n:6}),qs:getIdxModel6Params()});
    if(isIdxHeptaEnabled())modelQSList.push({label:t("twopass.modelN",{n:7}),qs:getIdxModel7Params()});
    if(isIdxOctaEnabled())modelQSList.push({label:t("twopass.modelN",{n:8}),qs:getIdxModel8Params()});
  } else {
    modelQSList.push({label:t("twopass.modelN",{n:1}),qs:getEmwlModelParams()});
    if(isEmwlDualEnabled())modelQSList.push({label:t("twopass.modelN",{n:2}),qs:getEmwlModel2Params()});
    if(isEmwlTriEnabled())modelQSList.push({label:t("twopass.modelN",{n:3}),qs:getEmwlModel3Params()});
    if(isEmwlQuadEnabled())modelQSList.push({label:t("twopass.modelN",{n:4}),qs:getEmwlModel4Params()});
    if(isEmwlPentaEnabled())modelQSList.push({label:t("twopass.modelN",{n:5}),qs:getEmwlModel5Params()});
    if(isEmwlHexaEnabled())modelQSList.push({label:t("twopass.modelN",{n:6}),qs:getEmwlModel6Params()});
    if(isEmwlHeptaEnabled())modelQSList.push({label:t("twopass.modelN",{n:7}),qs:getEmwlModel7Params()});
    if(isEmwlOctaEnabled())modelQSList.push({label:t("twopass.modelN",{n:8}),qs:getEmwlModel8Params()});
  }
  var modelCount = modelQSList.length;

  function updatePanel(msg) { if(tab.panel){tab.panel.classList.remove("hidden");if(tab.title)tab.title.textContent=t("twopass.v2Title",{n:modelCount});if(tab.body)tab.body.innerHTML=msg;} }

  function buildDownloadData() {
    var nowStr=new Date().toISOString().slice(0,19).replace("T"," ");
    var md=t("twopass.mdHeader",{now:nowStr});
    var csv=t("twopass.csvHeader");
    var jsonRows=[];
    bullishCodes.forEach(function(b){
      var nr=(newResults[b.code]||[{}])[0];
      var name=b.name||"",ps=b.price?b.price.toFixed(2):"-",cs=(b.change_pct!=null)?((b.change_pct>=0?"+":"")+b.change_pct.toFixed(2)+"%"):"-";
      var agree=nr.verdict===b.prevVerdict?t("twopass.agree"):t("twopass.disagree");
      md+="| "+b.code+" | "+name+" | "+ps+" | "+cs+" | "+(b.prevModel||"?")+" | "+(b.prevVerdict||"看多")+" | "+(nr.model_name||"?")+" | "+(nr.verdict||"?")+" | "+agree+" |\n";
      csv+=b.code+","+name+","+ps+","+cs+","+(b.prevModel||"?")+","+(b.prevVerdict||"看多")+","+(nr.model_name||"?")+","+(nr.verdict||"?")+","+agree.replace("✅ ","").replace("❌ ","")+"\n";
      jsonRows.push({code:b.code,name:name,price:b.price,change_pct:b.change_pct,prevModel:b.prevModel,prevVerdict:b.prevVerdict,newModel:nr.model_name||"?",newVerdict:nr.verdict||"?",agree:nr.verdict===b.prevVerdict});
    });
    return {md:md,csv:csv,json:JSON.stringify(jsonRows,null,2)};
  }
  function addDownloadButtons(html){
    var dlData=buildDownloadData();setTwoPassBodyData(tab.prefix,dlData);
    return html+'<div style="margin-top:8px;display:flex;gap:6px;align-items:center;"><span style="font-size:10px;color:var(--text-muted);">'+t("twopass.download")+'</span><button onclick="downloadTwoPassBlob(\''+tab.prefix+'\',\'md\')" class="btn secondary" style="font-size:10px;padding:2px 8px;">MD</button><button onclick="downloadTwoPassBlob(\''+tab.prefix+'\',\'csv\')" class="btn secondary" style="font-size:10px;padding:2px 8px;">CSV</button><button onclick="downloadTwoPassBlob(\''+tab.prefix+'\',\'json\')" class="btn secondary" style="font-size:10px;padding:2px 8px;">JSON</button><button onclick="downloadTwoPassDocx(\''+tab.prefix+'\')" class="btn secondary" style="font-size:10px;padding:2px 8px;">DOCX</button></div>';
  }

  // Worker-queue: each model picks next stock from queue (like batch)
  var nextIdx=0,totalStocks=bullishCodes.length,running=modelCount;
  function getNext(){if(twoPassStop||nextIdx>=totalStocks)return null;var c=bullishCodes[nextIdx];nextIdx++;return c}
  function updateTwoPassStatus(){
    var line=t("twopass.v2Status",{n:modelCount,done:Math.min(nextIdx,totalStocks),total:totalStocks});
    _twoPassWorkers.forEach(function(w,i){if(w.code)line+=" "+modelQSList[i].label+" "+esc(w.code)});
    line+=" <button onclick=\"twoPassStop=true;\" style=\"font-size:10px;padding:1px 6px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;\">"+t("common.stop")+"</button>";
    if(statusEl)statusEl.innerHTML=line;
  }
  var _twoPassWorkers=modelQSList.map(function(m){return {code:"",qs:m.qs,label:m.label}});
  function finishTwoPass(){
    twoPassRunning=false;
    var html="<div style=\"font-size:11px;\"><b>"+t("twopass.v2Complete")+"</b><br>"+t("twopass.reviewedCount",{n:modelCount,m:bullishCodes.length});
    html+="<table style=\"width:100%;font-size:9px;margin-top:6px;border-collapse:collapse;\">";
    html+="<tr style=\"color:var(--text-muted);\"><th>"+t("twopass.thCode")+"</th><th>"+t("twopass.thName")+"</th><th>"+t("twopass.thPrice")+"</th><th>"+t("twopass.thChange")+"</th><th>"+t("twopass.thPrevModel")+"</th><th>"+t("twopass.thPrevVerdict")+"</th><th>"+t("twopass.thNewModel")+"</th><th>"+t("twopass.thNewVerdict")+"</th><th>"+t("twopass.thAgree")+"</th></tr>";
    bullishCodes.forEach(function(b){
      var nr=(newResults[b.code]||[{}])[0];var agree=nr.verdict===b.prevVerdict;var bg=agree?"#dafbe1":"#ffebe9";
      html+="<tr style=\"background:"+bg+";\"><td>"+esc(b.code)+"</td><td style=\"max-width:60px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;\">"+esc(b.name||"")+"</td><td style=\"text-align:right;\">"+(b.price?b.price.toFixed(2):"-")+"</td><td style=\"text-align:right;color:"+((b.change_pct||0)>=0?"var(--green)":"var(--red)")+";\">"+((b.change_pct!=null)?((b.change_pct>=0?"+":"")+b.change_pct.toFixed(2)+"%"):"-")+"</td><td style=\"font-size:8px;\">"+esc(b.prevModel||"?")+"</td><td style=\"color:var(--green);\">"+esc(b.prevVerdict||"?")+"</td><td style=\"font-size:8px;\">"+esc(nr.model_name||"?")+"</td><td style=\"color:"+(nr.verdict==="看多"?"var(--green)":"var(--red)")+";\">"+esc(nr.verdict||"?")+"</td><td>"+(agree?"<b style=\"color:var(--green);\">"+t("twopass.agree")+"</b>":"<b style=\"color:var(--red);\">"+t("twopass.disagree")+"</b>")+"</td></tr>";
    });
    html+="</table>";html=addDownloadButtons(html);html+="</div>";updatePanel(html);
    // Save this two-pass run as a record (surfaced on Advisory left list + as agent tools)
    try {
      var recStocks=bullishCodes.map(function(b){
        var nr=(newResults[b.code]||[{}])[0];
        return {code:b.code,name:b.name||"",price:b.price||0,change_pct:b.change_pct||0,
                prevModel:b.prevModel||"",prevVerdict:b.prevVerdict||"",
                newModel:nr.model_name||"",newVerdict:nr.verdict||"",analysis:nr.analysis||""};
      });
      fetch("/api/twopass/records",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify({tab:tab.prefix,model_count:modelCount,stocks:recStocks})})
        .then(function(){loadTwoPassRecords();})
        .catch(function(e){console.warn("save two-pass record failed",e)});
    } catch(e) { console.warn("save two-pass record error",e); }
    if(renderFn)renderFn(stocksArray);if(updateBtnsFn)updateBtnsFn();
  }
  function worker(wi){
    function run(){
      if(twoPassStop){wi.code="";running--;if(running<=0)finishTwoPass();return}
      var b=getNext();if(!b){wi.code="";running--;if(running<=0)finishTwoPass();return}
      wi.code=b.code;updateTwoPassStatus();if(tab.rightFn)tab.rightFn(b.code);
      newResults[b.code]=[];
      fetch("/api/eastmoney/watchlist/analyze/"+encodeURIComponent(b.code)+(wi.qs?"?"+wi.qs:""))
        .then(function(r){return r.json()}).then(function(d){
          var v=(d.verdict||"")||"观望";newResults[b.code]=[{verdict:v,model_name:d.model_name||wi.label,analysis:d.analysis||""}];
          updateIdxOrEmwlVerdictCell(b.code,v,listEl);
          // Update verdict cache for persistence in table
          if(listEl===emwlList){emwlVerdictCache[b.code]=v}
          else{idxVerdictCache[b.code]=v}
          // Show result in analysis panel
          var p2=document.getElementById(tab.prefix+"-analysis-panel");if(p2){p2.classList.remove("hidden");var t2=document.getElementById(tab.prefix+"-analysis-title");if(t2)t2.textContent=b.code+" — "+verdictText(v)+(d.model_name?" 🤖 "+d.model_name:"");var b2=document.getElementById(tab.prefix+"-analysis-body");if(b2)b2.innerHTML='<div style="margin-bottom:8px;"><b>'+t(tab.prefix+".llmConclusion")+'</b><br>'+esc(d.analysis||t(tab.prefix+".analysisUnavailable"))+'</div>'}
        }).catch(function(){newResults[b.code]=[{verdict:"?",model_name:wi.label+t("twopass.failSuffix"),analysis:""}]})
        .finally(function(){wi.code="";if(!twoPassStop)setTimeout(run,200);else{running--;if(running<=0)finishTwoPass()}});
    }
    run();
  }
  updatePanel("<div style=\"font-size:11px;\"><b>"+t("twopass.v2StartTitle",{n:modelCount})+"</b><br>"+t("twopass.v2StartDetail",{n:modelCount,m:bullishCodes.length})+"</div>");
  _twoPassWorkers.forEach(function(w){worker(w)});
}

// ── Download helpers for two-pass results ──
function downloadTwoPassBlob(prefix, format) {
  var el = document.getElementById(prefix + "-analysis-body");
  var dlData = el ? el._dlData : null;
  if (!dlData) { console.warn("No download data available"); return; }
  var content = dlData[format] || "";
  if (!content) return;
  var mimeMap = {md: "text/markdown", csv: "text/csv", json: "application/json"};
  var now = new Date().toISOString().slice(0,10);
  var blob = new Blob(["﻿" + content], {type: (mimeMap[format] || "text/plain") + ";charset=utf-8"});
  var url = URL.createObjectURL(blob);
  var a = document.createElement("a");
  a.href = url; a.download = prefix + "-twopass-" + now + "." + format;
  document.body.appendChild(a); a.click();
  setTimeout(function() { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
}
function downloadTwoPassDocx(prefix) {
  var el = document.getElementById(prefix + "-analysis-body");
  var dlData = el ? el._dlData : null;
  if (!dlData || !dlData.json) { console.warn("No DOCX data"); return; }
  fetch("/api/eastmoney/watchlist/twopass/download?format=docx", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: dlData.json
  }).then(function(r) {
    if (!r.ok) throw new Error("DOCX generation failed: " + r.status);
    return r.blob();
  }).then(function(blob) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url; a.download = prefix + "-twopass-" + new Date().toISOString().slice(0,10) + ".docx";
    document.body.appendChild(a); a.click();
    setTimeout(function() { document.body.removeChild(a); URL.revokeObjectURL(url); }, 100);
  }).catch(function(e) { console.error("DOCX download failed:", e); });
}

// Store download data on the body element for access by download helpers
function setTwoPassBodyData(prefix, dlData) {
  var el = document.getElementById(prefix + "-analysis-body");
  if (el) el._dlData = dlData;
}

// ── Close button ──
var idxClose = document.getElementById("idx-analysis-close");
if (idxClose) idxClose.addEventListener("click", function() { var p = document.getElementById("idx-analysis-panel"); if (p) p.classList.add("hidden"); });

// ══════════════════════════════════════════════════════════════════════
// Two-Pass Analysis: qwen3:32b (all) → deepseek-v4-pro (看多 only)
// Shared by both eastmoney-wl and index-stocks tabs.
// ══════════════════════════════════════════════════════════════════════

var twoPassRunning = false;
var twoPassStop = false;

function runTwoPassAnalysis(codes, listEl, statusEl, modelSelectFn, renderFn, updateBtnsFn, stocksArray) {
  // modelSelectFn(provider, model) — sets the model selector
  // renderFn() — re-renders the table
  // updateBtnsFn() — updates batch button states
  if (twoPassRunning) { twoPassStop = true; return; }
  if (!codes || !codes.length) { if (statusEl) statusEl.textContent = "没有可分析的股票"; return; }

  twoPassRunning = true;
  twoPassStop = false;
  var bullishCodes = [];
  var pass1Results = {};  // code → {verdict, model_name, analysis}
  var pass2Results = {};  // code → {verdict, model_name, analysis}
  var tab = getTabPanel(listEl);
  var panel = tab.panel; var title = tab.title; var body = tab.body;

  function updatePanel(msg) {
    if (panel) { panel.classList.remove("hidden"); if (title) title.textContent = "🔬 二次分析"; if (body) body.innerHTML = msg; }
  }

  // ── Pass 1: qwen3:32b on all stocks ──
  function runPass1(idx) {
    if (twoPassStop || idx >= codes.length) {
      // Pass 1 complete
      bullishCodes = Object.keys(pass1Results).filter(function(c) { return pass1Results[c].verdict === "看多"; });
      updatePanel('<div style="font-size:11px;"><b>第一轮完成</b>: ' + codes.length + ' 只 → <span style="color:var(--green);">看多 ' + bullishCodes.length + ' 只</span><br>第二轮: deepseek-v4-pro 复核看多股票...</div>');
      if (bullishCodes.length === 0) {
        updatePanel('<div style="font-size:11px;"><b>第一轮完成</b>: ' + codes.length + ' 只 → <span style="color:#888;">无看多股票</span>，跳过第二轮。</div>');
        twoPassRunning = false;
        if (renderFn) renderFn(stocksArray);
        if (updateBtnsFn) updateBtnsFn();
        return;
      }
      setTimeout(function() { runPass2(0); }, 500);
      return;
    }
    var code = codes[idx];
    if (statusEl) statusEl.innerHTML = '🔬 第一轮(qwen3:32b): <b>' + (idx + 1) + '/' + codes.length + '</b> — ' + esc(code) + ' <button onclick="twoPassStop=true;" style="font-size:10px;padding:1px 6px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;">⏹ 停止</button>';
    showEmwlRightPanel(code);
    fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + "?provider=ollama&model=qwen3:32b")
      .then(function(r) { return r.json(); }).then(function(d) {
        var v = (d.verdict || "") || "观望";
        pass1Results[code] = {verdict: v, model_name: d.model_name || "ollama/qwen3:32b", analysis: d.analysis || ""};
        if (v === "看多") bullishCodes.push(code);
        // Update verdict in table
        updateIdxOrEmwlVerdictCell(code, v, listEl);
      }).catch(function() { pass1Results[code] = {verdict: "观望", model_name: "ollama/qwen3:32b", analysis: ""}; })
      .finally(function() { setTimeout(function() { runPass1(idx + 1); }, 300); });
  }

  // ── Pass 2: deepseek-v4-pro on 看多 stocks ──
  function runPass2(idx) {
    if (twoPassStop || idx >= bullishCodes.length) {
      // All done — show consensus list
      var bothBullish = [];
      bullishCodes.forEach(function(c) {
        var p2 = pass2Results[c];
        if (p2 && p2.verdict === "看多") bothBullish.push(c);
      });
      var html = '<div style="font-size:11px;">';
      html += '<b>🔬 二次分析完成</b><br>';
      html += '第一轮(qwen3:32b): ' + codes.length + ' 只 → 看多 ' + bullishCodes.length + ' 只<br>';
      html += '第二轮(deepseek-v4-pro): 复核 ' + bullishCodes.length + ' 只 → 看多 ' + bothBullish.length + ' 只<br>';
      html += '<div style="margin-top:6px;padding:6px;background:#dafbe1;border-radius:4px;font-weight:700;color:var(--green);">✅ 双模型一致看多: ' + bothBullish.length + ' 只</div>';
      html += '<table style="width:100%;font-size:10px;margin-top:4px;">';
      html += '<tr><th>代码</th><th>qwen3结论</th><th>deepseek结论</th><th>一致</th></tr>';
      bullishCodes.forEach(function(c) {
        var p1 = pass1Results[c] || {};
        var p2 = pass2Results[c] || {};
        var agree = (p2.verdict === "看多") ? "✅" : "❌";
        html += '<tr><td>' + esc(c) + '</td><td>' + esc(p1.verdict||"?") + '</td><td>' + esc(p2.verdict||"?") + '</td><td>' + agree + '</td></tr>';
      });
      html += '</table></div>';
      updatePanel(html);
      twoPassRunning = false;
      if (renderFn) renderFn(stocksArray);
      if (updateBtnsFn) updateBtnsFn();
      return;
    }
    var code = bullishCodes[idx];
    if (statusEl) statusEl.innerHTML = '🔬 第二轮(deepseek-v4-pro): <b>' + (idx + 1) + '/' + bullishCodes.length + '</b> — ' + esc(code) + ' <button onclick="twoPassStop=true;" style="font-size:10px;padding:1px 6px;background:var(--red);color:#fff;border:none;border-radius:3px;cursor:pointer;">⏹ 停止</button>';
    showEmwlRightPanel(code);
    fetch("/api/eastmoney/watchlist/analyze/" + encodeURIComponent(code) + "?provider=deepseek&model=deepseek-v4-pro")
      .then(function(r) { return r.json(); }).then(function(d) {
        var v = (d.verdict || "") || "观望";
        pass2Results[code] = {verdict: v, model_name: d.model_name || "deepseek/deepseek-v4-pro", analysis: d.analysis || ""};
        updateIdxOrEmwlVerdictCell(code, v, listEl);
      }).catch(function() { pass2Results[code] = {verdict: "观望", model_name: "deepseek/deepseek-v4-pro", analysis: ""}; })
      .finally(function() { setTimeout(function() { runPass2(idx + 1); }, 500); });
  }

  updatePanel('<div style="font-size:11px;"><b>🔬 二次分析启动</b><br>第一轮: qwen3:32b 分析全部 ' + codes.length + ' 只<br>第二轮: deepseek-v4-pro 复核看多股票</div>');
  runPass1(0);
}

function updateIdxOrEmwlVerdictCell(code, verdict, listEl) {
  var row = listEl.querySelector('[data-code="' + code + '"]');
  if (!row) return;
  var cells = row.querySelectorAll("td");
  // 结论 is col 8 in both table layouts
  if (cells.length >= 9) {
    cells[8].textContent = verdictDisplay(verdict);
    cells[8].style.color = VERDICT_COLOR[verdict] || "#888";
    cells[8].style.fontWeight = "600";
  }
  // Also update cache
  if (listEl === idxList) {
    idxVerdictCache[code] = verdict;
  } else {
    emwlVerdictCache[code] = verdict;
  }
}

// Show historical analyses for a stock (no new LLM call)
function showEmwlHistory(code) {
  var panel = document.getElementById("emwl-analysis-panel");
  var title = document.getElementById("emwl-analysis-title");
  var body = document.getElementById("emwl-analysis-body");
  if (!panel) return;
  panel.classList.remove("hidden");
  title.textContent = t("emwl.historyTitle", {code: code});

  // Also show latest cached verdict if any
  if (emwlVerdictCache[code]) {
    title.textContent = t("emwl.historyCachedTitle", {code: code, verdict: verdictDisplay(emwlVerdictCache[code])});
  }

  body.innerHTML = '<span style="color:var(--text-muted);">' + t("emwl.loadingHistory") + '</span>';

  fetch("/api/eastmoney/watchlist/returns/" + encodeURIComponent(code))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var returns = (d && d.returns) || [];
      if (!returns.length) {
        body.innerHTML = '<div style="font-size:11px;color:var(--text-muted);">' + t("emwl.noHistoryHint") + '</div>';
        return;
      }
      // Model consensus: compare last 3 verdicts
      var verdicts3 = returns.slice(0, 3).map(function(r) { return r.verdict || ''; }).filter(Boolean);
      var uniqueV3 = []; verdicts3.forEach(function(v) { if (uniqueV3.indexOf(v) === -1) uniqueV3.push(v); });
      var consensusBadge = '';
      if (verdicts3.length >= 2) {
        var bg = uniqueV3.length === 1 ? '#dafbe1' : (uniqueV3.length === 2 ? '#fef3e2' : '#ffebe9');
        var fg = uniqueV3.length === 1 ? 'var(--green)' : (uniqueV3.length === 2 ? '#d97706' : 'var(--red)');
        var lb = uniqueV3.length === 1 ? t("emwl.consensusSame", {v: esc(uniqueV3[0])}) : (uniqueV3.length === 2 ? t("emwl.consensusPartial", {v: esc(uniqueV3.join('/'))}) : t("emwl.consensusSevere", {v: esc(uniqueV3.join('/'))}));
        consensusBadge = '<div style="margin-bottom:4px;padding:3px 8px;border-radius:3px;background:' + bg + ';font-weight:700;font-size:10px;color:' + fg + ';">' + lb + '</div>';
      }
      var html = '<div style="font-size:10px;line-height:1.7;">';
      html += '<div style="font-weight:700;margin-bottom:4px;">' + t("emwl.historyCount", {n: returns.length}) + '</div>';
      html += consensusBadge;
      returns.forEach(function(r, i) {
        var at = (r.analyzed_at || "").replace("T", " ").slice(0, 16);
        var verdictTxt = r.verdict || "";
        var vc = verdictTxt.includes("看多") ? "var(--green)" : (verdictTxt.includes("看空") ? "var(--red)" : "#888");
        var retStr = (r.return_pct >= 0 ? "+" : "") + (r.return_pct != null ? r.return_pct.toFixed(2) : "0.00") + "%";
        var rc = (r.return_pct || 0) >= 0 ? "var(--green)" : "var(--red)";
        var modelLabel = r.model_name ? '<span style="color:var(--text-muted);font-size:9px;">🤖 ' + esc(r.model_name) + '</span>' : '';
        html += '<div style="margin-bottom:6px;padding:6px;border:1px solid var(--border);border-radius:4px;background:var(--bg);">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
        html += '<span style="font-weight:600;">#' + (i + 1) + ' <span style="color:' + vc + ';">' + esc(verdictTxt ? verdictDisplay(verdictTxt) : verdictDisplay("观望")) + '</span></span>';
        html += '<span style="font-size:9px;color:var(--text-muted);">' + esc(at) + '</span>';
        html += '</div>';
        html += '<div style="margin-top:2px;">' + modelLabel + '</div>';
        html += '<div style="font-size:9px;color:var(--text-muted);">' + t("emwl.buyPrice") + ' ' + esc(String(r.buy_price || "?")) + ' → ' + t("emwl.current") + ' ' + esc(String(r.sell_price || "?")) + ' | ' + t("emwl.returnLabel") + ' <span style="color:' + rc + ';font-weight:600;">' + retStr + '</span></div>';
        html += '<details style="margin-top:3px;"><summary style="cursor:pointer;font-size:9px;color:var(--accent);">' + t("emwl.details") + '</summary>';
        html += '<div style="font-size:10px;margin-top:4px;white-space:pre-wrap;">' + esc(r.analysis || t("emwl.none")) + '</div></details>';
        html += '<button class="btn emwl-del-analysis" data-id="' + (r.analysis_id || "") + '" data-code="' + esc(code) + '" style="font-size:8px;padding:1px 4px;color:var(--red);margin-top:2px;">' + t("emwl.deleteRecord") + '</button>';
        html += '</div>';
      });
      html += '</div>';
      body.innerHTML = html;
      // Wire delete buttons in history view
      body.querySelectorAll(".emwl-del-analysis").forEach(function(btn) {
        btn.addEventListener("click", function(e) {
          e.stopPropagation();
          var aid = btn.dataset.id;
          if (!aid || !confirm(t("emwl.deleteAnalysisConfirm"))) return;
          fetch("/api/eastmoney/watchlist/analysis/" + encodeURIComponent(aid), {method: "DELETE"})
            .then(function(r) {
              if (r.ok) { showEmwlHistory(code); updateBatchButtons(); }
              else alert(t("emwl.deleteFailed"));
            })
            .catch(function(e) { alert(t("emwl.deleteFailedPrefix") + e.message); });
        });
      });
    })
    .catch(function(e) {
      body.innerHTML = '<span style="color:var(--red);">' + t("emwl.loadFailed") + esc(e.message) + '</span>';
    });
}

// --- PM Q&A Chat Panel ---
let chatPanel = null;
let chatMessages = null;

function showChatPanel(message) {
  if (chatPanel) chatPanel.remove();

  chatPanel = document.createElement("div");
  chatPanel.id = "chat-panel";
  chatPanel.className = "chat-panel";
  chatPanel.innerHTML =
    '<div class="chat-header">' +
    '<span>' + t("chat.pmQA") + '</span>' +
    '<button id="chat-close" class="chat-close-btn">&times;</button>' +
    '</div>' +
    '<div id="chat-messages" class="chat-messages"></div>' +
    '<div class="chat-input-area">' +
    '<input type="text" id="chat-input" class="chat-input" placeholder="' + t("chat.askPM") + '" />' +
    '<button id="chat-send" class="chat-send-btn">' + t("chat.send") + '</button>' +
    '</div>';

  document.getElementById("right-panel").appendChild(chatPanel);

  chatMessages = document.getElementById("chat-messages");

  if (message) {
    appendChatMessage("system", message);
  }

  document.getElementById("chat-close").addEventListener("click", function() {
    closeChatPanel();
    if (eventSource) { eventSource.close(); eventSource = null; }
  });
  document.getElementById("chat-send").addEventListener("click", sendChatMessage);
  document.getElementById("chat-input").addEventListener("keydown", function(e) {
    if (e.key === "Enter") sendChatMessage();
  });
}

function closeChatPanel() {
  if (chatPanel) chatPanel.remove();
  chatPanel = null;
  chatMessages = null;
}

function showChatThinking() {
  if (!chatMessages) return;
  var existingThinking = chatMessages.querySelector(".chat-msg.thinking");
  if (existingThinking) existingThinking.remove();
  var div = document.createElement("div");
  div.className = "chat-msg pm thinking";
  div.textContent = t("chat.thinking");
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendChatMessage(role, text) {
  if (!chatMessages) return;
  var existingThinking = chatMessages.querySelector(".chat-msg.thinking");
  if (existingThinking) existingThinking.remove();
  if (text) {
    var div = document.createElement("div");
    div.className = "chat-msg " + role;
    if (role === "pm" || role === "assistant") {
      div.innerHTML = '<div class="markdown-body">' + renderMarkdown(text) + '</div>';
    } else {
      div.textContent = text;
    }
    chatMessages.appendChild(div);
  }
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function sendChatMessage() {
  if (!chatPanel || !sessionId) return;
  var input = document.getElementById("chat-input");
  if (!input) return;
  var text = input.value.trim();
  if (!text) return;

  appendChatMessage("user", text);
  input.value = "";
  input.disabled = true;
  document.getElementById("chat-send").disabled = true;

  showChatThinking();

  fetch("/api/chat/" + sessionId, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question: text }),
  }).then(function(resp) {
    if (!resp.ok) {
      appendChatMessage("system", "Error: " + resp.status);
    }
  }).catch(function(e) {
    appendChatMessage("system", "Error: " + e.message);
  }).finally(function() {
    if (input) input.disabled = false;
    var btn = document.getElementById("chat-send");
    if (btn) btn.disabled = false;
    if (input) input.focus();
  });
}

// ---- History Chat Panel ----
var hcpRunId = null;
var hcpActiveThreadId = null;
var hcpPanel = null;
var hcpMessages = null;
var hcpThreadList = null;
var hcpEventSource = null;

function showHistoryChatPanel(runId) {
  if (hcpPanel) closeHistoryChatPanel();
  hcpRunId = runId;

  hcpPanel = document.createElement("div");
  hcpPanel.id = "history-chat-panel";
  hcpPanel.className = "history-chat-panel";
  hcpPanel.innerHTML =
    '<div class="history-chat-header">' +
    '<span>' + t("chat.pmChat") + '</span>' +
    '<div style="display:flex;gap:8px;">' +
    '<button id="hcp-download" class="btn secondary" style="padding:3px 10px;font-size:11px;" title="' + t("chat.downloadMd") + '">&#x21e9; MD</button>' +
    '<button id="hcp-download-json" class="btn secondary" style="padding:3px 10px;font-size:11px;" title="' + t("chat.downloadJson") + '">&#x21e9; JSON</button>' +
    '<button id="hcp-new-thread" class="btn secondary" style="padding:3px 10px;font-size:11px;">' + t("chat.newThread") + '</button>' +
    '<button id="hcp-close" class="chat-close-btn">&times;</button>' +
    '</div>' +
    '</div>' +
    '<div class="history-chat-body">' +
    '<div id="hcp-thread-list" class="hcp-thread-list"></div>' +
    '<div class="hcp-main">' +
    '<div id="hcp-messages" class="hcp-messages">' +
    '<span class="no-results" style="padding:20px;">' + t("chat.selectThread") + '</span>' +
    '</div>' +
    '<div class="hcp-input-area">' +
    '<input type="text" id="hcp-input" class="chat-input" placeholder="' + t("chat.askPM") + '" disabled />' +
    '<button id="hcp-send" class="chat-send-btn" disabled>' + t("chat.send") + '</button>' +
    '</div>' +
    '</div>' +
    '</div>';

  document.getElementById("right-panel").appendChild(hcpPanel);

  hcpMessages = document.getElementById("hcp-messages");
  hcpThreadList = document.getElementById("hcp-thread-list");

  document.getElementById("hcp-close").addEventListener("click", closeHistoryChatPanel);
  document.getElementById("hcp-new-thread").addEventListener("click", function() {
    createAndOpenThread(runId);
  });
  document.getElementById("hcp-download").addEventListener("click", function() {
    if (hcpActiveThreadId) downloadChatThread(runId, hcpActiveThreadId, "md");
  });
  document.getElementById("hcp-download-json").addEventListener("click", function() {
    if (hcpActiveThreadId) downloadChatThread(runId, hcpActiveThreadId, "json");
  });
  document.getElementById("hcp-send").addEventListener("click", sendHistoryChatMessage);
  document.getElementById("hcp-input").addEventListener("keydown", function(e) {
    if (e.key === "Enter") sendHistoryChatMessage();
  });

  loadHistoryChatThreads(runId);
}

function closeHistoryChatPanel() {
  if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }
  if (hcpPanel) { hcpPanel.remove(); hcpPanel = null; }
  hcpMessages = null;
  hcpThreadList = null;
  hcpRunId = null;
  hcpActiveThreadId = null;
}

function downloadChatThread(runId, threadId, format) {
  var url = "/api/results/" + encodeURIComponent(runId) + "/chat/threads/" + encodeURIComponent(threadId) + "/download?format=" + format;
  var a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function loadHistoryChatThreads(runId) {
  fetch("/api/results/" + runId + "/chat/threads")
    .then(function(r) { return r.json(); })
    .then(function(threads) { renderHistoryChatThreads(threads); })
    .catch(function() {
      if (hcpThreadList) hcpThreadList.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">' + t("chat.threadsFailed") + ' <a href="#" onclick="createAndOpenThread(\'' + runId + '\');return false;">' + t("chat.createNow") + '</a></span>';
    });
}

function renderHistoryChatThreads(threads) {
  if (!hcpThreadList) return;
  if (!threads || !threads.length) {
    hcpThreadList.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">' + t("chat.creatingConversation") + '</span>';
    createAndOpenThread(hcpRunId);
    return;
  }
  // Auto-select the most recent thread if none is active yet
  if (!hcpActiveThreadId && threads.length > 0) {
    openHistoryChatThread(hcpRunId, threads[0].thread_id);
    return;
  }
  var html = "";
  threads.forEach(function(th) {
    var title = th.title || t("chat.newConversation");
    if (title.length > 30) title = title.substring(0, 30) + "...";
    var activeClass = (th.thread_id === hcpActiveThreadId) ? " active" : "";
    html +=
      '<div class="hcp-thread-item' + activeClass + '" data-thread-id="' + esc(th.thread_id) + '">' +
      '<span class="hcp-thread-title">' + esc(title) + '</span>' +
      '<span class="hcp-thread-meta">' + t("chat.msgsShort", {n: esc(String(th.message_count || "0"))}) + '</span>' +
      '<button class="hcp-thread-delete" data-thread-id="' + esc(th.thread_id) + '" title="' + t("chat.delete") + '">&times;</button>' +
      '</div>';
  });
  hcpThreadList.innerHTML = html;

  hcpThreadList.querySelectorAll(".hcp-thread-item").forEach(function(el) {
    el.addEventListener("click", function(e) {
      if (e.target.classList.contains("hcp-thread-delete")) return;
      openHistoryChatThread(hcpRunId, el.dataset.threadId);
    });
  });

  hcpThreadList.querySelectorAll(".hcp-thread-delete").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      var tid = btn.dataset.threadId;
      if (!confirm(t("chat.deleteConfirm"))) return;
      fetch("/api/results/" + hcpRunId + "/chat/threads/" + tid, { method: "DELETE" })
        .then(function(r) {
          if (r.ok) {
            if (hcpActiveThreadId === tid) {
              hcpActiveThreadId = null;
              if (hcpMessages) hcpMessages.innerHTML = '<span class="no-results" style="padding:20px;">' + t("chat.selectThreadShort") + '</span>';
            }
            loadHistoryChatThreads(hcpRunId);
          }
        });
    });
  });
}

function createAndOpenThread(runId) {
  fetch("/api/results/" + runId + "/chat/threads", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: "{}"
  })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      openHistoryChatThread(runId, data.thread_id);
      loadHistoryChatThreads(runId);
    })
    .catch(function() {
      if (hcpMessages) hcpMessages.innerHTML = '<span class="no-results" style="padding:20px;color:var(--danger);">' + t("chat.createFailed") + '</span>';
      var input = document.getElementById("hcp-input");
      var sendBtn = document.getElementById("hcp-send");
      if (input) { input.disabled = true; input.placeholder = t("chat.createFailedPh"); }
      if (sendBtn) sendBtn.disabled = true;
    });
}

function openHistoryChatThread(runId, threadId) {
  hcpActiveThreadId = threadId;
  var input = document.getElementById("hcp-input");
  var sendBtn = document.getElementById("hcp-send");
  if (input) input.disabled = false;
  if (sendBtn) sendBtn.disabled = false;

  fetch("/api/results/" + runId + "/chat/threads/" + threadId + "/messages")
    .then(function(r) { return r.json(); })
    .then(function(msgs) {
      if (!hcpMessages || hcpActiveThreadId !== threadId) return;
      hcpMessages.innerHTML = "";
      if (!msgs || !msgs.length) {
        hcpMessages.innerHTML = '<span class="no-results" style="padding:20px;">' + t("chat.startConversation") + '</span>';
      } else {
        msgs.forEach(function(m) {
          appendHistoryChatMessage(m.role, m.content, m.tool_calls);
        });
      }
    });

  loadHistoryChatThreads(runId);
}

function sendHistoryChatMessage() {
  if (!hcpRunId || !hcpActiveThreadId) return;
  var input = document.getElementById("hcp-input");
  var sendBtn = document.getElementById("hcp-send");
  if (!input) return;
  var text = input.value.trim();
  if (!text) return;

  appendHistoryChatMessage("user", text, "");
  input.value = "";
  input.disabled = true;
  if (sendBtn) sendBtn.disabled = true;

  appendHistoryChatThinking();

  if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }

  var url = "/api/results/" + hcpRunId + "/chat/stream?thread_id=" +
    encodeURIComponent(hcpActiveThreadId) + "&question=" + encodeURIComponent(text);
  hcpEventSource = new EventSource(url);

  hcpEventSource.addEventListener("chat-tool-call", function(e) {
    var d = safeJSON(e.data) || {};
    removeHistoryChatThinking();
    appendHistoryChatToolCall(d.tool_name);
  });

  hcpEventSource.addEventListener("chat-tool-result", function(e) {
    appendHistoryChatThinking();
  });

  hcpEventSource.addEventListener("chat-done", function(e) {
    var d = safeJSON(e.data) || {};
    removeHistoryChatThinking();
    appendHistoryChatMessage("assistant", d.full_response || "", "");
    if (input) input.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
    if (input) input.focus();
    if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }
    loadHistoryChatThreads(hcpRunId);
  });

  hcpEventSource.addEventListener("chat-error", function(e) {
    var d = safeJSON(e.data) || {};
    removeHistoryChatThinking();
    appendHistoryChatMessage("system", t("chat.errorPrefix") + (d.message || t("chat.unknownError")), "");
    if (input) input.disabled = false;
    if (sendBtn) sendBtn.disabled = false;
    if (hcpEventSource) { hcpEventSource.close(); hcpEventSource = null; }
  });

  hcpEventSource.onerror = function() {
    if (hcpEventSource && hcpEventSource.readyState === EventSource.CLOSED) {
      if (input) input.disabled = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  };
}

function appendHistoryChatMessage(role, content, toolCallsJson) {
  if (!hcpMessages) return;
  var div = document.createElement("div");
  div.className = "chat-msg " + role;
  if (role === "assistant" || role === "pm") {
    div.innerHTML = '<div class="markdown-body">' + renderMarkdown(content || "") + '</div>';
    if (toolCallsJson) {
      try {
        var tcs = JSON.parse(toolCallsJson);
        if (tcs && tcs.length) {
          var tcHtml = '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">' + t("chat.toolsUsed");
          tcHtml += tcs.map(function(tc) { return esc(tc.tool_name || "unknown"); }).join(", ");
          tcHtml += '</div>';
          div.innerHTML += tcHtml;
        }
      } catch(e) {}
    }
  } else if (role === "user") {
    div.textContent = content;
  } else {
    div.textContent = content;
  }
  hcpMessages.appendChild(div);
  hcpMessages.scrollTop = hcpMessages.scrollHeight;
}

function appendHistoryChatThinking() {
  if (!hcpMessages) return;
  var div = document.createElement("div");
  div.className = "chat-msg pm thinking hcp-thinking";
  div.textContent = t("chat.thinking");
  hcpMessages.appendChild(div);
  hcpMessages.scrollTop = hcpMessages.scrollHeight;
}

function removeHistoryChatThinking() {
  if (!hcpMessages) return;
  var el = hcpMessages.querySelector(".hcp-thinking");
  if (el) el.remove();
}

function appendHistoryChatToolCall(toolName) {
  if (!hcpMessages) return;
  var div = document.createElement("div");
  div.className = "chat-msg system";
  div.innerHTML = '<span style="opacity:0.7;">' + t("chat.calling", {tool: '<code>' + esc(toolName) + '</code>'}) + '</span>';
  hcpMessages.appendChild(div);
  hcpMessages.scrollTop = hcpMessages.scrollHeight;
}

// ---- Init ----
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initApp);
} else {
  initApp();
}


// ============================================================================


// HTML escape utility
function escHtml(str) {
  if (!str) return "";
  var div = document.createElement("div");
  div.appendChild(document.createTextNode(str));
  return div.innerHTML;
}


// ============================================================================
// CapitalRadar Advisor Tab - Thread-based chat with expanded right panel
// ============================================================================

var advisoryThreadId = null;
var advisoryStreaming = false;
var advisoryThreads = [];
var advisoryThreadsLoading = false;

// Web-advisor ↔ WeCom sharing controls (all persisted server-side).
//  - 「回复推微信」(web→WeChat): each completed web reply is pushed to the
//    BOUND user's personal chat (most-recent learned user when unbound).
//  - Channel select: bind the panel to one registered WeCom user.
//  - 「微信→网页」(WeChat→web): when on + bound, the panel follows that user's
//    live chat (their channel thread is opened; the 5s poll streams it in).
(function() {
  var toggle = document.getElementById("advisory-wechat-push-toggle");
  var channel = document.getElementById("advisory-wecom-channel");
  var webToggle = document.getElementById("advisory-wechat-web-toggle");
  if (toggle) {
    fetch("/api/advisory/wechat_push")
      .then(function(r) { return r.json(); })
      .then(function(d) { toggle.checked = !!d.enabled; })
      .catch(function() { /* server down/restarting: keep default (off) */ });
    toggle.addEventListener("change", function() {
      var desired = toggle.checked;
      fetch("/api/advisory/wechat_push", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled: desired})
      }).then(function(r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      }).catch(function() {
        toggle.checked = !desired;  // server didn't accept — reflect reality
      });
    });
  }
  if (!channel && !webToggle) return;

  // Populate the channel selector + reverse switch from server state, then
  // auto-follow the bound user's live chat when sharing is active. The dropdown
  // is fed by the per-user registry's `entries` (only active && mirror), so each
  // option shows 姓名(userid) and carries the row's push_reply state for the
  // bind-time auto-opt-in below.
  function loadBinding() {
    fetchJsonRetry("/api/advisory/wecom_binding", function(d) {
      if (!d) return;
      if (channel) {
        channel.innerHTML = "";
        var none = document.createElement("option");
        none.value = "";
        none.textContent = t("adv.channelNone");
        channel.appendChild(none);
        (d.entries || d.users || []).forEach(function(u) {
          if (!u.active || !u.mirror) return;
          var opt = document.createElement("option");
          opt.value = u.userid;
          opt.textContent = wecomUserLabel(u);
          opt.dataset.userid = u.userid;
          opt.dataset.pushReply = u.push_reply ? "1" : "0";
          channel.appendChild(opt);
        });
        channel.value = d.bound_user || "";
        window._wecomBoundUser = d.bound_user || "";
      }
      if (webToggle) webToggle.checked = !!d.wechat_to_web;
      if (d.bound_user && d.wechat_to_web && d.thread_id) {
        selectAdvisoryThread(d.thread_id);
      }
    }, function() {});
  }
  loadBinding();
  window.refreshWecomBinding = loadBinding;

  if (channel) {
    channel.addEventListener("change", function() {
      var user = channel.value || "";
      fetch("/api/advisory/wecom_binding", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({bound_user: user})
      }).then(function(r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      }).then(function(d) {
        // 网页独立 keeps the current thread; a real user binding opens that
        // user's channel thread so the two surfaces share a conversation.
        if (d && d.bound_user && d.wechat_to_web && d.thread_id) {
          selectAdvisoryThread(d.thread_id);
        }
        // Binding a user while 回复推微信 is ON must not silently no-op: if the
        // bound row hasn't opted into receiving web replies, turn push_reply on
        // for them (otherwise the reply push has no recipient and is dropped).
        var pushToggleEl = document.getElementById("advisory-wechat-push-toggle");
        var bound = null;
        if (d && d.entries) {
          for (var bi = 0; bi < d.entries.length; bi++) {
            if (d.entries[bi].userid === d.bound_user) { bound = d.entries[bi]; break; }
          }
        }
        if (pushToggleEl && pushToggleEl.checked && d && d.bound_user &&
            bound && !bound.push_reply) {
          fetch("/api/advisory/wecom_users", {
            method: "PATCH",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({userid: d.bound_user, push_reply: true})
          }).then(function(r) { return r.json(); }).then(function() {
            loadBinding();  // refresh entries so push_reply state matches
          }).catch(function() {});
        }
      }).catch(function() {
        channel.value = "";  // server didn't accept — reflect reality
      });
    });
  }
  if (webToggle) {
    webToggle.addEventListener("change", function() {
      var desired = webToggle.checked;
      fetch("/api/advisory/wecom_binding", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({wechat_to_web: desired})
      }).then(function(r) {
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      }).then(function(d) {
        if (d && desired && d.bound_user && d.thread_id) {
          selectAdvisoryThread(d.thread_id);
        }
      }).catch(function() {
        webToggle.checked = !desired;
      });
    });
  }

  // 👥 管理企微用户 entry point in the advisory header.
  var manageBtn = document.getElementById("adv-wecom-manage-btn");
  if (manageBtn) manageBtn.addEventListener("click", function() {
    if (window.openWecomUsersManager) window.openWecomUsersManager();
  });
})();

// WeCom users manager — pre-register users + the per-user connectivity matrix.
// Rows: ● activation dot | 姓名 (editable) | userid | advisor / push_reply /
// mirror switches | delete. Placeholders (not yet active) can't be pushed or
// bound, so their switches render disabled; the grey dot tooltip explains that
// the row auto-activates on the user's first message to the bot. Every mutation
// refreshes the binding dropdown and the task-form push-user checkboxes.
(function() {
  var overlay = null;

  function closeManager() {
    if (overlay) { overlay.remove(); overlay = null; }
  }
  window.closeWecomManager = closeManager;

  function openManager() {
    if (overlay) { overlay.remove(); overlay = null; }
    overlay = document.createElement("div");
    overlay.className = "interaction-overlay wecom-manager";
    overlay.id = "wecom-users-overlay";
    var box = document.createElement("div");
    box.className = "interaction-box wecom-panel";
    box.innerHTML =
      '<h3 class="interaction-title" style="display:flex;align-items:center;justify-content:space-between;gap:10px;">' +
        '<span>' + esc(t("adv.usersPanelTitle")) + '</span>' +
        '<button type="button" id="wu-close" class="btn secondary" style="font-size:11px;padding:2px 10px;white-space:nowrap;">' + esc(t("adv.usersClose")) + '</button>' +
      '</h3>' +
      '<p style="font-size:11px;color:var(--text-muted);margin:4px 0 10px 0;line-height:1.5;">' + esc(t("adv.usersPanelSub")) + '</p>' +
      '<div class="wecom-add-row">' +
        '<input id="wu-add-userid" placeholder="' + esc(t("adv.usersAddUseridPh")) + '" style="flex:1.3;min-width:140px;">' +
        '<input id="wu-add-name" placeholder="' + esc(t("adv.usersAddNamePh")) + '" style="flex:1;min-width:110px;">' +
        '<button type="button" id="wu-add-btn" class="btn primary" style="font-size:11px;padding:3px 10px;white-space:nowrap;">' + esc(t("adv.usersAdd")) + '</button>' +
      '</div>' +
      '<div class="wecom-list-head">' +
        '<span class="col-dot" title="' + esc(t("adv.usersActiveDot")) + '"></span>' +
        '<span>' + esc(t("adv.usersNameCol")) + '</span>' +
        '<span>' + esc(t("adv.usersUseridCol")) + '</span>' +
        '<span>' + esc(t("adv.usersConnCol")) + '</span>' +
        '<span></span>' +
      '</div>' +
      '<div id="wecom-users-list" class="wecom-list"><div style="color:var(--text-muted);font-size:12px;padding:12px 0;">…</div></div>';
    overlay.appendChild(box);
    document.body.appendChild(overlay);

    box.querySelector("#wu-close").addEventListener("click", closeManager);
    overlay.addEventListener("mousedown", function(ev) {
      if (ev.target === overlay) closeManager();
    });
    box.querySelector("#wu-add-btn").addEventListener("click", addUser);
    box.querySelectorAll("#wu-add-userid, #wu-add-name").forEach(function(inp) {
      inp.addEventListener("keydown", function(ev) {
        if (ev.key === "Enter") box.querySelector("#wu-add-btn").click();
      });
    });
    renderList();
  }
  window.openWecomUsersManager = openManager;

  function addUser() {
    var box = document.getElementById("wecom-users-overlay");
    if (!box) return;
    var uid = box.querySelector("#wu-add-userid").value.trim();
    var nm = box.querySelector("#wu-add-name").value.trim();
    if (!uid) { alert(t("adv.usersAddUseridPh")); return; }
    box.querySelector("#wu-add-userid").value = "";
    box.querySelector("#wu-add-name").value = "";
    apiCall("/api/advisory/wecom_users", "POST", {userid: uid, name: nm}, null, true);
  }

  // Uniform wrapper: {method, url} → JSON body or empty for GET; surfaces the
  // server's string `detail` (FastAPI HTTPException) via alert; on success runs
  // `after` then refreshes the binding dropdown + task-form user checkboxes.
  function apiCall(url, method, body, after, toast) {
    var opts = {method: method, headers: {"Content-Type": "application/json"}};
    if (body) opts.body = JSON.stringify(body);
    fetch(url, opts).then(function(r) {
      return r.json().then(function(j) { return {ok: r.ok, j: j}; });
    }).then(function(x) {
      if (!x.ok) {
        var msg = "";
        if (x.j && typeof x.j.detail === "string") msg = x.j.detail;
        else if (x.j && x.j.detail && typeof x.j.detail === "object") msg = (x.j.detail.message || JSON.stringify(x.j.detail));
        else if (x.j && x.j.errmsg) msg = x.j.errmsg;
        alert(msg || "HTTP " + (x.j && x.j.status_code) || "request failed");
        renderList();  // revert any optimistic toggle to server state
        return;
      }
      if (after) after(x.j);
      renderList();
      if (window.refreshWecomBinding) window.refreshWecomBinding();
      if (window.refreshWecomUserOptions) window.refreshWecomUserOptions();
      if (toast) showToast(t("adv.usersSaved"));
    }).catch(function(e) {
      alert(e.message);
      renderList();
    });
  }

  function renderList() {
    var list = document.getElementById("wecom-users-list");
    if (!list) return;
    list.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:12px 0;">…</div>';
    fetch("/api/advisory/wecom_users")
      .then(function(r) { return r.json(); })
      .then(function(d) {
        var rows = (d && d.users) || [];
        if (!rows.length) {
          list.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:14px 0;">' +
            esc(t("adv.usersEmpty")) + '</div>';
          return;
        }
        list.innerHTML = "";
        rows.forEach(function(u) { list.appendChild(rowEl(u)); });
      })
      .catch(function() {
        if (list) list.innerHTML = '<div style="color:var(--text-muted);font-size:12px;padding:12px 0;">…</div>';
      });
  }

  function rowEl(u) {
    var active = !!u.active;
    var row = document.createElement("div");
    row.className = "wecom-user-row" + (active ? "" : " inactive");
    row.setAttribute("data-userid", u.userid);

    var dot = document.createElement("span");
    dot.className = "wecom-dot" + (active ? " active" : "");
    dot.title = active ? t("adv.usersActiveDot") : t("adv.usersInactiveHint");
    row.appendChild(dot);

    var nameEl = document.createElement("input");
    nameEl.className = "wecom-name";
    nameEl.type = "text";
    nameEl.value = u.name || u.userid;
    nameEl.title = t("adv.usersNameCol");
    nameEl.addEventListener("change", function() {
      var nm = nameEl.value.trim();
      if (nm && nm !== (u.name || u.userid)) {
        apiCall("/api/advisory/wecom_users", "PATCH", {userid: u.userid, name: nm});
      } else if (!nm) {
        nameEl.value = u.name || u.userid;  // blank name not allowed — restore
      }
    });
    row.appendChild(nameEl);

    var uidEl = document.createElement("span");
    uidEl.className = "wecom-userid";
    uidEl.textContent = u.userid;
    uidEl.title = t("adv.usersUseridCol");
    row.appendChild(uidEl);

    var conn = document.createElement("div");
    conn.className = "wecom-conn";
    conn.appendChild(switchEl(u, "advisor", t("adv.usersAdvisor"), t("adv.usersAdvisorHint"), active));
    conn.appendChild(switchEl(u, "push_reply", t("adv.usersPushReply"), t("adv.usersPushReplyHint"), active));
    conn.appendChild(switchEl(u, "mirror", t("adv.usersMirror"), t("adv.usersMirrorHint"), active));
    row.appendChild(conn);

    var del = document.createElement("button");
    del.type = "button";
    del.className = "wecom-del";
    del.textContent = "🗑";
    del.title = t("adv.usersDelete");
    del.addEventListener("click", function() {
      if (!confirm(t("adv.usersDeleteConfirm"))) return;
      apiCall("/api/advisory/wecom_users?userid=" + encodeURIComponent(u.userid),
              "DELETE", null, renderList, true);
    });
    row.appendChild(del);
    return row;
  }

  // One connectivity switch: label + .switch knob. Disabled for placeholders the
  // bot has never met (their row has nothing to push/bind to yet).
  function switchEl(u, field, label, hint, active) {
    var wrap = document.createElement("div");
    wrap.className = "wecom-conn-item";
    wrap.title = hint;
    var lb = document.createElement("span");
    lb.className = "wecom-conn-label";
    lb.textContent = label;
    var sw = document.createElement("label");
    sw.className = "switch";
    var cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "wu-toggle";
    cb.checked = !!u[field];
    if (!active) cb.disabled = true;
    cb.addEventListener("change", function() {
      var body = {userid: u.userid};
      body[field] = cb.checked;
      apiCall("/api/advisory/wecom_users", "PATCH", body, function() {
        // Switching mirror OFF on the currently-bound user must clear the web
        // binding — the dropdown only lists active && mirror users, so a bound
        // user that lost mirror would otherwise dangle in a stale state.
        if (field === "mirror" && !cb.checked &&
            window._wecomBoundUser === u.userid) {
          fetch("/api/advisory/wecom_binding", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({bound_user: ""})
          }).catch(function() {});
        }
      });
    });
    sw.appendChild(cb);
    var slider = document.createElement("span");
    slider.className = "slider round";
    sw.appendChild(slider);
    wrap.appendChild(lb);
    wrap.appendChild(sw);
    return wrap;
  }
})();

// Live-sync: while the advisory panel is visible and not streaming, poll the
// active thread's messages so replies arriving from the WeCom bot appear in the
// UI without a manual refresh. Web and bot share ONE conversation (same thread).
var advisoryPollTimer = null;
var advisoryLastMsgSig = "";
var ADVISORY_POLL_MS = 5000;

function _advisoryPanelVisible() {
  var exp = document.getElementById("advisory-expanded");
  return exp && !exp.classList.contains("hidden") && currentTab === "advisory";
}
function _advisoryMsgSig(messages) {
  return (messages || []).map(function(m) {
    return (m.role || "") + "|" + (m.content || "").substring(0, 80);
  }).join("\n");
}
function renderAdvisoryMessages(messages) {
  var msgs = document.getElementById("advisory-expanded-messages");
  if (!msgs) return;
  if (!messages || !messages.length) {
    msgs.innerHTML = '<div class="chat-msg system">' + t("chat.noMessagesYet") + '</div>';
    return;
  }
  var html = "";
  messages.forEach(function(m) {
    if (m.role === "user") {
      html += '<div class="chat-msg user">' + escHtml(m.content) + '</div>';
    } else if (m.role === "assistant") {
      html += '<div class="chat-msg pm"><div class="markdown-body">' + renderMarkdown(m.content || "") + '</div></div>';
    } else {
      html += '<div class="chat-msg system">' + escHtml(m.content || "") + '</div>';
    }
  });
  msgs.innerHTML = html;
  msgs.scrollTop = msgs.scrollHeight;
}
function startAdvisoryPoll() {
  if (advisoryPollTimer) clearInterval(advisoryPollTimer);
  advisoryPollTimer = setInterval(function() {
    if (!_advisoryPanelVisible() || advisoryStreaming || !advisoryThreadId) return;
    fetchJsonRetry("/api/chat/threads/" + advisoryThreadId + "/messages",
      function(messages) {
        var sig = _advisoryMsgSig(messages);
        if (sig === advisoryLastMsgSig) return;
        advisoryLastMsgSig = sig;
        renderAdvisoryMessages(messages);
      }, function() { /* transient error: try again next tick */ });
  }, ADVISORY_POLL_MS);
}
startAdvisoryPoll();  // armed at load; only fetches while the advisory panel is open

var aipickThreadId = null;
var aipickStreaming = false;
var aipickThreads = [];

var predictionThreadId = null;
var predictionStreaming = false;
var predictionThreads = [];

// ===== Thread List Management =====

// Shared fetch helper: 3 attempts with backoff on transient network failures
// (e.g. web service mid-restart / port momentarily unreachable). Surfaces the
// real reason (HTTP status / network error) on permanent failure.
function fetchJsonRetry(url, onOk, onFail, attempt) {
  attempt = attempt || 1;
  fetch(url)
    .then(function(r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    })
    .then(function(data) { onOk(data); })
    .catch(function(e) {
      if (attempt < 3) {
        setTimeout(function() { fetchJsonRetry(url, onOk, onFail, attempt + 1); }, attempt * 1500);
      } else if (onFail) {
        onFail(e);
      }
    });
}

function loadAdvisoryThreads() {
  var list = document.getElementById("advisory-thread-list");
  if (!list) return;
  if (advisoryThreadsLoading) return;
  advisoryThreadsLoading = true;
  loadTwoPassRecords();
  loadStage3Records();
  list.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">' + t("chat.loadingConversation") + '</span>';
  fetchJsonRetry("/api/chat/threads",
    function(threads) {
      advisoryThreadsLoading = false;
      if (!Array.isArray(threads)) throw new Error("响应格式异常");
      advisoryThreads = threads;
      renderAdvisoryThreadList();
    },
    function(e) {
      advisoryThreadsLoading = false;
      list.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">' + t("chat.failedToLoad") + (e && e.message ? " (" + escHtml(e.message) + ")" : "") + ' <a href="#" onclick="loadAdvisoryThreads();return false;">' + t("chat.retry") + '</a></span>';
    });
}

function renderAdvisoryThreadList() {
  var list = document.getElementById("advisory-thread-list");
  if (!list) return;
  
  if (!advisoryThreads.length) {
    list.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">No conversations yet. Click "+ New" to start.</span>';
    return;
  }
  
  var html = "";
  advisoryThreads.forEach(function(th) {
    var title = (th.title || t("chat.newConversation"));
    if (title.length > 28) title = title.substring(0, 28) + "...";
    var count = th.message_count || 0;
    var date = (th.updated_at || th.created_at || "").substring(0, 10);
    var activeClass = (th.thread_id === advisoryThreadId) ? " active" : "";
    html += '<div class="advisory-thread-item' + activeClass + '" data-thread-id="' + th.thread_id + '" style="padding:8px 10px;margin:2px 0;border-radius:6px;cursor:pointer;border:1px solid ' + (th.thread_id === advisoryThreadId ? 'var(--accent)' : 'transparent') + ';background:' + (th.thread_id === advisoryThreadId ? 'var(--accent-light, #e8f0fe)' : 'transparent') + ';">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escHtml(title) + '</span>';
    html += '<button class="advisory-delete-thread-btn" data-thread-id="' + th.thread_id + '" style="font-size:14px;background:none;border:none;cursor:pointer;color:var(--text-muted);padding:0 4px;margin-left:4px;flex-shrink:0;" title="Delete">&times;</button>';
    html += '</div>';
    html += '<div style="font-size:10px;color:var(--text-muted);">' + t("chat.messagesCount", {n: count}) + ' &middot; ' + date + '</div>';
    html += '</div>';
  });
  list.innerHTML = html;
  
  // Bind click to select thread
  list.querySelectorAll(".advisory-thread-item").forEach(function(item) {
    item.addEventListener("click", function(e) {
      if (e.target.classList.contains("advisory-delete-thread-btn")) return;
      var tid = this.dataset.threadId;
      selectAdvisoryThread(tid);
    });
  });
  
  // Bind delete buttons
  list.querySelectorAll(".advisory-delete-thread-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      var tid = this.dataset.threadId;
      deleteAdvisoryThread(tid);
    });
  });
}

// ===== Two-Pass (二次分析) Records on Advisory left panel =====
var twoPassRecords = [];
var _tpTabLabels = {emwl:"tab.emwl", idx:"tab.idx"};

function loadTwoPassRecords() {
  fetch("/api/twopass/records")
    .then(function(r) { return r.json(); })
    .then(function(recs) {
      twoPassRecords = recs || [];
      renderTwoPassRecords();
    })
    .catch(function() {
      var list = document.getElementById("advisory-twopass-records-list");
      if (list) list.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("common.loadFailed") + '</span>';
    });
}

function renderTwoPassRecords() {
  var list = document.getElementById("advisory-twopass-records-list");
  if (!list) return;
  if (!twoPassRecords.length) {
    list.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("twopass.noRecords") + '</span>';
    return;
  }
  var html = "";
  twoPassRecords.forEach(function(r) {
    var lbl = (_tpTabLabels[r.tab] && t(_tpTabLabels[r.tab])) || r.tab || t("tab.emwl");
    var date = (r.created_at || "").substring(5, 16); // MM-DD HH:MM
    html += '<div class="advisory-twopass-item" data-id="' + r.id + '" style="padding:5px 8px;margin:2px 0;border-radius:6px;cursor:pointer;border:1px solid var(--border);background:var(--bg);">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-size:11px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">📈 ' + escHtml(r.title || (lbl + t("twopass.titleSuffix"))) + '</span>';
    html += '<button class="advisory-twopass-delete" data-id="' + r.id + '" style="font-size:13px;background:none;border:none;cursor:pointer;color:var(--text-muted);padding:0 3px;flex-shrink:0;" title="' + t("twopass.deleteRecord") + '">&times;</button>';
    html += '</div>';
    html += '<div style="font-size:10px;color:var(--text-muted);">🕐 ' + escHtml(date) + ' · ' + escHtml(lbl) + ' · ' + t("twopass.reviewedSuffix", {n: r.stock_count}) + ' · <b style="color:var(--green);">' + t("twopass.bullishSuffix", {n: r.bullish_count}) + '</b></div>';
    html += '</div>';
  });
  list.innerHTML = html;
  list.querySelectorAll(".advisory-twopass-item").forEach(function(item) {
    item.addEventListener("click", function(e) {
      if (e.target.classList.contains("advisory-twopass-delete")) return;
      startTwopassAdvisory(this.dataset.id);
    });
  });
  list.querySelectorAll(".advisory-twopass-delete").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      deleteTwoPassRecord(this.dataset.id);
    });
  });
}

function deleteTwoPassRecord(id) {
  if (!confirm(t("twopass.deleteConfirm"))) return;
  fetch("/api/twopass/records/" + id, { method: "DELETE" })
    .then(function() { loadTwoPassRecords(); })
    .catch(function() { alert(t("twopass.deleteFailed")); });
}

// ===== Stage-3 (阶段三·顾问综合报告) records on Advisory left panel — read-only =====
var stage3Records = [];
var _s3TabLabels = {emwl: "tab.emwl", idx: "tab.idx"};

function loadStage3Records() {
  fetch("/api/stage3/records")
    .then(function(r) { return r.json(); })
    .then(function(recs) {
      stage3Records = recs || [];
      renderStage3Records();
    })
    .catch(function() {
      var list = document.getElementById("advisory-stage3-records-list");
      if (list) list.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("common.loadFailed") + '</span>';
    });
}

function renderStage3Records() {
  var list = document.getElementById("advisory-stage3-records-list");
  if (!list) return;
  if (!stage3Records.length) {
    list.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("stage3.empty") + '</span>';
    return;
  }
  var html = "";
  stage3Records.forEach(function(r) {
    var lbl = (_s3TabLabels[r.tab] && t(_s3TabLabels[r.tab])) || r.tab || t("tab.emwl");
    var date = (r.created_at || "").substring(5, 16); // MM-DD HH:MM
    html += '<div class="advisory-stage3-item" data-id="' + r.id + '" style="padding:5px 8px;margin:2px 0;border-radius:6px;cursor:pointer;border:1px solid var(--border);background:var(--bg);">';
    html += '<div style="font-size:11px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">🧭 ' + escHtml(r.title || "") + '</div>';
    html += '<div style="font-size:10px;color:var(--text-muted);">🕐 ' + escHtml(date) + ' · ' + escHtml(lbl) + ' · ' + t("stage3.stockCountSuffix", {n: r.stock_count}) + (r.model ? ' · <b>' + escHtml(r.model) + '</b>' : '') + '</div>';
    html += '</div>';
  });
  list.innerHTML = html;
  list.querySelectorAll(".advisory-stage3-item").forEach(function(item) {
    item.addEventListener("click", function() { viewStage3Report(this.dataset.id); });
  });
}

function viewStage3Report(id) {
  fetch("/api/stage3/records/" + id)
    .then(function(r) { return r.json(); })
    .then(function(rec) {
      if (!rec || !rec.report) { alert(t("stage3.emptyReport")); return; }
      renderStage3Report(rec);
    })
    .catch(function() { alert(t("stage3.loadFailed")); });
}

// Render a saved stage-3 report read-only into the Advisory conversation panel.
function renderStage3Report(rec) {
  var app = document.getElementById("app");
  if (app) { app.classList.remove("aipick-mode","prediction-mode","strategy-mode","backtest-mode","historyagent-mode","sched-mode"); app.classList.add("advisory-mode"); }
  document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
  var advTab = document.querySelector('[data-tab="advisory"]');
  if (advTab) advTab.classList.add("active");
  var advContent = document.getElementById("tab-advisory");
  if (advContent) advContent.classList.add("active");
  currentTab = "advisory";
  var exp = document.getElementById("advisory-expanded");
  if (exp) { exp.classList.remove("hidden"); exp.style.display = "flex"; }
  var msgs = document.getElementById("advisory-expanded-messages");
  if (!msgs) return;
  msgs.innerHTML = "";
  var pmDiv = document.createElement("div");
  pmDiv.className = "chat-msg pm";
  var mdDiv = document.createElement("div");
  mdDiv.className = "markdown-body";
  mdDiv.innerHTML = renderMarkdown(rec.report || "");
  pmDiv.appendChild(mdDiv);
  msgs.appendChild(pmDiv);
  msgs.scrollTop = msgs.scrollHeight;
}

// Click a record → open Advisory conversation, deep-analyze the bullish candidates, ≤5 final picks
function startTwopassAdvisory(id) {
  fetch("/api/twopass/records/" + id)
    .then(function(r) { return r.json(); })
    .then(function(rec) {
      var stocks = rec.stocks || [];
      if (!stocks.length) { alert(t("twopass.noStockData")); return; }
      var lbl = (_tpTabLabels[rec.tab] && t(_tpTabLabels[rec.tab])) || rec.tab || t("tab.emwl");
      var rows = stocks.map(function(s) {
        var chg = (s.change_pct != null) ? ((s.change_pct >= 0 ? "+" : "") + s.change_pct.toFixed(2) + "%") : "-";
        var pr = s.price ? s.price.toFixed(2) : "-";
        var agree = s.newVerdict === s.prevVerdict ? "✅一致" : "❌分歧";
        var nv = s.newVerdict === "看多" ? "🟢看多" : (s.newVerdict === "看空" ? "🔴看空" : "🟡观望");
        return "| " + escHtml(s.code) + " | " + escHtml(s.name || "") + " | " + pr + " | " + chg + " | " + escHtml(s.prevVerdict || "") + " | " + escHtml(s.newModel || "") + " | " + nv + " | " + agree + " |";
      }).join("\n");
      var table = "| 代码 | 名称 | 现价 | 涨跌幅 | 前次结论 | 本次模型 | 本次结论 | 一致 |\n|---|---|---|---|---|---|---|---|\n" + rows;
      var question = [
        "以下是一条【二次分析】看多候选记录（记录ID " + rec.id + "）：" + lbl + " · " + rec.stock_count + "只复核 · " + rec.bullish_count + "只确认看多。",
        "",
        table,
        "",
        "请对这批候选做进一步深度分析：",
        "1. 先调用 get_twopass_record_detail(" + rec.id + ") 获取每只股票的完整分析结论；",
        "2. 对候选逐一交叉验证（get_smart_money_score / get_realtime_fund_flow / get_stock_data / web_search_current / query_eastmoney_data 等），核对主力资金、技术面、消息面；",
        "3. 最终给出不超过 5 只的最终建议，按确信度从高到低排序，每只附：推荐理由（引用具体数字）、建议仓位(%)、止损位（建议 -8%）。",
        "",
        "若确认看多不足 5 只，如实说明，并给出可观察的备选。"
      ].join("\n");
      openAdvisoryWithQuestion(question);
    })
    .catch(function() { alert(t("twopass.loadRecordFailed")); });
}

// Switch to Advisory tab + create thread + stream the response (mirrors advisorySend)
function openAdvisoryWithQuestion(question) {
  var app = document.getElementById("app");
  if (app) { app.classList.remove("aipick-mode","prediction-mode","strategy-mode","backtest-mode","historyagent-mode","sched-mode"); app.classList.add("advisory-mode"); }
  document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
  var advTab = document.querySelector('[data-tab="advisory"]');
  if (advTab) advTab.classList.add("active");
  var advContent = document.getElementById("tab-advisory");
  if (advContent) advContent.classList.add("active");
  currentTab = "advisory";
  var exp = document.getElementById("advisory-expanded");
  if (exp) { exp.classList.remove("hidden"); exp.style.display = "flex"; }

  if (advisoryStreaming) return;
  advisoryStreaming = true;
  var msgs = document.getElementById("advisory-expanded-messages");
  var status = document.getElementById("advisory-expanded-status");
  var btn = document.getElementById("advisory-expanded-send-btn");
  if (btn) btn.disabled = true;
  if (status) status.textContent = t("chat.thinking");
  if (msgs) msgs.innerHTML = "";
  var userDiv = document.createElement("div");
  userDiv.className = "chat-msg user";
  userDiv.textContent = question;
  if (msgs) { msgs.appendChild(userDiv); msgs.scrollTop = msgs.scrollHeight; }
  var pmDiv = document.createElement("div");
  pmDiv.className = "chat-msg pm";
  var mdDiv = document.createElement("div");
  mdDiv.className = "markdown-body";
  mdDiv.innerHTML = "<em>" + t("chat.thinking") + "</em>";
  pmDiv.appendChild(mdDiv);
  if (msgs) { msgs.appendChild(pmDiv); msgs.scrollTop = msgs.scrollHeight; }

  if (!advisoryThreadId) advisoryThreadId = "adv_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 6);
  var body = { question: question, thread_id: advisoryThreadId };
  fetch("/api/advisory/chat", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) })
    .then(function(r) { if (!r.ok) throw new Error("endpoint_unavailable"); return r.json(); })
    .then(function(data) {
      advisoryThreadId = data.thread_id;
      updateAdvisoryDownloadBtns();
      if (status) status.textContent = t("chat.streaming");
      var streamUrl = data.stream_url || ("/api/chat/stream?thread_id=" + data.thread_id + "&question=" + encodeURIComponent(question));
      streamAdvisoryResponse(streamUrl, question, mdDiv);
    })
    .catch(function(err) {
      if (err.message === "endpoint_unavailable") {
        if (status) status.textContent = t("chat.streaming");
        var fallbackUrl = "/api/chat/stream?thread_id=" + advisoryThreadId + "&question=" + encodeURIComponent(question);
        streamAdvisoryResponse(fallbackUrl, question, mdDiv);
        return;
      }
      mdDiv.innerHTML = t("chat.errorPrefix") + String(err);
      advisoryStreaming = false;
      if (btn) btn.disabled = false;
    });
}

// ===== Advisory 东方自选快速分析 (multi-select watchlist → quick prompt) =====
var advisoryEmwlStocks = [];
var advisoryEmwlQuery = "";
var advisoryEmwlSelected = {}; // code -> true (persistent selection, survives search filter)
var advisoryEmwlRefreshBtn = document.getElementById("advisory-emwl-refresh");
var advisoryEmwlSelectAll = document.getElementById("advisory-emwl-select-all");
var advisoryEmwlAnalyzeBtn = document.getElementById("advisory-emwl-analyze");
var advisoryEmwlSearch = document.getElementById("advisory-emwl-search");
var advisoryEmwlListEl = document.getElementById("advisory-emwl-list");
var advisoryEmwlStatusEl = document.getElementById("advisory-emwl-status");

function loadAdvisoryEmwl() {
  if (!advisoryEmwlListEl || !advisoryEmwlStatusEl) return;
  if (advisoryEmwlRefreshBtn) advisoryEmwlRefreshBtn.disabled = true;
  advisoryEmwlStatusEl.textContent = t("common.loading");
  advisoryEmwlListEl.innerHTML = t("common.loading");
  fetch("/api/eastmoney/watchlist")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.error) {
        advisoryEmwlStatusEl.textContent = t("common.loadFailed");
        advisoryEmwlListEl.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + esc(data.error) + '</span>';
        return;
      }
      advisoryEmwlStocks = data.stocks || [];
      advisoryEmwlSelected = {}; // fresh data → reset selection
      advisoryEmwlStatusEl.textContent = t("advisory.emwlCount", {n: advisoryEmwlStocks.length}) + (data.from_cache ? t("advisory.cached") : "");
      renderAdvisoryEmwl();
    })
    .catch(function() {
      advisoryEmwlStatusEl.textContent = t("common.loadFailed");
      advisoryEmwlListEl.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("common.loadFailed") + '</span>';
    })
    .finally(function() { if (advisoryEmwlRefreshBtn) advisoryEmwlRefreshBtn.disabled = false; });
}

// Currently-visible rows after applying the search query
function getAdvisoryEmwlVisible() {
  var q = (advisoryEmwlQuery || "").toLowerCase().trim();
  if (!q) return advisoryEmwlStocks;
  return advisoryEmwlStocks.filter(function(s) {
    return (String(s.code).toLowerCase().indexOf(q) !== -1) ||
           (String(s.name || "").toLowerCase().indexOf(q) !== -1);
  });
}

function renderAdvisoryEmwl() {
  if (!advisoryEmwlListEl) return;
  if (!advisoryEmwlStocks.length) {
    advisoryEmwlListEl.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("advisory.noStocks") + '</span>';
    updateAdvisoryEmwlCount();
    return;
  }
  var visible = getAdvisoryEmwlVisible();
  if (!visible.length) {
    advisoryEmwlListEl.innerHTML = '<span class="no-results" style="padding:4px;font-size:10px;">' + t("advisory.noMatch", {q: esc(advisoryEmwlQuery)}) + '</span>';
    updateAdvisoryEmwlCount();
    return;
  }
  var html = "";
  visible.forEach(function(s) {
    var chg = (typeof s.change_pct === "number") ? s.change_pct : 0;
    var color = chg >= 0 ? "var(--green)" : "var(--red)";
    var chgStr = (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%";
    var checked = advisoryEmwlSelected[s.code] ? " checked" : "";
    html += '<label style="display:flex;align-items:center;gap:5px;padding:2px 4px;cursor:pointer;border-radius:4px;">';
    html += '<input type="checkbox" class="advisory-emwl-check" data-code="' + esc(s.code) + '" onchange="onAdvisoryEmwlCheck(this)"' + checked + ' style="flex-shrink:0;">';
    html += '<span style="flex-shrink:0;font-weight:600;">' + esc(s.code) + '</span>';
    html += '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(s.name || "") + '</span>';
    html += '<span style="color:' + color + ';flex-shrink:0;">' + chgStr + '</span>';
    html += '</label>';
  });
  advisoryEmwlListEl.innerHTML = html;
  updateAdvisoryEmwlCount();
}

// Individual checkbox toggle → update persistent selection map
function onAdvisoryEmwlCheck(cb) {
  var code = cb && cb.dataset ? cb.dataset.code : "";
  if (!code) return;
  if (cb.checked) { advisoryEmwlSelected[code] = true; }
  else { delete advisoryEmwlSelected[code]; }
  updateAdvisoryEmwlCount();
}

// Search-as-you-type filter (code / name substring)
function onAdvisoryEmwlSearch() {
  advisoryEmwlQuery = advisoryEmwlSearch ? advisoryEmwlSearch.value : "";
  renderAdvisoryEmwl();
}

function updateAdvisoryEmwlCount() {
  if (!advisoryEmwlAnalyzeBtn) return;
  var n = 0;
  for (var k in advisoryEmwlSelected) { if (advisoryEmwlSelected.hasOwnProperty(k)) n++; }
  advisoryEmwlAnalyzeBtn.textContent = t("advisory.analyzeSelected", {n: n});
  advisoryEmwlAnalyzeBtn.disabled = n === 0;
  if (advisoryEmwlSelectAll) {
    var visible = getAdvisoryEmwlVisible();
    var allSelected = visible.length > 0;
    visible.forEach(function(s) { if (!advisoryEmwlSelected[s.code]) allSelected = false; });
    advisoryEmwlSelectAll.checked = allSelected;
  }
}

function advisoryEmwlAnalyze() {
  var codes = [];
  for (var k in advisoryEmwlSelected) {
    if (advisoryEmwlSelected.hasOwnProperty(k)) codes.push(k);
  }
  if (!codes.length) return;
  var rows = codes.map(function(code) {
    var s = null;
    for (var i = 0; i < advisoryEmwlStocks.length; i++) {
      if (advisoryEmwlStocks[i].code === code) { s = advisoryEmwlStocks[i]; break; }
    }
    var chg = (s && typeof s.change_pct === "number") ? s.change_pct : 0;
    var chgStr = (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%";
    var priceStr = (s && typeof s.price === "number") ? s.price.toFixed(2) : "-";
    var name = s ? (s.name || "") : "";
    return "| " + code + " | " + name + " | " + priceStr + " | " + chgStr + " |";
  }).join("\n");
  var question = [
    "请快速分析以下 " + codes.length + " 只自选股（只做快速点评，不必深度建模，直接给结论）：",
    "",
    "| 代码 | 名称 | 最新价 | 涨跌幅 |",
    "|------|------|--------|--------|",
    rows,
    "",
    "对每只给出 1-2 句话：主力资金方向、短期关注点、操作建议（看多/看空/观望）。",
    "最后用一张汇总表按关注度从高到低排序呈现。"
  ].join("\n");
  openAdvisoryWithQuestion(question);
}

if (advisoryEmwlRefreshBtn) {
  advisoryEmwlRefreshBtn.addEventListener("click", loadAdvisoryEmwl);
}
if (advisoryEmwlSearch) {
  advisoryEmwlSearch.addEventListener("input", onAdvisoryEmwlSearch);
}
if (advisoryEmwlSelectAll) {
  advisoryEmwlSelectAll.addEventListener("change", function() {
    var checked = advisoryEmwlSelectAll.checked;
    getAdvisoryEmwlVisible().forEach(function(s) {
      if (checked) { advisoryEmwlSelected[s.code] = true; }
      else { delete advisoryEmwlSelected[s.code]; }
    });
    renderAdvisoryEmwl();
  });
}
if (advisoryEmwlAnalyzeBtn) {
  advisoryEmwlAnalyzeBtn.addEventListener("click", advisoryEmwlAnalyze);
}

function selectAdvisoryThread(threadId) {
  advisoryThreadId = threadId;
  updateAdvisoryDownloadBtns();
  renderAdvisoryThreadList();
  
  // Load messages into right panel
  var msgs = document.getElementById("advisory-expanded-messages");
  if (!msgs) return;
  msgs.innerHTML = '<div class="chat-msg system">' + t("chat.loadingConversation") + '</div>';
  
  // Update title
  var thread = advisoryThreads.find(function(t) { return t.thread_id === threadId; });
  var titleEl = document.getElementById("adv-expanded-title");
  var subEl = document.getElementById("adv-expanded-subtitle");
  if (titleEl) titleEl.textContent = thread ? (thread.title || "Conversation") : "CapitalRadar Advisor";
  if (subEl) subEl.textContent = thread ? t("chat.messagesCount", {n: thread.message_count || 0}) : "";
  
  // Load messages from API
  fetchJsonRetry("/api/chat/threads/" + threadId + "/messages",
    function(messages) {
      advisoryLastMsgSig = _advisoryMsgSig(messages);
      renderAdvisoryMessages(messages);
      startAdvisoryPoll();  // keep watching for bot-arrived messages
    },
    function(e) {
      msgs.innerHTML = '<div class="chat-msg system">' + t("chat.failedToLoad") + (e && e.message ? " (" + escHtml(e.message) + ")" : "") + '</div>';
    });
}

function discussWithPM(runId) {
  // If a sessionId exists from the current analysis, use its run_id
  if (!runId && sessionId) runId = sessionId;

  // Switch to Advisory tab
  var app = document.getElementById("app");
  if (app) { app.classList.remove("aipick-mode", "sched-mode"); app.classList.add("advisory-mode"); }

  // Update tab UI
  document.querySelectorAll(".top-tab-btn").forEach(function(b) { b.classList.remove("active"); });
  document.querySelectorAll(".tab-content").forEach(function(c) { c.classList.remove("active"); });
  var advTab = document.querySelector('[data-tab="advisory"]');
  if (advTab) advTab.classList.add("active");
  var advContent = document.getElementById("tab-advisory");
  if (advContent) advContent.classList.add("active");
  currentTab = "advisory";

  // Create a new thread with the analysis context
  var ticker = selectedTicker ? selectedTicker.symbol : "";
  var question = ticker ? "I want to discuss the analysis results for " + ticker : "I want to discuss the latest analysis results";

  fetch("/api/advisory/chat", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({question: question, run_ids: runId ? [runId] : []})
  }).then(function(r) { return r.json(); })
  .then(function(data) {
    advisoryThreadId = data.thread_id;
    loadAdvisoryThreads();

    // Set the question in the advisory input
    var input = document.getElementById("advisory-expanded-input");
    if (input) {
      input.value = "请总结一下这次分析的核心要点和风险提示";
      setTimeout(function() {
        var sendBtn = document.getElementById("advisory-expanded-send-btn");
        if (sendBtn) sendBtn.click();
      }, 300);
    }
  });
}

function deleteAdvisoryThread(threadId) {
  if (!confirm("Delete this conversation?")) return;
  fetch("/api/chat/threads/" + threadId, { method: "DELETE" })
    .then(function() {
      if (advisoryThreadId === threadId) {
        advisoryThreadId = null;
        var msgs = document.getElementById("advisory-expanded-messages");
        if (msgs) msgs.innerHTML = '<div class="chat-msg system">' + t("chat.welcomeAdvisor") + '</div>';
        var titleEl = document.getElementById("adv-expanded-title");
        if (titleEl) titleEl.textContent = "CapitalRadar Advisor";
        var subEl = document.getElementById("adv-expanded-subtitle");
        if (subEl) subEl.textContent = t("chat.newConversation");
      }
      loadAdvisoryThreads();
    });
}

function deleteAipickThread(threadId) {
  if (!confirm("Delete this conversation?")) return;
  fetch("/api/chat/threads/" + threadId, { method: "DELETE" })
    .then(function() {
      if (aipickThreadId === threadId) {
        aipickThreadId = null;
        var msgs = document.getElementById("aipick-expanded-messages");
        if (msgs) msgs.innerHTML = '<div class="chat-msg system">' + t("chat.welcomeAipickHi") + '</div>';
        var titleEl = document.getElementById("aipick-expanded-title");
        if (titleEl) titleEl.textContent = "AI Smart Pick";
        var subEl = document.getElementById("aipick-expanded-subtitle");
        if (subEl) subEl.textContent = t("chat.newConversation");
        updateAipickDownloadBtns();
      }
      loadAipickThreads();
    });
}

function updateAdvisoryDownloadBtns() {
  var dlMdBtn = document.getElementById("advisory-download-md-btn");
  var dlDocxBtn = document.getElementById("advisory-download-docx-btn");
  if (!dlMdBtn || !dlDocxBtn) return;
  if (advisoryThreadId) {
    dlMdBtn.style.display = "";
    dlDocxBtn.style.display = "";
    dlMdBtn.onclick = function() { window.open("/api/chat/threads/" + advisoryThreadId + "/download?format=md"); };
    dlDocxBtn.onclick = function() { window.open("/api/chat/threads/" + advisoryThreadId + "/download?format=docx"); };
  } else {
    dlMdBtn.style.display = "none";
    dlDocxBtn.style.display = "none";
  }
}

function newAdvisoryThread() {
  advisoryThreadId = null;
  updateAdvisoryDownloadBtns();
  renderAdvisoryThreadList();
  
  var msgs = document.getElementById("advisory-expanded-messages");
  if (msgs) msgs.innerHTML = '<div class="chat-msg system">New conversation started. Ask me anything about stocks.</div>';
  
  var titleEl = document.getElementById("adv-expanded-title");
  if (titleEl) titleEl.textContent = "CapitalRadar Advisor";
  var subEl = document.getElementById("adv-expanded-subtitle");
  if (subEl) subEl.textContent = "New conversation";
  
  // Focus input
  var input = document.getElementById("advisory-expanded-input");
  if (input) input.focus();
}

// ===== Chat Functions (right panel) =====

function advisorySend() {
  var input = document.getElementById("advisory-expanded-input");
  var question = (input ? input.value : "").trim();
  if (!question || advisoryStreaming) return;
  if (input) input.value = "";
  advisoryStreaming = true;

  var msgs = document.getElementById("advisory-expanded-messages");
  var status = document.getElementById("advisory-expanded-status");
  var btn = document.getElementById("advisory-expanded-send-btn");
  if (btn) btn.disabled = true;
  if (status) status.textContent = t("chat.thinking");

  // Add user message to right panel
  var userDiv = document.createElement("div");
  userDiv.className = "chat-msg user";
  userDiv.textContent = question;
  if (msgs) { msgs.appendChild(userDiv); msgs.scrollTop = msgs.scrollHeight; }

  // Add thinking placeholder
  var pmDiv = document.createElement("div");
  pmDiv.className = "chat-msg pm";
  var mdDiv = document.createElement("div");
  mdDiv.className = "markdown-body";
  mdDiv.innerHTML = "<em>" + t("chat.thinking") + "</em>";
  pmDiv.appendChild(mdDiv);
  if (msgs) { msgs.appendChild(pmDiv); msgs.scrollTop = msgs.scrollHeight; }

  // Generate thread_id if new
  if (!advisoryThreadId) advisoryThreadId = "adv_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 6);

  var body = { question: question, thread_id: advisoryThreadId };

  fetch("/api/advisory/chat", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) })
    .then(function(r) {
      if (!r.ok) throw new Error("endpoint_unavailable");
      return r.json();
    })
    .then(function(data) {
      advisoryThreadId = data.thread_id;
      updateAdvisoryDownloadBtns();
      if (status) status.textContent = t("chat.streaming");
      var streamUrl = data.stream_url || ("/api/chat/stream?thread_id=" + data.thread_id + "&question=" + encodeURIComponent(question));
      streamAdvisoryResponse(streamUrl, question, mdDiv);
    })
    .catch(function(err) {
      if (err.message === "endpoint_unavailable") {
        if (status) status.textContent = t("chat.streaming");
        var fallbackUrl = "/api/chat/stream?thread_id=" + advisoryThreadId + "&question=" + encodeURIComponent(question);
        streamAdvisoryResponse(fallbackUrl, question, mdDiv);
        return;
      }
      mdDiv.innerHTML = t("chat.errorPrefix") + String(err);
      advisoryStreaming = false;
      if (btn) btn.disabled = false;
    });
}

function streamAdvisoryResponse(streamUrl, question, targetEl) {
  var status = document.getElementById("advisory-expanded-status");
  var btn = document.getElementById("advisory-expanded-send-btn");
  var msgs = document.getElementById("advisory-expanded-messages");
  var es = new EventSource(streamUrl);

  es.addEventListener("chat-tool-call", function(e) {
    var d = safeJSON(e.data) || {};
    var sysDiv = document.createElement("div");
    sysDiv.className = "chat-msg system";
    sysDiv.textContent = "Calling: " + d.tool_name + "...";
    if (msgs) { msgs.appendChild(sysDiv); msgs.scrollTop = msgs.scrollHeight; }
  });

  es.addEventListener("chat-done", function(e) {
    var d = safeJSON(e.data) || {};
    if (targetEl) targetEl.innerHTML = renderMarkdown(d.full_response || "");
    advisoryStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = t("chat.doneWithTools", {n: d.tool_calls_count||0});
    
    // Refresh thread list
    loadAdvisoryThreads();
    
    // Update subtitle
    var subEl = document.getElementById("adv-expanded-subtitle");
    if (subEl) subEl.textContent = t("chat.lastResponse");
    
    // Enable speak button
    var spk = document.getElementById("advisory-expanded-speak-btn");
    if (spk) { spk.style.display = "inline-block"; spk.dataset.text = d.full_response || ""; }
    
    es.close();
  });

  es.addEventListener("chat-error", function(e) {
    var d = safeJSON(e.data) || {};
    var errDiv = document.createElement("div");
    errDiv.className = "chat-msg system";
    errDiv.textContent = "Error: " + d.message;
    if (msgs) msgs.appendChild(errDiv);
    advisoryStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = "Error";
    es.close();
  });

  es.onerror = function() { advisoryStreaming = false; if (btn) btn.disabled = false; es.close(); };
}

// ============================================================================
// AI Pick Tab - Thread-based chat with expanded right panel (mirrors Advisory)
// ============================================================================

// ===== AI Pick Thread List Management =====

function loadAipickThreads() {
  fetch("/api/chat/threads").then(function(r){return r.json()}).then(function(threads){
    aipickThreads = threads || [];
    renderAipickThreadList();
  }).catch(function(){});
  loadAipickRankings();
}

function loadAipickRankings() {
  var cb = document.getElementById("aipick-rank-checkbox");
  if (cb && !cb.checked) {
    // Toggle OFF — hide rankings panel
    var panel = document.getElementById("aipick-rankings");
    if (panel) panel.style.display = "none";
    return;
  }
  // Toggle ON — show and load
  var panel = document.getElementById("aipick-rankings");
  if (panel) panel.style.display = "";
  ["aipick-gainers","aipick-inflow","aipick-multifactor"].forEach(function(id) {
    var col = document.getElementById(id);
    if (col) { var list = col.querySelector(".aipick-rank-list"); if (list) list.innerHTML = '<div style="padding:8px;color:var(--text-muted);">' + t("common.loading") + '</div>'; }
  });
  fetch("/api/pick/rankings").then(function(r){return r.json()}).then(function(data) {
    if (data.error) { console.log("rank API error:", data.error); return; }
    try {
      renderRankCol("aipick-gainers", data.gainers, "gainers");
      renderRankCol("aipick-inflow", data.inflow, "inflow");
      renderRankCol("aipick-multifactor", data.multifactor, "multifactor");
    } catch(e) { console.log("renderRankCol error:", e);
      document.getElementById("aipick-gainers").querySelector(".aipick-rank-list").innerHTML = '<div style=\"padding:8px;color:var(--red);\">Render error: '+e.message+'</div>';
    }
  }).catch(function(e){
    console.log("rank fetch error:", e);
    ["aipick-gainers","aipick-inflow","aipick-multifactor"].forEach(function(id) {
      var col = document.getElementById(id);
      if (col) { var list = col.querySelector(".aipick-rank-list"); if (list) list.innerHTML = '<div style="padding:8px;color:var(--red);">Failed: '+e.message+'</div>'; }
    });
  });
}

function renderRankCol(colId, items, type) {
  var col = document.getElementById(colId);
  if (!col) { console.log("rank: col not found:", colId); return; }
  var list = col.querySelector(".aipick-rank-list");
  if (!list) { console.log("rank: list not found in", colId); return; }
  if (!items || !items.length) { list.innerHTML = '<div style="padding:8px;color:var(--text-muted);">No data</div>'; return; }
  var html = "";
  items.forEach(function(item, i) {
    var ticker = item.ticker || "";
    var name = (item.name || ticker).substring(0, 10);
    var val = type === "gainers" ? (item.pct_chg||0).toFixed(1)+"%" :
              // get_top_net_inflow already returns 万元 (tushare moneyflow scale);
              // the old /1e4 turned 万元→亿 and labeled it "w" — a 1万倍 mislabel
              // (same unit family as the 002566 watchlist flow_detail bug).
              type === "inflow" ? (item.net_amount||item.volume||0).toFixed(0)+"w" :
              (item.score||Math.abs(item.pct_chg||0)*10).toFixed(0);
    var color = type === "gainers" ? ((item.pct_chg||0)>=0?"var(--red)":"var(--green)") : type === "inflow" ? "var(--accent)" : "var(--purple)";
    html += '<div class="aipick-rank-item" data-ticker="'+esc(ticker)+'" data-name="'+esc(item.name||ticker)+'" style="display:flex;justify-content:space-between;padding:3px 8px;cursor:pointer;border-bottom:1px solid var(--border);font-size:10px;" onmouseover="this.style.background=\"var(--panel-bg)\"" onmouseout="this.style.background=\"\"">';
    html += '<span><b style="color:var(--text-muted);width:16px;display:inline-block;">'+(i+1)+'</b> '+esc(ticker)+' <span style="color:var(--text-muted);">'+esc(name)+'</span></span>';
    html += '<span style="display:flex;align-items:center;gap:4px;">';
    html += '<span style="color:'+color+';font-weight:600;font-size:10px;">'+val+'</span>';
    html += '<button class="rank-save-btn" data-ticker="'+esc(ticker)+'" data-name="'+esc(item.name||ticker)+'" style="background:none;border:1px solid var(--accent);color:var(--accent);border-radius:3px;cursor:pointer;font-size:9px;padding:0 3px;" title="' + t("chat.saveToShortlist") + '">+</button>';
    html += '</span>';
    html += '</div>';
  });
  list.innerHTML = html;
  // [+] button — save to shortlist
  list.querySelectorAll(".rank-save-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      var t = this.dataset.ticker;
      var n = this.dataset.name;
      fetch("/api/shortlist", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ticker: t, name: n, score: 0, source: "pick_agent"})
      }).then(function(r){ return r.json(); }).then(function(){
        btn.textContent = "✓";
        btn.style.color = "var(--green)";
        btn.style.borderColor = "var(--green)";
      }).catch(function(){});
    });
  });
  list.querySelectorAll(".aipick-rank-item").forEach(function(el) {
    el.addEventListener("click", function() {
      var t = this.dataset.ticker;
      var n = this.dataset.name;
      // Set as selected ticker
      selectedTicker = {symbol: t, name: n, exchange: ""};
      var selSym = document.getElementById("selected-symbol");
      var selName = document.getElementById("selected-name");
      var selCard = document.getElementById("selected-card");
      if (selSym) selSym.textContent = t;
      if (selName) selName.textContent = n;
      if (selCard) selCard.classList.remove("hidden");
      // Load chart
      if (typeof loadChart === "function") loadChart(t, "max");
      // Switch to Deep Analysis tab
      var analyzeBtn = document.querySelector('[data-tab="analyze"]');
      if (analyzeBtn) analyzeBtn.click();
    });
  });
}

function renderAipickThreadList() {
  var list = document.getElementById("aipick-thread-list");
  if (!list) return;
  if (!aipickThreads.length) {
    list.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">No conversations yet. Start by describing what stocks you want to find.</span>';
    return;
  }
  var html = "";
  aipickThreads.forEach(function(th) {
    var title = (th.title || "Conversation");
    if (title.length > 28) title = title.substring(0, 28) + "...";
    var count = th.message_count || 0;
    var date = (th.updated_at || th.created_at || "").substring(0, 10);
    var activeClass = (th.thread_id === aipickThreadId) ? " active" : "";
    html += '<div class="advisory-thread-item' + activeClass + '" data-thread-id="' + th.thread_id + '" style="padding:8px 10px;margin:2px 0;border-radius:6px;cursor:pointer;border:1px solid ' + (th.thread_id === aipickThreadId ? 'var(--accent)' : 'transparent') + ';background:' + (th.thread_id === aipickThreadId ? 'var(--accent-light, #e8f0fe)' : 'transparent') + ';">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + esc(title) + '</span>';
    html += '<button class="advisory-delete-thread-btn" data-thread-id="' + th.thread_id + '" style="font-size:14px;background:none;border:none;cursor:pointer;color:var(--text-muted);padding:0 4px;margin-left:4px;flex-shrink:0;" title="Delete">&times;</button>';
    html += '</div>';
    html += '<div style="font-size:10px;color:var(--text-muted);">' + t("chat.messagesCount", {n: count}) + ' &middot; ' + date + '</div>';
    html += '</div>';
  });
  list.innerHTML = html;

  list.querySelectorAll(".advisory-thread-item").forEach(function(item) {
    item.addEventListener("click", function(e) {
      if (e.target.classList.contains("advisory-delete-thread-btn")) return;
      loadAipickThread(item.dataset.threadId);
    });
  });
  list.querySelectorAll(".advisory-delete-thread-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      deleteAipickThread(btn.dataset.threadId);
    });
  });
}

function loadAipickThread(threadId) {
  aipickThreadId = threadId;
  updateAipickDownloadBtns();
  renderAipickThreadList();
  var msgs = document.getElementById("aipick-expanded-messages");
  if (!msgs) return;
  msgs.innerHTML = '<div class="chat-msg system">' + t("chat.loadingConversation") + '</div>';

  var thread = aipickThreads.find(function(t) { return t.thread_id === threadId; });
  var titleEl = document.getElementById("aipick-expanded-title");
  var subEl = document.getElementById("aipick-expanded-subtitle");
  if (titleEl) titleEl.textContent = thread ? (thread.title || "AI Pick") : "AI Smart Pick";
  if (subEl) subEl.textContent = thread ? t("chat.messagesCount", {n: thread.message_count || 0}) : "";

  fetchJsonRetry("/api/chat/threads/" + threadId + "/messages",
    function(messages) {
      if (!msgs) return;
      msgs.innerHTML = "";
      messages.forEach(function(m) {
        var div = document.createElement("div");
        div.className = "chat-msg " + (m.role === "user" ? "user" : "pm");
        var mdDiv = document.createElement("div");
        mdDiv.className = "markdown-body";
        mdDiv.innerHTML = renderMarkdown(m.content);
        div.appendChild(mdDiv);
        msgs.appendChild(div);
      });
      msgs.scrollTop = msgs.scrollHeight;
    },
    function(e) {
      msgs.innerHTML = '<div class="chat-msg system">' + t("chat.failedToLoad") + (e && e.message ? " (" + escHtml(e.message) + ")" : "") + '</div>';
    });
}

function updateAipickDownloadBtns() {
  var dlMdBtn = document.getElementById("aipick-download-md-btn");
  var dlPdfBtn = document.getElementById("aipick-download-pdf-btn");
  if (!dlMdBtn || !dlPdfBtn) return;
  if (aipickThreadId) {
    dlMdBtn.style.display = ""; dlPdfBtn.style.display = "";
    dlMdBtn.onclick = function() { window.open("/api/chat/threads/" + aipickThreadId + "/download?format=md"); };
    dlPdfBtn.onclick = function() { window.open("/api/chat/threads/" + aipickThreadId + "/download?format=pdf"); };
  } else {
    dlMdBtn.style.display = "none"; dlPdfBtn.style.display = "none";
  }
}

// ===== AI Pick Chat Functions =====

function aipickSend() {
  var input = document.getElementById("aipick-expanded-input");
  var question = (input ? input.value : "").trim();
  if (!question || aipickStreaming) return;
  if (input) input.value = "";
  aipickStreaming = true;

  var msgs = document.getElementById("aipick-expanded-messages");
  var status = document.getElementById("aipick-expanded-status");
  var btn = document.getElementById("aipick-expanded-send-btn");
  if (btn) btn.disabled = true;
  if (status) status.textContent = t("chat.thinking");

  var userDiv = document.createElement("div");
  userDiv.className = "chat-msg user";
  userDiv.textContent = question;
  if (msgs) { msgs.appendChild(userDiv); msgs.scrollTop = msgs.scrollHeight; }

  var pmDiv = document.createElement("div");
  pmDiv.className = "chat-msg pm";
  var mdDiv = document.createElement("div");
  mdDiv.className = "markdown-body";
  mdDiv.innerHTML = "<em>" + t("chat.generatingStrategy") + "</em>";
  pmDiv.appendChild(mdDiv);
  if (msgs) { msgs.appendChild(pmDiv); msgs.scrollTop = msgs.scrollHeight; }

  if (!aipickThreadId) aipickThreadId = "aipick_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 6);
  var body = { question: question, thread_id: aipickThreadId };

  fetch("/api/aipick/chat", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      aipickThreadId = data.thread_id;
      updateAipickDownloadBtns();
      if (status) status.textContent = t("chat.streaming");
      streamAipickResponse(data.stream_url, question, mdDiv);
    })
    .catch(function(err) {
      mdDiv.innerHTML = t("chat.errorPrefix") + String(err);
      aipickStreaming = false;
      if (btn) btn.disabled = false;
    });
}

function streamAipickResponse(streamUrl, question, targetEl) {
  var status = document.getElementById("aipick-expanded-status");
  var btn = document.getElementById("aipick-expanded-send-btn");
  var msgs = document.getElementById("aipick-expanded-messages");
  var es = new EventSource(streamUrl);
  var content = "";

  es.addEventListener("chat-tool-call", function(e) {
    var d = safeJSON(e.data) || {};
    var sysDiv = document.createElement("div");
    sysDiv.className = "chat-msg system";
    sysDiv.textContent = "Calling: " + d.tool_name + "...";
    if (msgs) { msgs.appendChild(sysDiv); msgs.scrollTop = msgs.scrollHeight; }
  });

  es.addEventListener("chat-done", function(e) {
    var d = safeJSON(e.data) || {};
    targetEl.innerHTML = renderMarkdown(d.full_response);
    aipickStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = t("chat.done");
    es.close();
  });

  es.addEventListener("chat-error", function(e) {
    var d = safeJSON(e.data) || {};
    targetEl.innerHTML = "Error: " + esc(d.message);
    aipickStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = "";
    es.close();
  });

  es.onerror = function() {
    if (es.readyState === EventSource.CLOSED) {
      aipickStreaming = false;
      if (btn) btn.disabled = false;
    }
  };
}

function newAipickThread() {
  aipickThreadId = null;
  updateAipickDownloadBtns();
  renderAipickThreadList();
  var msgs = document.getElementById("aipick-expanded-messages");
  if (msgs) msgs.innerHTML = '<div class="chat-msg system">' + t("chat.welcomeAipickNew") + '</div>';
  var titleEl = document.getElementById("aipick-expanded-title");
  var subEl = document.getElementById("aipick-expanded-subtitle");
  if (titleEl) titleEl.textContent = "AI Smart Pick";
  if (subEl) subEl.textContent = "New conversation";
}

// ===== Quick actions =====

function advisoryQuickAsk(question) {
  var input = document.getElementById("advisory-expanded-input");
  if (input) { input.value = question; advisorySend(); }
}

// ===== Ticker Selector =====

var advisoryTickersLoaded = false;

function loadAdvisoryTickers() {
  var chips = document.getElementById("advisory-ticker-chips");
  var statusEl = document.getElementById("advisory-ticker-status");
  if (!chips || !statusEl) return;
  
  statusEl.textContent = t("common.loading");
  fetch("/api/results/tickers")
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var tickers = data.tickers || [];
      advisoryTickersLoaded = true;
      if (tickers.length === 0) {
        chips.innerHTML = "";
        statusEl.textContent = t("chat.noTickers");
        return;
      }
      statusEl.textContent = t("chat.stocksCount", {n: tickers.length});
      var html = "";
      tickers.forEach(function(item) {
        var t = typeof item === "string" ? item : item.ticker;
        var name = (typeof item === "object" && item.name) ? item.name : "";
        var label = name ? t + " " + name : t;
        html += '<button class="btn advisory-ticker-chip" data-ticker="' + t + '" style="font-size:10px;padding:2px 6px;background:var(--accent);color:#fff;border-radius:10px;cursor:pointer;border:none;" title="' + label + '">' + label + '</button>';
      });
      chips.innerHTML = html;
      
      chips.querySelectorAll(".advisory-ticker-chip").forEach(function(chip) {
        chip.addEventListener("click", function() {
          var ticker = this.dataset.ticker;
          selectAdvisoryTickerForChat(ticker);
        });
      });
    })
    .catch(function(err) {
      statusEl.textContent = t("chat.failed");
    });
}

function selectAdvisoryTickerForChat(ticker) {
  var input = document.getElementById("advisory-expanded-input");
  if (input) {
    input.value = "Tell me about " + ticker;
    input.focus();
  }
}

// ============================================================================
// Advisory Tab Event Bindings (replaces inline onclick)
// ============================================================================

// Voice input for expanded advisory panel
var voiceRecognition = null;
var voiceListening = false;

function voiceToggleExpanded() {
  var SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    showToast(t("chat.voiceUnsupported"));
    return;
  }
  if (voiceListening) {
    if (voiceRecognition) voiceRecognition.stop();
    return;
  }
  voiceRecognition = new SpeechRecognition();
  voiceRecognition.lang = "zh-CN";
  voiceRecognition.interimResults = true;
  voiceRecognition.continuous = false;
  var micBtn = document.getElementById("advisory-expanded-mic-btn");
  var voiceStatus = document.getElementById("advisory-expanded-voice-status");
  voiceRecognition.onstart = function() {
    voiceListening = true;
    if (micBtn) micBtn.style.background = "#ffebee";
    if (voiceStatus) { voiceStatus.style.display = "block"; voiceStatus.textContent = t("chat.listening"); }
  };
  voiceRecognition.onresult = function(event) {
    var t = "";
    for (var i = event.resultIndex; i < event.results.length; i++) t += event.results[i][0].transcript;
    var inp = document.getElementById("advisory-expanded-input");
    if (inp) inp.value = t;
    if (voiceStatus) voiceStatus.textContent = t("chat.heardPrefix") + t;
  };
  voiceRecognition.onerror = function(event) {
    voiceListening = false;
    if (micBtn) micBtn.style.background = "";
    if (voiceStatus) { voiceStatus.textContent = t("chat.voiceError") + event.error; voiceStatus.style.color = "red"; }
  };
  voiceRecognition.onend = function() {
    voiceListening = false;
    if (micBtn) micBtn.style.background = "";
    if (voiceStatus) voiceStatus.style.display = "none";
  };
  voiceRecognition.start();
}

function speakLastExpanded() {
  var spk = document.getElementById("advisory-expanded-speak-btn");
  var text = spk ? (spk.dataset.text || "") : "";
  if (!text) {
    var msgs = document.getElementById("advisory-expanded-messages");
    var pmMsgs = msgs ? msgs.querySelectorAll(".chat-msg.pm .markdown-body") : [];
    if (pmMsgs.length > 0) text = pmMsgs[pmMsgs.length-1].textContent || "";
  }
  if (!text || !text.trim()) return;
  text = text.replace(/[#*_`\[\]()>|-]/g, " ").replace(/\s+/g, " ").trim();
  window.speechSynthesis.cancel();
  var u = new SpeechSynthesisUtterance(text);
  u.lang = text.match(/[\u4e00-\u9fff]/) ? "zh-CN" : "en-US";
  u.rate = 1.0;
  window.speechSynthesis.speak(u);
}

// Patch: fix any remaining showChatPanel voice reference
var _origShowChatPanel2 = showChatPanel;
showChatPanel = function(message) {
  _origShowChatPanel2(message);
  setTimeout(function() {
    var area = document.querySelector("#chat-panel .chat-input-area");
    if (area && !area.querySelector(".chat-mic-btn")) {
      var mic = document.createElement("button");
      mic.type = "button";
      mic.className = "chat-mic-btn";
      mic.id = "live-mic-btn";
      mic.textContent = String.fromCodePoint(0x1F3A4);
      mic.title = t("chat.voiceInput");
      mic.onclick = function() { voiceToggleExpanded(); };
      area.insertBefore(mic, area.firstChild);
    }
  }, 100);
};


// Advisory resize handle: drag to adjust message/input split
(function initAdvisoryResize() {
  var handle = document.getElementById("advisory-resize-handle");
  var expanded = document.getElementById("advisory-expanded");
  var messages = document.getElementById("advisory-expanded-messages");
  if (!handle || !expanded || !messages) return;

  var isResizing = false;
  var startY = 0;
  var startMsgH = 0;

  function onMouseMove(e) {
    if (!isResizing) return;
    var delta = e.clientY - startY;
    var newH = startMsgH + delta;
    var containerH = expanded.clientHeight;
    // Allow messages to shrink to 80px or grow to 90% of container
    var maxH = containerH * 0.9;
    newH = Math.max(80, Math.min(newH, maxH));
    messages.style.flex = "0 0 " + newH + "px";
    messages.style.minHeight = newH + "px";
  }

  function onMouseUp() {
    if (!isResizing) return;
    isResizing = false;
    handle.style.background = "transparent";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }

  handle.addEventListener("mousedown", function(e) {
    isResizing = true;
    startY = e.clientY;
    startMsgH = messages.offsetHeight;
    handle.style.background = "var(--accent)";
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    e.preventDefault();
  });
})();

// Advisory left panel resizer: drag to adjust thread list / tickers / experience split
(function initAdvisoryPanelResize() {
  var handle = document.getElementById("advisory-resizer");
  var panel = document.getElementById("tab-advisory");
  if (!handle || !panel) return;

  var isResizing = false;
  var startY = 0;
  var startFlex = 0;
  var threadList = document.getElementById("advisory-thread-list");

  function onMouseMove(e) {
    if (!isResizing) return;
    var delta = e.clientY - startY;
    var newFlex = Math.max(60, startFlex + delta);
    if (threadList) {
      threadList.style.flex = "0 0 " + newFlex + "px";
    }
  }

  function onMouseUp() {
    if (!isResizing) return;
    isResizing = false;
    handle.style.background = "transparent";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }

  handle.addEventListener("mousedown", function(e) {
    isResizing = true;
    startY = e.clientY;
    startFlex = threadList ? threadList.offsetHeight : 200;
    handle.style.background = "var(--accent)";
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    e.preventDefault();
  });
})();

// AI Pick resize handle — mirror of advisory
(function initAipickResize() {
  var handle = document.getElementById("aipick-resize-handle");
  var expanded = document.getElementById("aipick-expanded");
  var messages = document.getElementById("aipick-expanded-messages");
  if (!handle || !expanded || !messages) return;

  var isResizing = false;
  var startY = 0;
  var startMsgH = 0;

  function onMouseMove(e) {
    if (!isResizing) return;
    var delta = e.clientY - startY;
    var newH = startMsgH + delta;
    var containerH = expanded.clientHeight;
    var maxH = containerH * 0.9;
    newH = Math.max(80, Math.min(newH, maxH));
    messages.style.flex = "0 0 " + newH + "px";
    messages.style.minHeight = newH + "px";
  }

  function onMouseUp() {
    if (!isResizing) return;
    isResizing = false;
    handle.style.background = "transparent";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }

  handle.addEventListener("mousedown", function(e) {
    isResizing = true;
    startY = e.clientY;
    startMsgH = messages.offsetHeight;
    handle.style.background = "var(--accent)";
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    e.preventDefault();
  });
})();

// Strategy resize handle — drag to adjust message/input split
(function initStrategyResize() {
  var handle = document.getElementById("strategy-resize-handle");
  var expanded = document.getElementById("strategy-expanded");
  var messages = document.getElementById("strategy-expanded-messages");
  if (!handle || !expanded || !messages) return;

  var isResizing = false;
  var startY = 0;
  var startMsgH = 0;

  function onMouseMove(e) {
    if (!isResizing) return;
    var delta = e.clientY - startY;
    var newH = startMsgH + delta;
    var containerH = expanded.clientHeight;
    var maxH = containerH * 0.9;
    newH = Math.max(80, Math.min(newH, maxH));
    messages.style.flex = "0 0 " + newH + "px";
    messages.style.minHeight = newH + "px";
  }

  function onMouseUp() {
    if (!isResizing) return;
    isResizing = false;
    handle.style.background = "transparent";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }

  handle.addEventListener("mousedown", function(e) {
    isResizing = true;
    startY = e.clientY;
    startMsgH = messages.offsetHeight;
    handle.style.background = "var(--accent)";
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    e.preventDefault();
  });
})();

// Backtest resize handle — drag to adjust message/input split
(function initBacktestResize() {
  var handle = document.getElementById("backtest-resize-handle");
  var expanded = document.getElementById("backtest-expanded");
  var messages = document.getElementById("backtest-expanded-messages");
  if (!handle || !expanded || !messages) return;

  var isResizing = false;
  var startY = 0;
  var startMsgH = 0;

  function onMouseMove(e) {
    if (!isResizing) return;
    var delta = e.clientY - startY;
    var newH = startMsgH + delta;
    var containerH = expanded.clientHeight;
    var maxH = containerH * 0.9;
    newH = Math.max(80, Math.min(newH, maxH));
    messages.style.flex = "0 0 " + newH + "px";
    messages.style.minHeight = newH + "px";
  }

  function onMouseUp() {
    if (!isResizing) return;
    isResizing = false;
    handle.style.background = "transparent";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }

  handle.addEventListener("mousedown", function(e) {
    isResizing = true;
    startY = e.clientY;
    startMsgH = messages.offsetHeight;
    handle.style.background = "var(--accent)";
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    e.preventDefault();
  });
})();


// Advisory Tab + Expanded Panel Event Bindings
(function bindAdvisory() {
  // Quick action buttons in left panel
  var qbtns = document.querySelectorAll(".advisory-quick-btn");
  qbtns.forEach(function(btn) {
    btn.addEventListener("click", function() {
      var q = this.dataset.question;
      if (q) advisoryQuickAsk(q);
    });
  });

  // New thread button
  var newBtn = document.getElementById("advisory-new-thread-btn");
  if (newBtn) newBtn.addEventListener("click", newAdvisoryThread);

  // Ticker load button
  var loadBtn = document.getElementById("advisory-load-tickers-btn");
  if (loadBtn) loadBtn.addEventListener("click", loadAdvisoryTickers);

  // Right panel: mic button
  var micBtn = document.getElementById("advisory-expanded-mic-btn");
  if (micBtn) micBtn.addEventListener("click", function() { voiceToggleExpanded(); });

  // Right panel: send button
  var sendBtn = document.getElementById("advisory-expanded-send-btn");
  if (sendBtn) sendBtn.addEventListener("click", advisorySend);

  // Right panel: speak button
  var spkBtn = document.getElementById("advisory-expanded-speak-btn");
  if (spkBtn) spkBtn.addEventListener("click", function() { speakLastExpanded(); });

  // Right panel: input enter key
  var input = document.getElementById("advisory-expanded-input");
  if (input) input.addEventListener("keydown", function(e) { if (e.key === "Enter") advisorySend(); });

  // Collapse button
  var collapseBtn = document.getElementById("advisory-collapse-btn");
  if (collapseBtn) {
    collapseBtn.addEventListener("click", function() {
      var app = document.getElementById("app");
      if (app) app.classList.remove("advisory-mode");
      var analyzeBtn = document.querySelector('[data-tab="analyze"]');
      if (analyzeBtn) analyzeBtn.click();
    });
  }

  // Load threads when advisory tab first opens
  var advisoryTab = document.querySelector('[data-tab="advisory"]');
  if (advisoryTab) {
    advisoryTab.addEventListener("click", function() {
      loadAdvisoryThreads();
      if (!advisoryTickersLoaded) loadAdvisoryTickers();
    });
  }
})();

// AI Pick Tab + Expanded Panel Event Bindings
(function bindAipick() {
  var sendBtn = document.getElementById("aipick-expanded-send-btn");
  if (sendBtn) sendBtn.addEventListener("click", aipickSend);
  var input = document.getElementById("aipick-expanded-input");
  if (input) input.addEventListener("keydown", function(e) { if (e.key === "Enter") aipickSend(); });
  var newBtn = document.getElementById("aipick-new-thread-btn");
  if (newBtn) newBtn.addEventListener("click", newAipickThread);
  var collapseBtn = document.getElementById("aipick-collapse-btn");
  if (collapseBtn) collapseBtn.addEventListener("click", function() {
    var app = document.getElementById("app");
    if (app) app.classList.remove("aipick-mode");
    var analyzeBtn = document.querySelector('[data-tab="analyze"]');
    if (analyzeBtn) analyzeBtn.click();
  });
  var aipickTab = document.querySelector('[data-tab="aipick"]');
  if (aipickTab) aipickTab.addEventListener("click", function() { loadAipickThreads(); });
  // Ranking toggle
  var rankCb = document.getElementById("aipick-rank-checkbox");
  if (rankCb) rankCb.addEventListener("change", function() { loadAipickRankings(); });
})();

// ============================================================================
// Prediction Agent Tab
// ============================================================================

function loadPredictionThreads() {
  var list = document.getElementById("prediction-thread-list");
  if (!list) return;
  fetch("/api/chat/threads")
    .then(function(r) { return r.json(); })
    .then(function(threads) {
      predictionThreads = threads || [];
      renderPredictionThreadList();
    })
    .catch(function() {});
}

function renderPredictionThreadList() {
  var list = document.getElementById("prediction-thread-list");
  if (!list) return;
  if (!predictionThreads.length) {
    list.innerHTML = '<span class="no-results" style="padding:8px;font-size:11px;">No predictions yet. Click + New to start.</span>';
    return;
  }
  var html = "";
  predictionThreads.forEach(function(th) {
    var title = (th.title || "Prediction");
    if (title.length > 28) title = title.substring(0, 28) + "...";
    var count = th.message_count || 0;
    var date = (th.updated_at || th.created_at || "").substring(0, 10);
    var activeClass = (th.thread_id === predictionThreadId) ? " active" : "";
    var activeBorder = (th.thread_id === predictionThreadId) ? 'var(--accent)' : 'transparent';
    var activeBg = (th.thread_id === predictionThreadId) ? 'var(--accent-light, #e8f0fe)' : 'transparent';
    html += '<div class="advisory-thread-item' + activeClass + '" data-thread-id="' + th.thread_id + '" style="padding:8px 10px;margin:2px 0;border-radius:6px;cursor:pointer;border:1px solid ' + activeBorder + ';background:' + activeBg + ';">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;">';
    html += '<span style="font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escHtml(title) + '</span>';
    html += '<button class="advisory-delete-thread-btn" data-thread-id="' + th.thread_id + '" style="font-size:14px;background:none;border:none;cursor:pointer;color:var(--text-muted);padding:0 4px;margin-left:4px;flex-shrink:0;" title="Delete">&times;</button>';
    html += '</div>';
    html += '<div style="font-size:10px;color:var(--text-muted);">' + t("chat.messagesCount", {n: count}) + ' · ' + date + '</div>';
    html += '</div>';
  });
  list.innerHTML = html;
  list.querySelectorAll(".advisory-thread-item").forEach(function(item) {
    item.addEventListener("click", function(e) {
      if (e.target.classList.contains("advisory-delete-thread-btn")) return;
      loadPredictionThread(item.dataset.threadId);
    });
  });
  list.querySelectorAll(".advisory-delete-thread-btn").forEach(function(btn) {
    btn.addEventListener("click", function(e) {
      e.stopPropagation();
      deletePredictionThread(btn.dataset.threadId);
    });
  });
}

function loadPredictionThread(threadId) {
  predictionThreadId = threadId;
  updatePredictionDownloadBtns();
  renderPredictionThreadList();
  var msgs = document.getElementById("prediction-expanded-messages");
  if (!msgs) return;
  msgs.innerHTML = '<div class="chat-msg system">' + t("common.loading") + '</div>';
  var thread = predictionThreads.find(function(t) { return t.thread_id === threadId; });
  var titleEl = document.getElementById("pred-expanded-title");
  var subEl = document.getElementById("pred-expanded-subtitle");
  if (titleEl) titleEl.textContent = thread ? (thread.title || "Prediction") : "Prediction Agent";
  if (subEl) subEl.textContent = thread ? t("chat.messagesCount", {n: thread.message_count || 0}) : "";
  fetchJsonRetry("/api/chat/threads/" + threadId + "/messages",
    function(messages) {
      if (!msgs) return;
      msgs.innerHTML = "";
      messages.forEach(function(m) {
        var div = document.createElement("div");
        div.className = "chat-msg " + (m.role === "user" ? "user" : "pm");
        var mdDiv = document.createElement("div");
        mdDiv.className = "markdown-body";
        mdDiv.innerHTML = renderMarkdown(m.content);
        div.appendChild(mdDiv);
        msgs.appendChild(div);
      });
      msgs.scrollTop = msgs.scrollHeight;
    },
    function(e) {
      msgs.innerHTML = '<div class="chat-msg system">' + t("chat.failedToLoad") + (e && e.message ? " (" + escHtml(e.message) + ")" : "") + '</div>';
    });
}

function updatePredictionDownloadBtns() {
  var dlMdBtn = document.getElementById("prediction-download-md-btn");
  var dlPdfBtn = document.getElementById("prediction-download-pdf-btn");
  if (!dlMdBtn || !dlPdfBtn) return;
  if (predictionThreadId) {
    dlMdBtn.style.display = ""; dlPdfBtn.style.display = "";
    dlMdBtn.onclick = function() { window.open("/api/chat/threads/" + predictionThreadId + "/download?format=md"); };
    dlPdfBtn.onclick = function() { window.open("/api/chat/threads/" + predictionThreadId + "/download?format=pdf"); };
  } else {
    dlMdBtn.style.display = "none"; dlPdfBtn.style.display = "none";
  }
}

function predictionSend() {
  var input = document.getElementById("prediction-expanded-input");
  var question = (input ? input.value : "").trim();
  if (!question || predictionStreaming) return;
  if (input) input.value = "";
  predictionStreaming = true;
  var msgs = document.getElementById("prediction-expanded-messages");
  var status = document.getElementById("prediction-expanded-status");
  var btn = document.getElementById("prediction-expanded-send-btn");
  if (btn) btn.disabled = true;
  if (status) status.textContent = "Analyzing...";
  var userDiv = document.createElement("div");
  userDiv.className = "chat-msg user";
  userDiv.textContent = question;
  if (msgs) { msgs.appendChild(userDiv); msgs.scrollTop = msgs.scrollHeight; }
  var pmDiv = document.createElement("div");
  pmDiv.className = "chat-msg pm";
  var mdDiv = document.createElement("div");
  mdDiv.className = "markdown-body";
  mdDiv.innerHTML = "<em>Running prediction models...</em>";
  pmDiv.appendChild(mdDiv);
  if (msgs) { msgs.appendChild(pmDiv); msgs.scrollTop = msgs.scrollHeight; }
  if (!predictionThreadId) predictionThreadId = "pred_" + Date.now().toString(36) + "_" + Math.random().toString(36).substr(2, 6);
  var body = { question: question, thread_id: predictionThreadId };
  fetch("/api/prediction/chat",
    { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      predictionThreadId = data.thread_id;
      updatePredictionDownloadBtns();
      if (status) status.textContent = "Running...";
      streamPredictionResponse(data.stream_url, question, mdDiv);
    })
    .catch(function(err) {
      mdDiv.innerHTML = "<em>" + t("chat.errorPrefix") + escHtml(String(err)) + "</em>";
      predictionStreaming = false;
      if (btn) btn.disabled = false;
      if (status) status.textContent = "";
    });
}

function streamPredictionResponse(streamUrl, question, targetEl) {
  var status = document.getElementById("prediction-expanded-status");
  var btn = document.getElementById("prediction-expanded-send-btn");
  var msgs = document.getElementById("prediction-expanded-messages");
  var es = new EventSource(streamUrl);
  es.addEventListener("chat-tool-call", function(e) {
    var d = safeJSON(e.data) || {};
    var sysDiv = document.createElement("div");
    sysDiv.className = "chat-msg system";
    sysDiv.textContent = "Calling: " + d.tool_name + "...";
    if (msgs) { msgs.appendChild(sysDiv); msgs.scrollTop = msgs.scrollHeight; }
  });
  es.addEventListener("chat-done", function(e) {
    var d = safeJSON(e.data) || {};
    targetEl.innerHTML = renderMarkdown(d.full_response);
    predictionStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = t("chat.done");
    es.close();
    loadPredictionThreads();
    var titleEl = document.getElementById("pred-expanded-title");
    var subEl = document.getElementById("pred-expanded-subtitle");
    var thread = predictionThreads.find(function(t) { return t.thread_id === predictionThreadId; });
    if (titleEl && thread) titleEl.textContent = thread.title || "Prediction";
    if (subEl) subEl.textContent = predictionThreadId ? (thread ? t("chat.messagesCount", {n: thread.message_count || 0}) : "") : "";
  });
  es.addEventListener("chat-error", function(e) {
    var d = safeJSON(e.data) || {};
    targetEl.innerHTML = "<em>Error: " + escHtml(d.message) + "</em>";
    predictionStreaming = false;
    if (btn) btn.disabled = false;
    if (status) status.textContent = "";
    es.close();
  });
  es.onerror = function() {
    if (es.readyState === EventSource.CLOSED) {
      predictionStreaming = false;
      if (btn) btn.disabled = false;
      if (status) status.textContent = "";
    }
  };
}

function newPredictionThread() {
  predictionThreadId = null;
  updatePredictionDownloadBtns();
  renderPredictionThreadList();
  var msgs = document.getElementById("prediction-expanded-messages");
  if (msgs) msgs.innerHTML = '<div class="chat-msg system">' + t("chat.welcomePredictionFull") + '</div>';
  var titleEl = document.getElementById("pred-expanded-title");
  var subEl = document.getElementById("pred-expanded-subtitle");
  if (titleEl) titleEl.textContent = "Prediction Agent";
  if (subEl) subEl.textContent = "New prediction";
  var input = document.getElementById("prediction-expanded-input");
  if (input) input.focus();
}

function deletePredictionThread(threadId) {
  if (!confirm("Delete this prediction?")) return;
  fetch("/api/chat/threads/" + threadId, { method: "DELETE" })
    .then(function() {
      if (predictionThreadId === threadId) {
        predictionThreadId = null;
        var msgs = document.getElementById("prediction-expanded-messages");
        if (msgs) msgs.innerHTML = '<div class="chat-msg system">' + t("chat.welcomePredictionShort") + '</div>';
        var titleEl = document.getElementById("pred-expanded-title");
        if (titleEl) titleEl.textContent = "Prediction Agent";
        var subEl = document.getElementById("pred-expanded-subtitle");
        if (subEl) subEl.textContent = "New prediction";
        updatePredictionDownloadBtns();
      }
      loadPredictionThreads();
    });
}

// Event bindings
(function bindPrediction() {
  var sendBtn = document.getElementById("prediction-expanded-send-btn");
  if (sendBtn) sendBtn.addEventListener("click", predictionSend);
  var input = document.getElementById("prediction-expanded-input");
  if (input) input.addEventListener("keydown", function(e) { if (e.key === "Enter") predictionSend(); });
  var newBtn = document.getElementById("prediction-new-thread-btn");
  if (newBtn) newBtn.addEventListener("click", newPredictionThread);
  var collapseBtn = document.getElementById("prediction-collapse-btn");
  if (collapseBtn) collapseBtn.addEventListener("click", function() {
    var app = document.getElementById("app");
    if (app) app.classList.remove("prediction-mode");
    var analyzeBtn = document.querySelector('[data-tab="analyze"]');
    if (analyzeBtn) analyzeBtn.click();
  });
  var predTab = document.querySelector('[data-tab="prediction"]');
  if (predTab) predTab.addEventListener("click", function() { loadPredictionThreads(); });
})();

(function initPredictionResize() {
  var handle = document.getElementById("prediction-resize-handle");
  var expanded = document.getElementById("prediction-expanded");
  var messages = document.getElementById("prediction-expanded-messages");
  if (!handle || !expanded || !messages) return;
  var isResizing = false, startY = 0, startMsgH = 0;
  function onMouseMove(e) {
    if (!isResizing) return;
    var delta = e.clientY - startY;
    var newH = startMsgH + delta;
    var containerH = expanded.clientHeight;
    var maxH = containerH * 0.9;
    newH = Math.max(80, Math.min(newH, maxH));
    messages.style.flex = "0 0 " + newH + "px";
    messages.style.minHeight = newH + "px";
  }
  function onMouseUp() {
    if (!isResizing) return;
    isResizing = false;
    handle.style.background = "transparent";
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }
  handle.addEventListener("mousedown", function(e) {
    isResizing = true;
    startY = e.clientY;
    startMsgH = messages.offsetHeight;
    handle.style.background = "var(--accent)";
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    e.preventDefault();
  });
})();
