/**
 * Доступность сервера.
 *
 * Сервер на бесплатном хостинге засыпает без запросов и просыпается до минуты. Вместо
 * бесконечной загрузки пользователь видит, что происходит и сколько примерно ждать.
 */

import { useEffect, useState } from 'react';
import { api } from './api';

export type ServerState = 'checking' | 'waking' | 'ready' | 'offline';

const WAKING_AFTER_MS = 1500;
const GIVE_UP_AFTER_MS = 90_000;
const RETRY_EVERY_MS = 4000;

export function useServer(): { state: ServerState; retry: () => void } {
  const [state, setState] = useState<ServerState>('checking');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const started = Date.now();
    const wakingTimer = window.setTimeout(() => {
      if (!cancelled) setState((s) => (s === 'checking' ? 'waking' : s));
    }, WAKING_AFTER_MS);

    const probe = async () => {
      while (!cancelled) {
        try {
          await api.health();
          if (!cancelled) setState('ready');
          return;
        } catch {
          if (Date.now() - started > GIVE_UP_AFTER_MS) {
            if (!cancelled) setState('offline');
            return;
          }
          await new Promise((resolve) => window.setTimeout(resolve, RETRY_EVERY_MS));
        }
      }
    };
    void probe();

    return () => {
      cancelled = true;
      window.clearTimeout(wakingTimer);
    };
  }, [attempt]);

  return {
    state,
    retry: () => {
      setState('checking');
      setAttempt((a) => a + 1);
    },
  };
}
