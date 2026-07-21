(function () {
  'use strict';

  const socket = io();

  // ----------------------------------------------------------------
  // State
  // ----------------------------------------------------------------
  const state = {
    phase: 'lobby',
    boards: [],          // ordered board ids from server
    currentBoardIdx: 0,
    scoresData: null,    // latest scores payload from server
    activeCellId: null,  // question_id of currently open scoring panel
  };

  // ----------------------------------------------------------------
  // Helpers
  // ----------------------------------------------------------------
  function fmt(v) {
    if (v === 0) return '0';
    if (Number.isInteger(v)) return String(v);
    // Trim trailing zeros but keep up to 1 decimal for common .5 values
    return parseFloat(v.toFixed(2)).toString();
  }

  function el(id) { return document.getElementById(id); }

  function fmtDelta(ms) {
    if (ms == null || isNaN(ms)) return '';
    if (ms < 1000) return `+${ms} ms`;
    return `+${(ms / 1000).toFixed(1)} s`;
  }

  // ----------------------------------------------------------------
  // Phase / view switching
  // ----------------------------------------------------------------
  function showLobby() {
    state.phase = 'lobby';
    el('view-lobby').classList.remove('hidden');
    el('phase-badge').className = 'phase-badge lobby';
    el('phase-badge').textContent = '⏱ lobby';
    updateSidebarVisibility();
  }

  function showLive() {
    state.phase = 'live';
    el('view-lobby').classList.add('hidden');
    el('phase-badge').className = 'phase-badge live';
    el('phase-badge').textContent = '⏺ live';
    updateSidebarVisibility();
  }

  // Board area (#view-live) is visible whenever a board has been uploaded,
  // independent of phase — this is what lets the host preview the board
  // before Start. The sidebar (queue/totals/add-player) within it only
  // shows once actually live, since none of it is meaningful pre-Start.
  function updateBoardAreaVisibility() {
    el('view-live').classList.toggle('hidden', !(state.boards && state.boards.length > 0));
  }

  function updateSidebarVisibility() {
    el('sidebar').classList.toggle('hidden', state.phase !== 'live');
  }

  // ----------------------------------------------------------------
  // Lobby rendering
  // ----------------------------------------------------------------
  function renderLobbyPlayers(players) {
    const grid = el('lobby-players');
    grid.innerHTML = players.map(p =>
      `<div class="player-item">${esc(p.name)}</div>`
    ).join('');
    el('lobby-count').textContent = players.length;
  }

  // ----------------------------------------------------------------
  // Board rendering
  // ----------------------------------------------------------------
  function renderBoard() {
    if (!state.scoresData || state.boards.length === 0) return;

    const board = state.boards[state.currentBoardIdx];
    const grid = state.scoresData.grid[board];
    if (!grid) return;

    const categories = Object.keys(grid);

    // Collect all unique values across categories, sort ascending
    const valueSet = new Set();
    categories.forEach(cat => Object.keys(grid[cat]).forEach(v => valueSet.add(parseInt(v, 10))));
    const values = Array.from(valueSet).sort((a, b) => a - b);

    const container = el('board-grid');
    container.innerHTML = '';
    container.style.gridTemplateColumns = `repeat(${categories.length}, minmax(90px, 1fr))`;

    // Header row
    categories.forEach(cat => {
      const h = document.createElement('div');
      h.className = 'cell-header';
      h.textContent = cat;
      container.appendChild(h);
    });

    // Value rows
    values.forEach(val => {
      categories.forEach(cat => {
        const qid = `${board}:${cat}:${val}`;
        const cellData = grid[cat] && grid[cat][String(val)];
        const cell = document.createElement('div');
        cell.className = 'cell';
        cell.dataset.qid = qid;

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

        if (state.activeCellId === qid) {
          cell.classList.add('cell-scoring');
        }

        cell.addEventListener('click', () => onCellClick(qid, board, cat, val));
        container.appendChild(cell);
      });
    });

    // Board nav
    el('board-label').textContent = `Board ${state.currentBoardIdx + 1} of ${state.boards.length}`;
    el('btn-prev').disabled = state.currentBoardIdx === 0;
    el('btn-next').disabled = state.currentBoardIdx === state.boards.length - 1;

    // Update totals for this board
    renderTotals(board);
  }

  // ----------------------------------------------------------------
  // Totals
  // ----------------------------------------------------------------
  function renderTotals(board) {
    if (!state.scoresData) return;
    const rows = (state.scoresData.per_board_totals[board] || []);
    const tbody = el('totals-body');
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td>${esc(r.name)}</td>
        <td>${fmt(r.board_total)}</td>
        <td>${fmt(r.cumulative)}</td>
      </tr>
    `).join('');
  }

  // ----------------------------------------------------------------
  // Queue
  // ----------------------------------------------------------------
  function renderQueue(data) {
    const list = el('queue-list');
    const empty = el('queue-empty');
    const lockedBadge = el('queue-locked-badge');

    if (data.locked) {
      lockedBadge.classList.remove('hidden');
    } else {
      lockedBadge.classList.add('hidden');
    }

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
  // Cell click + scoring panel
  // ----------------------------------------------------------------
  function onCellClick(qid, board, cat, val) {
    if (state.activeCellId === qid) {
      // Toggle off — dismiss without saving
      state.activeCellId = null;
      renderBoard();
      hideScoringPanel();
      return;
    }
    state.activeCellId = qid;
    renderBoard();
    if (state.phase === 'live') {
      showScoringPanel(qid, board, cat, val);
    } else {
      // Pre-Start: read-only Q&A preview, not the scoring panel — the
      // roster doesn't exist yet (populated by start_quiz()), so a
      // scoring panel here would just show zero player rows.
      showQuestionPeek(qid, board, cat, val);
    }
  }

  function showQuestionPeek(qid, board, cat, val) {
    const panel = el('scoring-panel');
    const grid = state.scoresData && state.scoresData.grid[board];
    const cellData = grid && grid[cat] && grid[cat][String(val)];
    if (!cellData) return;

    const mediaHtml = (cellData.media || [])
      .map(fn => `<img class="peek-media" src="/media/${JOIN_CODE}/${HOST_TOKEN}/${encodeURIComponent(fn)}" alt="">`)
      .join('');

    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title">${esc(cat)} · ${val}</span>
      </div>
      <div class="peek-question">${esc(cellData.question || '')}</div>
      ${mediaHtml}
      <div class="peek-answer"><strong>Answer:</strong> ${esc(cellData.answer || '')}</div>
    `;
    panel.classList.remove('hidden');
  }

  function showScoringPanel(qid, board, cat, val) {
    const panel = el('scoring-panel');
    const roster = (state.scoresData && state.scoresData.roster) || [];

    // Existing scores for this cell
    const existing = {};
    const grid = state.scoresData && state.scoresData.grid[board];
    const cellData = grid && grid[cat] && grid[cat][String(val)];
    if (cellData && cellData.entries) {
      cellData.entries.forEach(e => { existing[e.player_id] = e.value; });
    }

    const rows = roster.map(({ player_id, name }) => {
      const v = existing[player_id];
      const inputVal = v !== undefined ? v : '';
      return `
        <div class="panel-player-row">
          <span class="panel-player-name">${esc(name)}</span>
          <input type="number" class="score-input" data-pid="${player_id}"
                 value="${inputVal}" placeholder="—" step="any">
          <button class="btn-quickfill" data-pid="${player_id}" data-val="${val}">+${val}</button>
          <button class="btn-quickfill" data-pid="${player_id}" data-val="-${val}">-${val}</button>
        </div>
      `;
    }).join('');

    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title">${esc(cat)} · ${val}</span>
        <span class="panel-default">default +${val}</span>
      </div>
      <div class="panel-players" id="panel-players">${rows}</div>
      <button class="btn-close-question" id="btn-close-question">✓ close question</button>
      <div class="panel-hint">nothing saves until you close · blank rows are skipped</div>
    `;
    panel.classList.remove('hidden');

    // Quick-fill
    panel.querySelectorAll('.btn-quickfill').forEach(btn => {
      btn.addEventListener('click', () => {
        panel.querySelector(`.score-input[data-pid="${btn.dataset.pid}"]`).value = btn.dataset.val;
      });
    });

    // Close question
    el('btn-close-question').addEventListener('click', () => submitQuestion(qid));
  }

  function hideScoringPanel() {
    const panel = el('scoring-panel');
    panel.classList.add('hidden');
    panel.innerHTML = '';
  }

  // When state:scores arrives while panel is open, add any new roster members
  function updateScoringPanelRoster() {
    if (!state.activeCellId || !state.scoresData) return;
    const panel = el('scoring-panel');
    const playersContainer = el('panel-players');
    if (!playersContainer) return;

    const roster = state.scoresData.roster || [];
    const existing = new Set(
      Array.from(panel.querySelectorAll('.score-input')).map(i => i.dataset.pid)
    );

    const parts = state.activeCellId.split(':');
    const val = parts[parts.length - 1];

    roster.forEach(({ player_id, name }) => {
      if (existing.has(player_id)) return;
      const row = document.createElement('div');
      row.className = 'panel-player-row';
      row.innerHTML = `
        <span class="panel-player-name">${esc(name)}</span>
        <input type="number" class="score-input" data-pid="${player_id}"
               value="" placeholder="—" step="any">
        <button class="btn-quickfill" data-pid="${player_id}" data-val="${val}">+${val}</button>
        <button class="btn-quickfill" data-pid="${player_id}" data-val="-${val}">-${val}</button>
      `;
      row.querySelectorAll('.btn-quickfill').forEach(btn => {
        btn.addEventListener('click', () => {
          row.querySelector(`.score-input[data-pid="${player_id}"]`).value = btn.dataset.val;
        });
      });
      playersContainer.appendChild(row);
    });
  }

  function submitQuestion(qid) {
    const panel = el('scoring-panel');
    const scores = {};
    panel.querySelectorAll('.score-input').forEach(input => {
      const v = input.value.trim();
      if (v !== '') {
        const num = parseFloat(v);
        if (!isNaN(num)) scores[input.dataset.pid] = num;
      }
    });
    socket.emit('host:question_submit', { question_id: qid, scores });
    state.activeCellId = null;
    hideScoringPanel();
  }

  // ----------------------------------------------------------------
  // XSS-safe text escaping
  // ----------------------------------------------------------------
  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ----------------------------------------------------------------
  // Socket events
  // ----------------------------------------------------------------
  socket.on('connect', () => {
    socket.emit('host:join', { room_id: JOIN_CODE });
  });

  socket.on('state:full', (data) => {
    // Header subtitle
    el('header-subtitle').textContent =
      `sharing /play/${data.join_code} · host this page`;

    // Set up join URL in lobby
    const joinUrl = `${window.location.origin}/play/${data.join_code}`;
    el('join-url-input').value = joinUrl;

    state.boards = (data.scores && data.scores.boards) || [];
    state.scoresData = data.scores;

    if (data.phase === 'live') {
      showLive();
      renderQueue(data.queue);
    } else {
      showLobby();
      renderLobbyPlayers(data.lobby_players || []);
    }

    // Board + Start-button state must reflect an already-successful
    // upload on reconnect/second-tab, not just on the tab that did the
    // upload — previously this only happened in the live branch, so a
    // reload after uploading but before Start showed an empty board.
    updateBoardAreaVisibility();
    if (state.boards.length > 0) {
      renderBoard();
      el('start-btn').disabled = false;
    }
  });

  socket.on('state:players', ({ players }) => {
    if (state.phase === 'lobby') renderLobbyPlayers(players);
  });

  socket.on('state:phase', ({ phase }) => {
    if (phase === 'live') {
      showLive();
      if (state.scoresData) renderBoard();
    }
  });

  socket.on('state:scores', (data) => {
    state.scoresData = data;
    state.boards = data.boards || [];
    // Keep currentBoardIdx in bounds
    if (state.currentBoardIdx >= state.boards.length) state.currentBoardIdx = 0;
    updateBoardAreaVisibility();
    renderBoard();
    if (state.activeCellId) updateScoringPanelRoster();
  });

  socket.on('state:queue', (data) => {
    renderQueue(data);
  });

  socket.on('error', ({ message }) => {
    console.error('Server error:', message);
    // A rejected host:start_quiz (e.g. server-side "no content uploaded"
    // guard) must not leave the Start button stuck disabled with no
    // feedback — re-enable it and surface the message.
    el('start-btn').disabled = false;
    const errEl = el('upload-error');
    errEl.textContent = message;
    errEl.classList.remove('hidden');
  });

  // ----------------------------------------------------------------
  // UI event handlers
  // ----------------------------------------------------------------

  // Copy join URL
  el('copy-btn').addEventListener('click', () => {
    const input = el('join-url-input');
    input.select();
    navigator.clipboard.writeText(input.value).then(() => {
      el('copy-btn').textContent = '✓ copied';
      setTimeout(() => { el('copy-btn').textContent = '⎘ copy'; }, 1500);
    }).catch(() => {
      document.execCommand('copy');
    });
  });

  // Start quiz
  el('start-btn').addEventListener('click', () => {
    el('start-btn').disabled = true;
    socket.emit('host:start_quiz');
  });

  // Upload quiz bundle
  el('bundle-input').addEventListener('change', () => {
    el('upload-btn').disabled = !el('bundle-input').files.length;
  });

  el('upload-btn').addEventListener('click', async () => {
    const fileInput = el('bundle-input');
    if (!fileInput.files.length) return;

    const btn = el('upload-btn');
    const errEl = el('upload-error');
    const successEl = el('upload-success');
    btn.disabled = true;
    btn.textContent = 'Uploading…';
    errEl.classList.add('hidden');
    successEl.classList.add('hidden');

    const formData = new FormData();
    formData.append('bundle', fileInput.files[0]);

    try {
      const res = await fetch(`/host/${JOIN_CODE}/${HOST_TOKEN}/upload`, {
        method: 'POST',
        body: formData,
      });
      const body = await res.json();

      if (res.ok) {
        successEl.textContent = (body.warnings && body.warnings.length)
          ? `Loaded, with ${body.warnings.length} warning(s): ${body.warnings.join('; ')}`
          : 'Quiz content loaded.';
        successEl.classList.remove('hidden');
        el('start-btn').disabled = false;
        // Board itself renders via the server's state:scores broadcast —
        // this handler only owns the upload card's own feedback.
      } else {
        errEl.innerHTML = body.errors
          .map(e => `${e.row ? `Row ${e.row}: ` : ''}${esc(e.message)}`)
          .join('<br>');
        errEl.classList.remove('hidden');
        // A failed (re-)upload must not disturb an already-loaded board —
        // nothing here touches state.boards/renderBoard().
      }
    } catch {
      errEl.textContent = 'Unable to reach the server. Please try again.';
      errEl.classList.remove('hidden');
    } finally {
      btn.disabled = !fileInput.files.length;
      btn.textContent = 'Upload';
    }
  });

  // Board navigation
  el('btn-prev').addEventListener('click', () => {
    if (state.currentBoardIdx > 0) {
      state.currentBoardIdx--;
      state.activeCellId = null;
      hideScoringPanel();
      renderBoard();
    }
  });

  el('btn-next').addEventListener('click', () => {
    if (state.currentBoardIdx < state.boards.length - 1) {
      state.currentBoardIdx++;
      state.activeCellId = null;
      hideScoringPanel();
      renderBoard();
    }
  });

  // Queue controls
  el('btn-freeze').addEventListener('click', () => socket.emit('host:queue_freeze'));
  el('btn-reset').addEventListener('click', () => socket.emit('host:queue_reset'));

  // Add player
  el('btn-add-player').addEventListener('click', () => {
    const input = el('add-player-input');
    const name = input.value.trim();
    if (!name) return;
    socket.emit('host:roster_add', { name });
    input.value = '';
  });

  el('add-player-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') el('btn-add-player').click();
  });

}());
