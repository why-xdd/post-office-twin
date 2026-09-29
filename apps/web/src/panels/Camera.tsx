/**
 * Камера зала: из обычной записи видно, сколько посетителей в отделении и где они стоят.
 *
 * Запись раскладывается на кадры прямо в браузере, на сервер уезжают только они. Один раз
 * отмечаются четыре угла пола на кадре и то, чему они соответствуют на плане, — дальше сервис
 * считает людей по зонам и рисует тепловую карту прямо на чертеже.
 */

import { ChevronDown, Film, MapPin, RotateCcw } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { ApiError, api } from '../lib/api';
import type { Plan, VisionReport } from '../lib/plan';
import { formatNumber, plural } from '../lib/planFacts';
import { extractFrames, type ExtractedFrames } from '../lib/video';
import { useWorkspace } from '../state/store';
import './camera.css';

type Point = [number, number];

/** Углы пола отмечаются в одном порядке — иначе план и кадр «перекрутятся». */
const CORNERS = ['дальний левый', 'дальний правый', 'ближний правый', 'ближний левый'];

interface Area {
  id: string;
  label: string;
  /** Углы области на плане в том же порядке, что и на кадре. */
  corners: Point[];
}

function planAreas(doc: Plan): Area[] {
  const areas: Area[] = [];
  const bounds = (points: Point[]): Point[] => {
    const xs = points.map((p) => p[0]);
    const ys = points.map((p) => p[1]);
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    return [
      [minX, maxY],
      [maxX, maxY],
      [maxX, minY],
      [minX, minY],
    ];
  };
  const walls = (doc.walls ?? []).flatMap((wall) => [wall.from, wall.to] as Point[]);
  if (walls.length) areas.push({ id: 'outline', label: 'Помещение целиком', corners: bounds(walls) });
  for (const zone of doc.zones ?? []) {
    areas.push({ id: zone.id, label: zone.label || zone.type, corners: bounds(zone.polygon as Point[]) });
  }
  return areas;
}

