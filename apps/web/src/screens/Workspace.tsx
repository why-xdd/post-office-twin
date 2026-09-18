/**
 * Рабочее пространство: рейка, стеллаж библиотеки, лист плана, колонка ячеек-вкладок,
 * строка состояния. План остаётся на месте при переключении режима — меняются только
 * панель справа и то, что делает щелчок по объекту.
 */

import {
  ArrowLeft,
  Box,
  ChevronDown,
  CornerDownLeft,
  Download,
  Image,
  Keyboard,
  LoaderCircle,
  Maximize,
  PanelsTopLeft,
  Redo2,
  Save,
  Undo2,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { PlanCanvas, type Layers } from '../canvas/PlanCanvas';
import { Mark } from '../components/Mark';
import { ADDRESS_KIND_LABEL, buildAddressBook, parseAddress } from '../lib/addresses';
import { ApiError, api, type PlanVersion } from '../lib/api';
import type { View } from '../lib/geometry';
import type { EquipmentTypeInfo, Point, Rule, ZoneTypeInfo } from '../lib/plan';
import { formatNumber, planArea, plural } from '../lib/planFacts';
import { sortFindings } from '../lib/plainFindings';
import { withTransform } from '../lib/equipmentOps';
import { fitsRoom } from '../lib/room';
import { navigate } from '../lib/router';
import { clearDraft, readDraft, saveDraft } from '../lib/draft';
import { downloadJson } from '../lib/download';
import { downloadPlanSvg } from '../lib/planSvg';
import { Assistant } from '../panels/Assistant';
import { Checks } from '../panels/Checks';
import { Inventory } from '../panels/Inventory';
import { Library } from '../panels/Library';
import { Passport } from '../panels/Passport';
import { Properties } from '../panels/Properties';
import { Review } from '../panels/Review';
import { isDirty, useWorkspace, type Mode, type SidePanel } from '../state/store';
import './workspace.css';

interface WorkspaceProps {
  planId: string;
}

type LoadState = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; message: string };

const VALIDATE_DELAY_MS = 450;

