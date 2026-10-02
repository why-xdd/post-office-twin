/**
 * Кто вошёл в сервис. Без входа всё работает как раньше; вход добавляет действия по роли:
 * начальник и инженер берут обращения в работу и отмечают осмотры.
 */

import { create } from 'zustand';
import { api } from '../lib/api';
import type { User } from '../lib/plan';

interface SessionState {
  user: User | null;
  demo: boolean;
  roles: { role: string; label: string }[];
  loaded: boolean;
  refresh: () => Promise<void>;
  login: (login: string, password: string) => Promise<void>;
  demoLogin: (role: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useSession = create<SessionState>((set) => ({
  user: null,
  demo: false,
  roles: [],
  loaded: false,
  refresh: async () => {
    try {
      const state = await api.me();
      set({ user: state.user, demo: state.demo, roles: state.roles, loaded: true });
    } catch {
      // Сервер недоступен — работаем как гость, вход появится, когда он ответит.
      set({ loaded: true });
    }
  },
  login: async (login, password) => {
    const { user } = await api.login(login, password);
    set({ user });
  },
  demoLogin: async (role) => {
    const { user } = await api.demoLogin(role);
    set({ user });
  },
  logout: async () => {
    await api.logout();
    set({ user: null });
  },
}));

/** Может ли человек брать обращения в работу и отмечать осмотры. */
export function canManage(user: User | null): boolean {
  return user?.role === 'head' || user?.role === 'engineer';
}
