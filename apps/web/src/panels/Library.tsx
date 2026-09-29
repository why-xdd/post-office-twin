/**
 * Стеллаж библиотеки: инструменты, оборудование по группам перечня ОПС, слои плана.
 */

import { BrickWall, MousePointer2, PenLine, Pencil, Plus, Search, SquareDashed, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';
import type { Layers } from '../canvas/PlanCanvas';
import { glyphFor } from '../canvas/glyphs';
import type { EquipmentTypeInfo, ZoneTypeInfo } from '../lib/plan';
import { CustomTypeForm } from './CustomType';
import { formatNumber } from '../lib/planFacts';
import { useWorkspace, type Tool } from '../state/store';
import './library.css';

interface LibraryProps {
  types: EquipmentTypeInfo[];
  zoneTypes: ZoneTypeInfo[];
  layers: Layers;
  onLayers: (layers: Layers) => void;
  onNotice: (text: string) => void;
}

const TOOLS: { tool: Tool; label: string; hotkey: string; icon: typeof MousePointer2 }[] = [
  { tool: 'select', label: 'Выбор', hotkey: 'V', icon: MousePointer2 },
  { tool: 'outline', label: 'Контур', hotkey: 'O', icon: PenLine },
  { tool: 'wall', label: 'Перегородка', hotkey: 'W', icon: BrickWall },
  { tool: 'zone', label: 'Зона', hotkey: 'Z', icon: SquareDashed },
];

const LAYER_LABELS: { key: keyof Layers; label: string; onlyWithHeatmap?: boolean }[] = [
  { key: 'zones', label: 'Зоны' },
  { key: 'labels', label: 'Адреса и подписи' },
  { key: 'routes', label: 'Маршруты посетителей' },
  { key: 'grid', label: 'Сетка 25 см' },
  // Тепловая карта показывается в списке слоёв только после разбора записи с камеры.
  { key: 'heat', label: 'Тепловая карта камеры', onlyWithHeatmap: true },
];

export function Library({ types, zoneTypes, layers, onLayers, onNotice }: LibraryProps) {
  const tool = useWorkspace((s) => s.tool);
  const heatmap = useWorkspace((s) => s.heatmap);
  const placingType = useWorkspace((s) => s.placingType);
  const mode = useWorkspace((s) => s.mode);
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
            {groups.length === 0 && <p className="library__empty">Ничего не найдено. Проверьте написание.</p>}
            {groups.map(([group, list]) => (
              <section key={group} className="library__group">
                <h3>{group}</h3>
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
              </section>
            ))}
          </div>
        </div>
      )}

      {!design && (
        <div className="library__body library__operate">
          <p>
            Режим эксплуатации: план открыт только для просмотра. Выберите объект, чтобы открыть его паспорт —
            ответственного, учётные данные и сроки обслуживания.
          </p>
        </div>
      )}

      <fieldset className="library__layers">
        <legend>Слои</legend>
        {LAYER_LABELS.filter((layer) => !layer.onlyWithHeatmap || heatmap).map(({ key: k, label }) => (
          <label key={k} className="layer">
            <input type="checkbox" checked={layers[k]} onChange={(event) => onLayers({ ...layers, [k]: event.target.checked })} />
            <span className="layer__box" aria-hidden="true" />
            {label}
          </label>
        ))}
      </fieldset>
    </aside>
  );
}
