(function () {
  'use strict';

  const socket = io();

  let playerId = null;
  let currentPhase = null;

  const views = {
    join: document.getElementById('view-join'),
    waiting: document.getElementById('view-waiting'),
    buzzer: document.getElementById('view-buzzer'),
    position: document.getElementById('view-position'),
  };

  function showView(name) {
    Object.values(views).forEach(v => v.classList.add('hidden'));
    views[name].classList.remove('hidden');
  }

  function fmtDelta(ms) {
    if (ms == null || isNaN(ms)) return '';
    if (ms < 1000) return `+${ms} ms`;
    return `+${(ms / 1000).toFixed(1)} s`;
  }

  function esc(str) {
    return String(str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ---- Auto-join from URL param ----

  socket.on('connect', () => {
    const urlName = new URLSearchParams(window.location.search).get('name');
    if (urlName) {
      const nameInput = document.getElementById('name-input');
      nameInput.value = urlName;
      nameInput.disabled = true;
      document.getElementById('join-btn').disabled = true;
      socket.emit('player:join', { name: urlName, room_id: JOIN_CODE });
    } else {
      document.getElementById('name-input').focus();
    }
  });

  // ---- Socket events ----

  socket.on('player:accepted', ({ player_id, phase }) => {
    playerId = player_id;
    currentPhase = phase;
    if (phase === 'lobby') {
      showView('waiting');
    } else {
      document.getElementById('buzz-btn').disabled = false;
      showView('buzzer');
    }
  });

  socket.on('player:rejected', ({ reason }) => {
    const nameInput = document.getElementById('name-input');
    nameInput.disabled = false;
    document.getElementById('join-btn').disabled = false;
    const err = document.getElementById('join-error');
    err.textContent = reason;
    err.classList.remove('hidden');
  });

  socket.on('state:phase', ({ phase }) => {
    currentPhase = phase;
    if (phase === 'live' && !views.waiting.classList.contains('hidden')) {
      document.getElementById('buzz-btn').disabled = false;
      showView('buzzer');
    }
  });

  socket.on('state:queue', ({ queue, locked }) => {
    if (!playerId) return;
    const buzzBtn = document.getElementById('buzz-btn');
    const frozenLabel = document.getElementById('frozen-label');

    const pos = queue.findIndex(e => e.player_id === playerId);
    if (pos >= 0) {
      const listEl = document.getElementById('player-queue-list');
      listEl.innerHTML = queue.map((e, i) => {
        const badge = i === 0
          ? `<span class="buzz-delta first">⚡ first</span>`
          : `<span class="buzz-delta">${fmtDelta(e.delta_ms)}</span>`;
        const cls = e.player_id === playerId ? ' class="buzz-me"' : '';
        return `<li${cls}>${i + 1}. ${esc(e.name)} ${badge}</li>`;
      }).join('');
      showView('position');
    } else if (currentPhase === 'live') {
      document.getElementById('player-queue-list').innerHTML = '';
      buzzBtn.disabled = !!locked;
      if (locked) {
        frozenLabel.classList.remove('hidden');
      } else {
        frozenLabel.classList.add('hidden');
      }
      showView('buzzer');
    }
  });

  socket.on('state:roster', ({ names }) => {
    const rosterEl = document.getElementById('roster-display');
    const namesEl = document.getElementById('roster-names');
    if (!names || names.length === 0) {
      rosterEl.classList.add('hidden');
      return;
    }
    namesEl.innerHTML = names.map(n => `<span class="roster-chip">${esc(n)}</span>`).join('');
    rosterEl.classList.remove('hidden');
  });

  // ---- UI handlers ----

  document.getElementById('join-btn').addEventListener('click', () => {
    const name = document.getElementById('name-input').value.trim();
    if (!name) return;
    document.getElementById('join-error').classList.add('hidden');
    document.getElementById('join-btn').disabled = true;
    socket.emit('player:join', { name, room_id: JOIN_CODE });
  });

  document.getElementById('name-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') document.getElementById('join-btn').click();
  });

  document.getElementById('buzz-btn').addEventListener('click', () => {
    document.getElementById('buzz-btn').disabled = true;
    socket.emit('player:buzz');
  });
}());
