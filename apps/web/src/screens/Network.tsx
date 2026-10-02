/**
 * «Сеть»: все сохранённые отделения в одной очереди на ремонт. Ремонт нужен тысячам отделений,
 * а за год обновляют сотни — экран показывает, с каких начать и почему, с баллом по понятным весам.
 */

import { ArrowLeft, ArrowUpRight, Database, FileSpreadsheet, LoaderCircle } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Account } from '../components/Account';
import { Mark } from '../components/Mark';
import { ApiError, api } from '../lib/api';
import { downloadCsv } from '../lib/download';
import type { NetworkOffice, NetworkReport } from '../lib/plan';
import { formatNumber, plural } from '../lib/planFacts';
import { hashOf } from '../lib/router';
import './network.css';

const TONE: Record<string, string> = {
  fire: 'signal',
  accessibility: 'signal',
  windows: 'notice',
  out_of_service: 'caution',
  overdue: 'caution',
  other: 'plain',
};

export function Network() {
  const [report, setReport] = useState<NetworkReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);

  const load = useCallback(async () => {
    try {
      setReport(await api.network());
      setError(null);
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const seed = async () => {
    setSeeding(true);
    try {
      await api.networkDemo();
      await load();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.');
    } finally {
      setSeeding(false);
    }
  };

  const top = report ? Math.max(1, ...report.offices.map((o) => o.score)) : 1;
  const hasDemo = report?.offices.some((o) => o.title.includes('(условное)') && o.title !== 'Отделение «Заречное» (условное)');

  const exportCsv = () => {
    if (!report) return;
    downloadCsv('Очередь на ремонт', [
      ['Место', 'Отделение', 'Балл', 'Нарушений норм', 'Посетителей в день', 'Ожидание в пик, мин', 'Что сложило балл'],
      ...report.offices.map((o) => [
        o.rank,
        o.title,
        o.score,
        o.errors,
        o.daily_visitors,
        o.queue_unbounded ? 'растёт без предела' : o.worst_wait_min,
        o.factors.map((f) => `${f.text} (+${formatNumber(f.points, 1)})`).join('; '),
      ]),
    ]);
  };

  return (
    <div className="network-screen">
      <header className="rail on-steel">
        <a className="rail__brand" href="#/" aria-label="План ОПС — к списку планов">
          <Mark />
          <span>План ОПС</span>
        </a>
        <span className="rail__end">
          <Account />
        </span>
      </header>

      <main className="network">
        <a className="network__back" href="#/">
          <ArrowLeft size={15} aria-hidden="true" /> К планам
        </a>
        <header className="network__head">
          <div>
            <h1>Очередь отделений на ремонт</h1>
            <p>
              Все сохранённые планы — в одном списке: сначала те, где под угрозой люди и где посетители дольше всего
              ждут. Балл складывается из нарушений норм, нехватки окон в пик, неисправностей по обращениям и просроченных
              осмотров.
            </p>
          </div>
          <div className="network__tools">
            {report && report.offices.length > 0 && (
              <button type="button" className="btn btn--steel" onClick={exportCsv}>
                <FileSpreadsheet size={16} aria-hidden="true" /> Таблица для Excel
              </button>
            )}
            {report && !hasDemo && (
              <button type="button" className="btn btn--label" onClick={() => void seed()} disabled={seeding}>
                {seeding ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Database size={16} aria-hidden="true" />}
                Добавить условную сеть
              </button>
            )}
          </div>
        </header>

        {error && <p className="network__error">{error}</p>}
        {!report && !error && (
          <p className="network__note">
            <LoaderCircle size={15} className="spin" aria-hidden="true" /> Проверяем все отделения…
          </p>
        )}

        {report && report.offices.length === 0 && (
          <p className="network__note">
            Сохранённых планов пока нет. Добавьте условную сеть из восьми отделений или загрузите свои планы на главной.
          </p>
        )}

        {report && report.offices.length > 0 && (
          <>
            <dl className="network__totals">
              <div>
                <dt>Отделений</dt>
                <dd>{report.offices.length}</dd>
              </div>
              <div>
                <dt>С нарушением пожарной безопасности</dt>
                <dd className={report.offices_with_fire ? 'is-bad' : undefined}>{report.offices_with_fire}</dd>
              </div>
              <div>
                <dt>С нарушением доступности</dt>
                <dd className={report.offices_with_accessibility ? 'is-bad' : undefined}>{report.offices_with_accessibility}</dd>
              </div>
              <div>
                <dt>Окон не хватает в пик</dt>
                <dd>{report.windows_short}</dd>
              </div>
            </dl>

            <ol className="network__list">
              {report.offices.map((office) => (
                <OfficeRow key={office.plan_id} office={office} top={top} />
              ))}
            </ol>

            <details className="network__weights">
              <summary>Как считается балл</summary>
              <ul>
                {report.weights.map((w) => (
                  <li key={w.key}>
                    <b>+{formatNumber(w.weight, 1)}</b> за {w.label}
                  </li>
                ))}
              </ul>
              <p>
                Веса — допущение сервиса: угроза людям весит больше, чем неудобство, а нехватка окон — столько же, сколько
                доступность, потому что её каждый день видят посетители. При равном балле выше отделение с большим потоком.
              </p>
            </details>
          </>
        )}
      </main>
    </div>
  );
}

function OfficeRow({ office, top }: { office: NetworkOffice; top: number }) {
  return (
    <li className="network__office">
      <span className="network__rank">{office.rank}</span>
      <div className="network__body">
        <div className="network__title">
          <a href={hashOf({ name: 'plan', id: office.plan_id })}>
            {office.title}
            <ArrowUpRight size={14} aria-hidden="true" />
          </a>
          <span className="network__meta">
            {office.daily_visitors ? `${office.daily_visitors} ${plural(office.daily_visitors, 'посетитель', 'посетителя', 'посетителей')} в день` : 'поток не задан'}
            {office.area_sqm ? ` · ${formatNumber(office.area_sqm, 0)} м²` : ''}
            {office.queue_unbounded
              ? ' · очередь в пик растёт без предела'
              : office.worst_wait_min != null
                ? ` · ожидание в пик до ${formatNumber(office.worst_wait_min, 1)} мин`
                : ''}
          </span>
        </div>
        {office.failed ? (
          <p className="network__error">{office.failed}</p>
        ) : office.factors.length === 0 ? (
          <p className="network__clean">Нарушений и неисправностей нет</p>
        ) : (
          <ul className="network__factors">
            {office.factors.map((factor) => (
              <li key={factor.key} className={`network__factor network__factor--${TONE[factor.key] ?? 'plain'}`}>
                {factor.text}
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="network__score" aria-label={`Балл ${formatNumber(office.score, 1)}`}>
        <b>{formatNumber(office.score, 1)}</b>
        <span className="network__bar" aria-hidden="true">
          <span style={{ width: `${Math.max(2, (office.score / top) * 100)}%` }} />
        </span>
      </div>
    </li>
  );
}
