/**
 * Нарушение простыми словами — для обзора плана.
 *
 * Сервер пишет полные тексты с идентификаторами и пунктами норм: это нужно редактору и
 * отчёту. В обзоре человеку нужно другое — что не так, насколько и что мешает, одной-двумя
 * строками. Полный текст и ссылка на норму остаются под «Подробнее».
 */

import type { Finding, Plan, Severity } from './plan';
import { formatNumber } from './planFacts';

/**
 * Узкое место пути, при котором проход ещё допустим с карманами для разъезда (СП 59.13330.2020).
 * То же число, что MIN_PATH_WITH_POCKETS_M на сервере: ниже него путь — ошибка, выше — замечание.
 */
const PATH_WITH_POCKETS_M = '1,2';

export interface PlainFinding {
  title: string;
  /** Замер против нормы, если он есть. */
  measure: string | null;
  /** Чем это плохо для посетителя или сотрудника. */
  impact: string;
  /** Названия объектов, из-за которых возникло нарушение. */
  culprits: string;
}

interface Wording {
  title: string;
  /** Чем это плохо. Для замечания часто мягче, чем для ошибки: нарушение и допущение — разное. */
  impact: string | ((severity: Severity) => string);
  measure?: (measured: string, required: string, unit: string, severity: Severity) => string;
}

const WORDING: Record<string, Wording> = {
  movement_path_width: {
    // Замер — самое узкое место среди путей от входа к окнам и терминалам: так и называем.
    title: 'Узкое место на пути к окнам',
    // Ошибка и замечание по этому правилу — разные вещи: уже 1,2 м коляска не проезжает,
    // от 1,2 до нормы проезжает, но двоим не разойтись без карманов для разъезда.
    impact: (severity) =>
      severity === 'error'
        ? 'Посетитель на кресле-коляске не доберётся до окон.'
        : 'Коляска проедет, но двоим не разойтись без карманов для разъезда.',
    measure: (m, r, _unit, severity) =>
      severity === 'error'
        ? `${m} м при норме ${r} м — даже с карманами для разъезда нужно ${PATH_WITH_POCKETS_M} м`
        : `${m} м при норме ${r} м — допустимо только с карманами для разъезда`,
  },
  passage_between_workplaces: {
    title: 'Тесный проход между рабочими местами',
    impact: 'Сотрудникам трудно разойтись, мешает эвакуации.',
  },
  no_overlaps: {
    title: 'Объекты стоят друг на друге',
    impact: 'Так расставить оборудование в зале невозможно.',
    measure: () => 'габариты пересекаются',
  },
  objects_inside_outline: {
    title: 'Объект выходит за стены',
    impact: 'Объект не помещается в помещение.',
  },
  unique_ids: {
    title: 'Повторяются номера объектов',
    impact: 'В учёте два объекта с одним номером.',
  },
  zone_links: {
    title: 'Объект записан не в ту зону',
    impact: 'Инвентаризация по зонам будет неверной.',
  },
  zone_allowed_for_type: {
    title: 'Объект стоит не в своей зоне',
    impact: 'По справочнику этому оборудованию место в другой зоне.',
  },
  clearance_from_wall: {
    title: 'Рабочее место прижато к стене',
    impact: 'Сотруднику не хватает места за рабочим местом.',
    measure: (m, r) => `${m} м от стены при норме ${r} м`,
  },
  workplace_area: {
    title: 'Мало места на рабочем месте',
    impact: 'Площадь меньше отраслевой нормы.',
  },
  client_area_per_workplace: {
    title: 'Тесный зал для посетителей',
    impact: 'В часы пик посетителям негде стоять.',
    measure: (m, r) => `${m} м² на окно при норме ${r} м²`,
  },
  wheelchair_turning_circle: {
    title: 'Коляске негде развернуться',
    impact: 'Нет свободного круга диаметром 1,4 м.',
  },
  counter_height: {
    title: 'Слишком высокая стойка',
    impact: 'Посетителю на коляске неудобно у стойки.',
  },
  counter_front_width: {
    title: 'Узкий подход к стойке',
    impact: 'Посетителю на коляске не подъехать к стойке.',
  },
  door_maneuver_space: {
    title: 'Место перед дверью занято',
    impact: 'Перед дверью нужна свободная площадка 1,5 × 1,5 м.',
    measure: () => 'площадка 1,5 × 1,5 м занята',
  },
  doors_not_blocked: {
    title: 'Загорожена дверь',
    impact: 'Через проём не пройти: объект или место оператора стоит в двери.',
    measure: () => 'проём перекрыт',
  },
  queue_terminal_near_entrance: {
    title: 'Терминал очереди далеко от входа',
    impact: 'Посетитель не сразу находит, где взять талон.',
    measure: (m, r) => `${m} м от входа при норме не дальше ${r} м`,
  },
  ramp_width: {
    title: 'Узкий пандус',
    impact: 'Коляска не проедет по пандусу.',
  },
  coverage_per_service_window: {
    title: 'Не хватает окон обслуживания',
    impact: 'Для числа жителей района окон меньше нормы.',
  },
};

