(function(){
"use strict";

var haCfg = { active: false, selectedRunIds: [], currentThreadId: null, eventSource: null };

var haExp = document.getElementById("ha-expanded");
var haMsgs = document.getElementById("ha-messages");
var haInput = document.getElementById("ha-input");
var haSend = document.getElementById("ha-send-btn");
var haCollapse = document.getElementById("ha-collapse-btn");
var haStatus = document.getElementById("ha-status");
var haABtn = document.getElementById("ha-analyze-btn");
var haSBtn = document.getElementById("ha-analyze-selected-btn");
var haProgress = document.getElementById("ha-progress");
var haPBar = document.getElementById("ha-progress-bar");
var haPLabel = document.getElementById("ha-progress-label");
var haPPct = document.getElementById("ha-progress-pct");
var haMdBtn = document.getElementById("ha-download-md-btn");

function setProgress(pct, label) {
  if (!haProgress || !haPBar || !haPLabel || !haPPct) return;
  haProgress.style.display = "block";
  haPBar.style.width = pct + "%";
  haPLabel.textContent = label || "Analyzing...";
  haPPct.textContent = pct + "%";
}

function showPanel() {
  console.log("[HA] showPanel called");
  haCfg.currentThreadId = "ha-" + Date.now();
  var app = document.getElementById("app");
  var rp = document.getElementById("right-panel");
  if (app) { app.classList.remove("advisory-mode","aipick-mode","prediction-mode","strategy-mode","backtest-mode"); app.classList.add("historyagent-mode"); }
  console.log("[HA] app class:", app ? app.className : "null");
  // Force right-panel to flex layout (bypass CSS)
  if (rp) { rp.style.display = "flex"; rp.style.flexDirection = "column"; rp.style.height = "100%"; rp.style.minHeight = "0"; rp.style.overflow = "hidden"; }
  console.log("[HA] right-panel display:", rp ? rp.style.display : "null");
  // Hide other content in right panel
  ["results-container", "calibration-panel", "chart-container", "comparison-container"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = "none";
  });
  // Show HA panel
  console.log("[HA] haExp element:", haExp);
  if (haExp) {
    console.log("[HA] haExp before - display:", haExp.style.display, "hidden:", haExp.classList.contains("hidden"));
    haExp.classList.remove("hidden");
    haExp.style.display = "flex";
    console.log("[HA] haExp after - display:", haExp.style.display);
  }
  // Clear previous messages
  if (haMsgs) { haMsgs.innerHTML = '<div class="chat-msg system">Welcome to History Agent<br>Select analysis records on the left, then ask questions or click "Analyze" to extract experiences.</div>'; }
  console.log("[HA] showPanel complete");
}

function collapse() {
  var app = document.getElementById("app");
  var rp = document.getElementById("right-panel");
  if (app) app.classList.remove("historyagent-mode");
  // Restore right-panel to normal scroll
  if (rp) { rp.style.display = ""; rp.style.flexDirection = ""; rp.style.height = ""; rp.style.minHeight = ""; rp.style.overflow = ""; }
  // Show hidden content back
  ["results-container", "calibration-panel", "chart-container", "comparison-container"].forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.style.display = "";
  });
  if (haExp) { haExp.classList.add("hidden"); haExp.style.display = "none"; }
  if (haCfg.eventSource) { haCfg.eventSource.close(); haCfg.eventSource = null; }
  if (haStatus) haStatus.textContent = "";
  if (haProgress) haProgress.style.display = "none";
}

function getRunIds() {
  var ids = [];
  var checks = document.querySelectorAll("#history-list .hi-check:checked");
  checks.forEach(function(c) { ids.push(c.value); });
  if (typeof window.getSelectedHistoryIds === "function") {
    var gIds = window.getSelectedHistoryIds();
    if (gIds && gIds.length > 0) ids = gIds;
  }
  haCfg.selectedRunIds = ids;
  return ids;
}

function analyzeAll() {
  showPanel();
  if (haInput) { haInput.value = "Analyze all historical records and extract experiences"; sendMsg(); }
}

function analyzeSelected() {
  var ids = getRunIds();
  if (ids.length === 0) {
    appendMsg("system", "⚠ Please select at least one record first (check boxes in the list below)");
    showPanel();
    return;
  }
  showPanel();
  if (haInput) { haInput.value = "Analyze " + ids.length + " selected records and extract experiences"; sendMsg(); }
}

