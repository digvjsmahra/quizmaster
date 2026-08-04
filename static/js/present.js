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
  // Stage slides — board / question / answer are mutually exclusive.
  // ----------------------------------------------------------------
  function hideAllSlides() {
    el('present-board-slide').classList.add('hidden');
    el('present-question-slide').classList.add('hidden');
    el('present-answer-slide').classList.add('hidden');
  }

  function renderBoardSlide() {
    hideAllSlides();
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
  // Stage: question slide (also decides whether to hand off to the
  // dedicated answer slide below)
  // ----------------------------------------------------------------
  function renderQuestionSlide(live) {
    hideAllSlides();
    el('present-question-slide').classList.remove('hidden');

    el('present-question-text').textContent = live.question || '';
    el('present-question-media').innerHTML = mediaImagesHtml(live.question_media, 'present-media');

    const answerEl = el('present-answer');
    if (!live.answer) {
      answerEl.textContent = '';
      answerEl.classList.remove('answer-shown');
      return;
    }

    // Decide inline fade-in (today's behavior) vs. the dedicated full-stage
    // answer slide: an answer with its own image always gets the full
    // slide (there's nowhere to show it alongside the question); otherwise
    // measure whether the answer would fit here. answerEl is still
    // collapsed (max-height: 0) at this point, so scrollHeight reads its
    // true content height with no visible flash, and the whole decision
    // resolves before this frame paints.
    answerEl.textContent = live.answer;
    const slide = el('present-question-slide');
    const stage = el('present-stage-box');
    const hasOwnMedia = (live.answer_media || []).length > 0;

    // slide has `height: 100%` in CSS (needed so its own content centers
    // within the stage) — reading its scrollHeight directly would just
    // report the stage's full height, not the content's natural size. Drop
    // to auto height only for this synchronous measurement, then restore.
    // This captures everything except the answer, since answerEl's own
    // max-height: 0 still clamps its contribution here to nothing — so its
    // natural height is measured separately via its own scrollHeight
    // (which, same as the slide, ignores its *own* clamp) and added back.
    slide.style.height = 'auto';
    const questionOnlyHeight = slide.scrollHeight;
    slide.style.height = '';
    const contentHeight = questionOnlyHeight + answerEl.scrollHeight;

    const stageStyle = getComputedStyle(stage);
    const availableHeight = stage.clientHeight
      - parseFloat(stageStyle.paddingTop) - parseFloat(stageStyle.paddingBottom);
    const wouldOverflow = contentHeight > availableHeight;

    if (hasOwnMedia || wouldOverflow) {
      answerEl.textContent = '';
      answerEl.classList.remove('answer-shown');
      renderAnswerSlide(live);
    } else {
      answerEl.classList.add('answer-shown');
    }
  }

  // ----------------------------------------------------------------
  // Stage: dedicated answer slide — question and its media do not persist
  // here by design; only reached via renderQuestionSlide's decision above.
  // ----------------------------------------------------------------
  const ANSWER_SLIDE_MIN_FONT_PX = 20;
  const ANSWER_SLIDE_FONT_STEP_PX = 4;
  let answerSlideRenderId = 0;

  // Resolves once every <img> in container has finished loading (or
  // errored) — an image's natural size affects layout, so the fit
  // calculation below must not run before it's known.
  function waitForImages(container) {
    const imgs = Array.from(container.querySelectorAll('img'));
    return Promise.all(imgs.map(img => {
      if (img.complete) return Promise.resolve();
      return new Promise(resolve => {
        img.addEventListener('load', resolve, { once: true });
        img.addEventListener('error', resolve, { once: true });
      });
    }));
  }

  // Shrinks present-answer-slide-text until its content fits the stage,
  // down to a floor — below which it's better to read the whole answer
  // via scrolling than to render it unreadably small. Reuses the same
  // "temporarily height: auto, read scrollHeight, restore" measurement
  // idiom as renderQuestionSlide's inline-vs-full-slide decision above.
  function fitAnswerSlideContent() {
    const stage = el('present-stage-box');
    const slide = el('present-answer-slide');
    const textEl = el('present-answer-slide-text');

    const stageStyle = getComputedStyle(stage);
    const availableHeight = stage.clientHeight
      - parseFloat(stageStyle.paddingTop) - parseFloat(stageStyle.paddingBottom);

    function measure() {
      slide.style.height = 'auto';
      const height = slide.scrollHeight;
      slide.style.height = '';
      return height;
    }

    let fontPx = parseFloat(getComputedStyle(textEl).fontSize);
    while (measure() > availableHeight && fontPx > ANSWER_SLIDE_MIN_FONT_PX) {
      fontPx = Math.max(ANSWER_SLIDE_MIN_FONT_PX, fontPx - ANSWER_SLIDE_FONT_STEP_PX);
      textEl.style.fontSize = `${fontPx}px`;
    }

    // Still doesn't fit even at the floor: fall back to a scrollable view
    // anchored at the true top, rather than leaving it centered — centered
    // overflow inside a scrollable box makes the *start* of the content
    // unreachable by scrolling (scrollTop can't go negative), silently
    // cutting off however much overflows above the natural center.
    slide.classList.toggle('overflowing', measure() > availableHeight);
  }

  function renderAnswerSlide(live) {
    hideAllSlides();
    el('present-answer-slide').classList.remove('hidden');

    const textEl = el('present-answer-slide-text');
    const mediaEl = el('present-answer-slide-media');
    textEl.textContent = live.answer || '';
    textEl.style.fontSize = '';
    el('present-answer-slide').classList.remove('overflowing');
    mediaEl.innerHTML = mediaImagesHtml(live.answer_media, 'present-media');

    // Guard against a slow image load resolving after the host has
    // already moved on (cancelled, revealed something else) by the time
    // it finishes — only the most recent render's fit is ever applied.
    const renderId = ++answerSlideRenderId;
    waitForImages(mediaEl).then(() => {
      if (renderId !== answerSlideRenderId) return;
      fitAnswerSlideContent();
    });
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
      el('present-reviewing-badge').classList.toggle('hidden', !state.liveQuestion.reviewing);
      renderQuestionSlide(state.liveQuestion);
    } else {
      el('present-reviewing-badge').classList.add('hidden');
      renderBoardSlide();
    }
  });

  socket.on('state:queue', (data) => {
    renderQueue(data);
  });
}());
