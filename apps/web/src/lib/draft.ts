/**
 * Черновик плана в браузере.
 *
 * Правки живут в памяти вкладки до «Сохранить версию». Случайная перезагрузка или закрытая
 * вкладка не должны стоить часа работы, поэтому текущее состояние кладётся в localStorage и
 * при открытии плана предлагается вернуть.
 */

import type { Plan } from './plan';

const PREFIX = 'ops-twin:draft:';
/** Черновик старше суток — почти наверняка забытый, его не предлагаем. */
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export interface Draft {
  document: Plan;
  savedAt: number;
  version: number;
}

export function saveDraft(planId: string, document: Plan, version: number): void {
  try {
    localStorage.setItem(PREFIX + planId, JSON.stringify({ document, savedAt: Date.now(), version }));
  } catch {
    // Память браузера может быть переполнена или недоступна — работа продолжается без черновика.
  }
}

export function readDraft(planId: string): Draft | null {
  try {
    const raw = localStorage.getItem(PREFIX + planId);
    if (!raw) return null;
    const draft = JSON.parse(raw) as Draft;
    if (!draft?.document || Date.now() - draft.savedAt > MAX_AGE_MS) {
      clearDraft(planId);
      return null;
    }
    return draft;
  } catch {
    return null;
  }
}

export function clearDraft(planId: string): void {
  try {
    localStorage.removeItem(PREFIX + planId);
  } catch {
    // Нечего чистить — и ладно.
  }
}
