(function () {
  'use strict';

  const boxes = Array.from(document.querySelectorAll('.otp-box'));
  const nameInput = document.getElementById('name-input');
  const joinBtn = document.getElementById('join-btn');
  const errorEl = document.getElementById('join-error');

  function getCode() {
    return boxes.map(b => b.value).join('');
  }

  function updateBtn() {
    joinBtn.disabled = getCode().length < 4 || !nameInput.value.trim();
  }

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.classList.remove('hidden');
  }

  function clearError() {
    errorEl.classList.add('hidden');
  }

  boxes.forEach((box, i) => {
    box.addEventListener('input', () => {
      box.value = box.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 1);
      if (box.value && i < boxes.length - 1) boxes[i + 1].focus();
      updateBtn();
      clearError();
    });

    box.addEventListener('keydown', e => {
      if (e.key === 'Backspace' && !box.value && i > 0) {
        boxes[i - 1].focus();
      }
    });

    box.addEventListener('paste', e => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData)
        .getData('text')
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, '')
        .slice(0, 4);
      text.split('').forEach((ch, j) => { if (boxes[j]) boxes[j].value = ch; });
      const next = Math.min(text.length, boxes.length - 1);
      boxes[next].focus();
      updateBtn();
    });
  });

  nameInput.addEventListener('input', () => { updateBtn(); clearError(); });
  nameInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !joinBtn.disabled) joinBtn.click(); });

  joinBtn.addEventListener('click', async () => {
    const code = getCode();
    const name = nameInput.value.trim();
    joinBtn.disabled = true;
    joinBtn.textContent = 'Joining…';
    clearError();

    try {
      const res = await fetch(`/rooms/${code}/validate`);
      if (!res.ok) {
        showError('No room found with that code. Ask your host for the latest code.');
        joinBtn.disabled = false;
        joinBtn.textContent = 'Join Room';
        return;
      }
    } catch {
      showError('Unable to reach the server. Please try again.');
      joinBtn.disabled = false;
      joinBtn.textContent = 'Join Room';
      return;
    }

    window.location.href = `/play/${code}?name=${encodeURIComponent(name)}`;
  });

  boxes[0].focus();
}());
