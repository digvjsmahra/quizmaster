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

  socket.on('state:queue', ({ queue }) => {
    if (!playerId) return;
    const pos = queue.findIndex(e => e.player_id === playerId);
    if (pos >= 0) {
      document.getElementById('position-text').textContent = `You are #${pos + 1} in line`;
      showView('position');
    } else if (currentPhase === 'live') {
      document.getElementById('buzz-btn').disabled = false;
      showView('buzzer');
    }
  });

  // ---- UI handlers ----

  document.getElementById('join-form').addEventListener('submit', e => {
    e.preventDefault();
    const name = document.getElementById('name-input').value.trim();
    if (!name) return;
    document.getElementById('join-error').classList.add('hidden');
    socket.emit('player:join', { name });
  });

  document.getElementById('buzz-btn').addEventListener('click', () => {
    document.getElementById('buzz-btn').disabled = true;
    socket.emit('player:buzz');
  });
}());
