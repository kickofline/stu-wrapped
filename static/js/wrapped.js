/* Story-style Wrapped viewer: tap/swipe/keys to move between 9:16 slides. The owner's
   share button sends a link to the current slide (/wrapped/<id>/<slide>); the outro
   shares the whole Wrapped. Friends get a "Get your own" button instead.

   So that link previews (iMessage, Discord...) show the slide itself, the owner's
   browser renders each slide to a 1080-wide PNG shortly after showing it and uploads
   it once; the server uses it as that slide link's og:image. */

const stage = document.getElementById('stage');
const slides = Array.from(stage.querySelectorAll('.slide'));
const progress = document.getElementById('progress');
const shareBtn = document.getElementById('share-btn');
const shareLabel = document.getElementById('share-label');
const bars = slides.map(() => progress.appendChild(document.createElement('i')));
const images = new Map();   // slide index -> Promise<Blob>
let current = 0;
let furthest = 0;          // deepest slide reached, reported when the page is hidden
let reportedFurthest = -1;

function show(index) {
  if (index < 0 || index >= slides.length) return;
  current = index;
  furthest = Math.max(furthest, index);
  slides.forEach((slide, i) => {
    slide.classList.toggle('active', i === index);
    slide.classList.toggle('past', i < index);
  });
  bars.forEach((bar, i) => bar.classList.toggle('on', i <= index));
  const style = getComputedStyle(slides[index]);
  document.body.style.backgroundColor = style.getPropertyValue('--bg');
  progress.style.color = style.getPropertyValue('--fg');
  if (shareLabel) shareLabel.textContent = isLinkSlide(index) ? 'Share link' : 'Share';
  if (IS_OWNER && !isLinkSlide(index)) setTimeout(() => { if (current === index) uploadSlide(index); }, 650);
}

const isLinkSlide = (i) => slides[i].dataset.share === 'link';
const next = () => show(current + 1);
const prev = () => show(current - 1);

// ── Navigation: tap left third = back, elsewhere = forward; swipe; arrow keys ──
stage.addEventListener('click', (e) => {
  if (e.target.closest('a, button')) return;
  const box = stage.getBoundingClientRect();
  (e.clientX - box.left < box.width / 3) ? prev() : next();
});

let touchX = null;
stage.addEventListener('touchstart', (e) => { touchX = e.touches[0].clientX; }, { passive: true });
stage.addEventListener('touchend', (e) => {
  if (touchX === null) return;
  const dx = e.changedTouches[0].clientX - touchX;
  touchX = null;
  if (Math.abs(dx) > 45) { dx < 0 ? next() : prev(); }
}, { passive: true });

document.addEventListener('keydown', (e) => {
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); next(); }
  else if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
});

// ── Slide images for link previews (owner only) ─────────────────
function imageFor(index) {
  if (!images.has(index)) {
    const frame = slides[index].querySelector('.frame');
    const job = document.fonts.ready
      .then(() => html2canvas(frame, {
        scale: 1080 / frame.offsetWidth,
        backgroundColor: getComputedStyle(frame).getPropertyValue('--bg').trim(),
        useCORS: true,
        logging: false,
        onclone: (doc) => doc.documentElement.classList.add('capturing'),
      }))
      .then((canvas) => new Promise((resolve, reject) =>
        canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('empty image'))), 'image/png')));
    job.catch(() => images.delete(index));  // let a later visit retry
    images.set(index, job);
  }
  return images.get(index);
}

const slideName = (i) => slides[i].dataset.slide || String(i);
const slideUrl = (i) => (isLinkSlide(i) ? SHARE_URL : `${SHARE_URL}/${slideName(i)}`);

function uploadSlide(index) {
  const name = slideName(index);
  if (UPLOADED_SLIDES.has(name) || typeof html2canvas === 'undefined') return;
  UPLOADED_SLIDES.add(name);  // one attempt per page view
  imageFor(index)
    .then((blob) => fetch(`/api/slide-image/${SHARE_ID}/${name}`, {
      method: 'POST', body: blob, headers: { 'Content-Type': 'image/png' },
    }))
    .catch(() => UPLOADED_SLIDES.delete(name));
}

async function shareLink() {
  if (navigator.share) {
    try {
      await navigator.share({ title: SHARE_TITLE, url: SHARE_URL });
      track('share_link', { share_id: SHARE_ID, method: 'share' });
    } catch (_) {
      track('share_link', { share_id: SHARE_ID, method: 'dismissed' });
    }
    return;
  }
  await navigator.clipboard.writeText(SHARE_URL);
  track('share_link', { share_id: SHARE_ID, method: 'copy' });
  flashLabel('Link copied!');
}

function flashLabel(text) {
  const original = isLinkSlide(current) ? 'Share link' : 'Share';
  shareLabel.textContent = text;
  setTimeout(() => { shareLabel.textContent = original; }, 2200);
}

async function shareSlide() {
  const index = current;
  if (isLinkSlide(index)) return shareLink();
  const url = slideUrl(index);
  if (navigator.share) {
    try {
      await navigator.share({ title: SHARE_TITLE, url });
      track('share_slide', { share_id: SHARE_ID, slide: slideName(index), method: 'share' });
    } catch (_) {
      track('share_slide', { share_id: SHARE_ID, slide: slideName(index), method: 'dismissed' });
    }
    return;
  }
  await navigator.clipboard.writeText(url);
  track('share_slide', { share_id: SHARE_ID, slide: slideName(index), method: 'copy' });
  flashLabel('Link copied!');
}

if (shareBtn) shareBtn.addEventListener('click', shareSlide);
document.querySelectorAll('.js-share-link').forEach((btn) =>
  btn.addEventListener('click', (e) => { e.stopPropagation(); shareLink(); }));

// How far into the deck this visit got: sent when the page is hidden (tab switch,
// app switch, close), and again only if they went further after coming back.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'hidden' || furthest <= reportedFurthest) return;
  reportedFurthest = furthest;
  track('slide_reached', { share_id: SHARE_ID, index: furthest + 1, total: slides.length, slide: slideName(furthest) });
});

// ── Big headings: shrink one until its longest word fits on a line, instead of
// splitting the word ("Connoisseu / r"). Sizes are in frame units, so the fit is
// the same on every screen and in the exported image.
function fitDisplays() {
  document.querySelectorAll('.slide .display').forEach((el) => {
    if (!el.dataset.fs) el.dataset.fs = getComputedStyle(el).getPropertyValue('--fs').trim() || '80';
    const base = parseFloat(el.dataset.fs);
    let fs = base;
    el.style.overflowWrap = 'normal';
    el.style.setProperty('--fs', fs);
    while (el.scrollWidth > el.clientWidth + 1 && fs > base * 0.4) {
      fs -= 2;
      el.style.setProperty('--fs', fs);
    }
  });
}
document.fonts.ready.then(fitDisplays);
window.addEventListener('resize', fitDisplays);

// A slide link (/wrapped/<id>/<slide>) opens the deck at that slide.
const startIndex = slides.findIndex((slide) => slide.dataset.slide === START_SLIDE);
show(Math.max(0, startIndex));
