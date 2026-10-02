/**
 * Стеллаж библиотеки: инструменты, оборудование по группам перечня ОПС, слои плана.
 */

import { BrickWall, ChevronRight, MousePointer2, PenLine, Pencil, Plus, Search, SquareDashed, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { Layers } from '../canvas/PlanCanvas';
import { glyphFor } from '../canvas/glyphs';
import type { AddressBook } from '../lib/addresses';
import type { EquipmentTypeInfo, ZoneTypeInfo } from '../lib/plan';
import { CustomTypeForm } from './CustomType';
import { Maintenance } from './Maintenance';
import { formatNumber } from '../lib/planFacts';
import { useWorkspace, type Tool } from '../state/store';
import './library.css';

interface LibraryProps {
  book: AddressBook;
  types: EquipmentTypeInfo[];
  zoneTypes: ZoneTypeInfo[];
  layers: Layers;
  onLayers: (layers: Layers) => void;
  onNotice: (text: string) => void;
  /** Показать объект на плане: в «Эксплуатации» — из списка сроков осмотра. */
  onPick: (id: string) => void;
}

const TOOLS: { tool: Tool; label: string; hotkey: string; icon: typeof MousePointer2 }[] = [
  { tool: 'select', label: 'Выбор', hotkey: 'V', icon: MousePointer2 },
  { tool: 'outline', label: 'Контур', hotkey: 'O', icon: PenLine },
  { tool: 'wall', label: 'Перегородка', hotkey: 'W', icon: BrickWall },
  { tool: 'zone', label: 'Зона', hotkey: 'Z', icon: SquareDashed },
];

// onlyOnPlan — слой рисуется только на чертеже; в объёме его переключатель ничего не меняет.
const LAYER_LABELS: { key: keyof Layers; label: string; onlyWithHeatmap?: boolean; onlyInDesign?: boolean; onlyOnPlan?: boolean }[] = [
  // Метки нарушений нужны там, где план правят; в эксплуатации план — паспорт, а не чертёж.
  { key: 'findings', label: 'Метки нарушений', onlyInDesign: true, onlyOnPlan: true },
  { key: 'zones', label: 'Зоны' },
  { key: 'labels', label: 'Адреса и подписи' },
  { key: 'routes', label: 'Маршруты посетителей' },
  { key: 'grid', label: 'Сетка 25 см', onlyOnPlan: true },
  { key: 'rulers', label: 'Линейки', onlyOnPlan: true },
  // Тепловая карта показывается в списке слоёв только после разбора записи с камеры.
  { key: 'heat', label: 'Тепловая карта камеры', onlyWithHeatmap: true },
];

export function Library({ book, types, zoneTypes, layers, onLayers, onNotice, onPick }: LibraryProps) {
  const tool = useWorkspace((s) => s.tool);
  const heatmap = useWorkspace((s) => s.heatmap);
  const placingType = useWorkspace((s) => s.placingType);
  const mode = useWorkspace((s) => s.mode);
  const view = useWorkspace((s) => s.view);
  const setTool = useWorkspace((s) => s.setTool);
  const doc = useWorkspace((s) => s.doc);
  const commit = useWorkspace((s) => s.commit);
  const [query, setQuery] = useState('');
  const [form, setForm] = useState<{ edited: EquipmentTypeInfo | null } | null>(null);

  const custom = useMemo(
    () => new Set((doc?.dictionaries?.equipment_types ?? []).map((t) => t.code)),
    [doc?.dictionaries?.equipment_types],
  );

  const removeType = (type: EquipmentTypeInfo) => {
    const used = (doc?.equipment ?? []).filter((e) => e.type === type.code).length;
    if (used > 0) {
      onNotice(`На плане ${used} ${used === 1 ? 'объект' : 'объектов'} этого типа — сначала удалите их с плана.`);
      return;
    }
    commit((plan) => {
      const types = (plan.dictionaries?.equipment_types ?? []).filter((t) => t.code !== type.code);
      plan.dictionaries = { ...(plan.dictionaries ?? {}), equipment_types: types };
    });
    onNotice(`Предмет «${type.label}» убран из библиотеки. Отменить — Ctrl+Z`);
  };

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const map = new Map<string, EquipmentTypeInfo[]>();
    for (const type of types) {
      if (needle && !type.label.toLowerCase().includes(needle)) continue;
      const list = map.get(type.group) ?? [];
      list.push(type);
      map.set(type.group, list);
    }
    return [...map.entries()];
  }, [types, query]);

  const design = mode === 'design';
  // Открыта одна группа — та, с которой обычно начинают; остальные по щелчку. При поиске
  // открыты все найденные: иначе совпадение пряталось бы в свёрнутой группе.
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set(types[0] ? [types[0].group] : []));
  const toggleGroup = (group: string) =>
    setOpenGroups((open) => {
      const next = new Set(open);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });

  return (
    <aside className="library on-steel" aria-label="Инструменты и библиотека оборудования">
      {design && (
        <div className="library__tools" role="toolbar" aria-label="Инструменты">
          {TOOLS.map(({ tool: t, label, hotkey, icon: Icon }) => (
            <button
              key={t}
              type="button"
              className={`tool${tool === t ? ' tool--active' : ''}`}
              aria-pressed={tool === t}
              onClick={() => setTool(t)}
              title={`${label} — клавиша ${hotkey}`}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
              <kbd>{hotkey}</kbd>
            </button>
          ))}
        </div>
      )}

      {design && (
        <div className="library__body">
          <label className="library__search">
            <Search size={16} aria-hidden="true" />
            <span className="visually-hidden">Найти оборудование</span>
            <input value={query} placeholder="Найти оборудование" onChange={(event) => setQuery(event.target.value)} />
          </label>

          {form ? (
            <CustomTypeForm
              edited={form.edited}
              zoneTypes={zoneTypes}
              onDone={(code) => {
                setForm(null);
                if (code) {
                  onNotice(form.edited ? 'Предмет изменён' : 'Предмет добавлен. Щёлкните по плану, чтобы поставить его');
                  if (!form.edited) setTool('equipment', code);
                }
              }}
            />
          ) : (
            <button type="button" className="library__add" onClick={() => setForm({ edited: null })}>
              <Plus size={15} aria-hidden="true" />
              Свой предмет
            </button>
          )}

          <div className="library__groups">
            {groups.length === 0 && (
              <p className="library__empty">
                В перечне такого нет. Проверьте написание или добавьте его кнопкой «Свой предмет» — он
                появится в библиотеке этого плана.
              </p>
            )}
            {groups.map(([group, list]) => (
              <section key={group} className="library__group">
                <h3>
                  <button
                    type="button"
                    className="library__group-toggle"
                    aria-expanded={Boolean(query.trim()) || openGroups.has(group)}
                    onClick={() => toggleGroup(group)}
                  >
                    <ChevronRight size={13} aria-hidden="true" className="library__chevron" />
                    {group}
                    <span className="library__group-count">{list.length}</span>
                  </button>
                </h3>
                {(Boolean(query.trim()) || openGroups.has(group)) && (
                <ul>
                  {list.map((type) => {
                    const Glyph = glyphFor(type.code);
                    const active = tool === 'equipment' && placingType === type.code;
                    const own = custom.has(type.code);
                    return (
                      <li key={type.code} className={own ? 'library__own' : undefined}>
                        <button
                          type="button"
                          className={`item${active ? ' item--active' : ''}`}
                          aria-pressed={active}
                          onClick={() => setTool(active ? 'select' : 'equipment', active ? null : type.code)}
                          title={active ? 'Щёлкните по плану, чтобы поставить. Esc — отмена.' : `Поставить на план: ${type.label}`}
                        >
                          <span className="item__glyph">
                            <Glyph size={16} aria-hidden="true" />
                          </span>
                          <span className="item__label">{type.label}</span>
                          <span className="item__size">
                            {type.floor_object
                              ? `${formatNumber(type.default_size_m[0])}×${formatNumber(type.default_size_m[1])}`
                              : 'на стене'}
                          </span>
                        </button>
                        {own && (
                          <span className="library__own-actions">
                            <button type="button" className="library__icon" title="Изменить предмет" onClick={() => setForm({ edited: type })}>
                              <Pencil size={13} aria-hidden="true" />
                              <span className="visually-hidden">Изменить {type.label}</span>
                            </button>
                            <button type="button" className="library__icon" title="Убрать из библиотеки" onClick={() => removeType(type)}>
                              <Trash2 size={13} aria-hidden="true" />
                              <span className="visually-hidden">Убрать {type.label}</span>
                            </button>
                          </span>
                        )}
                      </li>
                    );
                  })}
                </ul>
                )}
              </section>
            ))}
          </div>
        </div>
      )}

      {!design && (
        <div className="library__body library__operate">
          <Maintenance book={book} types={types} onPick={onPick} />
        </div>
      )}

      {/* Слои нужны изредка: свёрнуты, чтобы библиотеке хватало высоты. */}
      <details className="library__layers">
        <summary>
          <ChevronRight size={13} aria-hidden="true" className="library__chevron" />
          Слои
        </summary>
        {LAYER_LABELS.filter(
          (layer) =>
            (!layer.onlyWithHeatmap || heatmap) && (!layer.onlyInDesign || mode === 'design') && (!layer.onlyOnPlan || view === '2d'),
        ).map(({ key: k, label }) => (
          <label key={k} className="layer">
            <input type="checkbox" checked={layers[k]} onChange={(event) => onLayers({ ...layers, [k]: event.target.checked })} />
            <span className="layer__box" aria-hidden="true" />
            {label}
          </label>
        ))}
      </details>
    </aside>
  );
}
