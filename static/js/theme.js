// Light/dark appearance (SPEC.md §2). Loaded synchronously in every page's
// <head>, before first paint, so a dark page never flashes light.
//
// Always stamps <html data-theme="light|dark"> — styles.css keeps a single
// dark token block keyed on that attribute rather than duplicating it under
// a prefers-color-scheme media query. An override in localStorage
// ('qb_theme', set only from the control center's header) wins over the OS
// setting. It's per-browser, never room state: the storage event carries a
// change to the same browser's presentation/summary windows live, so the
// toggle never has to appear on the screen-share itself.
(function () {
  'use strict';

  const KEY = 'qb_theme';
  const media = window.matchMedia('(prefers-color-scheme: dark)');

  function getOverride() {
    try {
      const v = localStorage.getItem(KEY);
      return v === 'light' || v === 'dark' ? v : null;
    } catch (e) { return null; }
  }

  function systemTheme() { return media.matches ? 'dark' : 'light'; }

  function apply() {
    document.documentElement.dataset.theme = getOverride() || systemTheme();
    document.dispatchEvent(new Event('qbthemechange'));
  }

  function setOverride(value) {
    try {
      if (value) localStorage.setItem(KEY, value);
      else localStorage.removeItem(KEY);
    } catch (e) { /* private mode — falls back to the OS setting */ }
    apply();
  }

  // Flip light <-> dark. Landing back on what the OS already uses clears the
  // override, so the page follows the OS again (e.g. an automatic switch to
  // dark at sunset) — a two-state toggle with no separate "System" option.
  function toggle() {
    const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    setOverride(next === systemTheme() ? null : next);
  }

  apply();
  media.addEventListener('change', apply);
  window.addEventListener('storage', e => { if (e.key === KEY) apply(); });

  // Theme toggle: one icon button showing the current theme — sun (light) or
  // moon (dark) — the common pattern. Icons are inline SVG (Feather-style
  // shapes), so no icon font or third-party file.
  const THEME_ICONS = {
    light: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    dark: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
  };

  function mountToggle(btn) {
    function render() {
      const current = document.documentElement.dataset.theme;
      const other = current === 'dark' ? 'light' : 'dark';
      btn.innerHTML = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${THEME_ICONS[current]}</svg>`;
      btn.setAttribute('aria-label', `Switch to ${other} theme`);
      btn.title = `Switch to ${other} theme`;
    }
    btn.addEventListener('click', toggle);
    document.addEventListener('qbthemechange', render);
    render();
  }

  window.qbTheme = { get: getOverride, toggle, mountToggle };
})();