const HOTKEYS: [string, string][] = [
  ['V · W · O · Z', 'выбор · перегородка · контур · зона'],
  ['Ctrl + S', 'сохранить версию'],
  ['Ctrl + Z · Ctrl + Y', 'отменить · повторить'],
  ['Ctrl + D', 'дублировать объект'],
  ['R · Shift + R', 'повернуть на 90° · на 15°'],
  ['Стрелки · Shift + стрелки', 'сдвинуть на 5 см · на 25 см'],
  ['Ctrl при перетаскивании', 'без привязки к стенам'],
  ['Del', 'удалить выделенное'],
  ['F', 'вписать план в экран'],
  ['/', 'перейти по адресу'],
  ['Esc', 'отменить действие, снять выделение'],
];

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
  const reviewErrors = useWorkspace((s) => s.report?.errors ?? 0);
  const previewing = useWorkspace((s) => s.preview !== null);

  const [load, setLoad] = useState<LoadState>({ kind: 'loading' });
  const [types, setTypes] = useState<EquipmentTypeInfo[]>([]);
  const [zoneTypes, setZoneTypes] = useState<ZoneTypeInfo[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [layers, setLayers] = useState<Layers>({ zones: true, labels: true, routes: false, grid: true });
  const [fitSignal, setFitSignal] = useState(0);
  const [focus, setFocus] = useState<{ id: string; nonce: number } | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [cursor, setCursor] = useState<Point | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [addressText, setAddressText] = useState('');
  const [addressError, setAddressError] = useState<string | null>(null);
  const [versions, setVersions] = useState<PlanVersion[] | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const [draft, setDraft] = useState<{ savedAt: number } | null>(null);
  const [hotkeys, setHotkeys] = useState(false);
  const addressInput = useRef<HTMLInputElement>(null);

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
  const toolHint =
    tool === 'outline'
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
        ];

  const area = planArea(doc);

  return (
    <div className={`workspace workspace--${mode}`}>
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
          <span className="ws-rail__name">{doc.ops.name || title}</span>
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
          <form className={`address${addressError ? ' address--error' : ''}`} onSubmit={goToAddress} role="search">
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
              aria-describedby="address-hint"
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
          </form>
        )}

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

        <div className="ws-rail__tools">
          {!review && (
            <>
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
              <button
                type="button"
                className="icon-btn"
                onClick={() => {
                  downloadJson(doc.ops.name || title, doc);
                  setNotice('План сохранён файлом в загрузки');
                }}
                title="Скачать план файлом JSON"
              >
                <Download size={17} aria-hidden="true" />
                <span className="visually-hidden">Скачать план</span>
              </button>
              <button
                type="button"
                className="icon-btn"
                onClick={() => {
                  downloadPlanSvg(doc);
                  setNotice('Чертёж сохранён файлом SVG в загрузки');
                }}
                title="Скачать чертёж картинкой SVG"
              >
                <Image size={17} aria-hidden="true" />
                <span className="visually-hidden">Скачать чертёж</span>
              </button>
              <button type="button" className="icon-btn" onClick={() => setHotkeys((open) => !open)} title="Клавиши — ?">
                <Keyboard size={17} aria-hidden="true" />
                <span className="visually-hidden">Горячие клавиши</span>
              </button>
            </>
          )}
          <button type="button" className="btn btn--label ws-rail__save" onClick={() => void save()} disabled={saving || !dirty}>
            {saving ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
            {dirty ? 'Сохранить версию' : 'Сохранено'}
          </button>
        </div>
      </header>

      {!review && <Library types={types} zoneTypes={zoneTypes} layers={layers} onLayers={setLayers} onNotice={setNotice} />}

      <main className="ws-sheet" aria-label="Лист плана">
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
        {mode === 'design' && toolHint && (
          <p className="ws-hint" role="note">
            {toolHint}
          </p>
        )}
        {review && reviewErrors > 0 && (
          <p className="ws-hint" role="note">
            {previewing
              ? 'Пунктирные стрелки — куда переедут объекты'
              : 'Красные номера — нарушения норм. Нажмите на номер, чтобы узнать, что не так'}
          </p>
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
            <Review book={book} saving={saving} onSave={() => void save()} onFocusObject={focusObject} onNotice={setNotice} />
          )}
          {panel === 'checks' && <Checks book={book} onFocusObject={focusObject} />}
          {panel === 'properties' && <Properties book={book} types={typeMap} zoneTypes={zoneTypes} />}
          {panel === 'assistant' && <Assistant book={book} types={types} onFocusObject={focusObject} onNotice={setNotice} />}
          {panel === 'inventory' && <Inventory book={book} types={typeMap} onFocusObject={focusObject} />}
          {panel === 'passport' && <Passport book={book} types={typeMap} />}
        </div>
      </aside>

      {!review && (
        <footer className="ws-status on-steel">
          <span>
            <PanelsTopLeft size={13} aria-hidden="true" /> {cursor ? `X ${formatNumber(cursor[0])} · Y ${formatNumber(cursor[1])} м` : 'Курсор вне плана'}
          </span>
          <span>Масштаб {view ? `${Math.round((view.scale / 60) * 100)}%` : '—'}</span>
          <span>Площадь {formatNumber(area)} м²</span>
          <span>
            <Box size={13} aria-hidden="true" /> {doc.equipment?.length ?? 0}{' '}
            {plural(doc.equipment?.length ?? 0, 'объект', 'объекта', 'объектов')}, {doc.zones?.length ?? 0}{' '}
            {plural(doc.zones?.length ?? 0, 'зона', 'зоны', 'зон')}
          </span>
          {selection && (
            <span>
              Выбрано: {book.byId.get(`${selection.kind}:${selection.id}`)} — {ADDRESS_KIND_LABEL[selection.kind]}
            </span>
          )}
          <span className="ws-status__hint">Колесо — масштаб · перетаскивание пустого места — сдвиг · / — адрес</span>
        </footer>
      )}

      <div className="ws-narrow on-steel" role="note">
        <Mark size={32} />
        <p>Редактор плана рассчитан на экран шире 1024 пикселей. Откройте его на компьютере.</p>
      </div>
    </div>
  );
}
