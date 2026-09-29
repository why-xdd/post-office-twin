/**
 * Клиент API сервиса.
 *
 * Ошибки сервера переводятся в понятный человеку текст: пользователь, загрузивший файл
 * неверного формата, должен увидеть, что именно не так, а не код состояния.
 */

import type {
  CommandResult,
  Conclusion,
  ImportResult,
  AssistantReport,
  CapacityReport,
  DictionariesInfo,
  ImprovementReport,
  Plan,
  PlanMetrics,
  QueueLogReport,
  Reconciliation,
  VisionReport,
  Recommendation,
  Rule,
  ValidationReport,
} from './plan';

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

/** Проверка на сервере отвечает по-английски; человеку нужно по-русски. */
const PROBLEMS: [RegExp, string][] = [
  [/^Field required$/i, 'поле обязательно'],
  [/^Input should be a valid list$/i, 'должно быть списком'],
  [/^Input should be a valid (number|integer)/i, 'должно быть числом'],
  [/^Input should be a valid string$/i, 'должно быть текстом'],
  [/^Input should be a valid dictionary/i, 'должно быть объектом'],
  [/^Input should be greater than 0$/i, 'должно быть больше нуля'],
  [/^Input should be greater than or equal to 0$/i, 'не может быть отрицательным'],
  [/^List should have at least (\d+) items?/i, 'нужно не меньше $1 значений'],
  [/^Value error, /i, ''],
];

function translateProblem(problem: string): string {
  for (const [pattern, replacement] of PROBLEMS) {
    if (pattern.test(problem)) return problem.replace(pattern, replacement) || problem;
  }
  return problem;
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
        (item) => `${item['поле']}: ${translateProblem(item['проблема'])}`,
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
    // У загрузки файла тип содержимого ставит сам браузер: ему нужна граница составного тела.
    const isForm = init?.body instanceof FormData;
    response = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: isForm ? init?.headers : { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
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

  savePlan: (id: string, document: Plan, note = '', title?: string) =>
    request<PlanRef>(`/api/plans/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ document, note, title }),
    }),

  versions: (id: string) => request<PlanVersion[]>(`/api/plans/${id}/versions`),

  deletePlan: (id: string) => request<void>(`/api/plans/${id}`, { method: 'DELETE' }),

  validate: (document: Plan, ruleOverrides: Record<string, boolean> = {}, signal?: AbortSignal) =>
    request<ValidationReport>('/api/validate', {
      method: 'POST',
      body: JSON.stringify({ document, rule_overrides: ruleOverrides }),
      signal,
    }),

  metrics: (document: Plan) =>
    request<PlanMetrics>('/api/metrics', {
      method: 'POST',
      body: JSON.stringify({ document }),
    }),

  dictionaries: (document: Plan) =>
    request<DictionariesInfo>('/api/dictionaries', {
      method: 'POST',
      body: JSON.stringify({ document }),
    }),

  capacity: (document: Plan) =>
    request<CapacityReport>('/api/capacity', { method: 'POST', body: JSON.stringify({ document }) }),

  assistant: (document: Plan) =>
    request<AssistantReport>('/api/assistant', { method: 'POST', body: JSON.stringify({ document }) }),

  improve: (document: Plan, fixesOnly = false) =>
    request<ImprovementReport>(`/api/assistant/improve${fixesOnly ? '?fixes_only=true' : ''}`, {
      method: 'POST',
      body: JSON.stringify({ document }),
    }),

  /** Опись оборудования для 1С: файл, а не JSON, поэтому мимо общего request. */
  exportForAccounting: async (document: Plan): Promise<{ blob: Blob; filename: string }> => {
    let response: Response;
    try {
      response = await fetch(`${BASE_URL}/api/exchange/1c/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ document }),
      });
    } catch {
      throw new ApiError('Не удалось связаться с сервером. Проверьте, что сервис запущен и доступен.', 0);
    }
    if (!response.ok) throw await toError(response);
    const disposition = response.headers.get('content-disposition') ?? '';
    const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
    return { blob: await response.blob(), filename: encoded ? decodeURIComponent(encoded) : 'опись для 1С.csv' };
  },

  /** Кадры записи с камеры: считает людей и строит тепловую карту по плану. */
  visionAnalyze: (
    document: Plan,
    frames: Blob[],
    seconds: number[],
    calibration: { frame: [number, number][]; plan: [number, number][]; frame_size: [number, number] },
    personAreaSqm?: number,
  ) => {
    const form = new FormData();
    form.append('document', JSON.stringify(document));
    form.append('calibration', JSON.stringify(calibration));
    form.append('seconds', JSON.stringify(seconds));
    if (personAreaSqm) form.append('person_area_sqm', String(personAreaSqm));
    frames.forEach((frame, index) => form.append('frames', frame, `${String(index).padStart(3, '0')}.jpg`));
    return request<VisionReport>('/api/vision/analyze', { method: 'POST', body: form });
  },

  queueLog: (document: Plan, file: File) => {
    const form = new FormData();
    form.append('document', JSON.stringify(document));
    form.append('file', file);
    return request<QueueLogReport>('/api/capacity/log', { method: 'POST', body: form });
  },

  reconcile: (document: Plan, file: File) => {
    const form = new FormData();
    form.append('document', JSON.stringify(document));
    form.append('file', file);
    return request<Reconciliation>('/api/exchange/1c/reconcile', { method: 'POST', body: form });
  },

  importDrawing: (file: File, realWidthM: number, name: string) => {
    const form = new FormData();
    form.append('file', file);
    form.append('real_width_m', String(realWidthM));
    form.append('name', name);
    return request<ImportResult>('/api/import/drawing', { method: 'POST', body: form });
  },

  command: (document: Plan, text: string) =>
    request<CommandResult>('/api/assistant/command', {
      method: 'POST',
      body: JSON.stringify({ document, text }),
    }),

  conclusion: (document: Plan) =>
    request<Conclusion>('/api/assistant/conclusion', {
      method: 'POST',
      body: JSON.stringify({ document }),
    }),

  place: (document: Plan, equipmentType: string, zoneId?: string) =>
    request<Recommendation>('/api/assistant/place', {
      method: 'POST',
      body: JSON.stringify({ document, equipment_type: equipmentType, zone_id: zoneId ?? null }),
    }),
};