function sendMsg() {
  if (!haInput || !haInput.value.trim()) return;
  var msg = haInput.value.trim();
  haInput.value = "";

  appendMsg("user", msg);
  if (haStatus) haStatus.textContent = "Analyzing...";
  if (haSend) haSend.disabled = true;

  var runIds = getRunIds();
  var tid = haCfg.currentThreadId || "ha-" + Date.now();
  haCfg.currentThreadId = tid;

  var params = "thread_id=" + encodeURIComponent(tid) + "&question=" + encodeURIComponent(msg) + "&run_ids=" + encodeURIComponent(runIds.join(","));
  if (haCfg.eventSource) haCfg.eventSource.close();
  var es = new EventSource("/api/history-agent/stream?" + params);
  haCfg.eventSource = es;

  es.addEventListener("ha-start", function(e) { setProgress(10, "Loading records..."); });
  es.addEventListener("ha-tool", function(e) {
    var d = JSON.parse(e.data);
    var cur = parseInt(haPBar ? haPBar.style.width : "0");
    setProgress(Math.min(cur + 20, 80), "Running " + (d.tool_name || "tool") + "...");
    appendMsg("system", "[running " + d.tool_name + "]");
  });
  es.addEventListener("ha-tool-result", function(e) {
    var d = JSON.parse(e.data);
    var cur = parseInt(haPBar ? haPBar.style.width : "0");
    setProgress(Math.min(cur + 10, 90), "Processing results...");
  });
  es.addEventListener("ha-done", function(e) {
    var d = JSON.parse(e.data);
    setProgress(100, "Complete");
    appendMsg("assistant", d.full_response || "");
    var exps = d.experiences || [];
    if (exps.length > 0) {
      appendMsg("system", "Extracted " + exps.length + " experience(s) — pending review");
    }
    if (haStatus) haStatus.textContent = exps.length > 0 ? "Complete — " + exps.length + " experiences extracted" : "Complete";
    if (haSend) haSend.disabled = false;
    updateDownloadBtns();
    es.close(); haCfg.eventSource = null;
  });
  es.addEventListener("ha-error", function(e) {
    var d = JSON.parse(e.data);
    if (haProgress) haProgress.style.display = "none";
    appendMsg("system", "Error: " + (d.message || "Unknown error"));
    if (haStatus) haStatus.textContent = "Error";
    if (haSend) haSend.disabled = false;
    es.close(); haCfg.eventSource = null;
  });
  es.onerror = function() {
    if (haStatus) haStatus.textContent = "Connection error";
    if (haSend) haSend.disabled = false;
    es.close(); haCfg.eventSource = null;
  };
}

function appendMsg(role, content) {
  if (!haMsgs) return;
  var div = document.createElement("div");
  div.className = "chat-msg " + role;
  if (role === "assistant") {
    var mdDiv = document.createElement("div");
    mdDiv.className = "markdown-body";
    mdDiv.innerHTML = typeof window.renderMarkdown === "function" ? window.renderMarkdown(content) : content.replace(/\n/g, '<br>');
    div.appendChild(mdDiv);
  } else {
    div.textContent = content;
  }
  haMsgs.appendChild(div);
  haMsgs.scrollTop = haMsgs.scrollHeight;
}

function updateDownloadBtns() {
  if (!haMdBtn) return;
  var hasMessages = haMsgs && haMsgs.querySelectorAll(".chat-msg.assistant").length > 0;
  haMdBtn.style.display = hasMessages ? "" : "none";
}

function escapeHtml(s) {
  if (!s) return "";
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function init() {
  if (haSend) haSend.addEventListener("click", sendMsg);
  if (haInput) haInput.addEventListener("keydown", function(e) { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMsg(); } });
  if (haCollapse) haCollapse.addEventListener("click", collapse);
  if (haABtn) haABtn.addEventListener("click", analyzeAll);
  if (haSBtn) haSBtn.addEventListener("click", analyzeSelected);
  loadCalibrationStatus();
  loadSkillList();
  if (haMdBtn) haMdBtn.addEventListener("click", function() {
    var text = "";
    var msgs = haMsgs ? haMsgs.querySelectorAll(".chat-msg") : [];
    msgs.forEach(function(m) {
      var role = m.classList.contains("user") ? "User" : (m.classList.contains("assistant") ? "Assistant" : "System");
      var content = m.textContent || m.innerText || "";
      text += "**" + role + "**: " + content + "\n\n";
    });
    if (text) {
      var blob = new Blob([text], {type: "text/markdown"});
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "mentor-chat.md";
      a.click();
    }
  });
}

function loadCalibrationStatus() {
  fetch("/api/calibration/runs/latest").then(function(r){return r.json()}).then(function(d){
    var el=document.getElementById("cal-status-text");
    if(!el)return;
    if(!d||!d.id){el.textContent="System Calibration: No runs yet";return;}
    var stats=typeof d.stats=="string"?JSON.parse(d.stats||"{}"):(d.stats||{});
    var acc=stats.accuracy||0;
    el.textContent="System Calibration: last run "+(d.run_date||"").slice(0,10)+" | Accuracy: "+acc+"% | "+d.resolved_count+" resolved";
  }).catch(function(){var el=document.getElementById("cal-status-text");if(el)el.textContent="System Calibration: error loading"});
}

