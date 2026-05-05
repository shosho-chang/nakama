// Upload page bootstrap — runs under CSP `script-src 'self'`. Wires drag-and-drop
// for the two dropzone labels; clicking still works through the native <input>.

function basename(name) {
  return name.split(/[/\\]/).pop() || name;
}

function setFile(zone, file) {
  const input = zone.querySelector('input[type="file"]');
  if (!input) return;
  const dt = new DataTransfer();
  dt.items.add(file);
  input.files = dt.files;
  zone.dataset.empty = 'false';
  zone.classList.add('is-filled');
  const label = zone.querySelector('[data-filename]');
  if (label) label.textContent = basename(file.name);
}

function clearFile(zone) {
  zone.dataset.empty = 'true';
  zone.classList.remove('is-filled');
  const label = zone.querySelector('[data-filename]');
  if (label) label.textContent = '';
}

document.querySelectorAll('.dropzone').forEach((zone) => {
  const input = zone.querySelector('input[type="file"]');

  input.addEventListener('change', () => {
    if (input.files && input.files[0]) {
      const file = input.files[0];
      zone.dataset.empty = 'false';
      zone.classList.add('is-filled');
      const label = zone.querySelector('[data-filename]');
      if (label) label.textContent = basename(file.name);
    } else {
      clearFile(zone);
    }
  });

  ['dragenter', 'dragover'].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.add('is-hover');
    });
  });

  ['dragleave', 'dragend'].forEach((evt) => {
    zone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      zone.classList.remove('is-hover');
    });
  });

  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.remove('is-hover');
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (file && /\.epub$/i.test(file.name)) {
      setFile(zone, file);
    }
  });
});
