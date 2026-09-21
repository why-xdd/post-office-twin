/**
 * Импорт чертежа: картинка или PDF — в черновик плана.
 *
 * Сервис распознаёт стены, проёмы и перегородки, а масштаб берёт из одного числа, которое
 * называет человек: действительной ширины помещения. Результат показывается рядом с исходным
 * чертежом вместе со списком того, что стоит проверить, — и только по кнопке становится планом.
 */

import { ArrowLeft, FileUp, LoaderCircle, Plus, TriangleAlert } from 'lucide-react';
import { useRef, useState, type DragEvent } from 'react';
import { NumberField, TextField } from '../components/fields';
import { Mark } from '../components/Mark';
import { PlanThumb } from '../components/PlanThumb';
import { ServerBadge } from '../components/ServerBadge';
import { ApiError, api } from '../lib/api';
import type { ImportResult } from '../lib/plan';
import { navigate } from '../lib/router';
import { useServer } from '../lib/useServer';
import '../components/fields.css';
import './rack.css';
import './new-plan.css';
import './import.css';

const MAX_FILE_MB = 8;

export function ImportDrawing() {
  const server = useServer();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [width, setWidth] = useState(12);
  const [name, setName] = useState('Отделение с чертежа');
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState<'read' | 'create' | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const accept = (chosen: File | undefined) => {
    if (!chosen) return;
    if (chosen.size > MAX_FILE_MB * 1024 * 1024) {
      setProblem(`Файл больше ${MAX_FILE_MB} МБ. Уменьшите изображение и попробуйте снова.`);
      return;
    }
    setProblem(null);
    setResult(null);
    setFile(chosen);
    setPreview((current) => {
      if (current) URL.revokeObjectURL(current);
      return chosen.type === 'application/pdf' ? null : URL.createObjectURL(chosen);
    });
  };

  const read = async () => {
    if (!file) return;
    setBusy('read');
    setProblem(null);
    try {
      const answer = await api.importDrawing(file, width, name);
      setResult(answer);
      if (!answer.document?.ops) setProblem(answer.notes[0] ?? 'Чертёж не распознан.');
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : 'Сервер не ответил. Попробуйте ещё раз.');
    } finally {
      setBusy(null);
    }
  };

  const create = async () => {
    if (!result?.document?.ops) return;
    setBusy('create');
    try {
      const ref = await api.createPlan(name.trim() || 'План с чертежа', result.document, 'Импорт чертежа');
      navigate({ name: 'plan', id: ref.id });
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : 'Не удалось создать план.');
      setBusy(null);
    }
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    accept(event.dataTransfer.files?.[0]);
  };

  const draft = result?.document?.ops ? result.document : null;

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
          <ArrowLeft size={16} aria-hidden="true" />К планам
        </a>
        <h1>Чертёж в цифровой план</h1>
        <p className="new-plan__lead">
          Загрузите рисунок плана — картинку или PDF. Сервис найдёт стены, двери и перегородки, а размеры пересчитает по
          одному числу: действительной ширине помещения. Получится черновик, который дальше правится в редакторе.
        </p>

        <div className="new-plan__body">
          <form
            className="new-plan__form"
            onSubmit={(event) => {
              event.preventDefault();
              void read();
            }}
          >
            <section className="new-plan__section">
              <h2>Чертёж</h2>
              <div
                className={`import__drop${file ? ' import__drop--filled' : ''}`}
                onDragOver={(event) => event.preventDefault()}
                onDrop={onDrop}
              >
                <FileUp size={20} aria-hidden="true" />
                <p>{file ? file.name : 'Перетащите файл сюда или выберите на диске'}</p>
                <button type="button" className="btn btn--steel" onClick={() => fileInput.current?.click()}>
                  Выбрать файл
                </button>
                <input
                  ref={fileInput}
                  type="file"
                  accept="image/png,image/jpeg,image/webp,application/pdf"
                  className="visually-hidden"
                  tabIndex={-1}
                  aria-hidden="true"
                  onChange={(event) => {
                    const chosen = event.target.files?.[0];
                    event.target.value = '';
                    accept(chosen);
                  }}
                />
              </div>
            </section>

            <section className="new-plan__section">
              <h2>Размеры и название</h2>
              <div className="fields">
                <TextField label="Название отделения" wide live value={name} onCommit={setName} />
                <NumberField
                  label="Ширина помещения"
                  unit="м"
                  min={1}
                  max={200}
                  step={0.5}
                  live
                  value={width}
                  hint="Действительный размер по чертежу — от него считается масштаб"
                  onCommit={(value) => value !== null && setWidth(value)}
                />
              </div>
            </section>

            {problem && (
              <p className="new-plan__problem" role="alert">
                <TriangleAlert size={15} aria-hidden="true" /> {problem}
              </p>
            )}

            <div className="new-plan__actions">
              <button type="submit" className="btn btn--steel new-plan__submit" disabled={!file || busy !== null || server.state !== 'ready'}>
                {busy === 'read' ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <FileUp size={18} aria-hidden="true" />}
                Распознать чертёж
              </button>
              {draft && (
                <button type="button" className="btn btn--label new-plan__submit" onClick={() => void create()} disabled={busy !== null}>
                  {busy === 'create' ? <LoaderCircle size={18} className="spin" aria-hidden="true" /> : <Plus size={18} aria-hidden="true" />}
                  Создать план
                </button>
              )}
            </div>

            {result && (
              <section className="new-plan__section">
                <h2>Что распозналось</h2>
                <ul className="import__notes">
                  {result.notes.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
                <dl className="import__stats">
                  {Object.entries(result.stats).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key.replace(/_/g, ' ')}</dt>
                      <dd>{String(value)}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            )}
          </form>

          <figure className="new-plan__preview">
            <div className="cabinet__sheet new-plan__sheet">
              {draft ? (
                <PlanThumb plan={draft} report={null} showSize />
              ) : preview ? (
                <img className="import__image" src={preview} alt="Загруженный чертёж" />
              ) : (
                <p className="import__empty">Здесь появится чертёж, а после распознавания — готовый план.</p>
              )}
            </div>
            <figcaption>
              {draft
                ? 'Черновик плана. Проверьте размеры и положение стен — дальше их можно тянуть прямо на плане.'
                : 'Подойдёт чертёж в линиях: экспорт из CAD, скан или снимок распечатки без сильного наклона.'}
            </figcaption>
          </figure>
        </div>
      </main>
    </div>
  );
}
