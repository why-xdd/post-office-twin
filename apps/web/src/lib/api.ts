/**
 * Клиент API сервиса.
 *
 * Ошибки сервера переводятся в понятный человеку текст: пользователь, загрузивший файл
 * неверного формата, должен увидеть, что именно не так, а не код состояния.
 */

import type { Plan, PlanMetrics, Rule, ValidationReport } from './plan';

const BASE_URL = (import.meta.env.VITE_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');

export class ApiError extends Error {
  readonly status: number;
  readonly details: string[];

  constructor(message: string, status: number, details: string[] = []) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

interface ValidationDetail {
  'сообщение'?: string;
  'что проверить'?: { 'поле': string; 'проблема': string }[];
}

async function toError(response: Response): Promise<ApiError> {
  let message = `Сервер ответил кодом ${response.status}`;
  let details: string[] = [];
  try {
    const body = (await response.json()) as { detail?: string | ValidationDetail };
    const detail = body.detail;
    if (typeof detail === 'string') {
      message = detail;
    } else if (detail && typeof detail === 'object') {
      message = detail['сообщение'] ?? message;
      details = (detail['что проверить'] ?? []).map(
        (item) => `${item['поле']}: ${item['проблема']}`,
      );
    }
  } catch {
    // Тело ответа не является JSON — оставляем сообщение по коду состояния.
  }
  if (response.status === 404) {
    message = message || 'Запрошенный план не найден.';
  }
  return new ApiError(message, response.status, details);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      'Не удалось связаться с сервером. Проверьте, что сервис запущен и доступен.',
      0,
    );
  }
  if (!response.ok) {
    throw await toError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export interface PlanSummary {
  id: string;
  title: string;
  created_at: string;
  versions: number;
}

export interface PlanRef {
  id: string;
  version: number;
}

export interface PlanVersion {
  version: number;
  note: string;
  created_at: string;
}

export const api = {
  health: () => request<{ status: string }>('/health'),

  rules: () => request<Rule[]>('/api/rules'),

  samplePlan: () => request<Plan>('/api/sample-plan'),

  listPlans: () => request<PlanSummary[]>('/api/plans'),

  getPlan: (id: string, version?: number) =>
    request<Plan>(`/api/plans/${id}${version ? `?version=${version}` : ''}`),

  createPlan: (title: string, document: Plan, note = '') =>
    request<PlanRef>('/api/plans', {
      method: 'POST',
      body: JSON.stringify({ title, document, note }),
    }),

  savePlan: (id: string, document: Plan, note = '') =>
    request<PlanRef>(`/api/plans/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ document, note }),
    }),

  versions: (id: string) => request<PlanVersion[]>(`/api/plans/${id}/versions`),

  deletePlan: (id: string) => request<void>(`/api/plans/${id}`, { method: 'DELETE' }),

  validate: (document: Plan, ruleOverrides: Record<string, boolean> = {}) =>
    request<ValidationReport>('/api/validate', {
      method: 'POST',
      body: JSON.stringify({ document, rule_overrides: ruleOverrides }),
    }),

  metrics: (document: Plan) =>
    request<PlanMetrics>('/api/metrics', {
      method: 'POST',
      body: JSON.stringify({ document }),
    }),
};
