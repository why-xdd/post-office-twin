/**
 * Предложение исправить все нарушения разом — для обзора плана.
 *
 * Сервер улучшает план пошагово и только перестановками. Расчёт занимает несколько секунд,
 * поэтому он запускается сразу при открытии плана, а результат запоминается для этого
 * состояния документа: к нажатию кнопки ответ обычно уже готов.
 */

import { api } from './api';
import type { ImprovementReport, Plan, RecommendationChange } from './plan';

export interface Shift {
  before: number;
  after: number;
}

/** Почему объект переезжает: какие нормы снимает перенос и насколько далеко он едет. */
export interface ProposalRow {
  objectId: string;
  /** На сколько метров переезжает объект. */
  distance: number | null;
  /** Основания: документ и пункт нормы, которые перенос выполняет. */
  bases: string[];
}

export interface FixProposal {
  changes: RecommendationChange[];
  errorsBefore: number;
  errorsAfter: number;
  entrance: Shift | null;
  waiting: Shift | null;
  movedObjects: string[];
  rows: ProposalRow[];
  warnings: Shift | null;
  assumptions: string[];
}

const CACHE_LIMIT = 6;
const cache = new Map<string, Promise<FixProposal>>();

/** Изменения всех шагов, сведённые к одному на объект: исходное место — из первого шага. */
export function mergeSteps(report: ImprovementReport): RecommendationChange[] {
  const byObject = new Map<string, RecommendationChange>();
  for (const step of report.steps) {
    for (const change of step.recommendation.changes) {
      const prior = byObject.get(change.object_id);
      byObject.set(change.object_id, {
        ...change,
        previous_position: prior?.previous_position ?? change.previous_position,
        op: prior?.op === 'add' ? 'add' : change.op,
      });
    }
  }
  return [...byObject.values()];
}

function shift(report: ImprovementReport, key: string): Shift | null {
  const before = report.initial[key];
  const after = report.final[key];
  if (typeof before !== 'number' || typeof after !== 'number' || before === after) return null;
  return { before, after };
}

function toRows(report: ImprovementReport, changes: RecommendationChange[]): ProposalRow[] {
  const bases = new Map<string, Set<string>>();
  for (const step of report.steps) {
    const found = step.recommendation.reasons
      .filter((reason) => reason.source)
      .map((reason) => `${reason.source}${reason.clause ? `, ${reason.clause}` : ''}`);
    for (const change of step.recommendation.changes) {
      const known = bases.get(change.object_id) ?? new Set<string>();
      found.forEach((basis) => known.add(basis));
      bases.set(change.object_id, known);
    }
  }
  return changes.map((change) => {
    const from = change.previous_position;
    const to = change.position;
    return {
      objectId: change.object_id,
      distance: from && to ? Math.hypot(to[0] - from[0], to[1] - from[1]) : null,
      bases: [...(bases.get(change.object_id) ?? [])],
    };
  });
}

function toProposal(report: ImprovementReport): FixProposal {
  const changes = mergeSteps(report);
  const assumptions = new Set(report.steps.flatMap((step) => step.recommendation.assumptions));
  return {
    rows: toRows(report, changes),
    warnings: shift(report, 'warnings'),
    assumptions: [...assumptions],
    changes,
    errorsBefore: Number(report.initial.errors ?? 0),
    errorsAfter: Number(report.final.errors ?? 0),
    entrance: shift(report, 'entrance_route_width'),
    waiting: shift(report, 'waiting_capacity'),
    movedObjects: changes.map((c) => c.object_id),
  };
}

export function proposeFix(doc: Plan): Promise<FixProposal> {
  const key = JSON.stringify(doc);
  const known = cache.get(key);
  if (known) return known;
  const pending = api.improve(doc, true).then(toProposal);
  pending.catch(() => cache.delete(key));
  cache.set(key, pending);
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value;
    if (oldest === undefined) break;
    cache.delete(oldest);
  }
  return pending;
}

let queue: Promise<unknown> = Promise.resolve();
let newest = '';

/**
 * Тот же расчёт, но заранее и в фоне: не больше одного разом и только для последней правки.
 *
 * Расчёт занимает на сервере секунды и идёт по одному запросу за раз. Пока он шёл, редактор
 * успевал измениться ещё несколько раз, и каждая правка ставила в очередь новый расчёт —
 * очередь росла, а нужен из неё был только последний. Устаревшая заявка теперь отменяется:
 * ответ null означает «документ уже другой, считать нечего».
 */
export function precomputeFix(doc: Plan): Promise<FixProposal | null> {
  const key = JSON.stringify(doc);
  newest = key;
  const known = cache.get(key);
  if (known) return known;
  const pending = queue.then(() => (newest === key ? proposeFix(doc) : null));
  queue = pending.catch(() => undefined);
  return pending;
}
