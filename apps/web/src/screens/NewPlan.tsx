/**
 * Мастер нового помещения: форма, размеры, вход и служебная зона. Предпросмотр
 * перерисовывается при каждом корректном изменении, ошибки показываются у своих полей.
 * Всё, что мастер не умеет, дорабатывается потом на плане: стены тянутся за ручку, двери
 * и окна ставятся двойным щелчком по стене.
 */

import { ArrowLeft, LoaderCircle, Plus } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';
import { NumberField, SelectField, TextField } from '../components/fields';
import { Mark } from '../components/Mark';
import { PlanThumb } from '../components/PlanThumb';
import { ServerBadge } from '../components/ServerBadge';
import { ApiError, api } from '../lib/api';
import { formatNumber } from '../lib/planFacts';
import {
  CORNER_LABEL,
  DEFAULT_ROOM,
  SIDE_LABEL,
  buildRoom,
  roomArea,
  validateRoom,
  type Corner,
  type RoomSpec,
  type Side,
} from '../lib/roomBuilder';
import { navigate } from '../lib/router';
import { useServer } from '../lib/useServer';
import '../components/fields.css';
import './rack.css';
import './new-plan.css';

const SIDES = (Object.keys(SIDE_LABEL) as Side[]).map((value) => ({ value, label: SIDE_LABEL[value] }));
const CORNERS = (Object.keys(CORNER_LABEL) as Corner[]).map((value) => ({ value, label: CORNER_LABEL[value] }));

