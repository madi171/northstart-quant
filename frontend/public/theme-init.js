try {
  const preference = localStorage.getItem('northstar-theme');
  const theme = preference === 'light' || preference === 'dark'
    ? preference
    : window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]').setAttribute('content', theme === 'dark' ? '#141620' : '#f2f4f8');
} catch {}
