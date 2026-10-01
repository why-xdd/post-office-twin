/**
 * Рабочее пространство: рейка, стеллаж библиотеки, лист плана, колонка ячеек-вкладок,
 * строка состояния. План остаётся на месте при переключении режима — меняются только
 * панель справа и то, что делает щелчок по объекту.
 *
 * На узком экране те же части собираются иначе: план занимает весь экран, а библиотека и
 * панели приходят снизу выдвижным листом по кнопкам нижней планки — там, куда достаёт большой
 * палец. Раскладка одна и та же, разметка тоже: меняется только то, как она разложена.
 */

import {
  ArrowLeft,
  Box,
  Boxes,
  CalendarClock,
  Check,
  ChevronDown,
  ClipboardList,
  CornerDownLeft,
  Ellipsis,
  FileJson,
  IdCard,
  ImageDown,
  Keyboard,
  ListChecks,
  LoaderCircle,
  Maximize,
  PanelsTopLeft,
  Redo2,
  Save,
  SlidersHorizontal,
  Sparkles,
  Undo2,
  Video,
  Wand2,
  X,
} from 'lucide-react';
import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { PlanCanvas, type Layers } from '../canvas/PlanCanvas';
import { ErrorBoundary, isStaleChunk } from '../components/ErrorBoundary';
import { Mark } from '../components/Mark';
import { MoreMenu } from '../components/MoreMenu';
import { ADDRESS_KIND_LABEL, ADDRESS_PREFIX, buildAddressBook, parseAddress, type AddressKind } from '../lib/addresses';
import { ApiError, api, type PlanVersion } from '../lib/api';
import type { View } from '../lib/geometry';
import type { EquipmentTypeInfo, Point, Rule, ZoneTypeInfo } from '../lib/plan';
import { formatNumber, planArea, plural } from '../lib/planFacts';
import { plainFinding, sortFindings } from '../lib/plainFindings';
import { transformInPlace, withTransform } from '../lib/equipmentOps';
import { fitsRoom } from '../lib/room';
import { navigate } from '../lib/router';
import { clearDraft, readDraft, saveDraft } from '../lib/draft';
import { downloadJson } from '../lib/download';
import { downloadPlanSvg } from '../lib/planSvg';
import { Assistant } from '../panels/Assistant';
import { Checks } from '../panels/Checks';
import { Inventory } from '../panels/Inventory';
import { Library } from '../panels/Library';
import { Camera } from '../panels/Camera';
import { Passport } from '../panels/Passport';
import { Properties } from '../panels/Properties';
import { Review } from '../panels/Review';
import { isDirty, useWorkspace, type Mode, type SidePanel, type ViewKind } from '../state/store';
import './workspace.css';

interface WorkspaceProps {
  planId: string;
}

type LoadState = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; message: string };

const VALIDATE_DELAY_MS = 450;

// Объёмный вид тянет за собой библиотеку трёхмерной графики: она грузится только когда нужна,
// иначе редактор плана платил бы мегабайтом за то, чем пользуются изредка.
const Plan3D = lazy(() => import('../canvas/Plan3D').then((module) => ({ default: module.Plan3D })));

const VIEWS: { id: ViewKind; label: string; hint: string }[] = [
  { id: '2d', label: 'План', hint: 'Чертёж сверху: правка, зоны, размеры' },
  { id: '3d', label: 'Объём', hint: 'То же помещение в объёме: высоты, проёмы, маршруты' },
];

const HOTKEYS: [string, string][] = [
  ['V · W · O · Z', 'выбор · перегородка · контур · зона'],
  ['Ctrl + S', 'сохранить версию'],
  ['Ctrl + Z · Ctrl + Y', 'отменить · повторить'],
  ['Ctrl + D', 'дублировать объект'],
  ['R · Shift + R', 'повернуть на 90° · на 15°'],
  ['Стрелки · Shift + стрелки', 'сдвинуть на 5 см · на 25 см'],
  ['Ctrl при перетаскивании', 'без привязки к стенам'],
  ['Shift + щелчок', 'добавить объект к выделению'],
  ['Shift + рамка', 'выделить объекты рамкой'],
  ['Ctrl + A', 'выделить всё оборудование'],
  ['Del', 'удалить выделенное'],
  ['F', 'вписать план в экран'],
  ['/', 'перейти по адресу'],
  ['Esc', 'отменить действие, снять выделение'],
];

/** Схема адресов: что значит буква на табличке. Показывается под полем «Адрес», пока оно в фокусе. */
const ADDRESS_LEGEND: [AddressKind, string][] = [
  ['equipment', 'объект'],
  ['zone', 'зона'],
  ['wall', 'стена'],
  ['door', 'дверь'],
  ['window', 'окно'],
  ['finding', 'нарушение'],
  ['recommendation', 'рекомендация'],
  ['rule', 'пункт нормы'],
];

// Значки нижней планки: на узком экране подпись коротка, и знак помогает найти нужное быстрее.
const DOCK_ICONS: Record<SidePanel, ReactNode> = {
  review: <Wand2 size={18} aria-hidden="true" />,
  checks: <ListChecks size={18} aria-hidden="true" />,
  assistant: <Sparkles size={18} aria-hidden="true" />,
  properties: <SlidersHorizontal size={18} aria-hidden="true" />,
  inventory: <ClipboardList size={18} aria-hidden="true" />,
  passport: <IdCard size={18} aria-hidden="true" />,
  camera: <Video size={18} aria-hidden="true" />,
};

