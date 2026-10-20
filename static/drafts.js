/* Server-scoped recoverable drafts. Secrets and source account screens are excluded. */
(() => {
  document.querySelectorAll('.errorlist').forEach(el => el.setAttribute('role', 'alert'));
  const context = document.querySelector('[data-draft-kind]');
  if (!context || !context.dataset.draftKind || !context.dataset.draftId) return;
  let dirty = false;
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.textContent = 'Unsaved changes will warn before navigation. Recoverable drafts require current permissions.';
  status.className = 'draft-status';
  const firstForm = context.querySelector('form[method="post"]');
  if (firstForm) firstForm.before(status);
  document.querySelectorAll('main form[method="post"]').forEach((form, index) => {
    const controls = Array.from(form.elements).filter(el => el.name && el.name !== 'csrfmiddlewaretoken' && !/password|secret|token|cookie|ciphertext|connection/i.test(el.name) && el.type !== 'file');
    if (!controls.length || Array.from(form.elements).some(el => el.type === 'password' || /password|secret|cookie|ciphertext/i.test(el.name))) return;
    const url = `/app/${context.dataset.workspace}/drafts/${context.dataset.draftKind}/${context.dataset.draftId}/form-${index}/`;
    const csrf = form.querySelector('[name="csrfmiddlewaretoken"]')?.value;
    let timer;
    const values = () => {
      const payload = {};
      controls.forEach(el => {
        if ((el.type === 'checkbox' || el.type === 'radio') && !el.checked) return;
        if (el.multiple && el.options) {
          (payload[el.name] ||= []).push(...Array.from(el.selectedOptions).map(option => option.value));
        } else (payload[el.name] ||= []).push(el.value);
      });
      return payload;
    };
    const save = async () => {
      try {
        const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify(values())});
        status.textContent = response.ok ? 'Recoverable draft saved. Submission is still required.' : response.status === 403 ? 'Permissions changed. No inaccessible draft was saved; submission will recheck access.' : 'Draft save failed. Your input is still here; retry by editing or submitting.';
      } catch { status.textContent = 'Draft save failed. Your input is still here; retry when connected.'; }
    };
    form.addEventListener('input', () => {dirty = true; clearTimeout(timer); timer = setTimeout(save, 1200);});
    form.addEventListener('submit', () => {dirty = false; clearTimeout(timer);});
    if (context.dataset.method !== 'POST') {
      fetch(url).then(response => response.ok ? response.json() : null).then(data => {
        if (!data || !Object.keys(data.payload).length) return;
        const restore = document.createElement('button');
        restore.type = 'button';
        restore.textContent = 'Restore your saved draft';
        restore.addEventListener('click', () => {
          controls.forEach(el => {
            const saved = data.payload[el.name] || [];
            if (el.type === 'checkbox' || el.type === 'radio') el.checked = saved.includes(el.value);
            else if (el.multiple && el.options) Array.from(el.options).forEach(option => {option.selected = saved.includes(option.value);});
            else if (saved.length) el.value = saved[0];
          });
          dirty = true;
          status.textContent = 'Draft restored. Review current versions before submitting.';
          restore.remove();
        });
        form.prepend(restore);
      }).catch(() => {status.textContent = 'Draft recovery unavailable. Your current input is unchanged.';});
    }
  });
  window.addEventListener('beforeunload', event => {if (dirty) {event.preventDefault(); event.returnValue = '';}});
})();
