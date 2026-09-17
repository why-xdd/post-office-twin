/**
 * Стеллаж планов — первый экран.
 *
 * Каждый план — дверца ячейки с адресом и этикеткой: название, площадь, объекты, версии и
 * итог проверки по нормам. Первая ячейка — демо-план в формате заказчика.
 */

import { CircleCheck, FileUp, KeyRound, LoaderCircle, Plus, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Mark } from '../components/Mark';
import { Plate } from '../components/Plate';
import { ServerBadge } from '../components/ServerBadge';
import { ApiError, api, type PlanSummary } from '../lib/api';
import type { Plan } from '../lib/plan';
import { blankPlan, formatNumber, planArea, plural } from '../lib/planFacts';
import { navigate } from '../lib/router';
import { useServer } from '../lib/useServer';
import './rack.css';

interface Facts {
  area: number | null;
  objects: number;
  errors: number | null;
  warnings: number | null;
}

type Busy = null | 'demo' | 'blank' | 'upload';

interface Problem {
  title: string;
  details: string[];
}

const DEMO_TITLE = 'ОПС-1 — демо-план заказчика';

function describeError(error: unknown, fallback: string): Problem {
  if (error instanceof ApiError) return { title: error.message, details: error.details };
  if (error instanceof SyntaxError) {
    return { title: 'Файл не является корректным JSON.', details: [error.message] };
  }
  return { title: fallback, details: [] };
}

