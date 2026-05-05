// Bookshelf bootstrap — runs under CSP `script-src 'self'`, so this is served
// from origin rather than inlined. Sole job: detect cover-image load failures
// and unhide the title-initial placeholder underneath.

function markBroken(img) {
  img.classList.add('broken');
}

document.querySelectorAll('.cover img').forEach((img) => {
  if (img.complete) {
    if (!img.naturalWidth) markBroken(img);
    return;
  }
  img.addEventListener('error', () => markBroken(img));
  img.addEventListener('load', () => {
    if (!img.naturalWidth) markBroken(img);
  });
});
