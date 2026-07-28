(function () {
  'use strict';

  const socket = io();

  const state = {
    boardName: null,
    boardIndex: 0,
    boardCount: 1,
    board: {},
    liveQuestion: null,
  };

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

  function fmtDelta(ms) {
    if (ms == null || isNaN(ms)) return '';
    if (ms < 1000) return `+${ms} ms`;
    return `+${(ms / 1000).toFixed(1)} s`;
  }

  // ----------------------------------------------------------------
  // Stage: board slide
  // ----------------------------------------------------------------
  function renderBoardSlide() {
    el('present-question-slide').classList.add('hidden');
    el('present-board-slide').classList.remove('hidden');

    const grid = state.board || {};
    const categories = Object.keys(grid);
    const valueSet = new Set();
    categories.forEach(cat => Object.keys(grid[cat]).forEach(v => valueSet.add(parseInt(v, 10))));
    const values = Array.from(valueSet).sort((a, b) => a - b);

    const container = el('present-board-grid');
    container.innerHTML = '';
    container.style.gridTemplateColumns = `repeat(${categories.length || 1}, minmax(90px, 1fr))`;

    categories.forEach(cat => {
      const h = document.createElement('div');
      h.className = 'cell-header';
      h.textContent = cat;
      container.appendChild(h);
    });

    values.forEach(val => {
      categories.forEach(cat => {
        const cellData = grid[cat] && grid[cat][String(val)];
        const cell = document.createElement('div');
        cell.className = 'cell';

        if (!cellData || cellData.state === 'unplayed') {
          cell.classList.add('cell-unplayed');
          cell.textContent = val;
        } else if (cellData.state === 'awarded') {
          cell.classList.add('cell-awarded');
          cell.innerHTML = cellData.entries
            .map(e => `${esc(e.name)} ${e.value >= 0 ? '+' : ''}${fmt(e.value)}`)
            .join('<br>');
        } else {
          cell.classList.add('cell-passed');
          cell.textContent = '~passed~';
        }
        container.appendChild(cell);
      });
    });
  }

  // ----------------------------------------------------------------
  // Stage: question slide
  // ----------------------------------------------------------------
  function renderQuestionSlide(live) {
    el('present-board-slide').classList.add('hidden');
    el('present-question-slide').classList.remove('hidden');

    el('present-reviewing-badge').classList.toggle('hidden', !live.reviewing);
    el('present-question-text').textContent = live.question || '';

    el('present-question-media').innerHTML = (live.media || [])
      .map(fn => `<img class="present-media" src="/media/${JOIN_CODE}/${HOST_TOKEN}/${encodeURIComponent(fn)}" alt="">`)
      .join('');

    const answerEl = el('present-answer');
    if (live.answer) {
      answerEl.textContent = live.answer;
      answerEl.classList.add('answer-shown');
    } else {
      answerEl.textContent = '';
      answerEl.classList.remove('answer-shown');
    }
  }

  // ----------------------------------------------------------------
  // Sidebar: totals + queue
  // ----------------------------------------------------------------
  function renderTotals(rows) {
    el('present-totals-body').innerHTML = (rows || []).map(r => `
      <tr>
        <td>${esc(r.name)}</td>
        <td>${fmt(r.board_total)}</td>
        <td>${fmt(r.cumulative)}</td>
      </tr>
    `).join('');
  }

  function renderQueue(data) {
    const list = el('present-queue-list');
    const empty = el('present-queue-empty');
    const lockedBadge = el('present-queue-locked-badge');

    lockedBadge.classList.toggle('hidden', !data.locked);

    if (data.queue.length === 0) {
      list.innerHTML = '';
      empty.classList.remove('hidden');
    } else {
      empty.classList.add('hidden');
      list.innerHTML = data.queue
        .map((e, i) => {
          const badge = i === 0
            ? `<span class="buzz-delta first">⚡ first</span>`
            : `<span class="buzz-delta">${fmtDelta(e.delta_ms)}</span>`;
          return `<li><span class="queue-name">${i + 1}. ${esc(e.name)}</span>${badge}</li>`;
        })
        .join('');
    }
  }

  // ----------------------------------------------------------------
  // Socket events
  // ----------------------------------------------------------------
  socket.on('connect', () => {
    socket.emit('present:join', { room_id: JOIN_CODE });
  });

  socket.on('state:presentation', (data) => {
    state.boardName = data.board_name;
    state.boardIndex = data.board_index;
    state.boardCount = data.board_count;
    state.board = data.board || {};
    state.liveQuestion = data.live_question;

    el('present-board-label').textContent = state.boardName
      ? `Board ${state.boardIndex + 1} of ${state.boardCount} — ${state.boardName}`
      : 'Waiting for quiz content…';

    renderTotals(data.totals);

    if (state.liveQuestion) {
      renderQuestionSlide(state.liveQuestion);
    } else {
      renderBoardSlide();
    }
  });

  socket.on('state:queue', (data) => {
    renderQueue(data);
  });
}());