export function Camera() {
  const doc = useWorkspace((s) => s.doc);
  const setHeatmap = useWorkspace((s) => s.setHeatmap);
  const input = useRef<HTMLInputElement>(null);
  const [video, setVideo] = useState<ExtractedFrames | null>(null);
  const [marks, setMarks] = useState<Point[]>([]);
  const [areaId, setAreaId] = useState('outline');
  const [busy, setBusy] = useState<'frames' | 'analyze' | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<VisionReport | null>(null);

  const areas = useMemo(() => (doc ? planAreas(doc) : []), [doc]);
  const area = areas.find((item) => item.id === areaId) ?? areas[0];

  if (!doc) return null;

  const pick = async (file: File) => {
    setBusy('frames');
    setError(null);
    setReport(null);
    setMarks([]);
    setHeatmap(null);
    try {
      const extracted = await extractFrames(file, { onProgress: (done, total) => setProgress(done / total) });
      setVideo(extracted);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : 'Не удалось разобрать запись');
    } finally {
      setBusy(null);
      setProgress(0);
    }
  };

  const markCorner = (event: React.MouseEvent<HTMLImageElement>) => {
    if (!video || marks.length >= 4) return;
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * video.width;
    const y = ((event.clientY - box.top) / box.height) * video.height;
    setMarks([...marks, [Number(x.toFixed(1)), Number(y.toFixed(1))]]);
  };

  const analyze = async () => {
    if (!video || marks.length !== 4 || !area) return;
    setBusy('analyze');
    setError(null);
    try {
      const result = await api.visionAnalyze(doc, video.frames, video.seconds, {
        frame: marks as [number, number][],
        plan: area.corners as [number, number][],
        frame_size: [video.width, video.height],
      });
      setReport(result);
      setHeatmap(result.heatmap);
    } catch (problem) {
      setError(problem instanceof ApiError ? problem.message : 'Не удалось разобрать запись');
    } finally {
      setBusy(null);
    }
  };

  const reset = () => {
    setVideo(null);
    setMarks([]);
    setReport(null);
    setHeatmap(null);
    setError(null);
  };

  return (
    <section className="camera">
      <header className="camera__head">
        <h2>Камера зала</h2>
        <p>
          Запись с камеры превращается в цифры по плану: сколько посетителей в зале, где они стоят и хватает ли
          мест. Видео остаётся на этом компьютере — на сервер уходят только кадры.
        </p>
      </header>

      {!video && (
        <div className="camera__start">
          <button type="button" className="btn btn--steel-light" onClick={() => input.current?.click()} disabled={busy !== null}>
            <Film size={15} aria-hidden="true" />
            {busy === 'frames' ? `Разбираем запись… ${Math.round(progress * 100)}%` : 'Выбрать запись'}
          </button>
          <p className="camera__hint">
            Подойдёт обычный файл с камеры: mp4, mov или webm. Из записи берётся один кадр в секунду, не больше 180.
          </p>
        </div>
      )}

      <input
        ref={input}
        type="file"
        accept="video/*"
        hidden
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void pick(file);
        }}
      />

      {video && (
        <>
          <div className="camera__step">
            <h3>
              <MapPin size={14} aria-hidden="true" />
              {marks.length < 4 ? `Отметьте ${CORNERS[marks.length]} угол пола` : 'Углы отмечены'}
            </h3>
            <p className="camera__hint">
              Щёлкните по кадру четыре раза: {CORNERS.join(', ')}. По этим точкам кадр совмещается с планом.
            </p>
            <div className="camera__frame">
              <img src={video.preview} alt="Первый кадр записи" onClick={markCorner} />
              {marks.map((point, index) => (
                <span
                  key={index}
                  className="camera__mark"
                  style={{ left: `${(point[0] / video.width) * 100}%`, top: `${(point[1] / video.height) * 100}%` }}
                >
                  {index + 1}
                </span>
              ))}
            </div>
            <div className="camera__row">
              <label className="camera__field">
                <span>Эти углы на плане — это</span>
                <select value={areaId} onChange={(event) => setAreaId(event.target.value)}>
                  {areas.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
              <button type="button" className="btn btn--outline" onClick={() => setMarks([])} disabled={!marks.length}>
                <RotateCcw size={14} aria-hidden="true" />
                Отметить заново
              </button>
            </div>
            <p className="camera__hint">
              Кадров в записи: {video.frames.length}, длительность {formatNumber(video.duration, 0)} с.
            </p>
            <div className="camera__row">
              <button type="button" className="btn btn--steel-light" onClick={analyze} disabled={marks.length !== 4 || busy !== null}>
                {busy === 'analyze' ? 'Считаем посетителей…' : 'Разобрать запись'}
              </button>
              <button type="button" className="btn btn--outline" onClick={reset} disabled={busy !== null}>
                Другая запись
              </button>
            </div>
          </div>
        </>
      )}

      <div aria-live="polite">
        {error && (
          <p className="camera__error" role="alert">
            {error}
          </p>
        )}

        {report && (
          <div className="camera__result">
            <p className="camera__source">
              Разобрано {report.frames} {plural(report.frames, 'кадр', 'кадра', 'кадров')} за{' '}
              {formatNumber(report.duration_s, 0)} с записи.
            </p>
            <ul className="camera__summary">
              {report.summary.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>

            <table className="capacity">
              <thead>
                <tr>
                  <th scope="col">Зона</th>
                  <th scope="col">В среднем</th>
                  <th scope="col">В пике</th>
                  <th scope="col">Мест</th>
                </tr>
              </thead>
              <tbody>
                {report.zones.map((zone) => (
                  <tr key={zone.zone_id ?? 'none'}>
                    <th scope="row">{zone.label}</th>
                    <td>{formatNumber(zone.mean_people, 1)}</td>
                    <td className={zone.seats && zone.peak_people > zone.seats ? 'capacity--hot' : ''}>{zone.peak_people}</td>
                    <td>{zone.seats ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <figure className="camera__legend">
              <span className="camera__ramp" aria-hidden="true" />
              <figcaption>
                Тепловая карта на плане: слева — где посетители бывали редко, справа — где стояли чаще всего.
                Слой выключается в списке слоёв слева.
              </figcaption>
            </figure>

            <details className="rec__assumptions">
              <summary>
                Допущения расчёта: {report.assumptions.length}
                <ChevronDown size={14} aria-hidden="true" />
              </summary>
              <ul>
                {report.assumptions.map((line, index) => (
                  <li key={index}>{line}</li>
                ))}
              </ul>
            </details>
          </div>
        )}
      </div>
    </section>
  );
}
