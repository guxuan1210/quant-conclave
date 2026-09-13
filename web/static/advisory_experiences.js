
(function() {
  'use strict';

  function loadExperiences() {
    fetch('/api/advisory/experiences')
      .then(function(r) { return r.json(); })
      .then(function(data) { renderPanel(data); })
      .catch(function(err) { console.error('Failed to load experiences:', err); });
  }

  function renderPanel(experiences) {
    if (!experiences || !Array.isArray(experiences)) return;
    var active = experiences.filter(function(e) { return e.status === 'active'; });
    var pending = experiences.filter(function(e) { return e.status === 'pending_review'; });
    var archived = experiences.filter(function(e) { return e.status === 'archived'; });

    var container = document.getElementById('experience-panel');
    if (!container) return;

    var html = '<div class="exp-controls">';
    html += '<button class="exp-tab active-tab" data-tab="all">' + t('exp.all', {n: experiences.length}) + '</button>';
    html += '<button class="exp-tab" data-tab="active">' + t('exp.active', {n: active.length}) + '</button>';
    html += '<button class="exp-tab" data-tab="pending">' + t('exp.pending', {n: pending.length}) + '</button>';
    html += '<button class="exp-tab" data-tab="archived">' + t('exp.archived', {n: archived.length}) + '</button>';
    html += '</div><div class="exp-list" id="exp-list">';

    for (var i = 0; i < experiences.length; i++) {
      var e = experiences[i];
      var statusIcon = e.status === 'active' ? '&#9989;' : (e.status === 'pending_review' ? '&#9744;' : '&#128230;');
      var cat = e.category || 'other';
      html += '<div class="exp-item" data-status="' + e.status + '">';
      html += '<div class="exp-header"><span class="exp-status">' + statusIcon + '</span> <span class="exp-cat">[' + cat + ']</span> ';
      html += '<span class="exp-content">' + escapeHtml(e.content || '') + '</span></div>';
      html += '<div class="exp-meta">' + (e.source_ticker || '') + ' ' + (e.outcome || '') + ' ' + (e.source_date || '') + '</div>';
      html += '<div class="exp-actions">';
      if (e.status === 'pending_review') {
        html += '<button onclick="approveExp(' + e.id + ')">' + t('exp.approve') + '</button> ';
        html += '<button onclick="archiveExp(' + e.id + ')">' + t('exp.reject') + '</button>';
      } else if (e.status === 'active') {
        html += '<button onclick="archiveExp(' + e.id + ')">' + t('exp.archive') + '</button>';
      } else if (e.status === 'archived') {
        html += '<button onclick="reactivateExp(' + e.id + ')">' + t('exp.reactivate') + '</button>';
      }
      html += '</div></div>';
    }
    html += '</div>';
    container.innerHTML = html;

    // Tab filtering
    container.querySelectorAll('.exp-tab').forEach(function(tab) {
      tab.addEventListener('click', function() {
        container.querySelectorAll('.exp-tab').forEach(function(t) { t.classList.remove('active-tab'); });
        tab.classList.add('active-tab');
        var filter = tab.getAttribute('data-tab');
        container.querySelectorAll('.exp-item').forEach(function(item) {
          if (filter === 'all' || item.getAttribute('data-status') === filter) {
            item.style.display = '';
          } else {
            item.style.display = 'none';
          }
        });
      });
    });
  }

  window.approveExp = function(id) {
    fetch('/api/advisory/experiences/' + id + '/approve', {method: 'PUT'})
      .then(function() { loadExperiences(); });
  };
  window.archiveExp = function(id) {
    fetch('/api/advisory/experiences/' + id + '/archive', {method: 'PUT'})
      .then(function() { loadExperiences(); });
  };
  window.reactivateExp = function(id) {
    fetch('/api/advisory/experiences/' + id + '/reactivate', {method: 'PUT'})
      .then(function() { loadExperiences(); });
  };

  function escapeHtml(s) {
    if (!s) return '';
    return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadExperiences);
  } else {
    loadExperiences();
  }
})();
