(function () {
  'use strict';

  // The template's inline guard already surfaced the failure; bail out
  // instead of throwing ReferenceError on the io() call below.
  if (typeof io === 'undefined') return;

  const socket = io();

  function el(id) { return document.getElementById(id); }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmt(v) {
    if (v === 0) return '0';
    if (Number.isInteger(v)) return String(v);
    return parseFloat(v.toFixed(2)).toString();
  }

  function fmtMs(ms) {
    if (ms == null) return '—';
    if (ms < 1000) return `${ms} ms`;
    return `${(ms / 1000).toFixed(2)} s`;
  }

  // ----------------------------------------------------------------
  // Rendering
  // ----------------------------------------------------------------

  function renderStandings(rows) {
    el('standings-body').innerHTML = rows.map((r, i) =>
      `<tr>
         <td class="summary-rank-col">${i + 1}</td>
         <td>${esc(r.name)}</td>
         <td class="summary-total">${fmt(r.total)}</td>
       </tr>`
    ).join('');
  }

  function renderBuzz(rows) {
    const anyBuzzes = rows.some(r => r.buzz_count > 0);
    el('buzz-empty').classList.toggle('hidden', anyBuzzes);
    el('buzz-body').innerHTML = rows.map(r =>
      `<tr${r.buzz_count ? '' : ' class="summary-row-idle"'}>
         <td>${esc(r.name)}</td>
         <td>${r.buzz_count}<span class="summary-of"> of ${r.closed_count}</span></td>
         <td>${fmtMs(r.avg_ms)}</td>
         <td>${fmtMs(r.median_ms)}</td>
         <td>${r.avg_position == null ? '—' : fmt(r.avg_position)}</td>
       </tr>`
    ).join('');
  }

  function render(payload) {
    const hasContent = payload.standings.length > 0;
    el('summary-empty').classList.toggle('hidden', hasContent);
    el('summary-body').classList.toggle('hidden', !hasContent);
    if (!hasContent) return;
    renderStandings(payload.standings);
    renderBuzz(payload.buzz_stats);
  }
  // ----------------------------------------------------------------
  // Socket
  // ----------------------------------------------------------------

  socket.on('connect', function () {
    el('summary-live-badge').classList.remove('offline');
    el('summary-live-badge').textContent = 'live';
    socket.emit('summary:join', { room_id: JOIN_CODE });
  });

  socket.on('disconnect', function () {
    el('summary-live-badge').classList.add('offline');
    el('summary-live-badge').textContent = 'reconnecting…';
  });

  socket.on('state:summary', render);

  // Last statement: the template's guard reads this to tell a fully executed
  // script from one that never loaded or died partway.
  window.qbReady = true;
}());
