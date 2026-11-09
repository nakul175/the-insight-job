/* Native permission checks remain authoritative. Never replay forms on refresh. */
(() => {
  const url = document.body.dataset.permissionUrl;
  const stamp = document.body.dataset.permissionStamp;
  if (!url || !stamp) return;
  let busy = false, terminal = false, hidden = false;
  const clear = message => {
    hidden = true;
    document.querySelectorAll('dialog').forEach(d => d.remove());
    const main = document.getElementById('main');
    if (main) { main.replaceChildren(); const p = document.createElement('p');
      p.setAttribute('role', 'alert'); p.textContent = message; main.append(p); }
  };
  async function check() {
    if (busy || terminal || document.hidden) return;
    busy = true;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch(url, {cache: 'no-store', credentials: 'same-origin',
        redirect: 'error', signal: controller.signal});
      if ([401,403,404].includes(response.status)) {
        terminal = true; clear('Workspace access changed. Sign in again to continue.'); return;
      }
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      if (typeof data.stamp !== 'string' || !/^\d+:(administrator|operator|reviewer|viewer)$/.test(data.stamp)) throw new Error('invalid');
      if (data.stamp !== stamp || hidden) { clear('Refreshing workspace access…'); location.replace(url.replace('permission-status/', ''));  }
    } catch (_) { clear('Access cannot be verified. Reconnecting…'); }
    finally { clearTimeout(timer); busy = false; }
  }
  setInterval(check, 5000);
  window.addEventListener('focus', check);
  document.addEventListener('visibilitychange', check);
  window.addEventListener('pageshow', e => { if (e.persisted) { clear('Checking workspace access…'); check(); } });
  check();
})();
