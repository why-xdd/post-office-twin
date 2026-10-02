/**
 * Отчёт по отделению для печати: чертёж, нарушения с пунктами норм, пропускная способность в пик,
 * хранение посылок, осмотры и неисправности. Один лист на совещание или в УФПС.
 */

import { ArrowLeft, LoaderCircle, Printer } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { ApiError, api } from '../lib/api';
import { formatDate } from '../lib/inspections';
import type { Conclusion, Plan, StorageReport, TodayReport, ValidationReport } from '../lib/plan';
import { formatNumber, planArea } from '../lib/planFacts';
import { plainFinding, sortFindings } from '../lib/plainFindings';
import { planToSvg } from '../lib/planSvg';
import { hashOf } from '../lib/router';
import './report.css';

interface Data {
  plan: Plan;
  check: ValidationReport;
  conclusion: Conclusion | null;
  today: TodayReport | null;
  storage: StorageReport | null;
}

export function Report({ planId }: { planId: string }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const plan = await api.getPlan(planId);
        const [check, conclusion, today, storage] = await Promise.all([
          api.validate(plan),
          api.conclusion(plan).catch(() => null),
          api.today(planId).catch(() => null),
          api.storage(plan, 1).catch(() => null),
        ]);
        if (alive) setData({ plan, check, conclusion, today, storage });
      } catch (reason) {
        if (alive) setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.');
      }
    })();
    return () => {
      alive = false;
    };
  }, [planId]);

  const drawing = useMemo(() => (data ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(planToSvg(data.plan))}` : null), [data]);

  return (
    <main className="report">
      <header className="report__bar">
        <a href={hashOf({ name: 'plan', id: planId })} className="report__back">
          <ArrowLeft size={16} aria-hidden="true" /> К плану
        </a>
        <button type="button" className="btn btn--label" onClick={() => window.print()} disabled={!data}>
          <Printer size={16} aria-hidden="true" /> Печать
        </button>
      </header>

      {error && <p className="report__error">{error}</p>}
      {!data && !error && (
        <p className="report__note">
          <LoaderCircle size={15} className="spin" aria-hidden="true" /> Собираем отчёт…
        </p>
      )}

      {data && (
        <article className="report__sheet">
          <header className="report__title">
            <p>Отчёт о планировке отделения · {formatDate(new Date().toISOString().slice(0, 10))}</p>
            <h1>{data.plan.ops.name ?? data.plan.ops.id}</h1>
            <dl>
              <div>
                <dt>Площадь</dt>
                <dd>{formatNumber(planArea(data.plan) ?? data.plan.ops.total_area_sqm ?? null, 1)} м²</dd>
              </div>
              <div>
                <dt>Объектов</dt>
                <dd>{data.plan.equipment?.length ?? 0}</dd>
              </div>
              <div>
                <dt>Посетителей в день</dt>
                <dd>{data.plan.flow_hints?.avg_daily_visitors ?? '—'}</dd>
              </div>
              <div>
                <dt>Нарушений норм</dt>
                <dd className={data.check.errors ? 'is-bad' : 'is-ok'}>{data.check.errors}</dd>
              </div>
            </dl>
          </header>

          {drawing && <img className="report__drawing" src={drawing} alt="Чертёж отделения" />}

          {data.conclusion && (
            <section>
              <h2>Заключение</h2>
              <p className="report__text">{data.conclusion.text}</p>
            </section>
          )}

          <section>
            <h2>Проверка норм</h2>
            {data.check.findings.length === 0 ? (
              <p>Нарушений и замечаний нет.</p>
            ) : (
              <table className="report__table">
                <thead>
                  <tr>
                    <th scope="col">Что</th>
                    <th scope="col">Замер</th>
                    <th scope="col">Основание</th>
                  </tr>
                </thead>
                <tbody>
                  {sortFindings(data.check.findings).map((finding, index) => {
                    const plain = plainFinding(finding, data.plan);
                    return (
                      <tr key={`${finding.rule_id}-${index}`} className={finding.severity === 'error' ? 'is-error' : undefined}>
                        <td>
                          <b>{plain.title}</b>
                          {plain.culprits && <small>{plain.culprits}</small>}
                        </td>
                        <td>{plain.measure ?? '—'}</td>
                        <td>
                          {finding.source}, {finding.clause}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>

          {data.today && data.today.windows.length > 0 && (
            <section>
              <h2>Окна в час пик</h2>
              <table className="report__table">
                <thead>
                  <tr>
                    <th scope="col">Группа</th>
                    <th scope="col">Работает</th>
                    <th scope="col">Нужно</th>
                    <th scope="col">Ожидание</th>
                  </tr>
                </thead>
                <tbody>
                  {data.today.windows.map((group) => (
                    <tr key={group.name} className={group.working < group.needed ? 'is-error' : undefined}>
                      <td>{group.name}</td>
                      <td>
                        {group.working} из {group.total}
                      </td>
                      <td>{group.needed}</td>
                      <td>{group.mean_wait_min == null ? 'растёт без предела' : `${formatNumber(group.mean_wait_min, 1)} мин`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.today.target_wait_min != null && <p className="report__small">Цель — ожидание не дольше {formatNumber(data.today.target_wait_min)} мин.</p>}
            </section>
          )}

          {data.storage?.available && data.storage.required != null && (
            <section>
              <h2>Хранение посылок</h2>
              <p>
                {data.storage.advice[0]} Основание — {data.storage.source}.
              </p>
            </section>
          )}

          {data.today && (data.today.incidents.length > 0 || data.today.inspections.length > 0) && (
            <section>
              <h2>Эксплуатация</h2>
              {data.today.incidents.length > 0 && (
                <>
                  <h3>Открытые обращения</h3>
                  <ul>
                    {data.today.incidents.map((incident) => (
                      <li key={incident.id}>
                        {incident.object_label} — {incident.text} ({incident.status_label.toLowerCase()}
                        {incident.out_of_service ? ', не работает' : ''})
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {data.today.inspections.length > 0 && (
                <>
                  <h3>Осмотры</h3>
                  <ul>
                    {data.today.inspections.map((due) => (
                      <li key={due.object_id}>
                        {due.label} — {formatDate(due.due)}
                        {due.days < 0 ? `, просрочен на ${-due.days} дн.` : ''}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          )}

          <footer className="report__foot">
            Нормы: ВНТП 311-98, СП 59.13330.2020, СП 1.13130.2020, ПП РФ № 1479, Приказ Минцифры № 538. Расчёт
            очереди — модель M/M/c. Сформировано сервисом «План ОПС».
          </footer>
        </article>
      )}
    </main>
  );
}
