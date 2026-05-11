export function configureErrorPanel(message: string, onRetry: () => void): void {
  const msgEl = document.getElementById("error-msg") as HTMLParagraphElement | null;
  const retryBtn = document.getElementById("btn-retry") as HTMLButtonElement | null;
  if (msgEl) msgEl.textContent = message;
  if (retryBtn) retryBtn.onclick = onRetry;
}
