/* Wrapped slideshow navigation */

const slides = Array.from(document.querySelectorAll('.slide'));
const dots   = Array.from(document.querySelectorAll('.nav-dot'));
let current  = 0;
const total  = slides.length;

function goTo(index) {
  if (index < 0 || index >= total) return;

  // Exit current slide
  slides[current].classList.remove('active');
  slides[current].classList.add('exiting');
  const prev = current;
  setTimeout(() => slides[prev].classList.remove('exiting'), 600);

  // Enter new slide
  current = index;
  slides[current].classList.add('active');

  // Update dots
  dots.forEach((d, i) => d.classList.toggle('active', i === current));
}

function next() { goTo(current + 1); }
function prev() { goTo(current - 1); }

// Click: right half = next, left half = prev
document.querySelector('.wrapped-container').addEventListener('click', e => {
  // Ignore clicks on buttons/links
  if (e.target.closest('a, button')) return;
  if (e.clientX > window.innerWidth / 2) next(); else prev();
});

// Dot clicks
dots.forEach((dot, i) => {
  dot.addEventListener('click', e => {
    e.stopPropagation();
    goTo(i);
  });
});

// Keyboard
document.addEventListener('keydown', e => {
  if (e.key === 'ArrowRight' || e.key === ' ') { e.preventDefault(); next(); }
  else if (e.key === 'ArrowLeft')               { e.preventDefault(); prev(); }
  else if (e.key === 'Escape')                  { window.location.href = DASHBOARD_URL; }
});

// Save image button
const saveBtn = document.getElementById('save-img-btn');
if (saveBtn) {
  saveBtn.addEventListener('click', e => {
    e.stopPropagation();
    const card = document.getElementById('share-card');
    saveBtn.textContent = 'Saving…';
    saveBtn.disabled = true;
    html2canvas(card, { width: 1080, height: 1080, scale: 1, useCORS: true, backgroundColor: null })
      .then(canvas => {
        const link = document.createElement('a');
        link.download = 'stu-wrapped.png';
        link.href = canvas.toDataURL('image/png');
        link.click();
        saveBtn.textContent = 'Saved! 🎉';
        setTimeout(() => { saveBtn.textContent = 'Save Image 📸'; saveBtn.disabled = false; }, 2500);
      })
      .catch(() => { saveBtn.textContent = 'Save Image 📸'; saveBtn.disabled = false; });
  });
}

// Touch / swipe
let touchStartX = 0;
document.addEventListener('touchstart', e => {
  touchStartX = e.touches[0].clientX;
}, { passive: true });

document.addEventListener('touchend', e => {
  const dx = touchStartX - e.changedTouches[0].clientX;
  if (Math.abs(dx) > 50) { dx > 0 ? next() : prev(); }
}, { passive: true });
