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
    renderChart(payload.timeline);
    renderBuzz(payload.buzz_stats);
  }

  // ----------------------------------------------------------------
  // Cumulative score chart
  //
  // Hand-rolled SVG on purpose: no build step and no third-party runtime
  // asset (SPEC.md §11), which rules out a charting library the same way it
  // ruled out a CDN-hosted Socket.IO client.
  //
  // Palette is the validated 8-slot categorical order, assigned in fixed
  // order and never cycled — a 9th player reuses a hue with a dashed stroke
  // rather than inventing one. Three of the eight sit under 3:1 against a
  // white card, so identity never rests on colour: every line is directly
  // labelled, the legend is always present, and the same numbers are one
  // click away as a table.
  // ----------------------------------------------------------------

  const SERIES_COLORS = [
    '#2a78d6', '#eb6834', '#1baf7a', '#eda100',
    '#e87ba4', '#008300', '#4a3aa7', '#e34948',
  ];

  const CHART = {
    w: 820, h: 360,
    top: 16, right: 132, bottom: 40, left: 52,
  };

  function seriesStyle(i) {
    return {
      color: SERIES_COLORS[i % SERIES_COLORS.length],
      dash: i >= SERIES_COLORS.length ? '7 4' : null,
    };
  }

  function niceTicks(min, max, count) {
    const raw = (max - min) / count;
    const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
    const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || mag * 10;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) {
      ticks.push(Math.round(v * 100) / 100);
    }
    return ticks;
  }

  let chartState = null;   // { xAt, yAt, cols, series, labels }

  function renderChart(timeline) {
    const host = el('chart');
    const questions = timeline.questions;
    const series = timeline.series;
    const hasData = questions.length > 0 && series.length > 0;

    el('chart-empty').classList.toggle('hidden', hasData);
    el('chart-wrap').classList.toggle('hidden', !hasData);
    el('chart-legend').classList.toggle('hidden', !hasData);
    el('chart-table-toggle').classList.toggle('hidden', !hasData);
    if (!hasData) { host.innerHTML = ''; chartState = null; return; }

    const n = questions.length;                    // points per line = n + 1
    const plotW = CHART.w - CHART.left - CHART.right;
    const plotH = CHART.h - CHART.top - CHART.bottom;

    const all = series.reduce((acc, s) => acc.concat(s.points), [0]);
    let lo = Math.min.apply(null, all);
    let hi = Math.max.apply(null, all);
    if (lo === hi) { lo -= 1; hi += 1; }
    const pad = (hi - lo) * 0.08;
    lo -= pad; hi += pad;

    const xAt = i => CHART.left + (n === 0 ? 0 : (i / n) * plotW);
    const yAt = v => CHART.top + plotH - ((v - lo) / (hi - lo)) * plotH;

    const yTicks = niceTicks(lo, hi, 5);
    const spacing = plotW / Math.max(n, 1);
    const showDots = spacing >= 26;
    // Thin the x labels until they stop colliding (~46px each).
    const everyX = Math.max(1, Math.ceil(46 / spacing));

    const parts = [];
    parts.push(`<svg viewBox="0 0 ${CHART.w} ${CHART.h}" class="chart-svg" role="img"
      aria-label="Cumulative score by question, ${series.length} players over ${n} questions">`);

    // Gridlines + y ticks
    yTicks.forEach(t => {
      const y = yAt(t);
      const zero = Math.abs(t) < 1e-9;
      parts.push(`<line class="chart-grid${zero ? ' chart-grid-zero' : ''}"
        x1="${CHART.left}" x2="${CHART.left + plotW}" y1="${y}" y2="${y}"/>`);
      parts.push(`<text class="chart-tick chart-tick-y" x="${CHART.left - 8}" y="${y}">${fmt(t)}</text>`);
    });

    // X axis: 0 is the pre-quiz baseline, then one tick per closed question
    for (let i = 0; i <= n; i++) {
      if (i > 0 && (i % everyX !== 0) && i !== n) continue;
      parts.push(`<text class="chart-tick chart-tick-x" x="${xAt(i)}" y="${CHART.top + plotH + 18}">${i === 0 ? 'start' : i}</text>`);
    }
    parts.push(`<text class="chart-axis-title" x="${CHART.left + plotW / 2}" y="${CHART.h - 4}">question, in play order</text>`);

    // Series
    series.forEach((s, i) => {
      const st = seriesStyle(i);
      const d = s.points.map((v, x) => `${x === 0 ? 'M' : 'L'}${xAt(x).toFixed(1)},${yAt(v).toFixed(1)}`).join(' ');
      parts.push(`<path class="chart-line" d="${d}" stroke="${st.color}"${st.dash ? ` stroke-dasharray="${st.dash}"` : ''}/>`);
      if (showDots) {
        s.points.forEach((v, x) => {
          parts.push(`<circle class="chart-dot" cx="${xAt(x).toFixed(1)}" cy="${yAt(v).toFixed(1)}" r="4" fill="${st.color}"/>`);
        });
      }
    });

    // Direct labels at the line ends — ink text beside a colour chip, never
    // coloured text. Nudged apart so they stay legible when lines converge.
    const ends = series.map((s, i) => ({
      i, name: s.name, value: s.points[n], y: yAt(s.points[n]),
    })).sort((a, b) => a.y - b.y);
    for (let k = 1; k < ends.length; k++) {
      if (ends[k].y - ends[k - 1].y < 15) ends[k].y = ends[k - 1].y + 15;
    }
    const overflow = ends.length ? ends[ends.length - 1].y - (CHART.top + plotH) : 0;
    if (overflow > 0) ends.forEach(e => { e.y -= overflow; });
    ends.forEach(e => {
      const st = seriesStyle(e.i);
      const x = CHART.left + plotW + 10;
      parts.push(`<rect class="chart-chip" x="${x}" y="${e.y - 4}" width="8" height="8" rx="2" fill="${st.color}"/>`);
      parts.push(`<text class="chart-end-label" x="${x + 13}" y="${e.y}">${esc(e.name)} <tspan class="chart-end-value">${fmt(e.value)}</tspan></text>`);
    });

    // Hover layer: one full-height column per x position, wider than any mark.
    parts.push(`<line class="chart-crosshair hidden" id="chart-crosshair" y1="${CHART.top}" y2="${CHART.top + plotH}"/>`);
    for (let i = 0; i <= n; i++) {
      parts.push(`<rect class="chart-hit" data-i="${i}"
        x="${(xAt(i) - spacing / 2).toFixed(1)}" y="${CHART.top}"
        width="${spacing.toFixed(1)}" height="${plotH}"/>`);
    }
    parts.push('</svg>');
    host.innerHTML = parts.join('');

    chartState = { xAt, yAt, questions, series, plotH };
    attachHover();
    renderLegend(series);
    renderChartTable(questions, series);
  }

  function renderLegend(series) {
    el('chart-legend').innerHTML = series.map((s, i) => {
      const st = seriesStyle(i);
      return `<span class="chart-legend-item">
        <span class="chart-legend-swatch${st.dash ? ' chart-legend-dashed' : ''}" style="background:${st.color}"></span>
        ${esc(s.name)}
      </span>`;
    }).join('');
  }

  function renderChartTable(questions, series) {
    const head = ['Question'].concat(series.map(s => esc(s.name)));
    const rows = [`<tr><td>start</td>${series.map(() => '<td>0</td>').join('')}</tr>`];
    questions.forEach((q, x) => {
      rows.push(`<tr><td>${x + 1}. ${esc(q.label)}</td>${
        series.map(s => `<td>${fmt(s.points[x + 1])}</td>`).join('')}</tr>`);
    });
    el('chart-table').innerHTML =
      `<table class="totals-table summary-buzz"><thead><tr>${
        head.map(h => `<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
  }

  function attachHover() {
    const wrap = el('chart-wrap');
    const tip = el('chart-tooltip');
    const cross = document.getElementById('chart-crosshair');

    wrap.querySelectorAll('.chart-hit').forEach(function (hit) {
      hit.addEventListener('mouseenter', function () {
        if (!chartState) return;
        const i = Number(hit.dataset.i);
        const x = chartState.xAt(i);
        cross.setAttribute('x1', x);
        cross.setAttribute('x2', x);
        cross.classList.remove('hidden');

        const title = i === 0 ? 'Before the first question'
          : `${i}. ${esc(chartState.questions[i - 1].label)}`;
        const ranked = chartState.series
          .map((s, si) => ({ name: s.name, v: s.points[i], si }))
          .sort((a, b) => b.v - a.v);
        tip.innerHTML = `<div class="chart-tooltip-title">${title}</div>` +
          ranked.map(r =>
            `<div class="chart-tooltip-row">
               <span class="chart-legend-swatch" style="background:${seriesStyle(r.si).color}"></span>
               <span class="chart-tooltip-name">${esc(r.name)}</span>
               <span class="chart-tooltip-value">${fmt(r.v)}</span>
             </div>`).join('');
        tip.classList.remove('hidden');

        // Flip to the left of the cursor near the right edge.
        const frac = x / CHART.w;
        tip.style.left = (frac * 100) + '%';
        tip.style.transform = frac > 0.6 ? 'translate(-100%, 0)' : 'translate(8px, 0)';
      });
    });

    wrap.addEventListener('mouseleave', function () {
      tip.classList.add('hidden');
      if (cross) cross.classList.add('hidden');
    });
  }

  el('chart-table-toggle').addEventListener('click', function () {
    const table = el('chart-table');
    const shown = !table.classList.contains('hidden');
    table.classList.toggle('hidden', shown);
    this.setAttribute('aria-expanded', String(!shown));
    this.textContent = shown ? 'Show as table' : 'Hide table';
  });

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
