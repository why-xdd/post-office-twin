/**
 * Обращения о неисправностях по открытому плану. Их видят все, кто открыл план: список
 * обновляется сам раз в полминуты, чтобы сообщение с QR-бирки дошло до всех без перезагрузки.
 */

import { useEffect } from 'react';
import { create } from 'zustand';
import { api } from '../lib/api';
import type { Incident } from '../lib/plan';

const REFRESH_MS = 30_000;

interface IncidentsState {
  planId: string | null;
  items: Incident[];
  error: string | null;
  load: (planId: string) => Promise<void>;
  upsert: (incident: Incident) => void;
}

export const useIncidents = create<IncidentsState>((set, get) => ({
  planId: null,
  items: [],
  error: null,
  load: async (planId) => {
    if (get().planId !== planId) set({ planId, items: [], error: null });
    try {
      const items = await api.incidents(planId);
      if (get().planId === planId) set({ items, error: null });
    } catch {
      if (get().planId === planId) set({ error: 'Обращения не загрузились: сервер не ответил.' });
    }
  },
  upsert: (incident) =>
    set((state) => ({
      items: [incident, ...state.items.filter((item) => item.id !== incident.id)],
    })),
}));

/** Держит список обращений свежим, пока план открыт. */
export function useIncidentsFeed(planId: string | null): void {
  const load = useIncidents((s) => s.load);
  useEffect(() => {
    if (!planId) return;
    void load(planId);
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load(planId);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [planId, load]);
}

export const isOpen = (incident: Incident) => incident.status !== 'resolved';

/** Объекты, отмеченные «не работает» в открытых обращениях. */
export function outOfService(items: Incident[]): string[] {
  return [...new Set(items.filter((i) => isOpen(i) && i.out_of_service).map((i) => i.object_id))];
}