const MODES: { id: Mode; label: string; hint: string }[] = [
  { id: 'review', label: 'Обзор', hint: 'Нарушения и исправление в один шаг' },
  { id: 'design', label: 'Редактор', hint: 'Инструменты, свойства объектов, ассистент' },
  { id: 'operate', label: 'Эксплуатация', hint: 'Паспорта оборудования и инвентаризация' },
];

export function Workspace({ planId }: WorkspaceProps) {
  const open = useWorkspace((s) => s.open);
  const doc = useWorkspace((s) => s.doc);
  const title = useWorkspace((s) => s.title);
  const version = useWorkspace((s) => s.version);
  const mode = useWorkspace((s) => s.mode);
  const panel = useWorkspace((s) => s.panel);
  const report = useWorkspace((s) => s.report);
  const selection = useWorkspace((s) => s.selection);
  const past = useWorkspace((s) => s.past);
  const future = useWorkspace((s) => s.future);
  const dirty = useWorkspace(isDirty);
  const setMode = useWorkspace((s) => s.setMode);
  const setPanel = useWorkspace((s) => s.setPanel);
  const setReport = useWorkspace((s) => s.setReport);
  const undo = useWorkspace((s) => s.undo);
  const redo = useWorkspace((s) => s.redo);
  const markSaved = useWorkspace((s) => s.markSaved);
  const restoreVersion = useWorkspace((s) => s.restoreVersion);
  const select = useWorkspace((s) => s.select);
  const highlight = useWorkspace((s) => s.highlight);
  const tool = useWorkspace((s) => s.tool);
  const placingType = useWorkspace((s) => s.placingType);
  const reviewErrors = useWorkspace((s) => s.report?.errors ?? 0);
  const viewKind = useWorkspace((s) => s.view);
  const setViewKind = useWorkspace((s) => s.setView);
  const previewing = useWorkspace((s) => s.preview !== null);
  const activeFinding = useWorkspace((s) => s.activeFinding);
  const setActiveFinding = useWorkspace((s) => s.setActiveFinding);
  const group = useWorkspace((s) => s.group);

  const [load, setLoad] = useState<LoadState>({ kind: 'loading' });
  const [types, setTypes] = useState<EquipmentTypeInfo[]>([]);
  const [zoneTypes, setZoneTypes] = useState<ZoneTypeInfo[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [layers, setLayers] = useState<Layers>({ zones: true, labels: true, routes: false, grid: true, heat: true, findings: true, rulers: true });
  const [fitSignal, setFitSignal] = useState(0);
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  // Что выдвинуто снизу на узком экране: панель вкладки, библиотека или ничего.
  const [drawer, setDrawer] = useState<'panel' | 'library' | null>(null);
  // Пальцем или мышью: от этого зависит текст подсказок к инструментам.
  const touch = useMemo(
    () => typeof window !== 'undefined' && window.matchMedia('(pointer: coarse)').matches,
    [],
  );
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [addressText, setAddressText] = useState('');
  const [addressError, setAddressError] = useState<string | null>(null);
  const [versions, setVersions] = useState<PlanVersion[] | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [draft, setDraft] = useState<{ savedAt: number } | null>(null);
  const [hotkeys, setHotkeys] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [addressFocus, setAddressFocus] = useState(false);
  const addressInput = useRef<HTMLInputElement>(null);
  const more = useRef<HTMLDivElement>(null);

  // Загрузка плана
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [document, plans, ruleList] = await Promise.all([
          api.getPlan(planId),
          api.listPlans(),
          api.rules().catch(() => [] as Rule[]),
        ]);
        if (cancelled) return;
        const summary = plans.find((p) => p.id === planId);
        open(planId, summary?.title ?? document.ops.name ?? 'План', summary?.versions ?? 1, document);
        // Пустое помещение нечего проверять: оно открывается сразу в редакторе.
        if ((document.equipment ?? []).length === 0) useWorkspace.getState().setMode('design');
        setRules(ruleList);
        setLoad({ kind: 'ready' });
        // Незаконченная правка пережила перезагрузку: предлагаем вернуть её.
        const stored = readDraft(planId);
        if (stored && JSON.stringify(stored.document) !== JSON.stringify(document)) {
          setDraft({ savedAt: stored.savedAt });
        }
        const dictionaries = await api.dictionaries(document).catch(() => null);
        if (!cancelled && dictionaries) {
          setTypes(dictionaries.equipment_types);
          setZoneTypes(dictionaries.zone_types);
        }
      } catch (error) {
        if (!cancelled) {
          setLoad({
            kind: 'error',
            message: error instanceof ApiError ? error.message : 'Не удалось открыть план.',
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [planId, open]);

  // Черновик в браузере: правка переживает перезагрузку вкладки.
  useEffect(() => {
    if (!doc || load.kind !== 'ready') return;
    const timer = window.setTimeout(() => {
      if (isDirty(useWorkspace.getState())) saveDraft(planId, doc, version);
      else clearDraft(planId);
    }, 800);
    return () => window.clearTimeout(timer);
  }, [doc, planId, version, load.kind]);

  // Проверка по нормам после каждой правки
  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    // Правка идёт быстрее ответа сервера: прежний запрос отменяется, чтобы не занимать
    // ни сеть, ни расчёт на сервере ответом, который уже никому не нужен.
    const abort = new AbortController();
    setReport(useWorkspace.getState().report, 'checking');
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.validate(doc, {}, abort.signal);
        // Порядок задаёт и номера меток, и адреса Н-01…: они совпадают с тем, что видно в списке.
        result.findings = sortFindings(result.findings);
        if (!cancelled) setReport(result, 'ready');
      } catch (error) {
        if (!cancelled && !(error instanceof DOMException && error.name === 'AbortError')) {
          setReport(useWorkspace.getState().report, 'error');
        }
      }
    }, VALIDATE_DELAY_MS);
    return () => {
      cancelled = true;
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [doc, setReport]);

  // Свои типы лежат в самом плане: после их правки справочник берётся с сервера заново.
  const customTypes = doc?.dictionaries ? JSON.stringify(doc.dictionaries) : '';
  useEffect(() => {
    if (load.kind !== 'ready' || !doc) return;
    let cancelled = false;
    api
      .dictionaries(doc)
      .then((dictionaries) => {
        if (cancelled) return;
        setTypes(dictionaries.equipment_types);
        setZoneTypes(dictionaries.zone_types);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [customTypes, load.kind]);

  const typeMap = useMemo(() => new Map(types.map((t) => [t.code, t])), [types]);
  const typesRef = useRef(typeMap);
  typesRef.current = typeMap;
  const book = useMemo(
    () => buildAddressBook(doc, report?.findings ?? [], [], rules.map((r) => r.id)),
    [doc, report, rules],
  );

  const focusObject = useCallback((id: string) => setFocus({ id, nonce: Date.now() }), []);

  const save = useCallback(async () => {
    const state = useWorkspace.getState();
    if (!state.doc || !state.planId || saving) return;
    setSaving(true);
    try {
      const ref = await api.savePlan(state.planId, state.doc, 'Сохранено из редактора', state.doc.ops.name);
      markSaved(ref.id, ref.version);
      clearDraft(ref.id);
      setDraft(null);
      setVersions(null);
      setNotice(`Сохранено как версия ${ref.version}`);
    } catch (error) {
      setNotice(error instanceof ApiError ? `Не сохранено: ${error.message}` : 'Не сохранено: сервер недоступен');
    } finally {
      setSaving(false);
    }
  }, [markSaved, saving]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  // Выбрали инструмент или предмет в библиотеке — выдвинутый лист уходит: дальше работают по
  // плану, а на узком экране лист его закрывает.
  useEffect(() => {
    if (tool !== 'select' || placingType) setDrawer((open) => (open === 'library' ? null : open));
  }, [tool, placingType]);

  // На телефоне предпросмотр сжимает лист, и план вписывается в видимую часть уже после этого.
  useEffect(() => {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    const timer = window.setTimeout(() => setFitSignal((n) => n + 1), 80);
    return () => window.clearTimeout(timer);
  }, [previewing]);

  // Лист плана меняет ширину вместе с режимом: план вписывается заново, когда раскладка уже сменилась.
  useEffect(() => {
    if (load.kind !== 'ready') return;
    const frame = window.requestAnimationFrame(() => setFitSignal((n) => n + 1));
    return () => window.cancelAnimationFrame(frame);
  }, [mode, load.kind]);

  // Предупреждение о несохранённом
  useEffect(() => {
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      if (isDirty(useWorkspace.getState())) event.preventDefault();
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, []);

  // Горячие клавиши
  useEffect(() => {
    // Клавиши для группы: удалить, сдвинуть, дублировать. Поворот — только по одному объекту.
    const onGroupKey = (event: KeyboardEvent, mod: boolean, state: ReturnType<typeof useWorkspace.getState>) => {
      const doc = state.doc!;
      const members = state.group
        .map((id) => doc.equipment?.find((e) => e.id === id))
        .filter((member): member is NonNullable<typeof member> => Boolean(member));
      const count = members.length;
      const objects = `${count} ${plural(count, 'объект', 'объекта', 'объектов')}`;
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        state.removeSelection();
        setNotice(`Удалено ${objects}. Отменить — Ctrl+Z`);
        return;
      }
      if (event.code === 'KeyR' && !mod) {
        setNotice('Группу не повернуть: поверните объекты по одному');
        return;
      }
      if (mod && event.code === 'KeyD') {
        event.preventDefault();
        const stamp = Date.now().toString(36);
        const copies = members.map((member, index) => ({
          ...structuredClone(member),
          ...withTransform(member, { position: [member.position[0] + 0.5, member.position[1] - 0.5] }),
          id: `${member.type}_${stamp}${index}`,
        }));
        state.commit((draft) => {
          draft.equipment = [...(draft.equipment ?? []), ...copies];
        });
        state.setGroup(copies.map((copy) => copy.id));
        setNotice(`Скопировано ${objects}`);
        return;
      }
      const nudge = event.shiftKey ? 0.25 : 0.05;
      const delta: Record<string, [number, number]> = {
        ArrowLeft: [-nudge, 0],
        ArrowRight: [nudge, 0],
        ArrowUp: [0, nudge],
        ArrowDown: [0, -nudge],
      };
      const step = delta[event.key];
      if (!step) return;
      event.preventDefault();
      const moved = members.map((member) => ({
        member,
        position: [Math.round((member.position[0] + step[0]) * 1000) / 1000, Math.round((member.position[1] + step[1]) * 1000) / 1000] as [number, number],
      }));
      const stuck = moved.find(
        ({ member, position }) => (typesRef.current.get(member.type)?.floor_object ?? true) && !fitsRoom(doc, { ...member, position }),
      );
      if (stuck) {
        setNotice(`Группа упирается в стену: «${stuck.member.label || stuck.member.type}» дальше не пройдёт`);
        return;
      }
      state.commit((draft) => {
        for (const { member, position } of moved) {
          const target = draft.equipment?.find((e) => e.id === member.id);
          if (target) transformInPlace(target, { position });
        }
      });
    };

    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const typing = target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT';
      const mod = event.ctrlKey || event.metaKey;
      const state = useWorkspace.getState();

      if (mod && event.code === 'KeyS') {
        event.preventDefault();
        void save();
        return;
      }
      if (typing) return;

      if (mod && event.code === 'KeyZ' && !event.shiftKey) {
        event.preventDefault();
        state.undo();
        return;
      }
      if (mod && (event.code === 'KeyY' || (event.code === 'KeyZ' && event.shiftKey))) {
        event.preventDefault();
        state.redo();
        return;
      }
      if (event.key === '?' || (event.key === '/' && event.shiftKey)) {
        event.preventDefault();
        setHotkeys((open) => !open);
        return;
      }
      if (event.key === '/') {
        event.preventDefault();
        addressInput.current?.focus();
        return;
      }
      if (event.key === 'Escape') {
        setHotkeys(false);
        setMoreOpen(false);
        setLeaving(false);
        state.setActiveFinding(null);
        state.highlight([]);
        state.setPreview(null);
        state.setTool('select');
        state.select(null);
        return;
      }
      if (event.code === 'KeyF' && !mod) {
        setFitSignal((n) => n + 1);
        return;
      }
      if (state.mode !== 'design') return;
      if (event.code === 'KeyV') state.setTool('select');
      if (event.code === 'KeyW') state.setTool('wall');
      if (event.code === 'KeyO' && !mod) state.setTool('outline');
      if (event.code === 'KeyZ' && !mod) state.setTool('zone');

      if (mod && event.code === 'KeyA') {
        event.preventDefault();
        state.setGroup((state.doc?.equipment ?? []).map((e) => e.id));
        return;
      }
      if (state.group.length > 1 && state.doc) {
        onGroupKey(event, mod, state);
        return;
      }

      const sel = state.selection;
      if ((event.key === 'Delete' || event.key === 'Backspace') && sel) {
        event.preventDefault();
        state.removeSelection();
        setNotice('Удалено. Отменить — Ctrl+Z');
        return;
      }
      if (sel?.kind !== 'equipment' || !state.doc) return;
      const item = state.doc.equipment?.find((e) => e.id === sel.id);
      if (!item) return;
      const floor = typesRef.current.get(item.type)?.floor_object ?? true;
      const blocked = (patch: Partial<typeof item>) => floor && !fitsRoom(state.doc!, { ...item, ...patch });
      if (event.code === 'KeyR' && !mod) {
        const patch = { rotation_deg: ((item.rotation_deg ?? 0) + (event.shiftKey ? 15 : 90)) % 360 };
        if (blocked(patch)) {
          setNotice('Не повернуть: объект упрётся в стену. Отодвиньте его от стены.');
          return;
        }
        state.updateEquipment(item.id, patch);
        return;
      }
      if (mod && event.code === 'KeyD') {
        event.preventDefault();
        const copyId = `${item.type}_${Date.now().toString(36)}`;
        state.commit((draft) => {
          draft.equipment = [
            ...(draft.equipment ?? []),
            { ...structuredClone(item), ...withTransform(item, { position: [item.position[0] + 0.5, item.position[1] - 0.5] }), id: copyId },
          ];
        });
        state.select({ kind: 'equipment', id: copyId });
        return;
      }
      const nudge = event.shiftKey ? 0.25 : 0.05;
      const delta: Record<string, [number, number]> = {
        ArrowLeft: [-nudge, 0],
        ArrowRight: [nudge, 0],
        ArrowUp: [0, nudge],
        ArrowDown: [0, -nudge],
      };
      const step = delta[event.key];
      if (step) {
        event.preventDefault();
        const patch = {
          position: [Math.round((item.position[0] + step[0]) * 1000) / 1000, Math.round((item.position[1] + step[1]) * 1000) / 1000] as [number, number],
        };
        if (!blocked(patch)) state.updateEquipment(item.id, patch);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [save]);

  const goToAddress = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseAddress(addressText);
    if (!parsed) {
      setAddressError('Адрес вида О-07, З-02, Н-03');
      return;
    }
    const target = book.byAddress.get(parsed);
    if (!target) {
      setAddressError(`Ячейки ${parsed} на плане нет`);
      return;
    }
    setAddressError(null);
    setAddressText('');
    addressInput.current?.blur();
    if (target.kind === 'equipment' || target.kind === 'zone' || target.kind === 'wall' || target.kind === 'door') {
      select({ kind: target.kind, id: target.id });
      if (target.kind === 'equipment' || target.kind === 'zone') focusObject(target.id);
      if (mode === 'design') setPanel('properties');
      else if (target.kind === 'equipment') setPanel('passport');
    } else if (target.kind === 'finding') {
      const finding = report?.findings[Number(target.id)];
      setPanel('checks');
      if (finding) {
        highlight(finding.objects);
        if (finding.objects[0]) focusObject(finding.objects[0]);
      }
    } else if (target.kind === 'rule') {
      setPanel('checks');
    }
  };

  // При выборе объекта в проектировании показываем его свойства
  useEffect(() => {
    // Выбор на плане открывает свойства: иначе щелчок по объекту ничего не показывает.
    // Ассистент и инвентаризация остаются: там выбор — часть их собственного сценария.
    if (selection && mode === 'design' && panel !== 'assistant' && panel !== 'inventory') setPanel('properties');
    if (selection?.kind === 'equipment' && mode === 'operate') setPanel('passport');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection?.id]);

  if (load.kind === 'loading') {
    return (
      <div className="workspace-loading on-steel">
        <LoaderCircle size={22} className="spin" aria-hidden="true" />
        <p>Открываем план</p>
      </div>
    );
  }

  if (load.kind === 'error' || !doc) {
    return (
      <div className="workspace-loading on-steel">
        <p className="workspace-loading__title">{load.kind === 'error' ? load.message : 'План не найден.'}</p>
        <button type="button" className="btn btn--label" onClick={() => navigate({ name: 'rack' })}>
          <ArrowLeft size={16} aria-hidden="true" />К стеллажу планов
        </button>
      </div>
    );
  }

  const review = mode === 'review';
  // На сенсорном экране по плану не щёлкают и клавиш Esc и Shift там нет: подсказка другая.
  const toolHint = touch
    ? tool === 'outline'
      ? 'Отмечайте углы помещения. Замкнуть — касание первой точки'
      : tool === 'wall'
        ? 'Отметьте начало и конец перегородки'
        : tool === 'zone'
          ? 'Отмечайте вершины зоны. Замкнуть — двойное касание'
          : tool === 'equipment'
            ? 'Отметьте место на плане, чтобы поставить предмет'
            : null
    : tool === 'outline'
      ? 'Щёлкайте по углам помещения. Замкнуть — щелчок по первой точке или Enter, Backspace — убрать точку, Esc — отмена'
      : tool === 'wall'
        ? 'Щёлкните начало и конец перегородки. Shift — под любым углом, Esc — отмена'
        : tool === 'zone'
          ? 'Щёлкайте по вершинам зоны. Замкнуть — двойной щелчок или Enter, Esc — отмена'
          : tool === 'equipment'
            ? 'Щёлкните по плану, чтобы поставить. С Shift — поставить несколько, Esc — отмена'
            : selection?.kind === 'wall'
              ? 'Тяните синюю ручку, чтобы сдвинуть стену. Двойной щелчок по стене — дверь или окно'
              : null;
  const tabs: { id: SidePanel; label: string; count?: number; tone?: 'signal' }[] =
    review
      ? []
      : mode === 'design'
      ? [
          { id: 'checks', label: 'Проверка', count: report?.errors ?? undefined, tone: report && report.errors > 0 ? 'signal' : undefined },
          { id: 'assistant', label: 'Ассистент' },
          { id: 'properties', label: 'Свойства' },
          { id: 'inventory', label: 'Инвентарь' },
        ]
      : [
          { id: 'passport', label: 'Паспорт' },
          { id: 'inventory', label: 'Инвентарь' },
          { id: 'camera', label: 'Камера' },
        ];

  const area = planArea(doc);

  const peek = review && activeFinding !== null ? report?.findings[activeFinding] : undefined;

  const openPanel = (id: SidePanel) => {
    setPanel(id);
    setDrawer('panel');
  };

  return (
    <div className={`workspace workspace--${mode}${drawer ? ` workspace--drawer-${drawer}` : ''}${previewing ? ' workspace--previewing' : ''}`}>
      <header className="ws-rail on-steel">
        <button
          type="button"
          className="ws-rail__back"
          onClick={() => (dirty ? setLeaving(true) : navigate({ name: 'rack' }))}
          title="К стеллажу планов"
        >
          <ArrowLeft size={16} aria-hidden="true" />
          <Mark size={22} />
        </button>

        <div className="ws-rail__title">
          <span className="ws-rail__name" title={doc.ops.name || title}>
            {doc.ops.name || title}
          </span>
          <button
            type="button"
            className="ws-rail__version"
            aria-expanded={versionsOpen}
            onClick={() => {
              setVersionsOpen((open) => !open);
              if (!versions && planId) void api.versions(planId).then(setVersions).catch(() => setVersions([]));
            }}
          >
            {dirty ? 'Есть несохранённые изменения' : `Версия ${version}`}
            <ChevronDown size={13} aria-hidden="true" />
          </button>
          {versionsOpen && (
            <div className="versions" role="dialog" aria-label="Версии плана">
              <p className="versions__title">Сохранённые версии</p>
              {versions === null && <p className="versions__empty">Загружаем…</p>}
              {versions?.length === 0 && <p className="versions__empty">Версий пока нет.</p>}
              <ul>
                {versions?.map((item) => (
                  <li key={item.version}>
                    <span className="versions__number">Версия {item.version}</span>
                    <span className="versions__date">
                      {new Date(item.created_at).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}
                      {item.note ? ` · ${item.note}` : ''}
                    </span>
                    {item.version === version ? (
                      <span className="versions__current">открыта</span>
                    ) : (
                      <button
                        type="button"
                        onClick={async () => {
                          if (!planId) return;
                          try {
                            const document = await api.getPlan(planId, item.version);
                            restoreVersion(document);
                            setVersionsOpen(false);
                            setNotice(`Открыта версия ${item.version}. Сохранить — Ctrl+S, отменить — Ctrl+Z`);
                          } catch {
                            setNotice('Не удалось открыть версию: сервер не ответил');
                          }
                        }}
                      >
                        Открыть
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {!review && (
          <form
            className={`address${addressError ? ' address--error' : ''}`}
            onSubmit={goToAddress}
            role="search"
            onFocus={() => setAddressFocus(true)}
            onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setAddressFocus(false);
            }}
          >
            <label htmlFor="address-input" className="address__label">
              Адрес
            </label>
            <input
              id="address-input"
              ref={addressInput}
              value={addressText}
              placeholder="О-07"
              autoComplete="off"
              spellCheck={false}
              aria-describedby={addressFocus && !addressError ? 'address-hint address-legend' : 'address-hint'}
              onChange={(event) => {
                setAddressText(event.target.value);
                setAddressError(null);
              }}
            />
            <kbd aria-hidden="true">/</kbd>
            <button type="submit" className="address__go" aria-label="Перейти к адресу">
              <CornerDownLeft size={14} aria-hidden="true" />
            </button>
            <span id="address-hint" className="address__hint" role="status">
              {addressError ?? ''}
            </span>
            {addressFocus && !addressError && (
              <div id="address-legend" className="address__legend">
                <p className="address__legend-title">Буква на табличке — что это</p>
                <dl>
                  {ADDRESS_LEGEND.map(([kind, what]) => (
                    <div key={kind}>
                      <dt>{ADDRESS_PREFIX[kind]}</dt>
                      <dd>{what}</dd>
                    </div>
                  ))}
                </dl>
                <p className="address__legend-foot">Например, О-07 — седьмой объект. Enter — показать на плане.</p>
              </div>
            )}
          </form>
        )}

        {/* Перенос строки рейки на узком экране: переключатели уходят под название плана. */}
        <span className="ws-rail__break" aria-hidden="true" />

        <div className="segmented" role="radiogroup" aria-label="Режим">
          {MODES.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={mode === item.id}
              title={item.hint}
              onClick={() => setMode(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="segmented" role="radiogroup" aria-label="Вид плана">
          {VIEWS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="radio"
              aria-checked={viewKind === item.id}
              title={item.hint}
              onClick={() => setViewKind(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="ws-rail__tools">
          {!review && (
            <>
              {/* На планшете стеллаж библиотеки уезжает за край: эта кнопка возвращает его. */}
              <button
                type="button"
                className="icon-btn ws-rail__library"
                aria-pressed={drawer === 'library'}
                onClick={() => setDrawer(drawer === 'library' ? null : 'library')}
                title={mode === 'operate' ? 'Сроки осмотра и слои' : 'Библиотека оборудования'}
              >
                {mode === 'operate' ? <CalendarClock size={17} aria-hidden="true" /> : <Boxes size={17} aria-hidden="true" />}
                <span className="visually-hidden">{mode === 'operate' ? 'Сроки осмотра и слои' : 'Библиотека оборудования'}</span>
              </button>
              <button type="button" className="icon-btn" onClick={undo} disabled={past.length === 0} title="Отменить — Ctrl+Z">
                <Undo2 size={17} aria-hidden="true" />
                <span className="visually-hidden">Отменить</span>
              </button>
              <button type="button" className="icon-btn" onClick={redo} disabled={future.length === 0} title="Повторить — Ctrl+Y">
                <Redo2 size={17} aria-hidden="true" />
                <span className="visually-hidden">Повторить</span>
              </button>
              <button type="button" className="icon-btn" onClick={() => setFitSignal((n) => n + 1)} title="Вписать план — F">
                <Maximize size={17} aria-hidden="true" />
                <span className="visually-hidden">Вписать план</span>
              </button>
              <div className="ws-more" ref={more}>
                <button
                  type="button"
                  className="icon-btn"
                  aria-haspopup="menu"
                  aria-expanded={moreOpen}
                  aria-controls="ws-more-menu"
                  onClick={() => setMoreOpen((open) => !open)}
                  title="Ещё: скачать план, клавиши"
                >
                  <Ellipsis size={17} aria-hidden="true" />
                  <span className="visually-hidden">Ещё</span>
                </button>
                {moreOpen && (
                  <MoreMenu
                    id="ws-more-menu"
                    onClose={(refocus) => {
                      setMoreOpen(false);
                      if (refocus) more.current?.querySelector<HTMLButtonElement>('.icon-btn')?.focus();
                    }}
                    container={more}
                    items={[
                      {
                        icon: <FileJson size={16} aria-hidden="true" />,
                        label: 'Скачать план',
                        note: 'JSON',
                        run: () => {
                          downloadJson(doc.ops.name || title, doc);
                          setNotice('План сохранён файлом в загрузки');
                        },
                      },
                      {
                        icon: <ImageDown size={16} aria-hidden="true" />,
                        label: 'Скачать чертёж',
                        note: 'SVG',
                        run: () => {
                          downloadPlanSvg(doc);
                          setNotice('Чертёж сохранён файлом SVG в загрузки');
                        },
                      },
                      // Клавиши нужны там, где есть клавиатура: на телефоне пункт только мешает.
                      ...(touch
                        ? []
                        : [
                            {
                              icon: <Keyboard size={16} aria-hidden="true" />,
                              label: 'Горячие клавиши',
                              note: '?',
                              run: () => setHotkeys(true),
                            },
                          ]),
                    ]}
                  />
                )}
              </div>
            </>
          )}
          {/* Сохранённое состояние — тихая строка, а не кнопка: действие появляется, когда есть что сохранять. */}
          <button
            type="button"
            className={`btn ws-rail__save${dirty || saving ? ' btn--label' : ' ws-rail__save--saved'}`}
            onClick={() => void save()}
            disabled={saving || !dirty}
            title={dirty ? 'Сохранить версию — Ctrl+S' : 'Все изменения сохранены'}
          >
            {saving ? (
              <LoaderCircle size={16} className="spin" aria-hidden="true" />
            ) : dirty ? (
              <Save size={16} aria-hidden="true" />
            ) : (
              <Check size={16} aria-hidden="true" />
            )}
            <span className="ws-rail__save-label">{saving ? 'Сохраняем' : dirty ? 'Сохранить' : 'Сохранено'}</span>
          </button>
        </div>
      </header>

      {!review && (
        <Library
          book={book}
          types={types}
          zoneTypes={zoneTypes}
          layers={layers}
          onLayers={setLayers}
          onNotice={setNotice}
          onPick={(id) => {
            select({ kind: 'equipment', id });
            focusObject(id);
            // На узком экране лист со списком уходит: объект и его паспорт должны быть видны.
            setDrawer(null);
          }}
        />
      )}

      <main className="ws-sheet" aria-label="Лист плана">
        {viewKind === '3d' ? (
          <ErrorBoundary
            fallback={(error, retry) => (
              <div className="ws-fallback" role="alert">
                <p className="ws-fallback__title">Объёмный вид не открылся</p>
                <p>
                  {isStaleChunk(error)
                    ? 'Сервис обновился, пока страница была открыта. Перезагрузите страницу — план сохранён.'
                    : 'План при этом не пострадал: вернитесь к чертежу и продолжайте работу.'}
                </p>
                <div className="ws-fallback__actions">
                  <button type="button" className="btn btn--label" onClick={() => setViewKind('2d')}>
                    Вернуться к плану
                  </button>
                  <button type="button" className="btn btn--outline" onClick={isStaleChunk(error) ? () => window.location.reload() : retry}>
                    {isStaleChunk(error) ? 'Перезагрузить страницу' : 'Попробовать снова'}
                  </button>
                </div>
              </div>
            )}
          >
            <Suspense fallback={<p className="ws-hint">Загружается объёмный вид…</p>}>
              <Plan3D types={typeMap} />
            </Suspense>
          </ErrorBoundary>
        ) : (
          <PlanCanvas
            types={typeMap}
            book={book}
            layers={layers}
            fitSignal={fitSignal}
            focus={focus}
            onViewChange={setView}
            onCursor={setCursor}
            onNotice={setNotice}
          />
        )}
        {viewKind === '2d' && mode === 'design' && toolHint && (
          <p className="ws-hint" role="note">
            {toolHint}
          </p>
        )}
        {viewKind === '2d' && review && reviewErrors > 0 && !peek && (
          <p className="ws-hint" role="note">
            {previewing
              ? 'Пунктирные стрелки — куда переедут объекты'
              : 'Красные номера — нарушения норм. Нажмите на номер, чтобы узнать, что не так'}
          </p>
        )}
        {/* Телефон: метку нажали — коротко, что не так, прямо над планом. Лист обзора план закрыл бы. */}
        {viewKind === '2d' && peek && drawer === null && (
          <section className={`ws-peek ws-peek--${peek.severity}`} aria-label="Нарушение на плане">
            <span className="ws-peek__pin" aria-hidden="true">
              {peek.severity === 'error' ? (activeFinding ?? 0) + 1 : '!'}
            </span>
            <div className="ws-peek__text">
              <p className="ws-peek__title">{plainFinding(peek, doc).title}</p>
              {plainFinding(peek, doc).measure && <p className="ws-peek__measure">{plainFinding(peek, doc).measure}</p>}
              <button type="button" className="btn btn--steel ws-peek__more" onClick={() => openPanel('review')}>
                Подробнее и исправление
              </button>
            </div>
            <button
              type="button"
              className="ws-peek__close"
              aria-label="Закрыть"
              onClick={() => {
                setActiveFinding(null);
                highlight([]);
              }}
            >
              <X size={18} aria-hidden="true" />
            </button>
          </section>
        )}
        {notice && (
          <div className="ws-notice" role="status">
            {notice}
          </div>
        )}
        {draft && (
          <div className="ws-confirm" role="alertdialog" aria-label="Незаконченная правка">
            <p>
              В этом браузере остались несохранённые изменения от{' '}
              {new Date(draft.savedAt).toLocaleString('ru-RU', { day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit' })}.
            </p>
            <div className="ws-confirm__actions">
              <button
                type="button"
                className="btn btn--label"
                onClick={() => {
                  const stored = readDraft(planId);
                  if (stored) restoreVersion(stored.document);
                  setDraft(null);
                  setNotice('Черновик восстановлен. Отменить — Ctrl+Z');
                }}
              >
                Вернуть изменения
              </button>
              <button
                type="button"
                className="btn btn--steel"
                onClick={() => {
                  clearDraft(planId);
                  setDraft(null);
                }}
              >
                Открыть сохранённую версию
              </button>
            </div>
          </div>
        )}
        {hotkeys && (
          <div className="ws-hotkeys" role="dialog" aria-label="Горячие клавиши">
            <p className="ws-hotkeys__title">Клавиши</p>
            <dl>
              {HOTKEYS.map(([keys, what]) => (
                <div key={keys}>
                  <dt>{keys}</dt>
                  <dd>{what}</dd>
                </div>
              ))}
            </dl>
            <button type="button" className="btn btn--steel" onClick={() => setHotkeys(false)}>
              Закрыть
            </button>
          </div>
        )}
        {leaving && (
          <div className="ws-confirm" role="alertdialog" aria-label="Несохранённые изменения">
            <p>Изменения не сохранены. Уйти к списку планов?</p>
            <div className="ws-confirm__actions">
              <button type="button" className="btn btn--label" onClick={() => void save().then(() => navigate({ name: 'rack' }))}>
                Сохранить и выйти
              </button>
              <button type="button" className="btn btn--steel" onClick={() => navigate({ name: 'rack' })}>
                Выйти без сохранения
              </button>
              <button type="button" className="btn btn--steel" onClick={() => setLeaving(false)}>
                Остаться
              </button>
            </div>
          </div>
        )}
      </main>

      <aside className="ws-column" aria-label="Панели">
        {!review && (
          <div className="ws-tabs" role="tablist" aria-label="Панели справа">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                role="tab"
                aria-selected={panel === tab.id}
                className="ws-tab"
                onClick={() => setPanel(tab.id)}
              >
                {tab.label}
                {tab.count !== undefined && <span className={`ws-tab__count${tab.tone ? ' ws-tab__count--signal' : ''}`}>{tab.count}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="ws-panel" role={review ? 'region' : 'tabpanel'} aria-label={review ? 'Обзор плана' : undefined}>
          {panel === 'review' && (
            <Review
              book={book}
              saving={saving}
              onSave={() => void save()}
              onFocusObject={focusObject}
              onFit={() => setFitSignal((n) => n + 1)}
              onNotice={setNotice}
            />
          )}
          {panel === 'checks' && <Checks book={book} onFocusObject={focusObject} />}
          {panel === 'properties' && <Properties book={book} types={typeMap} zoneTypes={zoneTypes} />}
          {panel === 'assistant' && <Assistant book={book} types={types} onFocusObject={focusObject} onNotice={setNotice} />}
          {panel === 'inventory' && <Inventory book={book} types={typeMap} onFocusObject={focusObject} />}
          {panel === 'passport' && <Passport book={book} types={typeMap} />}
          {panel === 'camera' && <Camera />}
        </div>
      </aside>

      {!review && (
        <footer className="ws-status on-steel">
          {/* Курсор и масштаб относятся к чертежу: в объёме их нет. */}
          {viewKind === '2d' && (
            <>
              <span>
                <PanelsTopLeft size={13} aria-hidden="true" /> {cursor ? `X ${formatNumber(cursor[0])} · Y ${formatNumber(cursor[1])} м` : 'Курсор вне плана'}
              </span>
              <span>Масштаб {view ? `${Math.round((view.scale / 60) * 100)}%` : '—'}</span>
            </>
          )}
          <span>Площадь {formatNumber(area)} м²</span>
          <span>
            <Box size={13} aria-hidden="true" /> {doc.equipment?.length ?? 0}{' '}
            {plural(doc.equipment?.length ?? 0, 'объект', 'объекта', 'объектов')}, {doc.zones?.length ?? 0}{' '}
            {plural(doc.zones?.length ?? 0, 'зона', 'зоны', 'зон')}
          </span>
          {group.length > 1 ? (
            <span>
              Выбрано: {group.length} {plural(group.length, 'объект', 'объекта', 'объектов')}
            </span>
          ) : (
            selection && (
              <span>
                Выбрано: {book.byId.get(`${selection.kind}:${selection.id}`)} — {ADDRESS_KIND_LABEL[selection.kind]}
              </span>
            )
          )}
          <span className="ws-status__hint">
            {viewKind === '3d'
              ? 'Перетаскивание — поворот · колесо — приближение · правая кнопка — сдвиг'
              : 'Колесо — масштаб · перетаскивание пустого места — сдвиг · / — адрес'}
          </span>
        </footer>
      )}

      {/* Затемнение над выдвинутым листом: касание мимо листа закрывает его, как принято. */}
      {drawer && <button type="button" className="ws-scrim" aria-label="Закрыть панель" onClick={() => setDrawer(null)} />}

      {/* Нижняя планка узкого экрана: план, панели, библиотека — в пределах большого пальца. */}
      <nav className="ws-dock on-steel" aria-label="Разделы">
        <button
          type="button"
          className="ws-dock__item"
          aria-current={drawer === null}
          onClick={() => setDrawer(null)}
        >
          <PanelsTopLeft size={18} aria-hidden="true" />
          <span className="ws-dock__label">План</span>
        </button>
        {review ? (
          <button
            type="button"
            className="ws-dock__item"
            aria-current={drawer === 'panel'}
            onClick={() => openPanel('review')}
          >
            <Wand2 size={18} aria-hidden="true" />
            <span className="ws-dock__label">Обзор</span>
            {reviewErrors > 0 && <span className="ws-dock__count">{reviewErrors}</span>}
          </button>
        ) : (
          <>
            <button
              type="button"
              className="ws-dock__item"
              aria-current={drawer === 'library'}
              onClick={() => setDrawer(drawer === 'library' ? null : 'library')}
            >
              {/* В «Эксплуатации» в этом листе не оборудование, а сроки осмотра и слои. */}
              {mode === 'operate' ? <CalendarClock size={18} aria-hidden="true" /> : <Boxes size={18} aria-hidden="true" />}
              <span className="ws-dock__label">{mode === 'operate' ? 'Сроки' : 'Библиотека'}</span>
            </button>
            {tabs.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className="ws-dock__item"
                aria-current={drawer === 'panel' && panel === tab.id}
                onClick={() => openPanel(tab.id)}
              >
                {DOCK_ICONS[tab.id]}
                <span className="ws-dock__label">{tab.label}</span>
                {tab.count !== undefined && tab.count > 0 && <span className="ws-dock__count">{tab.count}</span>}
              </button>
            ))}
          </>
        )}
      </nav>
    </div>
  );
}
