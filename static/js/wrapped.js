/* Story-style Wrapped viewer: tap/swipe/keys to move between 9:16 slides, and a
   share button that sends the current slide as a 1080x1920 image through the
   phone's share sheet (download on desktop). The outro slide shares the link.

   iOS only opens the share sheet while the tap is still "fresh", so each slide's
   image is rendered in the background as soon as it is shown; the tap then just
   hands the cached file to navigator.share(). */

const stage = document.getElementById('stage');
const slides = Array.from(stage.querySelectorAll('.slide'));
const progress = document.getElementById('progress');
const shareBtn = document.getElementById('share-btn');
const shareLabel = document.getElementById('share-label');
const bars = slides.map(() => progress.appendChild(document.createElement('i')));
const images = new Map();   // slide index -> Promise<Blob>
let current = 0;

function show(index) {
  if (index < 0 || index >= slides.length) return;
  current = index;
  slides.forEach((slide, i) => {
    slide.classList.toggle('active', i === index);
    slide.classList.toggle('past', i < index);
  });
  bars.forEach((bar, i) => bar.classList.toggle('on', i <= index));
  const style = getComputedStyle(slides[index]);
  document.body.style.backgroundColor = style.getPropertyValue('--bg');
  progress.style.color = style.getPropertyValue('--fg');
  shareLabel.textContent = isLinkSlide(index) ? 'Share link' : 'Share';
  if (!isLinkSlide(index)) setTimeout(() => { if (current === index) imageFor(index); }, 650);
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

// ── Share images ──────────────────────────────────────────────
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
    job.catch(() => images.delete(index));  // let a later tap retry
    images.set(index, job);
  }
  return images.get(index);
}

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function shareLink() {
  if (navigator.share) {
    try { await navigator.share({ title: SHARE_TITLE, url: SHARE_URL }); } catch (_) { /* dismissed */ }
    return;
  }
  await navigator.clipboard.writeText(SHARE_URL);
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

  shareBtn.disabled = true;
  shareLabel.textContent = 'Preparing…';
  let blob;
  try {
    blob = await imageFor(index);
  } catch (_) {
    shareBtn.disabled = false;
    flashLabel('Try again');
    return;
  }
  shareBtn.disabled = false;
  shareLabel.textContent = 'Share';

  const file = new File([blob], `obu-stu-wrapped-${index + 1}.png`, { type: 'image/png' });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: SHARE_TITLE, text: 'Get yours at wrapped.drew.place' });
    } catch (err) {
      // iOS refuses if rendering took long enough to expire the tap; the image is
      // cached now, so the next tap shares instantly.
      if (err.name === 'NotAllowedError') flashLabel('Tap again to share');
    }
    return;
  }
  download(blob, file.name);
  flashLabel('Saved!');
}

shareBtn.addEventListener('click', shareSlide);
document.querySelectorAll('.js-share-link').forEach((btn) =>
  btn.addEventListener('click', (e) => { e.stopPropagation(); shareLink(); }));

show(0);