export function NewPlan() {
  const server = useServer();
  const [spec, setSpec] = useState<RoomSpec>(DEFAULT_ROOM);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const errors = useMemo(() => validateRoom(spec), [spec]);
  const valid = Object.keys(errors).length === 0;
  // Пока форма с ошибкой, показывается последнее корректное помещение, а не пустой лист.
  const [lastValid, setLastValid] = useState<RoomSpec>(DEFAULT_ROOM);
  if (valid && lastValid !== spec) setLastValid(spec);
  const preview = useMemo(() => buildRoom(lastValid), [lastValid]);

  const set = <K extends keyof RoomSpec>(field: K, value: RoomSpec[K]) => setSpec((current) => ({ ...current, [field]: value }));
  const number = (field: keyof RoomSpec) => (value: number | null) => value !== null && set(field, value as never);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      const ref = await api.createPlan(spec.name.trim(), buildRoom(spec), 'Создан мастером помещения');
      navigate({ name: 'plan', id: ref.id });
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : 'Не удалось создать план: сервер не ответил.');
      setBusy(false);
    }
  };

  return (
    <div className="rack-screen">
      <header className="rail on-steel">
        <a className="rail__brand" href="#/" aria-label="План ОПС — к списку планов">
          <Mark />
          <span>План ОПС</span>
        </a>
        <ServerBadge state={server.state} onRetry={server.retry} />
      </header>

      <main className="new-plan on-steel">
        <a className="new-plan__back" href="#/">
          <ArrowLeft size={16} aria-hidden="true" />
          К планам
        </a>
        <h1>Новое помещение</h1>
        <p className="new-plan__lead">
          Задайте размеры и вход — стены, зоны и двери появятся сами. Остальное поправите на плане: стену можно
          тянуть за ручку, дверь или окно — поставить двойным щелчком по стене.
        </p>

        <div className="new-plan__body">
          <form className="new-plan__form" onSubmit={submit} noValidate>
            <section className="new-plan__section" aria-labelledby="new-plan-0">
              <h2 id="new-plan-0">Отделение</h2>
              <div className="fields">
                <TextField label="Название" wide live value={spec.name} error={errors.name} onCommit={(name) => set('name', name)} />
                <NumberField
                  label="Население пункта"
                  unit="тыс. чел."
                  step={1}
                  min={0}
                  allowEmpty
                  live
                  wide
                  value={spec.population}
                  hint="Необязательно. Нужно для проверки числа окон по Приказу № 538"
                  onCommit={(value) => set('population', value)}
                />
              </div>
            </section>

            <section className="new-plan__section" aria-labelledby="new-plan-1">
              <h2 id="new-plan-1">Форма и размеры</h2>
              <div className="segmented segmented--paper" role="radiogroup" aria-label="Форма помещения">
                {(
                  [
                    ['rect', 'Прямоугольник'],
                    ['l', 'Г-образное'],
                  ] as const
                ).map(([value, label]) => (
                  <button key={value} type="button" role="radio" aria-checked={spec.shape === value} onClick={() => set('shape', value)}>
                    {label}
                  </button>
                ))}
              </div>
              <div className="fields">
                <NumberField label="Ширина" unit="м" step={0.5} live value={spec.width} error={errors.width} onCommit={number('width')} />
                <NumberField label="Глубина" unit="м" step={0.5} live value={spec.depth} error={errors.depth} onCommit={number('depth')} />
                {spec.shape === 'l' && (
                  <>
                    <SelectField label="Вырез в углу" wide value={spec.cutCorner} options={CORNERS} onCommit={(value) => set('cutCorner', value as Corner)} />
                    <NumberField label="Ширина выреза" unit="м" step={0.5} live value={spec.cutWidth} error={errors.cutWidth} onCommit={number('cutWidth')} />
                    <NumberField label="Глубина выреза" unit="м" step={0.5} live value={spec.cutDepth} error={errors.cutDepth} onCommit={number('cutDepth')} />
                  </>
                )}
              </div>
              <p className="new-plan__area">
                Площадь помещения <b>{valid ? formatNumber(roomArea(spec), 1) : '—'} м²</b>
              </p>
            </section>

            <section className="new-plan__section" aria-labelledby="new-plan-2">
              <h2 id="new-plan-2">Вход для посетителей</h2>
              <div className="fields">
                <SelectField label="Стена" wide value={spec.entranceSide} options={SIDES} error={errors.entranceSide} onCommit={(value) => set('entranceSide', value as Side)} />
                <NumberField
                  label="Отступ от угла"
                  unit="м"
                  step={0.25}
                  live
                  value={spec.entranceOffset}
                  error={errors.entranceOffset}
                  hint={spec.entranceSide === 'left' || spec.entranceSide === 'right' ? 'От нижнего угла' : 'От левого угла'}
                  onCommit={number('entranceOffset')}
                />
                <NumberField label="Ширина проёма" unit="м" step={0.1} live value={spec.entranceWidth} error={errors.entranceWidth} hint="Для МГН — не меньше 0,9 м" onCommit={number('entranceWidth')} />
              </div>
            </section>

            <section className="new-plan__section" aria-labelledby="new-plan-3">
              <h2 id="new-plan-3">Служебная зона</h2>
              <label className="check">
                <input type="checkbox" checked={spec.service} onChange={(event) => set('service', event.target.checked)} />
                <span className="check__box" aria-hidden="true" />
                Отделить перегородкой
              </label>
              {spec.service && (
                <div className="fields">
                  <SelectField label="У стены" value={spec.serviceSide} options={SIDES} error={errors.serviceSide} onCommit={(value) => set('serviceSide', value as Side)} />
                  <NumberField label="Глубина зоны" unit="м" step={0.5} live value={spec.serviceDepth} error={errors.serviceDepth} onCommit={number('serviceDepth')} />
                  <label className="check field--wide">
                    <input type="checkbox" checked={spec.serviceDoor} onChange={(event) => set('serviceDoor', event.target.checked)} />
                    <span className="check__box" aria-hidden="true" />
                    Служебный вход с улицы
                  </label>
                </div>
              )}
            </section>

            {problem && (
              <p className="new-plan__problem" role="alert">
                {problem}
              </p>
            )}

            <div className="new-plan__actions">
              <button type="submit" className="btn btn--steel new-plan__submit" disabled={!valid || busy || server.state !== 'ready'}>
                {busy ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <Plus size={18} aria-hidden="true" />}
                Создать план
              </button>
              {!valid && <span className="new-plan__invalid">Исправьте отмеченные поля</span>}
            </div>
          </form>

          <figure className="new-plan__preview">
            <div className="cabinet__sheet new-plan__sheet">
              <PlanThumb plan={preview} report={null} showSize />
            </div>
            <figcaption>
              Предпросмотр. Толстая линия — наружные стены, тонкая — перегородка, разрывы — двери.
            </figcaption>
          </figure>
        </div>
      </main>
    </div>
  );
}
