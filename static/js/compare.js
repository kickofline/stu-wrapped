const compareShare = document.getElementById('share-compare');

if (compareShare) {
  compareShare.addEventListener('click', async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title: COMPARE_SHARE_TITLE, url: COMPARE_SHARE_URL });
        track('compare_shared', { method: 'share' });
      } catch (_) { track('compare_shared', { method: 'dismissed' }); }
      return;
    }
    try {
      await navigator.clipboard.writeText(COMPARE_SHARE_URL);
      track('compare_shared', { method: 'copy' });
      const original = compareShare.textContent;
      compareShare.textContent = 'Link copied';
      setTimeout(() => { compareShare.textContent = original; }, 2200);
    } catch (_) {
      window.prompt('Copy this link', COMPARE_SHARE_URL);
    }
  });
}
