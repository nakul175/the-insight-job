/* Tabs keep the shared correction form in place, including its pending value/reason. */
(() => {
  const tabs = Array.from(document.querySelectorAll('[data-review-tab]'));
  const select = button => {
    tabs.forEach(tab => {
      const active = tab === button;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute('aria-controls')).hidden = !active;
    });
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      const keys = {ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1};
      if (!(event.key in keys)) return;
      event.preventDefault();
      const target = tabs[keys[event.key]];
      select(target);
      target.focus();
    });
  });
  if (tabs.length) select(tabs[0]);
  document.querySelector('#use-current-revision')?.addEventListener('click', event => {
    document.querySelector('#review-action [name="expected_version"]').value = event.currentTarget.dataset.version;
    document.querySelector('#conflict-status').textContent = 'Current review revision selected. Check the compared evidence and pending correction before submitting.';
  });
  const error = document.querySelector('#review-action .errorlist');
  if (error) {error.setAttribute('role', 'alert'); error.tabIndex = -1; error.focus();}
})();