function loadSkillList() {
  fetch("/api/skill/summary").then(function(r){return r.json()}).then(function(data){
    var bar=document.getElementById("ha-skills-bar"),list=document.getElementById("ha-skill-list");
    if(!bar||!list)return;
    var versions = data.versions || [];
    if(versions.length>0){
      bar.style.display="block";
      var active = data.active || data.latest || versions[versions.length - 1];
      var usable = data.usable_versions || [];
      var activeInUsable = usable.some(function(v){ return v.version === active.version; });
      var choices = usable.slice();
      if (!activeInUsable && active) choices.push(active);
      choices.sort(function(a,b){ return (b.version || 0) - (a.version || 0); });
      var options = choices.map(function(v) {
        var label = "v" + v.version + " · " + (v.rule_count || 0) + " rules · " + (v.created || "");
        return '<option value="' + v.version + '"' + (v.version === active.version ? " selected" : "") + '>' + escapeHtml(label) + '</option>';
      }).join("");
      var draftNote = data.draft_count > 0
        ? '<div class="ha-skill-note">' + data.draft_count + ' empty draft version(s) hidden from active choices.</div>'
        : "";
      var activeMeta = "v" + active.version + " · " + (active.rule_count || 0) + " rules · " + (active.active_exp_count || 0) + " active experiences";
      list.innerHTML =
        '<div class="ha-skill-summary">' +
          '<div class="ha-skill-title-row"><span class="ha-skill-title">Analyst Skill</span><span class="ha-skill-active">ACTIVE</span></div>' +
          '<div class="ha-skill-meta">' + escapeHtml(activeMeta) + '</div>' +
          '<div class="ha-skill-controls">' +
            '<select id="ha-skill-select" class="ha-skill-select">' + options + '</select>' +
            '<button type="button" id="ha-skill-use-btn" class="ha-skill-btn">Use</button>' +
            '<button type="button" id="ha-skill-view-btn" class="ha-skill-btn">View</button>' +
          '</div>' +
          draftNote +
          '<div id="ha-skill-preview" class="ha-skill-preview" style="display:none;"></div>' +
        '</div>';
      var useBtn = document.getElementById("ha-skill-use-btn");
      var viewBtn = document.getElementById("ha-skill-view-btn");
      if (useBtn) useBtn.addEventListener("click", setSelectedSkillActive);
      if (viewBtn) viewBtn.addEventListener("click", viewSelectedSkill);
    }else{
      bar.style.display="none";
    }
  }).catch(function(){});
}

function selectedSkillVersion() {
  var select = document.getElementById("ha-skill-select");
  return select ? parseInt(select.value, 10) : 0;
}

function setSelectedSkillActive() {
  var version = selectedSkillVersion();
  if (!version) return;
  var btn = document.getElementById("ha-skill-use-btn");
  if (btn) { btn.textContent = "Using..."; btn.disabled = true; }
  fetch("/api/skill/active/" + encodeURIComponent(version), {method:"POST"})
    .then(function(r){ if(!r.ok) throw new Error("Failed to set active skill"); return r.json(); })
    .then(function(data){
      appendMsg("system", "Analyst Skill v" + data.version + " is now active for future analyses.");
      loadSkillList();
    })
    .catch(function(e){ appendMsg("system", "Skill switch failed: " + e.message); })
    .finally(function(){ if (btn) { btn.textContent = "Use"; btn.disabled = false; } });
}

function viewSelectedSkill() {
  var version = selectedSkillVersion();
  var preview = document.getElementById("ha-skill-preview");
  if (!version || !preview) return;
  preview.style.display = "block";
  preview.textContent = "Loading...";
  fetch("/api/skill/versions/" + encodeURIComponent(version))
    .then(function(r){ if(!r.ok) throw new Error("Failed to load skill"); return r.json(); })
    .then(function(data){
      preview.innerHTML =
        '<div class="ha-skill-preview-title">v' + data.version + ' · ' + escapeHtml(data.metadata.created || "") + '</div>' +
        '<pre>' + escapeHtml(data.content || "") + '</pre>';
    })
    .catch(function(e){ preview.textContent = e.message; });
}
function triggerCalibrationBtn() {
  var btn=document.getElementById("ha-calibration-run-btn");
  if(btn){btn.textContent="Running...";btn.disabled=true}
  fetch("/api/calibration/run",{method:"POST"}).then(function(r){return r.json()}).then(function(){
    loadCalibrationStatus();if(btn){btn.textContent="Run Calibration";btn.disabled=false}
  }).catch(function(){if(btn){btn.textContent="Run Calibration";btn.disabled=false}});
}
function extractExperiencesBtn() {
  var btn=document.getElementById("ha-extract-experiences-btn");
  if(btn){btn.textContent="Extracting...";btn.disabled=true}
  fetch("/api/advisory/experiences/extract",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({})
  }).then(function(r){return r.json()}).then(function(d){
    alert("Extracted "+(d.proposals||0)+" experience(s).");
    loadCalibrationStatus();loadSkillList();
    if(btn){btn.textContent="Extract";btn.disabled=false}
  }).catch(function(){if(btn){btn.textContent="Extract";btn.disabled=false}});
}
window.analyzeAllRecords = analyzeAll;
window.analyzeSelectedRecords = analyzeSelected;
window.collapseHA = collapse;
window.showHistoryAgentPanel = showPanel;
window.triggerCalibrationBtn = triggerCalibrationBtn;
window.extractExperiencesBtn = extractExperiencesBtn;

if (document.readyState === "loading") { document.addEventListener("DOMContentLoaded", init); }
else { init(); }
})();
