/**
 * Первый экран: что делает сервис, одна главная кнопка — открыть пример, и сохранённые планы.
 *
 * Каждый план — дверца с этикеткой: название, площадь, объекты, версии и итог проверки.
 */

import { CircleCheck, Download, FileUp, FolderOpen, LoaderCircle, Trash2, TriangleAlert } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Mark } from '../components/Mark';
import { PlanThumb } from '../components/PlanThumb';
import { ServerBadge } from '../components/ServerBadge';
import { Account } from '../components/Account';
import { uniqueTitle } from '../lib/titles';
import { ApiError, api, type PlanSummary } from '../lib/api';
import type { Plan, ValidationReport } from '../lib/plan';
import { formatNumber, planArea, plural } from '../lib/planFacts';
import { sortFindings } from '../lib/plainFindings';
import { downloadJson } from '../lib/download';
import { navigate } from '../lib/router';
import { useServer } from '../lib/useServer';
import './rack.css';

interface Facts {
  area: number | null;
  objects: number;
  errors: number | null;
  warnings: number | null;
}

type Busy = null | 'demo' | 'upload';

interface Problem {
  title: string;
  details: string[];
}

const DEMO_TITLE = 'Демо: отделение «Заречное»';

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
  const [sampleReport, setSampleReport] = useState<ValidationReport | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [list, demo] = await Promise.all([api.listPlans(), api.samplePlan().catch(() => null)]);
      setPlans(list);
      setSample(demo);
      if (demo) {
        const report = await api.validate(demo).catch(() => null);
        setSampleReport(report ? { ...report, findings: sortFindings(report.findings) } : null);
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
    // Нетронутая копия примера уже есть — открываем её, а не плодим одинаковые планы.
    const untouched = plans?.find((plan) => plan.title === DEMO_TITLE && plan.versions === 1);
    if (untouched) {
      navigate({ name: 'plan', id: untouched.id });
      return;
    }
    setBusy('demo');
    setProblem(null);
    try {
      const ref = await api.createPlan(DEMO_TITLE, sample, 'Демонстрационный план');
      navigate({ name: 'plan', id: ref.id });
    } catch (error) {
      setProblem(describeError(error, 'Не удалось открыть демо-план.'));
      setBusy(null);
    }
  };

  const download = async (plan: PlanSummary) => {
    try {
      downloadJson(plan.title, await api.getPlan(plan.id));
    } catch (error) {
      setProblem(describeError(error, 'Не удалось скачать план.'));
    }
  };

  const remove = async (id: string) => {
    try {
      await api.deletePlan(id);
      setRemoving(null);
      setPlans((current) => (current ?? []).filter((p) => p.id !== id));
    } catch (error) {
      setProblem(describeError(error, 'Не удалось удалить план.'));
      setRemoving(null);
    }
  };

  const upload = async (file: File) => {
    setBusy('upload');
    setProblem(null);
    try {
      const text = await file.text();
      const document = JSON.parse(text) as Plan;
      const base = document?.ops?.name || file.name.replace(/\.json$/i, '');
      // Тот же файл, загруженный ещё раз, получает номер копии: иначе карточки на стеллаже не различить.
      const title = uniqueTitle(base, (plans ?? []).map((plan) => plan.title));
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
        <span className="rail__end">
          <a className="rail__link" href="#/network">
            Сеть отделений
          </a>
          <ServerBadge state={server.state} onRetry={server.retry} />
          <Account />
        </span>
      </header>

      <main className="cabinet on-steel">
        <section className="cabinet__hero">
          <div className="cabinet__intro">
            <h1>Проверка планировки отделения по нормам</h1>
            <p className="cabinet__lead">
              Откройте план — нарушения отметятся прямо на чертеже, а расстановку, при которой их нет, можно получить одной
              кнопкой.
            </p>
            <div className="cabinet__actions">
              <button
                type="button"
                className="btn btn--label cabinet__primary"
                onClick={openDemo}
                disabled={!ready || !sample || busy !== null}
              >
                {busy === 'demo' ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <FolderOpen size={18} aria-hidden="true" />}
                Открыть пример
              </button>
              <button
                type="button"
                className="btn btn--steel cabinet__secondary"
                onClick={() => fileInput.current?.click()}
                disabled={!ready || busy !== null}
              >
                {busy === 'upload' ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <FileUp size={18} aria-hidden="true" />}
                Загрузить свой план
              </button>
              <input
                ref={fileInput}
                type="file"
                accept="application/json,.json"
                className="visually-hidden"
                tabIndex={-1}
                aria-hidden="true"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = '';
                  if (file) void upload(file);
                }}
              />
            </div>
            <p className="cabinet__aside">
              {sampleFacts && sampleFacts.errors !== null && sampleFacts.errors > 0 ? (
                <>
                  В примере — отделение на {formatNumber(sampleFacts.area, 0)} м² с {sampleFacts.errors}{' '}
                  {plural(sampleFacts.errors, 'проблемой', 'проблемами', 'проблемами')} по нормам и данным плана.{' '}
                </>
              ) : null}
              <a className="cabinet__link" href="#/new">
                Создать своё помещение
              </a>
              {' · '}
              <a className="cabinet__link" href="#/import">
                Загрузить чертёж картинкой
              </a>
            </p>
          </div>
          {sample && (
            <button
              type="button"
              className="cabinet__sheet"
              onClick={openDemo}
              disabled={!ready || busy !== null}
              aria-label="Открыть пример плана"
            >
              <PlanThumb plan={sample} report={sampleReport} />
            </button>
          )}
        </section>

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

        {plans && plans.length > 0 && (
          <section className="cabinet__plans" aria-labelledby="plans-title">
            <h2 id="plans-title">Сохранённые планы</h2>
            <ul className="cells">
              {plans.map((plan) => (
                <li key={plan.id}>
                  <article className="door">
                    <a className="door__label" href={`#/plan/${plan.id}`}>
                      <span className="door__title">{plan.title}</span>
                      <span className="door__sub">
                        {new Date(plan.created_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' })}
                        {' · '}
                        {plan.versions} {plural(plan.versions, 'версия', 'версии', 'версий')}
                      </span>
                      <DoorFacts facts={facts[plan.id] ?? null} />
                    </a>
                    {removing === plan.id ? (
                      <div className="door__confirm" role="alertdialog" aria-label={`Удалить план «${plan.title}»`}>
                        <p>Удалить план со всеми версиями?</p>
                        <div>
                          <button type="button" className="btn btn--label" onClick={() => void remove(plan.id)}>
                            Удалить
                          </button>
                          <button type="button" className="btn btn--steel" onClick={() => setRemoving(null)}>
                            Отмена
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="door__tools">
                        <button type="button" onClick={() => void download(plan)} title="Скачать план файлом JSON">
                          <Download size={14} aria-hidden="true" />
                          <span className="visually-hidden">Скачать {plan.title}</span>
                        </button>
                        <button type="button" onClick={() => setRemoving(plan.id)} title="Удалить план">
                          <Trash2 size={14} aria-hidden="true" />
                          <span className="visually-hidden">Удалить {plan.title}</span>
                        </button>
                      </div>
                    )}
                  </article>
                </li>
              ))}
            </ul>
          </section>
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
          {facts.errors} {plural(facts.errors, 'проблема', 'проблемы', 'проблем')}
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
