// Shared between host.js and present.js — the one piece of question
// rendering that's genuinely identical between the control center and
// the presentation view. Loaded via a plain <script> tag before either
// page's own script (no build step, no module system in this project).
function mediaImagesHtml(media, imgClass) {
  return (media || [])
    .map(fn => `<img class="${imgClass}" src="/media/${JOIN_CODE}/${HOST_TOKEN}/${encodeURIComponent(fn)}" alt="">`)
    .join('');
}
