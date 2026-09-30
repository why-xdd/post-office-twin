/**
 * Паспорт объекта в режиме эксплуатации: ответственный, учётные данные, сроки обслуживания
 * и ссылки во внутренние системы. План тот же, что в проектировании, — меняется только то,
 * что человек видит по щелчку.
 */

import { ExternalLink, IdCard, MapPin, Wrench } from 'lucide-react';
import { useState } from 'react';
import { TextField } from '../components/fields';
import { Plate } from '../components/Plate';
import type { AddressBook } from '../lib/addresses';
import { key } from '../lib/addresses';
import type { Equipment, EquipmentTypeInfo } from '../lib/plan';
import { formatNumber } from '../lib/planFacts';
import { formatDate, inspectionState } from '../lib/inspections';
import { useWorkspace } from '../state/store';
import { glyphFor } from '../canvas/glyphs';
import './inventory.css';

/** Отметка демонстрационных данных: сотрудники и учётные номера в демо-плане придуманы. */
const SYNTHETIC_MARK = 'synthetic';

interface PassportProps {
  book: AddressBook;
  types: Map<string, EquipmentTypeInfo>;
}

export function Passport({ book, types }: PassportProps) {
  const doc = useWorkspace((s) => s.doc);
  const selection = useWorkspace((s) => s.selection);
  const commit = useWorkspace((s) => s.commit);
  const [editing, setEditing] = useState(false);

  const item = selection?.kind === 'equipment' ? doc?.equipment?.find((e) => e.id === selection.id) : undefined;

  if (!doc) return null;

  // Что просрочено, видно слева в «Сроках осмотра»; здесь — только приглашение выбрать объект.
  if (!item) return <p className="passport__prompt">Щёлкните объект на плане, чтобы открыть его паспорт.</p>;

  const info = types.get(item.type);
  const Glyph = glyphFor(item.type);
  const zone = doc.zones?.find((z) => z.id === item.zone);
  const next = inspectionState(item.inventory?.next_inspection);
  const synthetic = item.attributes?.data_origin === SYNTHETIC_MARK;
  const integrations = Object.keys(item.integrations ?? {});

  const update = (mutate: (target: Equipment) => void) =>
    commit((draft) => {
      const target = draft.equipment?.find((e) => e.id === item.id);
      if (target) mutate(target);
    });

  return (
    <div className="passport">
      <header className="passport__head">
        <span className="props__glyph">
          <Glyph size={22} aria-hidden="true" />
        </span>
        <div>
          <Plate surface="label" size="sm">
            {book.byId.get(key('equipment', item.id)) ?? ''}
          </Plate>
          <h2>{item.label || info?.label || item.type}</h2>
          <p>{info?.label ?? item.type}</p>
        </div>
      </header>

      {synthetic && <p className="passport__synthetic">Демонстрационные данные: сотрудники и учётные номера придуманы.</p>}

      <section className="passport__section">
        <h3>
          <IdCard size={15} aria-hidden="true" /> Ответственный
        </h3>
        {item.staff?.full_name ? (
          <dl className="passport__facts">
            <dt>Сотрудник</dt>
            <dd>{item.staff.full_name}</dd>
            {item.staff.position && (
              <>
                <dt>Должность</dt>
                <dd>{item.staff.position}</dd>
              </>
            )}
            {item.staff.personnel_number && (
              <>
                <dt>Табельный №</dt>
                <dd>{item.staff.personnel_number}</dd>
              </>
            )}
            {item.staff.schedule && (
              <>
                <dt>График</dt>
                <dd>{item.staff.schedule}</dd>
              </>
            )}
            {item.staff.avg_service_time_min && (
              <>
                <dt>Среднее обслуживание</dt>
                <dd>{formatNumber(item.staff.avg_service_time_min, 1)} мин</dd>
              </>
            )}
          </dl>
        ) : (
          <p className="passport__none">Сотрудник не закреплён.</p>
        )}
      </section>

      <section className="passport__section">
        <h3>
          <Wrench size={15} aria-hidden="true" /> Учёт и обслуживание
        </h3>
        {editing ? (
          <div className="fields">
            <TextField label="Инвентарный номер" wide value={item.inventory?.inventory_number} onCommit={(v) => update((t) => { t.inventory = { ...(t.inventory ?? {}), inventory_number: v || undefined }; })} />
            <TextField label="Серийный номер" wide value={item.inventory?.serial_number} onCommit={(v) => update((t) => { t.inventory = { ...(t.inventory ?? {}), serial_number: v || undefined }; })} />
            <TextField label="Производитель" value={item.inventory?.vendor} onCommit={(v) => update((t) => { t.inventory = { ...(t.inventory ?? {}), vendor: v || undefined }; })} />
            <TextField label="Модель" value={item.inventory?.model} onCommit={(v) => update((t) => { t.inventory = { ...(t.inventory ?? {}), model: v || undefined }; })} />
            <TextField label="Последний осмотр" value={item.inventory?.last_inspection} placeholder="ГГГГ-ММ-ДД" onCommit={(v) => update((t) => { t.inventory = { ...(t.inventory ?? {}), last_inspection: v || undefined }; })} />
            <TextField label="Следующий осмотр" value={item.inventory?.next_inspection} placeholder="ГГГГ-ММ-ДД" onCommit={(v) => update((t) => { t.inventory = { ...(t.inventory ?? {}), next_inspection: v || undefined }; })} />
          </div>
        ) : item.inventory ? (
          <dl className="passport__facts">
            <dt>Инвентарный №</dt>
            <dd>{item.inventory.inventory_number ?? '—'}</dd>
            <dt>Серийный №</dt>
            <dd>{item.inventory.serial_number ?? '—'}</dd>
            <dt>Модель</dt>
            <dd>{[item.inventory.vendor, item.inventory.model].filter(Boolean).join(', ') || '—'}</dd>
            <dt>Последний осмотр</dt>
            <dd>{formatDate(item.inventory.last_inspection)}</dd>
            <dt>Следующий осмотр</dt>
            <dd className={`passport__due passport__due--${next.tone}`}>{next.text}</dd>
          </dl>
        ) : (
          <p className="passport__none">Учётные данные не заполнены.</p>
        )}
        <button type="button" className="btn btn--outline passport__edit" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Готово' : 'Изменить учётные данные'}
        </button>
      </section>

      {item.attributes && (item.attributes.status !== undefined || item.attributes.ip_address !== undefined) && (
        <section className="passport__section">
          <h3>Состояние</h3>
          <dl className="passport__facts">
            {item.attributes.status !== undefined && (
              <>
                <dt>Статус</dt>
                <dd>{String(item.attributes.status)}</dd>
              </>
            )}
            {item.attributes.uses_per_month !== undefined && (
              <>
                <dt>Использований в месяц</dt>
                <dd>{String(item.attributes.uses_per_month)}</dd>
              </>
            )}
            {item.attributes.ip_address !== undefined && (
              <>
                <dt>IP-адрес</dt>
                <dd>{String(item.attributes.ip_address)}</dd>
              </>
            )}
          </dl>
        </section>
      )}

      {integrations.length > 0 && (
        <section className="passport__section">
          <h3>
            <ExternalLink size={15} aria-hidden="true" /> Внутренние системы
          </h3>
          <div className="passport__links">
            {integrations.map((name) => {
              const url = item.integrations?.[name];
              return url ? (
                <a key={name} className="btn btn--outline" href={url} target="_blank" rel="noreferrer">
                  {name}
                </a>
              ) : (
                <button key={name} type="button" className="btn btn--outline" disabled title="Адрес задаётся при подключении к системе">
                  {name}
                </button>
              );
            })}
          </div>
          <p className="passport__none">Ссылки становятся активными после подключения к системам Почты.</p>
        </section>
      )}

      <section className="passport__section">
        <h3>
          <MapPin size={15} aria-hidden="true" /> Расположение
        </h3>
        <dl className="passport__facts">
          <dt>Зона</dt>
          <dd>{zone ? `${book.byId.get(key('zone', zone.id)) ?? ''} ${zone.label ?? zone.type}` : 'Вне зон'}</dd>
          <dt>Центр</dt>
          <dd>
            X {formatNumber(item.position[0])} · Y {formatNumber(item.position[1])} м
          </dd>
          <dt>Габарит</dt>
          <dd>
            {formatNumber(item.size_m[0])} × {formatNumber(item.size_m[1])} м
          </dd>
        </dl>
      </section>
    </div>
  );
}
