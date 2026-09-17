/**
 * Рабочее пространство: рейка, стеллаж библиотеки, лист плана, колонка ячеек-вкладок,
 * строка состояния. План остаётся на месте при переключении режима — меняются только
 * панель справа и то, что делает щелчок по объекту.
 */

import {
  ArrowLeft,
  Box,
  CornerDownLeft,
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
import { ApiError, api } from '../lib/api';
import type { View } from '../lib/geometry';
import type { EquipmentTypeInfo, Point, Rule, ZoneTypeInfo } from '../lib/plan';
import { formatNumber, planArea, plural } from '../lib/planFacts';
import { sortFindings } from '../lib/plainFindings';
import { navigate } from '../lib/router';
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
  const select = useWorkspace((s) => s.select);
  const highlight = useWorkspace((s) => s.highlight);
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
        setRules(ruleList);
        setLoad({ kind: 'ready' });
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

  // Проверка по нормам после каждой правки
  useEffect(() => {
    if (!doc) return;
    let cancelled = false;
    setReport(useWorkspace.getState().report, 'checking');
    const timer = window.setTimeout(async () => {
      try {
        const result = await api.validate(doc);
        // Порядок задаёт и номера меток, и адреса Н-01…: они совпадают с тем, что видно в списке.
        result.findings = sortFindings(result.findings);
        if (!cancelled) setReport(result, 'ready');
      } catch {
        if (!cancelled) setReport(useWorkspace.getState().report, 'error');
      }
    }, VALIDATE_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [doc, setReport]);

  const typeMap = useMemo(() => new Map(types.map((t) => [t.code, t])), [types]);
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
      const ref = await api.savePlan(state.planId, state.doc, 'Сохранено из редактора');
      markSaved(ref.id, ref.version);
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
      if (event.key === '/') {
        event.preventDefault();
        addressInput.current?.focus();
        return;
      }
      if (event.key === 'Escape') {
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
      if (event.code === 'KeyR' && !mod) {
        state.updateEquipment(item.id, { rotation_deg: ((item.rotation_deg ?? 0) + (event.shiftKey ? 15 : 90)) % 360 });
        return;
      }
      if (mod && event.code === 'KeyD') {
        event.preventDefault();
        const copyId = `${item.type}_${Date.now().toString(36)}`;
        state.commit((draft) => {
          draft.equipment = [...(draft.equipment ?? []), { ...structuredClone(item), id: copyId, position: [item.position[0] + 0.5, item.position[1] - 0.5] }];
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
        state.updateEquipment(item.id, {
          position: [Math.round((item.position[0] + step[0]) * 1000) / 1000, Math.round((item.position[1] + step[1]) * 1000) / 1000],
        });
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
    if (selection && mode === 'design' && panel !== 'checks' && panel !== 'assistant' && panel !== 'inventory') setPanel('properties');
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
          onClick={() => {
            if (!dirty || window.confirm('Есть несохранённые изменения. Уйти к стеллажу без сохранения?')) {
              navigate({ name: 'rack' });
            }
          }}
          title="К стеллажу планов"
        >
          <ArrowLeft size={16} aria-hidden="true" />
          <Mark size={22} />
        </button>

        <div className="ws-rail__title">
          <span className="ws-rail__name">{title}</span>
          <span className="ws-rail__version">
            {dirty ? 'Есть несохранённые изменения' : `Версия ${version}`}
          </span>
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
            </>
          )}
          <button type="button" className="btn btn--label ws-rail__save" onClick={() => void save()} disabled={saving || !dirty}>
            {saving ? <LoaderCircle size={16} className="spin" aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
            {dirty ? 'Сохранить версию' : 'Сохранено'}
          </button>
        </div>
      </header>

      {!review && <Library types={types} layers={layers} onLayers={setLayers} />}

      <main className="ws-sheet" aria-label="Лист плана">
        <PlanCanvas
          types={typeMap}
          book={book}
          layers={layers}
          fitSignal={fitSignal}
          focus={focus}
          onViewChange={setView}
          onCursor={setCursor}
        />
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
