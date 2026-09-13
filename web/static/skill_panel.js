(function(){
"use strict";

var panel = document.getElementById("skill-panel");
var list = document.getElementById("skill-version-list");

function loadVersions() {
  if (!list) return;
  list.innerHTML = "Loading...";
  fetch("/api/skill/summary")
    .then(function(r) { return r.json(); })
    .then(renderSummary)
    .catch(function() { list.innerHTML = '<span class="no-results">Failed to load</span>'; });
}

function renderSummary(summary) {
  if (!list) return;
  var versions = summary.versions || [];
  if (!versions || !versions.length) {
    list.innerHTML = '<span class="no-results">No skill versions yet</span>';
    return;
  }

  var active = summary.active || summary.latest || versions[versions.length - 1];
  var activeV = active.version || 0;
  var sourceVersions = summary.usable_versions && summary.usable_versions.length ? summary.usable_versions : versions;
  var visible = sourceVersions.slice().sort(function(a,b) {
    return (b.version || 0) - (a.version || 0);
  }).slice(0, 8);
  var html =
    '<div class="skill-current-card">' +
      '<div><span class="skill-current-title">Active Analyst Skill</span><span class="skill-active-badge">ACTIVE</span></div>' +
      '<div class="skill-current-meta">v' + activeV + ' · ' + (active.rule_count || 0) + ' rules · ' + (active.active_exp_count || 0) + ' active experiences</div>' +
      '<div class="skill-current-note">Used automatically by future Deep Analysis runs.</div>' +
    '</div>';

  if (summary.draft_count > 0) {
    html += '<div class="skill-draft-note">' + summary.draft_count + ' empty draft version(s) hidden.</div>';
  }

  visible.forEach(function(v, idx) {
    var isActive = v.version === activeV;
    var prev = visible[idx + 1];
    var useBtn = isActive ? "" : '<button class="skill-use-btn" data-v="' + v.version + '">Use</button>';
    var diffBtn = prev ? '<button class="skill-diff-btn" data-v1="' + prev.version + '" data-v2="' + v.version + '">Diff</button>' : "";
    html += '<div class="skill-version-item' + (isActive ? ' active' : '') + '">' +
      '<div class="skill-version-main"><span class="skill-version-label">v' + v.version + '</span><span>' + (v.created || "") + '</span><span>' + (v.rule_count || 0) + ' rules</span></div>' +
      '<div class="skill-version-actions">' +
        '<button class="skill-view-btn" data-v="' + v.version + '">View</button>' + useBtn + diffBtn +
      '</div>' +
    '</div>';
  });
  list.innerHTML = html;
  wireButtons();
}

function wireButtons() {
  list.querySelectorAll(".skill-view-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var v = btn.getAttribute("data-v");
      fetch("/api/skill/versions/" + v)
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var viewer = document.getElementById("skill-viewer");
          if (viewer) {
            viewer.innerHTML = '<pre class="skill-view-pre">' + escapeHtml(data.content || "") + '</pre>';
            viewer.style.display = "";
          }
        });
    });
  });

  list.querySelectorAll(".skill-use-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var v = btn.getAttribute("data-v");
      fetch("/api/skill/active/" + v, {method:"POST"})
        .then(function(r) { if (!r.ok) throw new Error("Failed"); return r.json(); })
        .then(loadVersions);
    });
  });

  list.querySelectorAll(".skill-diff-btn").forEach(function(btn) {
    btn.addEventListener("click", function() {
      var v1 = btn.getAttribute("data-v1");
      var v2 = btn.getAttribute("data-v2");
      fetch("/api/skill/diff?v1=" + v1 + "&v2=" + v2)
        .then(function(r) { return r.json(); })
        .then(function(data) {
          var viewer = document.getElementById("skill-viewer");
          if (viewer) {
            var h = '<div class="skill-diff-view">';
            h += '<div class="skill-diff-title">v' + v1 + ' -> v' + v2 + ' | +' + data.added.length + ' -' + data.removed.length + '</div>';
            data.added.forEach(function(l) { h += '<div class="skill-diff-added">+ ' + escapeHtml(l) + '</div>'; });
            data.removed.forEach(function(l) { h += '<div class="skill-diff-removed">- ' + escapeHtml(l) + '</div>'; });
            if (!data.added.length && !data.removed.length) h += '<div class="skill-draft-note">No content changes.</div>';
            h += '</div>';
            viewer.innerHTML = h;
            viewer.style.display = "";
          }
        });
    });
  });
}

function escapeHtml(s) {
  if (!s) return "";
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", loadVersions);
} else {
  loadVersions();
}
})();