export function Rack() {
  const server = useServer();
  const [plans, setPlans] = useState<PlanSummary[] | null>(null);
  const [facts, setFacts] = useState<Record<string, Facts>>({});
  const [sample, setSample] = useState<Plan | null>(null);
  const [sampleFacts, setSampleFacts] = useState<Facts | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [list, demo] = await Promise.all([api.listPlans(), api.samplePlan().catch(() => null)]);
      setPlans(list);
      setSample(demo);
      if (demo) {
        const report = await api.validate(demo).catch(() => null);
        setSampleFacts({
          area: planArea(demo),
          objects: demo.equipment?.length ?? 0,
          errors: report?.errors ?? null,
          warnings: report?.warnings ?? null,
        });
      }
      for (const summary of list) {
        const document = await api.getPlan(summary.id).catch(() => null);
        if (!document) continue;
        const report = await api.validate(document).catch(() => null);
        setFacts((prev) => ({
          ...prev,
          [summary.id]: {
            area: planArea(document),
            objects: document.equipment?.length ?? 0,
            errors: report?.errors ?? null,
            warnings: report?.warnings ?? null,
          },
        }));
      }
    } catch (error) {
      setProblem(describeError(error, 'Не удалось получить список планов.'));
    }
  }, []);

  useEffect(() => {
    if (server.state === 'ready') void load();
  }, [server.state, load]);

  const openDemo = async () => {
    if (!sample) return;
    setBusy('demo');
    setProblem(null);
    try {
      const ref = await api.createPlan(DEMO_TITLE, sample, 'Исходный файл заказчика');
      navigate({ name: 'plan', id: ref.id });
    } catch (error) {
      setProblem(describeError(error, 'Не удалось открыть демо-план.'));
      setBusy(null);
    }
  };

  const createBlank = async () => {
    setBusy('blank');
    setProblem(null);
    try {
      const ref = await api.createPlan('Новое отделение', blankPlan(), 'Пустой план');
      navigate({ name: 'plan', id: ref.id });
    } catch (error) {
      setProblem(describeError(error, 'Не удалось создать план.'));
      setBusy(null);
    }
  };

  const upload = async (file: File) => {
    setBusy('upload');
    setProblem(null);
    try {
      const text = await file.text();
      const document = JSON.parse(text) as Plan;
      const title = document?.ops?.name || file.name.replace(/\.json$/i, '');
      const ref = await api.createPlan(title, document, `Загружен из файла ${file.name}`);
      navigate({ name: 'plan', id: ref.id });
    } catch (error) {
      setProblem(describeError(error, 'Не удалось загрузить файл плана.'));
      setBusy(null);
    }
  };

  const ready = server.state === 'ready';

  return (
    <div className="rack-screen">
      <header className="rail on-steel">
        <a className="rail__brand" href="#/" aria-label="План ОПС — к списку планов">
          <Mark />
          <span>План ОПС</span>
        </a>
        <ServerBadge state={server.state} onRetry={server.retry} />
      </header>

      <main className="cabinet on-steel">
        <div className="cabinet__head">
          <div>
            <h1>Планы отделений</h1>
            <p className="cabinet__lead">
              Откройте план, чтобы проверить планировку по отраслевым нормам и изменить её.
            </p>
          </div>
          <div className="cabinet__actions">
            <button
              type="button"
              className="btn btn--label"
              onClick={openDemo}
              disabled={!ready || !sample || busy !== null}
            >
              {busy === 'demo' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <KeyRound size={16} aria-hidden="true" />}
              Открыть демо-план
            </button>
            <button type="button" className="btn btn--steel" onClick={createBlank} disabled={!ready || busy !== null}>
              {busy === 'blank' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Plus size={16} aria-hidden="true" />}
              Новый план
            </button>
            <button
              type="button"
              className="btn btn--steel"
              onClick={() => fileInput.current?.click()}
              disabled={!ready || busy !== null}
            >
              {busy === 'upload' ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <FileUp size={16} aria-hidden="true" />}
              Загрузить файл плана
            </button>
            <input
              ref={fileInput}
              type="file"
              accept="application/json,.json"
              className="visually-hidden"
              tabIndex={-1}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void upload(file);
              }}
            />
          </div>
        </div>

        {problem && (
          <div className="cabinet__problem" role="alert">
            <TriangleAlert size={18} aria-hidden="true" />
            <div>
              <p className="cabinet__problem-title">{problem.title}</p>
              {problem.details.length > 0 && (
                <ul>
                  {problem.details.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        )}

        {server.state === 'waking' && (
          <p className="cabinet__note">
            Сервер работает на бесплатном хостинге и засыпает, когда им не пользуются. Первый запрос будит его —
            это занимает до минуты, дальше всё работает без задержек.
          </p>
        )}

        <ul className="cells" aria-label="Ячейки планов">
          {sample && (
            <li>
              <button type="button" className="door door--demo" onClick={openDemo} disabled={!ready || busy !== null}>
                <span className="door__top">
                  <Plate size="md">Я-00</Plate>
                  <span className="door__kind">
                    <KeyRound size={14} aria-hidden="true" />
                    Демо
                  </span>
                </span>
                <span className="door__label">
                  <span className="door__title">{sample.ops.name ?? 'Демо-план'}</span>
                  <span className="door__sub">Пример в формате заказчика. Откроется копия — исходник не меняется.</span>
                  <DoorFacts facts={sampleFacts} />
                </span>
              </button>
            </li>
          )}

          {plans?.map((plan, index) => (
            <li key={plan.id}>
              <button
                type="button"
                className="door"
                onClick={() => navigate({ name: 'plan', id: plan.id })}
                disabled={!ready}
              >
                <span className="door__top">
                  <Plate size="md">{`Я-${String(index + 1).padStart(2, '0')}`}</Plate>
                  <span className="door__kind">
                    {plan.versions} {plural(plan.versions, 'версия', 'версии', 'версий')}
                  </span>
                </span>
                <span className="door__label">
                  <span className="door__title">{plan.title}</span>
                  <span className="door__sub">
                    Создан {new Date(plan.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}
                  </span>
                  <DoorFacts facts={facts[plan.id] ?? null} />
                </span>
              </button>
            </li>
          ))}

          {Array.from({ length: Math.max(0, 7 - (plans?.length ?? 0) - (sample ? 1 : 0)) }).map((_, i) => (
            <li key={`empty-${i}`} aria-hidden="true">
              <span className="door door--empty">
                <span className="door__top">
                  <Plate size="md">{`Я-${String((plans?.length ?? 0) + i + 1).padStart(2, '0')}`}</Plate>
                </span>
                <span className="door__slot" />
                <span className="door__lock" />
              </span>
            </li>
          ))}
        </ul>

        {plans && plans.length === 0 && (
          <p className="cabinet__note">
            Своих планов пока нет. Откройте демо-план заказчика или создайте план с нуля — он появится в следующей
            ячейке.
          </p>
        )}
      </main>
    </div>
  );
}

function DoorFacts({ facts }: { facts: Facts | null }) {
  if (!facts) {
    return (
      <span className="door__facts door__facts--pending">
        <LoaderCircle size={14} className="spin" aria-hidden="true" />
        Проверяется
      </span>
    );
  }
  return (
    <span className="door__facts">
      <span>
        <b>{formatNumber(facts.area, 1)}</b> м²
      </span>
      <span>
        <b>{facts.objects}</b> {plural(facts.objects, 'объект', 'объекта', 'объектов')}
      </span>
      {facts.errors === null ? (
        <span className="door__status">нет проверки</span>
      ) : facts.errors > 0 ? (
        <span className="door__status door__status--error">
          <TriangleAlert size={14} aria-hidden="true" />
          {facts.errors} {plural(facts.errors, 'нарушение', 'нарушения', 'нарушений')}
        </span>
      ) : (
        <span className="door__status door__status--ok">
          <CircleCheck size={14} aria-hidden="true" />
          Нормы соблюдены
        </span>
      )}
    </span>
  );
}
