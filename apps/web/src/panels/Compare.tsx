/**
 * Сравнение вариантов: сохранённая версия против того, что сейчас на плане. Что изменилось, как
 * сдвинулись нормы, проходы и очередь, и смета перехода по ценам, которые вводит человек.
 */

import { ArrowRight, FileSpreadsheet, LoaderCircle, Printer, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { ApiError, api } from '../lib/api';
import { downloadCsv } from '../lib/download';
import type { Comparison, Plan, Prices } from '../lib/plan';
import { formatNumber } from '../lib/planFacts';
import './compare.css';

const PRICES_KEY = 'plan-ops:prices';
const RECALC_DELAY_MS = 400;

function readPrices(): Prices {
  try {
    const raw = window.localStorage.getItem(PRICES_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Prices;
      return { ...parsed, equipment: parsed.equipment ?? {} };
    }
  } catch {
    // Хранилище браузера недоступно — цены просто не запомнятся.
  }
  return { equipment: {} };
}

function writePrices(prices: Prices) {
  try {
    window.localStorage.setItem(PRICES_KEY, JSON.stringify(prices));
  } catch {
    // См. readPrices.
  }
}

const rub = (value: number) => `${formatNumber(Math.round(value), 0)} ₽`;

interface CompareProps {
  before: Plan;
  after: Plan;
  beforeLabel: string;
  onClose: () => void;
}

export function Compare({ before, after, beforeLabel, onClose }: CompareProps) {
  const [prices, setPrices] = useState<Prices>(readPrices);
  const [result, setResult] = useState<Comparison | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const dialog = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    dialog.current?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    writePrices(prices);
    const controller = new AbortController();
    setBusy(true);
    const timer = window.setTimeout(() => {
      api
        .compare(before, after, prices, controller.signal)
        .then((value) => {
          setResult(value);
          setError(null);
        })
        .catch((reason) => {
          if (reason instanceof DOMException && reason.name === 'AbortError') return;
          setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.');
        })
        .finally(() => setBusy(false));
    }, RECALC_DELAY_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [before, after, prices]);

  const priceOf = (key: string): number | null | undefined =>
    key.startsWith('equipment:') ? prices.equipment[key.slice(10)] : (prices[key as keyof Omit<Prices, 'equipment'>] as number | null | undefined);

  const setPrice = (key: string, value: number | null) =>
    setPrices((current) => {
      if (key.startsWith('equipment:')) {
        const equipment = { ...current.equipment };
        if (value == null) delete equipment[key.slice(10)];
        else equipment[key.slice(10)] = value;
        return { ...current, equipment };
      }
      return { ...current, [key]: value };
    });

  const exportCsv = () => {
    if (!result) return;
    downloadCsv('Смета перехода', [
      ['Работа', 'Количество', 'Ед.', 'Цена, ₽', 'Сумма, ₽'],
      ...result.lines.map((line) => [line.text, line.quantity, line.unit, line.price, line.total]),
      ['Итого', null, null, null, result.total],
      ...(result.complete ? [] : [['Без цены: ' + result.missing.join(', ')]]),
    ]);
  };

  const changes = result ? result.added.length + result.removed.length + result.moved.length : 0;

  return (
    <div className="compare-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div className="compare" role="dialog" aria-modal="true" aria-labelledby="compare-title" ref={dialog} tabIndex={-1}>
        <header className="compare__head">
          <div>
            <h2 id="compare-title">Сравнение вариантов</h2>
            <p>
              {beforeLabel} <ArrowRight size={13} aria-hidden="true" /> вариант на плане сейчас
            </p>
          </div>
          <div className="compare__tools">
            <button type="button" className="icon-btn" onClick={() => window.print()} title="Распечатать" disabled={!result}>
              <Printer size={17} aria-hidden="true" />
              <span className="visually-hidden">Распечатать</span>
            </button>
            <button type="button" className="icon-btn" onClick={exportCsv} title="Смета для Excel" disabled={!result}>
              <FileSpreadsheet size={17} aria-hidden="true" />
              <span className="visually-hidden">Смета для Excel</span>
            </button>
            <button type="button" className="icon-btn" onClick={onClose} title="Закрыть — Esc">
              <X size={18} aria-hidden="true" />
              <span className="visually-hidden">Закрыть</span>
            </button>
          </div>
        </header>

        {error && <p className="compare__error">{error}</p>}
        {!result && !error && (
          <p className="compare__note">
            <LoaderCircle size={14} className="spin" aria-hidden="true" /> Сравниваем…
          </p>
        )}

        {result && (
          <div className={`compare__body${busy ? ' compare__body--busy' : ''}`}>
            <section aria-labelledby="compare-metrics">
              <h3 id="compare-metrics">Что даёт вариант</h3>
              <table className="compare__table">
                <thead>
                  <tr>
                    <th scope="col">Показатель</th>
                    <th scope="col">Было</th>
                    <th scope="col">Стало</th>
                  </tr>
                </thead>
                <tbody>
                  {result.metrics.map((metric) => (
                    <tr key={metric.key} className={metric.better === true ? 'is-better' : metric.better === false ? 'is-worse' : undefined}>
                      <th scope="row">{metric.label}</th>
                      <td>{show(metric.before, metric.unit)}</td>
                      <td>{show(metric.after, metric.unit)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            <section aria-labelledby="compare-changes">
              <h3 id="compare-changes">Что меняется</h3>
              {changes === 0 && result.walls_changed_m === 0 ? (
                <p className="compare__note">Варианты совпадают: ни один объект не поставлен, не убран и не переставлен.</p>
              ) : (
                <ul className="compare__changes">
                  {result.moved.length > 0 && <li><b>Переставить:</b> {result.moved.map((i) => i.label).join(', ')}</li>}
                  {result.added.length > 0 && <li><b>Поставить:</b> {result.added.map((i) => i.label).join(', ')}</li>}
                  {result.removed.length > 0 && <li><b>Убрать:</b> {result.removed.map((i) => i.label).join(', ')}</li>}
                  {result.walls_changed_m > 0 && (
                    <li>
                      <b>Перегородки:</b> {formatNumber(result.walls_changed_m, 1)} м новых или перенесённых
                    </li>
                  )}
                </ul>
              )}
            </section>

            {result.lines.length > 0 && (
              <section aria-labelledby="compare-estimate">
                <h3 id="compare-estimate">Смета перехода</h3>
                <p className="compare__note">
                  Цены вводите вы — по расценкам своего региона и подрядчика. Сервис их не подставляет; строка без цены
                  остаётся в смете с количеством. Цены запоминаются в этом браузере.
                </p>
                <table className="compare__table compare__estimate">
                  <thead>
                    <tr>
                      <th scope="col">Работа</th>
                      <th scope="col">Кол-во</th>
                      <th scope="col">Цена за ед., ₽</th>
                      <th scope="col">Сумма</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.lines.map((line) => (
                      <tr key={line.key}>
                        <th scope="row">{line.text}</th>
                        <td>
                          {formatNumber(line.quantity, line.unit === 'м' ? 1 : 0)} {line.unit}
                        </td>
                        <td>
                          <PriceInput label={`Цена: ${line.text}`} value={priceOf(line.key)} onChange={(value) => setPrice(line.key, value)} />
                        </td>
                        <td>{line.total == null ? <span className="compare__missing">нет цены</span> : rub(line.total)}</td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr>
                      <th scope="row" colSpan={3}>
                        {result.complete ? 'Итого' : 'Итого по строкам с ценой'}
                      </th>
                      <td>{rub(result.total)}</td>
                    </tr>
                  </tfoot>
                </table>
                {!result.complete && (
                  <p className="compare__note">Смета неполная: нет цены для {result.missing.length} {result.missing.length === 1 ? 'строки' : 'строк'}.</p>
                )}
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function show(value: number | null, unit: string): string {
  if (value == null) return '—';
  return `${formatNumber(value, Number.isInteger(value) ? 0 : 2)}${unit ? ` ${unit}` : ''}`;
}

function PriceInput({ label, value, onChange }: { label: string; value: number | null | undefined; onChange: (value: number | null) => void }) {
  const [text, setText] = useState(value == null ? '' : String(value));
  useEffect(() => setText(value == null ? '' : String(value)), [value]);
  return (
    <input
      className="compare__price"
      inputMode="decimal"
      aria-label={label}
      placeholder="—"
      value={text}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => {
        const cleaned = text.replace(/\s/g, '').replace(',', '.');
        if (cleaned === '') return onChange(null);
        const number = Number(cleaned);
        if (Number.isFinite(number) && number >= 0) onChange(number);
        else setText(value == null ? '' : String(value));
      }}
      onKeyDown={(event) => {
        if (event.key === 'Enter') (event.target as HTMLInputElement).blur();
      }}
    />
  );
}
