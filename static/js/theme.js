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

  function apply() {
    document.documentElement.dataset.theme =
      getOverride() || (media.matches ? 'dark' : 'light');
  }

  apply();
  media.addEventListener('change', apply);
  window.addEventListener('storage', e => { if (e.key === KEY) apply(); });

  window.qbTheme = {
    get: getOverride,
    // value: 'light' | 'dark' | null (null = follow the OS)
    set(value) {
      try {
        if (value) localStorage.setItem(KEY, value);
        else localStorage.removeItem(KEY);
      } catch (e) { /* private mode — falls back to the OS setting */ }
      apply();
    },
  };
})();
