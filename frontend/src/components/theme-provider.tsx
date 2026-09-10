"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useSyncExternalStore,
} from "react";

type Theme = "light" | "dark";
type ThemeState = { theme: Theme; toggle: () => void; setTheme: (t: Theme) => void };

const ThemeContext = createContext<ThemeState | null>(null);
const STORAGE_KEY = "curewise-theme";

/**
 * The `.dark` class on <html> is the single source of truth — the no-flash
 * script in `app/layout.tsx` applies it before React ever runs. React
 * subscribes to that class rather than keeping a second copy in state, so the
 * two can't drift and there's no mount-time sync effect.
 */
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/** Light is the default, and what the server renders. */
function getServerSnapshot(): Theme {
  return "light";
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const apply = useCallback((t: Theme) => {
    document.documentElement.classList.toggle("dark", t === "dark");
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {}
    listeners.forEach((listener) => listener());
  }, []);

  // Read the class rather than closing over `theme` so a toggle always acts on
  // what's actually applied.
  const toggle = useCallback(
    () => apply(getSnapshot() === "dark" ? "light" : "dark"),
    [apply]
  );

  const value = useMemo(
    () => ({ theme, toggle, setTheme: apply }),
    [theme, toggle, apply]
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
