/**
 * Лист QR-бирок для печати: по бирке на каждый объект отделения. Бирку клеят на оборудование;
 * сотрудник сканирует её телефоном и сообщает о неисправности.
 */

import { ArrowLeft, LoaderCircle, Printer } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { buildAddressBook, key } from '../lib/addresses';
import { ApiError, api } from '../lib/api';
import type { EquipmentTypeInfo, Plan } from '../lib/plan';
import { hashOf, tagUrl } from '../lib/router';
import { Qr } from '../components/Qr';
import './tags.css';

export function Tags({ planId }: { planId: string }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [types, setTypes] = useState<Map<string, EquipmentTypeInfo>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const [only, setOnly] = useState<string>('all');

  useEffect(() => {
    let alive = true;
    api
      .getPlan(planId)
      .then(async (document) => {
        if (!alive) return;
        setPlan(document);
        const dictionaries = await api.dictionaries(document).catch(() => null);
        if (alive && dictionaries) setTypes(new Map(dictionaries.equipment_types.map((t) => [t.code, t])));
      })
      .catch((reason) => {
        if (alive) setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил.');
      });
    return () => {
      alive = false;
    };
  }, [planId]);

  const book = useMemo(() => buildAddressBook(plan), [plan]);
  const label = (type: string) => types.get(type)?.label ?? type;
  const groups = [...new Set((plan?.equipment ?? []).map((e) => e.type))];
  const items = (plan?.equipment ?? []).filter((e) => only === 'all' || e.type === only);

  return (
    <main className="tags">
      <header className="tags__bar">
        <a href={hashOf({ name: 'plan', id: planId })} className="tags__back">
          <ArrowLeft size={16} aria-hidden="true" /> К плану
        </a>
        <div className="tags__title">
          <h1>QR-бирки</h1>
          <p>{plan?.ops.name ?? ''}</p>
        </div>
        <label className="tags__filter">
          <span>Объекты</span>
          <select value={only} onChange={(event) => setOnly(event.target.value)}>
            <option value="all">Все ({plan?.equipment?.length ?? 0})</option>
            {groups.map((type) => (
              <option key={type} value={type}>
                {label(type)}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn--label" onClick={() => window.print()} disabled={!plan}>
          <Printer size={16} aria-hidden="true" /> Печать
        </button>
      </header>
      <p className="tags__hint">
        Бирка ведёт на страницу объекта: сотрудник сканирует её камерой телефона и сообщает о неисправности — без входа и
        без установки приложений. Обращение сразу видят начальник отделения и инженер.
      </p>
      {error && <p className="tags__error">{error}</p>}
      {!plan && !error && (
        <p className="tags__hint">
          <LoaderCircle size={14} className="spin" aria-hidden="true" /> Загружаем план…
        </p>
      )}
      <ul className="tags__grid">
        {items.map((item) => {
          const address = book.byId.get(key('equipment', item.id)) ?? '';
          return (
            <li key={item.id} className="tags__card">
              <Qr text={tagUrl(planId, item.id)} size={104} label={`QR-код объекта ${address}`} />
              <div>
                <p className="tags__address">{address}</p>
                <p className="tags__name">{item.label || label(item.type)}</p>
                {item.inventory?.inventory_number && <p className="tags__inv">Инв. № {item.inventory.inventory_number}</p>}
                <p className="tags__call">Сломалось? Наведите камеру</p>
              </div>
            </li>
          );
        })}
      </ul>
    </main>
  );
}
