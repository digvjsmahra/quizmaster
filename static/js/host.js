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
    activeCellId: null,  // question_id of the currently open pre-Start peek (live phase uses liveQuestion instead)
    liveQuestion: null,  // server-confirmed reveal state (B1's state:live_question), or null
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
    updateBoardAreaVisibility();
  }

  function showLive() {
    state.phase = 'live';
    el('view-lobby').classList.add('hidden');
    el('phase-badge').className = 'phase-badge live';
    el('phase-badge').textContent = '⏺ live';
    updateSidebarVisibility();
    updateBoardAreaVisibility();
  }

  // Board area (#view-live) is visible whenever a board has been uploaded,
  // independent of phase — this is what lets the host preview the board
  // before Start. The sidebar (queue/totals/add-player) within it only
  // shows once actually live, since none of it is meaningful pre-Start.
  function updateBoardAreaVisibility() {
    const hasBoard = state.boards && state.boards.length > 0;
    el('view-live').classList.toggle('hidden', !hasBoard);
    el('board-preview-hint').classList.toggle('hidden', !(hasBoard && state.phase !== 'live'));
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

        // Live phase: highlight whatever the server confirms is live.
        // Pre-Start: highlight the locally-toggled peek cell.
        const isActive = state.phase === 'live'
          ? (state.liveQuestion && state.liveQuestion.question_id === qid)
          : (state.activeCellId === qid);
        if (isActive) {
          cell.classList.add('cell-scoring');
        }

        cell.addEventListener('click', () => onCellClick(qid, board, cat, val));
        container.appendChild(cell);
      });
    });

    // Board nav
    el('board-label').textContent = `Board ${state.currentBoardIdx + 1} of ${state.boards.length}`;
    // Locked while a question is live — the host must cancel/close it
    // before navigating away (keeps the presentation view's board
    // unambiguous: it's always wherever the live question is).
    const navLocked = !!state.liveQuestion;
    el('btn-prev').disabled = state.currentBoardIdx === 0 || navLocked;
    el('btn-next').disabled = state.currentBoardIdx === state.boards.length - 1 || navLocked;

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
  // Cell click — pre-Start peek vs. live reveal flow
  // ----------------------------------------------------------------
  function onCellClick(qid, board, cat, val) {
    if (state.phase === 'live') {
      if (state.liveQuestion && state.liveQuestion.question_id === qid) {
        confirmCancelReveal();
      } else {
        socket.emit('host:question_reveal', { question_id: qid });
      }
      return;
    }

    // Pre-Start: read-only Q&A preview, not the scoring panel — the
    // roster doesn't exist yet (populated by start_quiz()), so a
    // scoring panel here would just show zero player rows. Purely a
    // local toggle — no server round-trip needed for a preview.
    if (state.activeCellId === qid) {
      state.activeCellId = null;
      renderBoard();
      hideScoringPanel();
      return;
    }
    state.activeCellId = qid;
    renderBoard();
    showQuestionPeek(qid, board, cat, val);
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

  // ----------------------------------------------------------------
  // Live reveal panel — question always shown, answer host-private
  // from the moment of reveal, scoring rows once answer_shown
  // ----------------------------------------------------------------
  function showRevealPanel(live) {
    const panel = el('scoring-panel');

    const mediaHtml = (live.media || [])
      .map(fn => `<img class="peek-media" src="/media/${JOIN_CODE}/${HOST_TOKEN}/${encodeURIComponent(fn)}" alt="">`)
      .join('');

    const revealBtnHtml = live.status === 'revealed'
      ? `<button class="btn-close-question" id="btn-reveal-answer">👁 reveal answer</button>`
      : '';

    let scoringHtml = '';
    if (live.status === 'answer_shown') {
      const roster = (state.scoresData && state.scoresData.roster) || [];
      const existing = {};
      const grid = state.scoresData && state.scoresData.grid[live.board];
      const cellData = grid && grid[live.category] && grid[live.category][String(live.value)];
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
            <button class="btn-quickfill" data-pid="${player_id}" data-val="${live.value}">+${live.value}</button>
            <button class="btn-quickfill" data-pid="${player_id}" data-val="-${live.value}">-${live.value}</button>
          </div>
        `;
      }).join('');
      scoringHtml = `
        <div class="panel-players" id="panel-players">${rows}</div>
        <button class="btn-close-question" id="btn-close-question">✓ close question</button>
        <div class="panel-hint">nothing saves until you close · blank rows are skipped</div>
      `;
    }

    panel.innerHTML = `
      <div class="panel-header">
        <span class="panel-title">${esc(live.category)} · ${live.value}</span>
        ${live.reviewing ? '<span class="panel-default">reviewing</span>' : ''}
      </div>
      <div class="peek-question">${esc(live.question || '')}</div>
      ${mediaHtml}
      <div class="peek-answer"><strong>Answer:</strong> ${esc(live.answer || '')}</div>
      ${revealBtnHtml}
      ${scoringHtml}
      <button class="btn-cancel-reveal" id="btn-cancel-reveal">✕ cancel</button>
    `;
    panel.classList.remove('hidden');

    if (live.status === 'revealed') {
      el('btn-reveal-answer').addEventListener('click', () => socket.emit('host:answer_reveal'));
    }

    if (live.status === 'answer_shown') {
      panel.querySelectorAll('.btn-quickfill').forEach(btn => {
        btn.addEventListener('click', () => {
          panel.querySelector(`.score-input[data-pid="${btn.dataset.pid}"]`).value = btn.dataset.val;
        });
      });
      el('btn-close-question').addEventListener('click', () => submitQuestion(live.question_id));
    }

    el('btn-cancel-reveal').addEventListener('click', confirmCancelReveal);
  }

  function confirmCancelReveal() {
    if (!confirm('Cancel this reveal? Any entered scores will be lost.')) return;
    socket.emit('host:question_cancel');
  }

  function hideScoringPanel() {
    const panel = el('scoring-panel');
    panel.classList.add('hidden');
    panel.innerHTML = '';
  }

  // When state:scores arrives while the reveal panel's scoring rows are
  // open, add any new roster members without wiping already-typed values.
  function updateScoringPanelRoster() {
    if (!state.liveQuestion || state.liveQuestion.status !== 'answer_shown' || !state.scoresData) return;
    const panel = el('scoring-panel');
    const playersContainer = el('panel-players');
    if (!playersContainer) return;

    const roster = state.scoresData.roster || [];
    const existing = new Set(
      Array.from(panel.querySelectorAll('.score-input')).map(i => i.dataset.pid)
    );
    const val = state.liveQuestion.value;

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
    // Panel closes when the server confirms via state:live_question
    // (live_question: null) — not optimistically here.
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

  function showRevealError(message) {
    const errEl = el('reveal-error');
    errEl.textContent = message;
    errEl.classList.remove('hidden');
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
    state.liveQuestion = data.live_question || null;

    if (data.phase === 'live') {
      showLive();
      renderQueue(data.queue);
    } else {
      showLobby();
      renderLobbyPlayers(data.lobby_players || []);
    }

    // Board state must reflect an already-successful upload on
    // reconnect/second-tab, not just on the tab that did the upload —
    // previously this only happened in the live branch, so a reload
    // after uploading but before Start showed an empty board.
    updateBoardAreaVisibility();
    if (state.boards.length > 0) renderBoard();
    if (state.liveQuestion) showRevealPanel(state.liveQuestion);
  });

  socket.on('state:players', ({ players }) => {
    if (state.phase === 'lobby') renderLobbyPlayers(players);
  });

  socket.on('state:phase', ({ phase }) => {
    if (phase === 'live') {
      // This event fires exactly once, at the moment Start is clicked —
      // not on every reconnect (state:full handles that separately) — so
      // this is the right, and only, place to reset the pre-Start
      // preview back to a clean slate: Board 1, no leftover peek/scoring
      // panel from wherever the host had navigated to during preview.
      state.currentBoardIdx = 0;
      state.activeCellId = null;
      hideScoringPanel();
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
    updateScoringPanelRoster();
  });

  socket.on('state:live_question', ({ live_question }) => {
    el('reveal-error').classList.add('hidden');
    state.liveQuestion = live_question;
    renderBoard();
    if (state.liveQuestion) {
      showRevealPanel(state.liveQuestion);
    } else {
      hideScoringPanel();
    }
  });

  socket.on('state:queue', (data) => {
    renderQueue(data);
  });

  socket.on('error', ({ message, context }) => {
    console.error('Server error:', message, context);
    if (context === 'start_quiz') {
      // A rejected host:start_quiz (server-side backstop for the client-side
      // check below) must not leave the Start button stuck disabled with no
      // feedback — re-enable it and surface the message.
      el('start-btn').disabled = false;
      const errEl = el('start-error');
      errEl.textContent = message;
      errEl.classList.remove('hidden');
    } else if (['question_reveal', 'answer_reveal', 'question_cancel', 'question_submit', 'board_select'].includes(context)) {
      showRevealError(message);
    }
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

  // Start quiz. Always clickable (never a silently-disabled dead end,
  // same lesson as the upload button below) — clicking with no content
  // uploaded yet shows a message immediately instead of doing nothing.
  el('start-btn').addEventListener('click', () => {
    if (!state.boards || state.boards.length === 0) {
      const errEl = el('start-error');
      errEl.textContent = 'Upload a quiz bundle above before starting.';
      errEl.classList.remove('hidden');
      return;
    }
    el('start-error').classList.add('hidden');
    el('start-btn').disabled = true;
    socket.emit('host:start_quiz');
  });

  // Upload quiz bundle — a single button. Clicking it opens the native
  // file picker (no separate "choose" vs "upload" steps to get wrong);
  // picking a .zip there uploads it immediately.
  const uploadBtnLabel = el('upload-btn').textContent;

  el('upload-btn').addEventListener('click', () => {
    el('bundle-input').click();
  });

  el('bundle-input').addEventListener('change', () => {
    if (el('bundle-input').files.length) uploadBundle();
  });

  async function uploadBundle() {
    const fileInput = el('bundle-input');
    const file = fileInput.files[0];
    const btn = el('upload-btn');
    const errEl = el('upload-error');
    const successEl = el('upload-success');

    btn.disabled = true;
    btn.textContent = 'Uploading…';
    errEl.classList.add('hidden');
    successEl.classList.add('hidden');

    const formData = new FormData();
    formData.append('bundle', file);

    try {
      const res = await fetch(`/host/${JOIN_CODE}/${HOST_TOKEN}/upload`, {
        method: 'POST',
        body: formData,
      });
      const body = await res.json();

      if (res.ok) {
        const warningNote = (body.warnings && body.warnings.length)
          ? ` — ${body.warnings.length} warning(s): ${body.warnings.join('; ')}`
          : '';
        successEl.textContent = `${file.name} loaded.${warningNote}`;
        successEl.classList.remove('hidden');
        // Board itself renders via the server's state:scores broadcast —
        // this handler only owns the upload card's own feedback.
      } else {
        errEl.innerHTML = `<strong>${esc(file.name)}</strong> couldn't be loaded:<br>` + body.errors
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
      btn.disabled = false;
      btn.textContent = uploadBtnLabel;
      fileInput.value = ''; // allow re-selecting the same file to retry
    }
  }

  // Board navigation — also notifies the server (host:board_select) so
  // the presentation view can follow free browsing when nothing's live.
  // Unreachable while a question is live: the buttons are disabled by
  // renderBoard()'s navLocked check above.
  el('btn-prev').addEventListener('click', () => {
    if (state.currentBoardIdx > 0) {
      state.currentBoardIdx--;
      state.activeCellId = null;
      hideScoringPanel();
      renderBoard();
      socket.emit('host:board_select', { board_index: state.currentBoardIdx });
    }
  });

  el('btn-next').addEventListener('click', () => {
    if (state.currentBoardIdx < state.boards.length - 1) {
      state.currentBoardIdx++;
      state.activeCellId = null;
      hideScoringPanel();
      renderBoard();
      socket.emit('host:board_select', { board_index: state.currentBoardIdx });
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
