/**
 * Свой тип оборудования. Хранится в справочнике самого плана, поэтому уезжает вместе с
 * файлом и не требует ничего менять в коде: проверки, поиск мест и инвентаризация видят его
 * наравне с типами из перечня ОПС.
 */

import { Check, X } from 'lucide-react';
import { useState } from 'react';
import { NumberField, SelectField, TextField } from '../components/fields';
import type { EquipmentTypeDef, EquipmentTypeInfo, ZoneTypeInfo } from '../lib/plan';
import { useWorkspace } from '../state/store';

interface CustomTypeFormProps {
  /** Редактируемый тип; если не задан — создаётся новый. */
  edited: EquipmentTypeInfo | null;
  zoneTypes: ZoneTypeInfo[];
  onDone: (code: string | null) => void;
}

interface Draft {
  label: string;
  width: number;
  depth: number;
  placement: 'floor' | 'wall';
  zones: string[];
  operator: boolean;
  capacity: number | null;
}

const TRANSLIT: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'i',
  к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f',
  х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
};

export function codeFor(label: string, taken: Set<string>): string {
  const slug =
    [...label.toLowerCase()]
      .map((char) => TRANSLIT[char] ?? (/[a-z0-9]/.test(char) ? char : '_'))
      .join('')
      .replace(/_+/g, '_')
      .replace(/^_|_$/g, '')
      .slice(0, 28) || 'type';
  let code = slug;
  let index = 2;
  while (taken.has(code)) code = `${slug}_${index++}`;
  return code;
}

export function CustomTypeForm({ edited, zoneTypes, onDone }: CustomTypeFormProps) {
  const doc = useWorkspace((s) => s.doc);
  const commit = useWorkspace((s) => s.commit);
  const [draft, setDraft] = useState<Draft>({
    label: edited?.label ?? '',
    width: edited?.default_size_m[0] ?? 0.6,
    depth: edited?.default_size_m[1] ?? 0.6,
    placement: edited ? (edited.floor_object ? 'floor' : 'wall') : 'floor',
    zones: edited?.allowed_zones ?? [],
    operator: edited?.operator_workplace ?? false,
    capacity: edited?.capacity_persons ?? null,
  });
  const [error, setError] = useState<string | null>(null);

  const set = <K extends keyof Draft>(field: K, value: Draft[K]) => setDraft((current) => ({ ...current, [field]: value }));

  const save = () => {
    const label = draft.label.trim();
    if (!label) {
      setError('Укажите название.');
      return;
    }
    const existing = doc?.dictionaries?.equipment_types ?? [];
    const duplicate = existing.some((t) => t.code !== edited?.code && t.label.toLowerCase() === label.toLowerCase());
    if (duplicate) {
      setError('Такой предмет уже есть в справочнике.');
      return;
    }
    const code = edited?.code ?? codeFor(label, new Set(existing.map((t) => t.code)));
    const definition: EquipmentTypeDef = {
      code,
      label,
      group: 'Свои предметы',
      default_size_m: [draft.width, draft.depth],
      floor_object: draft.placement === 'floor',
      allowed_zones: draft.zones,
      operator_workplace: draft.operator,
      ...(draft.capacity !== null ? { capacity_persons: Math.round(draft.capacity) } : {}),
    };
    commit((plan) => {
      const dictionaries = plan.dictionaries ?? {};
      const types = [...(dictionaries.equipment_types ?? [])];
      const index = types.findIndex((t) => t.code === code);
      if (index === -1) types.push(definition);
      else types[index] = definition;
      plan.dictionaries = { ...dictionaries, equipment_types: types };
    });
    onDone(code);
  };

  return (
    <div className="custom-type">
      <h3>{edited ? 'Изменить предмет' : 'Свой предмет'}</h3>
      <div className="fields">
        <TextField label="Название" wide value={draft.label} error={error ?? undefined} onCommit={(label) => { setDraft((d) => ({ ...d, label })); setError(null); }} live />
        <NumberField label="Ширина" unit="м" min={0.05} max={30} step={0.1} value={draft.width} onCommit={(v) => v !== null && set('width', v)} />
        <NumberField label="Глубина" unit="м" min={0.05} max={30} step={0.1} value={draft.depth} onCommit={(v) => v !== null && set('depth', v)} />
        <SelectField
          label="Размещение"
          wide
          value={draft.placement}
          options={[
            { value: 'floor', label: 'На полу — занимает площадь' },
            { value: 'wall', label: 'На стене или на столе' },
          ]}
          onCommit={(value) => set('placement', value as Draft['placement'])}
        />
        <NumberField
          label="Вместимость"
          unit="чел."
          step={1}
          min={0}
          allowEmpty
          value={draft.capacity}
          hint="Для мест ожидания"
          onCommit={(value) => set('capacity', value)}
        />
      </div>

      <label className="check">
        <input type="checkbox" checked={draft.operator} onChange={(event) => set('operator', event.target.checked)} />
        <span className="check__box" aria-hidden="true" />
        Рабочее место с сотрудником
      </label>

      <fieldset className="custom-type__zones">
        <legend>Где можно ставить</legend>
        {zoneTypes.map((zone) => (
          <label key={zone.code} className="check">
            <input
              type="checkbox"
              checked={draft.zones.includes(zone.code)}
              onChange={(event) =>
                set('zones', event.target.checked ? [...draft.zones, zone.code] : draft.zones.filter((c) => c !== zone.code))
              }
            />
            <span className="check__box" aria-hidden="true" />
            {zone.label}
          </label>
        ))}
        <p className="custom-type__hint">Ничего не отмечено — ассистент ставит предмет в любой доступной зоне.</p>
      </fieldset>

      <div className="custom-type__actions">
        <button type="button" className="btn btn--steel-light" onClick={save}>
          <Check size={15} aria-hidden="true" />
          {edited ? 'Сохранить' : 'Добавить в библиотеку'}
        </button>
        <button type="button" className="btn btn--outline" onClick={() => onDone(null)}>
          <X size={15} aria-hidden="true" />
          Отмена
        </button>
      </div>
    </div>
  );
}
