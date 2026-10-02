/**
 * Страница QR-бирки: сотрудник наводит телефон на бирку объекта и сразу видит, что это за
 * объект, кто за него отвечает, когда осмотр, — и может сообщить о неисправности без входа.
 */

import { ArrowLeft, CalendarClock, IdCard, LoaderCircle, MapPin } from 'lucide-react';
import { useEffect, useState } from 'react';
import { glyphFor } from '../canvas/glyphs';
import { Account } from '../components/Account';
import { Plate } from '../components/Plate';
import { buildAddressBook, key } from '../lib/addresses';
import { ApiError, api } from '../lib/api';
import { inspectionState } from '../lib/inspections';
import type { EquipmentTypeInfo, Plan } from '../lib/plan';
import { hashOf } from '../lib/router';
import { IncidentCard, ReportForm } from '../panels/Incidents';
import { isOpen, useIncidents, useIncidentsFeed } from '../state/incidents';
import './tag.css';

export function Tag({ planId, objectId }: { planId: string; objectId: string }) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [types, setTypes] = useState<Map<string, EquipmentTypeInfo>>(new Map());
  const [error, setError] = useState<string | null>(null);
  useIncidentsFeed(planId);
  const incidents = useIncidents((s) => s.items).filter((i) => i.object_id === objectId && isOpen(i));

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
        if (alive) setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил. Проверьте связь и обновите страницу.');
      });
    return () => {
      alive = false;
    };
  }, [planId]);

  const item = plan?.equipment?.find((e) => e.id === objectId);

  return (
    <main className="tag">
      <header className="tag__bar on-steel">
        <a href={hashOf({ name: 'plan', id: planId })} className="tag__back">
          <ArrowLeft size={16} aria-hidden="true" /> План отделения
        </a>
        <Account />
      </header>

      {error && <p className="tag__error">{error}</p>}
      {!plan && !error && (
        <p className="tag__loading">
          <LoaderCircle size={16} className="spin" aria-hidden="true" /> Загружаем паспорт объекта…
        </p>
      )}
      {plan && !item && (
        <p className="tag__error">
          Объекта с этой бирки на плане больше нет — возможно, его убрали при перепланировке. Сообщите начальнику
          отделения.
        </p>
      )}

      {plan && item && (
        <>
          <ObjectHead plan={plan} objectId={objectId} types={types} />
          {incidents.length > 0 && (
            <section className="tag__section" aria-labelledby="tag-open">
              <h2 id="tag-open">Уже сообщили</h2>
              {incidents.map((incident) => (
                <IncidentCard key={incident.id} incident={incident} />
              ))}
            </section>
          )}
          <section className="tag__section" aria-labelledby="tag-report">
            <h2 id="tag-report">Сообщить о неисправности</h2>
            <ReportForm planId={planId} objectId={objectId} />
          </section>
        </>
      )}
    </main>
  );
}

function ObjectHead({ plan, objectId, types }: { plan: Plan; objectId: string; types: Map<string, EquipmentTypeInfo> }) {
  const item = plan.equipment!.find((e) => e.id === objectId)!;
  const Glyph = glyphFor(item.type);
  const book = buildAddressBook(plan);
  const zone = plan.zones?.find((z) => z.id === item.zone);
  const due = inspectionState(item.inventory?.next_inspection);
  const typeLabel = types.get(item.type)?.label ?? item.type;
  return (
    <section className="tag__head">
      <span className="tag__glyph">
        <Glyph size={28} aria-hidden="true" />
      </span>
      <div>
        <Plate surface="label" size="sm">
          {book.byId.get(key('equipment', item.id)) ?? ''}
        </Plate>
        <h1>{item.label || typeLabel}</h1>
        <p>{typeLabel}</p>
        <p className="tag__where">{plan.ops.name}</p>
      </div>
      <dl className="tag__facts">
        {zone && (
          <div>
            <dt>
              <MapPin size={14} aria-hidden="true" /> Где
            </dt>
            <dd>{zone.label || zone.type}</dd>
          </div>
        )}
        {item.staff?.full_name && (
          <div>
            <dt>
              <IdCard size={14} aria-hidden="true" /> Ответственный
            </dt>
            <dd>
              {item.staff.full_name}
              {item.staff.position ? `, ${item.staff.position}` : ''}
            </dd>
          </div>
        )}
        {item.inventory?.next_inspection && (
          <div>
            <dt>
              <CalendarClock size={14} aria-hidden="true" /> Осмотр
            </dt>
            <dd className={due.tone === 'overdue' ? 'tag__late' : undefined}>{due.text}</dd>
          </div>
        )}
      </dl>
    </section>
  );
}
