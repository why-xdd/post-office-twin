/**
 * Свойства выделенного: объекта, зоны, стены, проёма — или всего плана, если ничего не выбрано.
 */

import {
  AlignCenterHorizontal,
  AlignCenterVertical,
  AlignEndHorizontal,
  AlignEndVertical,
  AlignHorizontalSpaceBetween,
  AlignStartHorizontal,
  AlignStartVertical,
  AlignVerticalSpaceBetween,
  CircleCheck,
  Copy,
  OctagonAlert,
  RotateCw,
  Trash2,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react';
import { useState } from 'react';
import { NumberField, ReadonlyField, SelectField, TextField } from '../components/fields';
import { Plate } from '../components/Plate';
import type { AddressBook } from '../lib/addresses';
import { formatAddress, key } from '../lib/addresses';
import { alignGroup, type AlignMode } from '../lib/align';
import { transformInPlace, withTransform } from '../lib/equipmentOps';
import { round3, uniqueId, zoneAt } from '../lib/geometry';
import type { Door, Equipment, EquipmentTypeInfo, Plan, WindowOpening, ZoneTypeInfo } from '../lib/plan';
import { isNormFinding, plainFinding } from '../lib/plainFindings';
import { fitsRoom, hostWall } from '../lib/room';
import { MIN_WALL_M, openingPlacement, resizeOpening, wallAxis, wallLength } from '../lib/wallEdit';
import { formatNumber, planArea, plural, polygonArea } from '../lib/planFacts';
import { useWorkspace } from '../state/store';
import { glyphFor } from '../canvas/glyphs';
import '../components/fields.css';
import './panels.css';

interface PropertiesProps {
  book: AddressBook;
  types: Map<string, EquipmentTypeInfo>;
  zoneTypes: ZoneTypeInfo[];
}

const SERVICE_TYPES = new Set(['cashier_window', 'pvz_counter', 'payment_terminal_stand']);

export function Properties({ book, types, zoneTypes }: PropertiesProps) {
  const doc = useWorkspace((s) => s.doc);
  const selection = useWorkspace((s) => s.selection);
  const commit = useWorkspace((s) => s.commit);
  const updateEquipment = useWorkspace((s) => s.updateEquipment);
  const updateZone = useWorkspace((s) => s.updateZone);
  const removeSelection = useWorkspace((s) => s.removeSelection);
  const select = useWorkspace((s) => s.select);
  const group = useWorkspace((s) => s.group);

  if (!doc) return null;

  // После отмены в группе могут остаться объекты, которых на плане уже нет.
  const liveGroup = group.filter((id) => doc.equipment?.some((e) => e.id === id));
  if (liveGroup.length > 1) return <GroupProperties ids={liveGroup} book={book} types={types} />;

  if (selection?.kind === 'equipment') {
    const item = doc.equipment?.find((e) => e.id === selection.id);
    if (!item) return <PlanProperties doc={doc} />;
    const info = types.get(item.type);
    const Glyph = glyphFor(item.type);
    const typeOptions = [...types.values()].map((t) => ({ value: t.code, label: t.label }));
    const zoneOptions = (doc.zones ?? []).map((z) => ({ value: z.id, label: `${book.byId.get(key('zone', z.id)) ?? ''} ${z.label ?? z.type}` }));
    const schema = info?.attribute_schema ?? {};
    return (
      <div className="props">
        <header className="props__head">
          <span className="props__glyph">
            <Glyph size={20} aria-hidden="true" />
          </span>
          <div>
            <Plate surface="label" size="sm">
              {book.byId.get(key('equipment', item.id)) ?? ''}
            </Plate>
            <h2>{item.label || info?.label || item.type}</h2>
            <p className="props__id">{item.id}</p>
          </div>
        </header>

        <ObjectFindings id={item.id} />

        <div className="fields">
          <TextField label="Подпись" wide value={item.label} placeholder={info?.label} onCommit={(label) => updateEquipment(item.id, { label: label || undefined })} />
          <SelectField label="Тип" wide value={item.type} options={typeOptions} onCommit={(type) => updateEquipment(item.id, { type })} />
          <NumberField label="X центра" unit="м" value={item.position[0]} onCommit={(v) => v !== null && moveTo(item.id, [v, item.position[1]])} />
          <NumberField label="Y центра" unit="м" value={item.position[1]} onCommit={(v) => v !== null && moveTo(item.id, [item.position[0], v])} />
          <NumberField label="Длина по X" unit="м" min={0.05} max={30} value={item.size_m[0]} onCommit={(v) => v !== null && updateEquipment(item.id, { size_m: [v, item.size_m[1]] })} />
          <NumberField label="Длина по Y" unit="м" min={0.05} max={30} value={item.size_m[1]} onCommit={(v) => v !== null && updateEquipment(item.id, { size_m: [item.size_m[0], v] })} />
          <NumberField label="Поворот" unit="°" step={15} min={0} max={359} value={item.rotation_deg ?? 0} onCommit={(v) => updateEquipment(item.id, { rotation_deg: v ?? 0 })} />
          <SelectField
            label="Зона"
            value={item.zone}
            emptyLabel="Вне зон"
            options={zoneOptions}
            hint={zoneHint(doc, item.id)}
            onCommit={(zone) => updateEquipment(item.id, { zone: zone || undefined })}
          />
          {SERVICE_TYPES.has(item.type) && (
            <NumberField
              label="Высота низа окна"
              unit="м"
              min={0.3}
              max={1.5}
              allowEmpty
              value={item.counter_height_m}
              hint="Для МГН — не выше 0,85 м"
              onCommit={(v) => updateEquipment(item.id, { counter_height_m: v ?? undefined })}
            />
          )}
          {(item.type === 'waiting_seat' || info?.capacity_persons) && (
            <NumberField
              label="Вместимость"
              unit="чел."
              step={1}
              min={0}
              max={200}
              allowEmpty
              value={item.capacity_persons}
              onCommit={(v) => updateEquipment(item.id, { capacity_persons: v === null ? undefined : Math.round(v) })}
            />
          )}
          {Object.entries(schema).map(([name, kind]) => (
            <TextField
              key={name}
              label={ATTRIBUTE_LABEL[name] ?? name}
              value={readAttribute(item, name)}
              placeholder={kind === 'date' ? 'ГГГГ-ММ-ДД' : undefined}
              onCommit={(value) => writeAttribute(item.id, name, value)}
            />
          ))}
        </div>

        <div className="props__actions">
          <button type="button" className="action" onClick={() => updateEquipment(item.id, { rotation_deg: (((item.rotation_deg ?? 0) + 90) % 360) })}>
            <RotateCw size={15} aria-hidden="true" />
            Повернуть на 90°
            <kbd>R</kbd>
          </button>
          <button type="button" className="action" onClick={() => duplicate(item.id)}>
            <Copy size={15} aria-hidden="true" />
            Дублировать
            <kbd>Ctrl D</kbd>
          </button>
          <button type="button" className="action action--danger" onClick={removeSelection}>
            <Trash2 size={15} aria-hidden="true" />
            Удалить
            <kbd>Del</kbd>
          </button>
        </div>
      </div>
    );
  }

  if (selection?.kind === 'zone') {
    const zone = doc.zones?.find((z) => z.id === selection.id);
    if (!zone) return <PlanProperties doc={doc} />;
    const contained = (doc.equipment ?? []).filter((e) => e.zone === zone.id);
    return (
      <div className="props">
        <header className="props__head">
          <div>
            <Plate surface="label" size="sm">
              {book.byId.get(key('zone', zone.id)) ?? ''}
            </Plate>
            <h2>{zone.label || 'Зона'}</h2>
            <p className="props__id">{zone.id}</p>
          </div>
        </header>
        <ObjectFindings id={zone.id} />
        <div className="fields">
          <TextField label="Название" wide value={zone.label} onCommit={(label) => updateZone(zone.id, { label })} />
          <SelectField
            label="Тип зоны"
            wide
            value={zone.type}
            options={zoneTypes.map((t) => ({ value: t.code, label: t.label }))}
            hint={zoneTypes.find((t) => t.code === zone.type)?.public ? 'Доступна посетителям' : 'Служебная, посетителей нет'}
            onCommit={(type) => updateZone(zone.id, { type })}
          />
          <ReadonlyField label="Площадь" value={`${formatNumber(polygonArea(zone.polygon))} м²`} />
          <ReadonlyField label="Вершин" value={zone.polygon.length} />
          <NumberField
            label="Расчётная вместимость"
            unit="чел."
            step={1}
            min={0}
            allowEmpty
            value={zone.expected_capacity_persons}
            onCommit={(v) => updateZone(zone.id, { expected_capacity_persons: v === null ? undefined : Math.round(v) })}
          />
          <ReadonlyField label="Объектов в зоне" value={contained.length} />
        </div>
        <div className="props__actions">
          <button type="button" className="action action--danger" onClick={removeSelection}>
            <Trash2 size={15} aria-hidden="true" />
            Удалить зону
            <kbd>Del</kbd>
          </button>
        </div>
      </div>
    );
  }

  if (selection?.kind === 'wall') {
    const wall = doc.walls?.find((w) => w.id === selection.id);
    if (!wall) return <PlanProperties doc={doc} />;
    const length = Math.hypot(wall.to[0] - wall.from[0], wall.to[1] - wall.from[1]);
    return (
      <div className="props">
        <header className="props__head">
          <div>
            <Plate surface="label" size="sm">
              {book.byId.get(key('wall', wall.id)) ?? ''}
            </Plate>
            <h2>{wall.label || (wall.type === 'outer' ? 'Наружная стена' : 'Перегородка')}</h2>
            <p className="props__id">{wall.id}</p>
          </div>
        </header>
        <ObjectFindings id={wall.id} />
        <div className="fields">
          <SelectField
            label="Тип"
            wide
            value={wall.type ?? 'outer'}
            options={[
              { value: 'outer', label: 'Наружная стена' },
              { value: 'partition', label: 'Перегородка' },
            ]}
            onCommit={(type) => commit((draft) => { const w = draft.walls?.find((x) => x.id === wall.id); if (w) w.type = type; })}
          />
          {wall.type === 'outer' ? (
            <ReadonlyField label="Длина" value={`${formatNumber(length)} м`} />
          ) : (
            <NumberField
              label="Длина"
              unit="м"
              min={MIN_WALL_M}
              max={80}
              value={round3(length)}
              hint="Конец перегородки сдвигается вдоль неё"
              onCommit={(v) => v !== null && setPartitionLength(wall.id, v)}
            />
          )}
          <ReadonlyField label="Направление" value={Math.abs(wall.from[0] - wall.to[0]) < 1e-6 ? 'вертикально' : Math.abs(wall.from[1] - wall.to[1]) < 1e-6 ? 'горизонтально' : 'под углом'} />
        </div>
        <p className="props__note">
          {wallAxis(wall)
            ? 'Чтобы сдвинуть стену, тяните синюю ручку на плане. Двойной щелчок по стене добавит дверь или окно.'
            : 'Двойной щелчок по стене добавит дверь или окно.'}
        </p>
        <div className="props__actions">
          <button type="button" className="action action--danger" onClick={removeSelection}>
            <Trash2 size={15} aria-hidden="true" />
            Удалить стену
            <kbd>Del</kbd>
          </button>
        </div>
      </div>
    );
  }

  if (selection?.kind === 'door' || selection?.kind === 'window') {
    return <OpeningProperties key={selection.id} kind={selection.kind} id={selection.id} book={book} />;
  }

  return <PlanProperties doc={doc} />;

  function setPartitionLength(id: string, length: number) {
    commit((draft) => {
      const target = draft.walls?.find((w) => w.id === id);
      if (!target) return;
      const current = Math.hypot(target.to[0] - target.from[0], target.to[1] - target.from[1]) || 1;
      target.to = [
        round3(target.from[0] + ((target.to[0] - target.from[0]) / current) * length),
        round3(target.from[1] + ((target.to[1] - target.from[1]) / current) * length),
      ];
    });
  }

  function moveTo(id: string, position: [number, number]) {
    commit((draft) => {
      const target = draft.equipment?.find((e) => e.id === id);
      if (!target) return;
      transformInPlace(target, { position: [round3(position[0]), round3(position[1])] });
      const zone = zoneAt(draft, target.position);
      if (zone) target.zone = zone.id;
      else delete target.zone;
    });
  }

  function duplicate(id: string) {
    const source = doc?.equipment?.find((e) => e.id === id);
    if (!source) return;
    const copyId = uniqueId((doc?.equipment ?? []).map((e) => e.id), source.type);
    commit((draft) => {
      draft.equipment = [
        ...(draft.equipment ?? []),
        {
          ...structuredClone(source),
          ...withTransform(source, { position: [round3(source.position[0] + 0.5), round3(source.position[1] - 0.5)] }),
          id: copyId,
        },
      ];
    });
    select({ kind: 'equipment', id: copyId });
  }

  function writeAttribute(id: string, name: string, value: string) {
    commit((draft) => {
      const target = draft.equipment?.find((e) => e.id === id);
      if (!target) return;
      if (INVENTORY_FIELDS.has(name)) {
        target.inventory = { ...(target.inventory ?? {}), [name]: value || undefined };
      } else {
        target.attributes = { ...(target.attributes ?? {}), [name]: value || undefined };
      }
    });
  }
}

const INVENTORY_FIELDS = new Set(['serial_number', 'vendor', 'model', 'last_inspection', 'next_inspection']);

const ATTRIBUTE_LABEL: Record<string, string> = {
  serial_number: 'Серийный номер',
  last_inspection: 'Последний осмотр',
  next_inspection: 'Следующий осмотр',
  vendor: 'Производитель',
  model: 'Модель',
  ip_address: 'IP-адрес',
  status: 'Статус',
  window_number: 'Номер окна',
  services: 'Услуги',
  cells: 'Ячеек',
  parcels_loaded: 'Загружено посылок',
  usage_per_month: 'Обращений в месяц',
  uses_per_month: 'Обращений в месяц',
  diagonal_inch: 'Диагональ, дюймов',
  shelves: 'Полок',
  computer: 'Компьютер',
};

function readAttribute(item: Equipment, name: string): string {
  const value = INVENTORY_FIELDS.has(name)
    ? (item.inventory as Record<string, unknown> | undefined)?.[name]
    : item.attributes?.[name];
  return value === undefined || value === null ? '' : String(value);
}

function zoneHint(doc: Plan, id: string): string | undefined {
  const item = doc.equipment?.find((e) => e.id === id);
  if (!item) return undefined;
  const actual = zoneAt(doc, item.position);
  if (actual && item.zone && actual.id !== item.zone) return `Центр объекта лежит в зоне «${actual.label ?? actual.id}»`;
  return undefined;
}

function PlanProperties({ doc }: { doc: Plan }) {
  const commit = useWorkspace((s) => s.commit);
  const setOps = (patch: Partial<Plan['ops']>) => commit((draft) => Object.assign(draft.ops, patch));
  const setFlow = (patch: Partial<NonNullable<Plan['flow_hints']>>) =>
    commit((draft) => {
      draft.flow_hints = { ...(draft.flow_hints ?? {}), ...patch };
    });
  return (
    <div className="props">
      <header className="props__head">
        <div>
          <h2>{doc.ops.name || 'План отделения'}</h2>
          <p className="props__id">Ничего не выбрано — свойства всего плана</p>
        </div>
      </header>
      <div className="fields">
        <TextField label="Название отделения" wide value={doc.ops.name} onCommit={(name) => setOps({ name })} />
        <TextField label="Тип отделения" wide value={doc.ops.type_label} placeholder="Городское малое отделение" onCommit={(type_label) => setOps({ type_label })} />
        <ReadonlyField label="Площадь по контуру" value={`${formatNumber(planArea(doc))} м²`} />
        <NumberField label="Высота потолка" unit="м" min={2} max={8} allowEmpty value={doc.ops.ceiling_height_m} onCommit={(v) => setOps({ ceiling_height_m: v ?? undefined })} />
        <NumberField
          label="Население пункта"
          unit="тыс. чел."
          step={1}
          min={0}
          allowEmpty
          value={doc.ops.settlement_population}
          hint="Для проверки охвата по Приказу № 538"
          onCommit={(v) => setOps({ settlement_population: v ?? undefined })}
        />
        <NumberField
          label="Посетителей в день"
          unit="чел."
          step={10}
          min={0}
          allowEmpty
          value={doc.flow_hints?.avg_daily_visitors}
          hint="Для расчёта очереди в час пик"
          onCommit={(v) => setFlow({ avg_daily_visitors: v === null ? undefined : Math.round(v) })}
        />
        <NumberField
          label="Средний визит"
          unit="мин"
          step={0.5}
          min={0.5}
          allowEmpty
          value={doc.flow_hints?.typical_visit_duration_min}
          onCommit={(v) => setFlow({ typical_visit_duration_min: v ?? undefined })}
        />
      </div>
    </div>
  );
}

const ALIGN_ACTIONS: { mode: AlignMode; label: string; icon: LucideIcon }[] = [
  { mode: 'left', label: 'По левому краю', icon: AlignStartVertical },
  { mode: 'centerX', label: 'По центру слева направо', icon: AlignCenterVertical },
  { mode: 'right', label: 'По правому краю', icon: AlignEndVertical },
  { mode: 'top', label: 'По верхнему краю', icon: AlignStartHorizontal },
  { mode: 'centerY', label: 'По центру сверху вниз', icon: AlignCenterHorizontal },
  { mode: 'bottom', label: 'По нижнему краю', icon: AlignEndHorizontal },
];

const SPREAD_ACTIONS: { mode: AlignMode; label: string; icon: LucideIcon }[] = [
  { mode: 'spreadX', label: 'Равные промежутки слева направо', icon: AlignHorizontalSpaceBetween },
  { mode: 'spreadY', label: 'Равные промежутки сверху вниз', icon: AlignVerticalSpaceBetween },
];

/**
 * Несколько объектов сразу: выровнять по краю или центру, расставить с равными промежутками,
 * удалить. Правка — одна на всю группу, отменяется одним Ctrl+Z.
 */
function GroupProperties({ ids, book, types }: { ids: string[]; book: AddressBook; types: Map<string, EquipmentTypeInfo> }) {
  const doc = useWorkspace((s) => s.doc);
  const commit = useWorkspace((s) => s.commit);
  const select = useWorkspace((s) => s.select);
  const removeSelection = useWorkspace((s) => s.removeSelection);
  const [problem, setProblem] = useState<string | null>(null);
  if (!doc) return null;
  const members = ids
    .map((id) => doc.equipment?.find((e) => e.id === id))
    .filter((member): member is Equipment => Boolean(member));
  const name = (member: Equipment) => member.label || types.get(member.type)?.label || member.type;
  const count = `${members.length} ${plural(members.length, 'объект', 'объекта', 'объектов')}`;

  const align = (mode: AlignMode) => {
    const moves = alignGroup(members, mode);
    if (moves.size === 0) {
      setProblem(mode === 'spreadX' || mode === 'spreadY' ? 'Раздвигать некуда: объекты стоят вплотную или уже ровно' : 'Объекты уже стоят так');
      return;
    }
    const stuck = members.find((member) => {
      const position = moves.get(member.id);
      return position && (types.get(member.type)?.floor_object ?? true) && !fitsRoom(doc, { ...member, position });
    });
    if (stuck) {
      setProblem(`Не выровнять: «${name(stuck)}» упрётся в стену`);
      return;
    }
    setProblem(null);
    commit((draft) => {
      for (const [id, position] of moves) {
        const target = draft.equipment?.find((e) => e.id === id);
        if (!target) continue;
        transformInPlace(target, { position });
        const zone = zoneAt(draft, target.position);
        if (zone) target.zone = zone.id;
        else delete target.zone;
      }
    });
  };

  return (
    <div className="props">
      <header className="props__head">
        <div>
          <h2>Выбрано {count}</h2>
          <p className="props__id">Shift + щелчок добавляет объект или убирает его из выделения</p>
        </div>
      </header>

      <section className="props-group" aria-label="Выравнивание">
        <h3 className="props-findings__title">Выровнять</h3>
        <div className="props-group__row">
          {ALIGN_ACTIONS.map(({ mode, label, icon: Icon }) => (
            <button key={mode} type="button" className="props-group__tool" onClick={() => align(mode)} title={label} aria-label={label}>
              <Icon size={17} aria-hidden="true" />
            </button>
          ))}
          <span className="props-group__gap" aria-hidden="true" />
          {SPREAD_ACTIONS.map(({ mode, label, icon: Icon }) => (
            <button
              key={mode}
              type="button"
              className="props-group__tool"
              onClick={() => align(mode)}
              disabled={members.length < 3}
              title={members.length < 3 ? `${label}: нужно три объекта и больше` : label}
              aria-label={label}
            >
              <Icon size={17} aria-hidden="true" />
            </button>
          ))}
        </div>
        {problem && (
          <p className="props-group__problem" role="status">
            {problem}
          </p>
        )}
      </section>

      <ul className="props-group__list" aria-label="Выбранные объекты">
        {members.map((member) => (
          <li key={member.id}>
            <button type="button" className="props-group__member" onClick={() => select({ kind: 'equipment', id: member.id })} title="Выбрать только этот объект">
              <Plate surface="label" size="sm">
                {book.byId.get(key('equipment', member.id)) ?? ''}
              </Plate>
              <span>{name(member)}</span>
            </button>
          </li>
        ))}
      </ul>

      <div className="props__actions">
        <button type="button" className="action action--danger" onClick={removeSelection}>
          <Trash2 size={15} aria-hidden="true" />
          Удалить {count}
          <kbd>Del</kbd>
        </button>
      </div>
    </div>
  );
}

/**
 * Нарушения и замечания, в которых участвует выбранное: чтобы узнать, всё ли с объектом в
 * порядке, не нужно искать его в списке «Проверки». Щелчок закрепляет нарушение на плане.
 */
function ObjectFindings({ id }: { id: string }) {
  const report = useWorkspace((s) => s.report);
  const state = useWorkspace((s) => s.reportState);
  const doc = useWorkspace((s) => s.doc);
  const active = useWorkspace((s) => s.activeFinding);
  const setActive = useWorkspace((s) => s.setActiveFinding);
  const highlight = useWorkspace((s) => s.highlight);
  // До первого ответа проверки сказать нечего: пустое место лучше, чем «нарушений нет».
  if (!report) return null;
  const stale = state === 'checking' ? ' props-findings--stale' : '';
  const related = report.findings
    .map((finding, index) => ({ finding, index }))
    .filter(({ finding }) => finding.severity !== 'info' && finding.objects.includes(id));

  if (related.length === 0) {
    return (
      <p className={`props-findings__clean${stale}`}>
        <CircleCheck size={15} aria-hidden="true" />
        Нарушений с участием объекта нет
      </p>
    );
  }

  const errors = related.filter(({ finding }) => finding.severity === 'error').length;
  const title =
    errors === related.length
      ? 'Нарушения с участием объекта'
      : errors === 0
        ? 'Замечания с участием объекта'
        : 'Нарушения и замечания с участием объекта';

  return (
    <section className={`props-findings${stale}`} aria-label={title}>
      <h3 className="props-findings__title">
        {title}
        <b>{related.length}</b>
      </h3>
      <ul className="props-findings__list">
        {related.map(({ finding, index }) => {
          const plain = plainFinding(finding, doc);
          const Icon = finding.severity === 'error' ? OctagonAlert : TriangleAlert;
          const pinned = active === index;
          const kind = finding.severity === 'error' ? (isNormFinding(finding) ? 'Нарушение' : 'Ошибка в данных') : 'Замечание';
          return (
            <li key={index}>
              <button
                type="button"
                className={`props-finding props-finding--${finding.severity}`}
                aria-pressed={pinned}
                title={pinned ? 'Снять подсветку с плана' : 'Подсветить на плане'}
                onClick={() => {
                  if (pinned) {
                    setActive(null);
                    highlight([]);
                    return;
                  }
                  setActive(index);
                  highlight(finding.objects);
                }}
              >
                <Icon size={15} aria-hidden="true" className="props-finding__icon" />
                <span className="props-finding__text">
                  <span className="props-finding__title">
                    <span className="visually-hidden">{kind}: </span>
                    {plain.title}
                  </span>
                  {plain.measure && <span className="props-finding__measure">{plain.measure}</span>}
                  <cite className="props-finding__source">
                    {finding.source}
                    {finding.clause ? `, ${finding.clause}` : ''}
                  </cite>
                </span>
                <Plate surface="label" size="sm">
                  {formatAddress('finding', index)}
                </Plate>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const DOOR_TYPES = [
  { value: 'entrance', label: 'Главный вход для посетителей' },
  { value: 'service', label: 'Служебный вход' },
  { value: 'internal', label: 'Внутренняя дверь' },
];

function OpeningProperties({ kind, id, book }: { kind: 'door' | 'window'; id: string; book: AddressBook }) {
  const doc = useWorkspace((s) => s.doc);
  const commit = useWorkspace((s) => s.commit);
  const removeSelection = useWorkspace((s) => s.removeSelection);
  const [problem, setProblem] = useState<string | null>(null);
  if (!doc) return null;
  const opening = kind === 'door' ? doc.doors?.find((d) => d.id === id) : doc.windows?.find((w) => w.id === id);
  if (!opening) return <PlanProperties doc={doc} />;
  const wall = hostWall(doc, opening.wall);
  const placement = wall ? openingPlacement(wall, opening.wall) : null;
  const door = kind === 'door' ? (opening as Door) : null;
  const win = kind === 'window' ? (opening as WindowOpening) : null;

  const resize = (patch: { offset?: number; width?: number }) => {
    const result = resizeOpening(doc, kind, id, patch);
    if ('error' in result) {
      setProblem(result.error);
      return;
    }
    setProblem(null);
    commit((draft) => {
      draft.doors = result.plan.doors;
      draft.windows = result.plan.windows;
    });
  };

  return (
    <div className="props">
      <header className="props__head">
        <div>
          <Plate surface="label" size="sm">
            {book.byId.get(key(kind, id)) ?? ''}
          </Plate>
          <h2>{door ? door.label || 'Дверь' : 'Окно'}</h2>
          <p className="props__id">{id}</p>
        </div>
      </header>
      <ObjectFindings id={id} />
      <div className="fields">
        {door && (
          <>
            <TextField
              label="Подпись"
              wide
              value={door.label}
              onCommit={(label) => commit((draft) => { const d = draft.doors?.find((x) => x.id === id); if (d) d.label = label || undefined; })}
            />
            <SelectField
              label="Назначение"
              wide
              value={door.type ?? 'internal'}
              options={DOOR_TYPES}
              hint={door.type === 'entrance' ? 'От этой двери считаются пути посетителей' : undefined}
              onCommit={(type) =>
                commit((draft) => {
                  const d = draft.doors?.find((x) => x.id === id);
                  if (!d) return;
                  d.type = type;
                  // Главный вход один: от него считаются пути и площадка у входа.
                  if (type === 'entrance') draft.flow_hints = { ...(draft.flow_hints ?? {}), main_entrance: id };
                })
              }
            />
          </>
        )}
        {placement ? (
          <>
            <NumberField label="Ширина" unit="м" min={0.5} max={6} step={0.1} value={placement.width} error={problem ?? undefined} onCommit={(v) => v !== null && resize({ width: v })} />
            <NumberField label="Отступ от угла" unit="м" min={0} step={0.1} value={placement.offset} onCommit={(v) => v !== null && resize({ offset: v })} />
          </>
        ) : (
          <p className="props__note field--wide">Проём не лежит ни на одной стене. Удалите его и поставьте заново двойным щелчком по стене.</p>
        )}
        {win && (
          <NumberField
            label="Высота окна"
            unit="м"
            min={0.3}
            max={4}
            allowEmpty
            value={win.height_m}
            onCommit={(v) => commit((draft) => { const w = draft.windows?.find((x) => x.id === id); if (w) w.height_m = v ?? undefined; })}
          />
        )}
        {wall && <ReadonlyField label="Стена" value={`${book.byId.get(key('wall', wall.id)) ?? ''} · ${formatNumber(wallLength(wall))} м`} />}
      </div>
      <div className="props__actions">
        <button type="button" className="action action--danger" onClick={removeSelection}>
          <Trash2 size={15} aria-hidden="true" />
          {door ? 'Удалить дверь' : 'Удалить окно'}
          <kbd>Del</kbd>
        </button>
      </div>
    </div>
  );
}