function inSentence(name: string): string {
  return name.length > 1 && name[0] === name[0].toUpperCase() && name[1] === name[1].toLowerCase()
    ? name[0].toLowerCase() + name.slice(1)
    : name;
}

export function objectName(plan: Plan | null, id: string): string | null {
  const item = plan?.equipment?.find((e) => e.id === id);
  if (item) return inSentence(item.label ?? item.type);
  const zone = plan?.zones?.find((z) => z.id === id);
  if (zone) return inSentence(zone.label ?? zone.type);
  return null;
}

function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] ?? '';
  return `${names.slice(0, -1).join(', ')} и ${names[names.length - 1]}`;
}

export function plainFinding(finding: Finding, plan: Plan | null): PlainFinding {
  const wording = WORDING[finding.rule_id];
  const names = finding.objects.map((id) => objectName(plan, id)).filter((n): n is string => Boolean(n));
  const hasNumbers = finding.measured !== null && finding.measured !== undefined;
  const measured = hasNumbers ? formatNumber(finding.measured) : '';
  const required = finding.required !== null && finding.required !== undefined ? formatNumber(finding.required) : '';
  let measure: string | null = null;
  if (wording?.measure) {
    measure = wording.measure(measured, required, finding.unit, finding.severity);
  } else if (hasNumbers && required) {
    measure = `${measured} ${finding.unit} при норме ${required} ${finding.unit}`;
  }
  const impact = typeof wording?.impact === 'function' ? wording.impact(finding.severity) : (wording?.impact ?? '');
  return {
    title: wording?.title ?? finding.title,
    measure,
    impact,
    culprits: joinNames(names),
  };
}

/** Нарушение нормы — не то же, что ошибка в данных плана: их считают и называют отдельно. */
export function isNormFinding(finding: Finding): boolean {
  return finding.category !== 'geometry';
}

/**
 * Порядок показа: сначала то, что не пускает посетителя, затем рабочие места и площади,
 * затем ошибки данных. Внутри серьёзности порядок одинаков в списке, на метках и в адресах.
 */
const RULE_ORDER = [
  'movement_path_width',
  'wheelchair_turning_circle',
  'door_maneuver_space',
  'doors_not_blocked',
  'counter_front_width',
  'counter_height',
  'ramp_width',
  'passage_between_workplaces',
  'clearance_from_wall',
  'workplace_area',
  'client_area_per_workplace',
  'queue_terminal_near_entrance',
  'coverage_per_service_window',
  'no_overlaps',
  'objects_inside_outline',
  'unique_ids',
  'zone_links',
  'zone_allowed_for_type',
];

const SEVERITY_ORDER = { error: 0, warning: 1, info: 2 } as const;

export function sortFindings(findings: Finding[]): Finding[] {
  const rank = (id: string) => {
    const position = RULE_ORDER.indexOf(id);
    return position === -1 ? RULE_ORDER.length : position;
  };
  return [...findings].sort(
    (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] || rank(a.rule_id) - rank(b.rule_id),
  );
}
