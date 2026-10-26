import {useEffect, useState} from 'react';
import {Monitor, Moon, Sun} from 'lucide-react';

type Theme = 'system' | 'light' | 'dark';
const storageKey = 'northstar-theme';

function savedTheme(): Theme {
  try {
    const value = localStorage.getItem(storageKey);
    if (value === 'light' || value === 'dark') return value;
  } catch {}
  return 'system';
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(savedTheme);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const resolved = theme === 'system' ? media.matches ? 'dark' : 'light' : theme;
      document.documentElement.dataset.theme = resolved;
      document.documentElement.style.colorScheme = resolved;
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#141620' : '#f2f4f8');
    };
    apply();
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [theme]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === storageKey || event.key === null) setTheme(savedTheme());
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);
  const Icon = theme === 'system' ? Monitor : theme === 'dark' ? Moon : Sun;
  return <label className="theme-toggle" title="切换界面主题">
    <Icon size={16} aria-hidden="true"/>
    <select aria-label="界面主题" value={theme} onChange={event => {
      const value = event.target.value as Theme;
      setTheme(value);
      try { localStorage.setItem(storageKey, value); } catch {}
    }}>
      <option value="system">跟随系统</option>
      <option value="light">浅色模式</option>
      <option value="dark">深色模式</option>
    </select>
  </label>;
}
