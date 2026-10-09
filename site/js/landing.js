(function () {
  'use strict';

  // The game app's address — the ONLY place this page names it. Everything
  // here hands off to the app by URL; the page owns no game state.
  const APP_URL = 'https://quizmaster.digvijaymahra.com';

  // Host: a plain cross-origin form POST to the app's /rooms, which
  // redirects to a fresh control center — the same request the app's own
  // "Host a new game" button makes.
  const hostForm = document.getElementById('host-form');
  hostForm.action = APP_URL + '/rooms';
  document.getElementById('cta-host').addEventListener('click', e => {
    e.preventDefault();
    hostForm.submit();
  });

  // Join: code only. The app's /play/<code> checks it, asks for the name,
  // and shows an inline error with the code kept if no such room exists.
  // OTP handling mirrors the app's static/js/create.js.
  const boxes = Array.from(document.querySelectorAll('.otp-box'));
  const joinBtn = document.getElementById('join-btn');

  function getCode() { return boxes.map(b => b.value).join(''); }
  function updateBtn() { joinBtn.disabled = getCode().length < 4; }

  boxes.forEach((box, i) => {
    box.addEventListener('input', () => {
      box.value = box.value.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 1);
      if (box.value && i < boxes.length - 1) boxes[i + 1].focus();
      updateBtn();
    });

    box.addEventListener('keydown', e => {
      if (e.key === 'Backspace' && !box.value && i > 0) boxes[i - 1].focus();
      if (e.key === 'Enter' && !joinBtn.disabled) joinBtn.click();
    });

    box.addEventListener('paste', e => {
      e.preventDefault();
      const text = (e.clipboardData || window.clipboardData)
        .getData('text')
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, '')
        .slice(0, 4);
      text.split('').forEach((ch, j) => { if (boxes[j]) boxes[j].value = ch; });
      boxes[Math.min(text.length, boxes.length - 1)].focus();
      updateBtn();
    });
  });

  joinBtn.addEventListener('click', () => {
    joinBtn.disabled = true;
    joinBtn.textContent = 'Joining…';
    window.location.href = APP_URL + '/play/' + getCode();
  });

  // Coming back from the app restores this page from the back/forward cache
  // with the button still saying "Joining…".
  window.addEventListener('pageshow', () => {
    joinBtn.textContent = 'Join game';
    updateBtn();
  });
})();
