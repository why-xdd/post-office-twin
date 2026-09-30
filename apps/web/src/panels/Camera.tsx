/**
 * Камера зала: из обычной записи видно, сколько посетителей в отделении и где они стоят.
 *
 * Запись раскладывается на кадры прямо в браузере, на сервер уезжают только они. Один раз
 * отмечаются четыре угла пола на кадре и то, чему они соответствуют на плане, — дальше сервис
 * считает людей по зонам и рисует тепловую карту прямо на чертеже.
 */

import { ChevronDown, Film, MapPin, RotateCcw, Scan } from 'lucide-react';
import { useMemo, useRef, useState } from 'react';
import { ApiError, api } from '../lib/api';
import { homography, project } from '../lib/homography';
import type { Plan, VisionReport } from '../lib/plan';
import { formatNumber, outlinePoints, plural } from '../lib/planFacts';
import { extractFrames, type ExtractedFrames } from '../lib/video';
import { useWorkspace } from '../state/store';
import './camera.css';

type Point = [number, number];

/** Габарит плана: по нему строится предпросмотр и переводятся щелчки по нему. */
function planBox(doc: Plan): { minX: number; minY: number; width: number; height: number } {
  const points = [
    ...(doc.walls ?? []).flatMap((wall) => [wall.from, wall.to] as Point[]),
    ...(doc.zones ?? []).flatMap((zone) => zone.polygon as Point[]),
  ];
  if (!points.length) return { minX: 0, minY: 0, width: 10, height: 10 };
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  return { minX, minY, width: Math.max(maxX - minX, 0.1), height: Math.max(maxY - minY, 0.1) };
}

/** Углы помещения, к которым притягивается щелчок по плану: попасть точно мышью трудно. */
function planCorners(doc: Plan): Point[] {
  const outline = outlinePoints(doc);
  if (outline) return outline;
  return (doc.walls ?? []).flatMap((wall) => [wall.from, wall.to] as Point[]);
}

export function Camera() {
  const doc = useWorkspace((s) => s.doc);
  const setHeatmap = useWorkspace((s) => s.setHeatmap);
  const input = useRef<HTMLInputElement>(null);
  const [video, setVideo] = useState<ExtractedFrames | null>(null);
  const [marks, setMarks] = useState<Point[]>([]);
  const [planMarks, setPlanMarks] = useState<Point[]>([]);
  const [busy, setBusy] = useState<'frames' | 'analyze' | null>(null);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<VisionReport | null>(null);

  const box = useMemo(() => (doc ? planBox(doc) : null), [doc]);
  const corners = useMemo(() => (doc ? planCorners(doc) : []), [doc]);
  const outline = useMemo(() => (doc ? outlinePoints(doc) : null), [doc]);
  const ready = marks.length === 4 && planMarks.length === 4;

  // Проверка привязки: контур помещения с плана, наложенный обратно на кадр. Лёг на стены —
  // точки отмечены верно; уехал — видно сразу, до расчёта.
  const check = useMemo(() => {
    if (!ready || !outline) return null;
    const matrix = homography(planMarks, marks);
    if (!matrix) return null;
    return outline.map((point) => project(matrix, point));
  }, [ready, outline, planMarks, marks]);

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

  /** Камера смотрит на зал целиком: углы кадра и углы габарита помещения — одни и те же точки. */
  const useWholeRoom = () => {
    if (!video || !box) return;
    setMarks([
      [0, 0],
      [video.width, 0],
      [video.width, video.height],
      [0, video.height],
    ]);
    setPlanMarks([
      [box.minX, box.minY + box.height],
      [box.minX + box.width, box.minY + box.height],
      [box.minX + box.width, box.minY],
      [box.minX, box.minY],
    ]);
  };

  /** Щелчок по плану: точка притягивается к ближайшему углу помещения, если он рядом. */
  const markPlan = (event: React.MouseEvent<SVGSVGElement>) => {
    if (!box || planMarks.length >= 4) return;
    const rect = event.currentTarget.getBoundingClientRect();
    // Поле вокруг плана в viewBox: щелчок переводится с его учётом.
    const pad = box.width * 0.04;
    const spanX = box.width + pad * 2;
    const spanY = box.height + pad * 2;
    const x = box.minX - pad + ((event.clientX - rect.left) / rect.width) * spanX;
    const y = box.minY + box.height + pad - ((event.clientY - rect.top) / rect.height) * spanY;
    const snap = (spanX / rect.width) * 14;
    let point: Point = [x, y];
    let best = snap;
    for (const corner of corners) {
      const distance = Math.hypot(corner[0] - x, corner[1] - y);
      if (distance < best) {
        best = distance;
        point = [corner[0], corner[1]];
      }
    }
    setPlanMarks([...planMarks, [Number(point[0].toFixed(2)), Number(point[1].toFixed(2))]]);
  };

  /** Точка плана в координаты предпросмотра: ось Y на плане растёт вверх, у картинки — вниз. */
  const onPreview = (point: Point): Point =>
    box ? [point[0] - box.minX, box.minY + box.height - point[1]] : [0, 0];

  const markCorner = (event: React.MouseEvent<HTMLImageElement>) => {
    if (!video || marks.length >= 4) return;
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * video.width;
    const y = ((event.clientY - box.top) / box.height) * video.height;
    setMarks([...marks, [Number(x.toFixed(1)), Number(y.toFixed(1))]]);
  };

  const analyze = async () => {
    if (!video || !ready) return;
    setBusy('analyze');
    setError(null);
    try {
      const result = await api.visionAnalyze(doc, video.frames, video.seconds, {
        frame: marks as [number, number][],
        plan: planMarks as [number, number][],
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
    setPlanMarks([]);
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
              {marks.length < 4
                ? `Точка ${marks.length + 1} на кадре`
                : planMarks.length < 4
                  ? `Точка ${planMarks.length + 1} на плане`
                  : 'Привязка готова'}
            </h3>
            <p className="camera__hint">
              Отметьте четыре точки пола на кадре, а потом <b>те же самые</b> точки на плане — в
              том же порядке. Годятся любые четыре узнаваемые точки, лишь бы они не лежали на
              одной линии: углы помещения, углы перегородки, края дверного проёма. Щелчок по плану
              притягивается к ближайшему углу помещения. Если камера видит зал целиком, всё это
              заменяет кнопка «Зал целиком».
            </p>
            <div className="camera__frame">
              <img src={video.preview} alt="Первый кадр записи" onClick={markCorner} />
              <svg
                className="camera__overlay"
                viewBox={`0 0 ${video.width} ${video.height}`}
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                {marks.length > 1 && (
                  <polygon
                    className="camera__quad"
                    points={marks.map((point) => point.join(',')).join(' ')}
                  />
                )}
                {check && (
                  <polygon className="camera__outline" points={check.map((point) => point.join(',')).join(' ')} />
                )}
              </svg>
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
            {check && (
              <p className="camera__hint camera__hint--check">
                Синим — контур помещения с плана, наложенный на кадр по вашим точкам. Лёг на стены
                — привязка верная; уехал — отметьте точки заново, иначе людей посчитает неверно.
              </p>
            )}

            {box && (
              <>
                <p className="camera__hint">
                  {planMarks.length < 4
                    ? `Те же точки на плане, по порядку. Отмечено ${planMarks.length} из 4.`
                    : 'Точки на плане отмечены.'}
                </p>
                <svg
                  className="camera__plan"
                  viewBox={`${-box.width * 0.04} ${-box.width * 0.04} ${box.width * 1.08} ${box.height + box.width * 0.08}`}
                  role="img"
                  aria-label="План отделения для привязки"
                  onClick={markPlan}
                >
                  {(doc.zones ?? []).map((zone) => (
                    <polygon
                      key={zone.id}
                      className="camera__plan-zone"
                      points={(zone.polygon as Point[]).map((point) => onPreview(point).join(',')).join(' ')}
                    />
                  ))}
                  {outline && (
                    <polygon
                      className="camera__plan-outline"
                      points={outline.map((point) => onPreview(point).join(',')).join(' ')}
                    />
                  )}
                  {corners.map((corner, index) => {
                    const [cx, cy] = onPreview(corner);
                    return <circle key={index} className="camera__plan-corner" cx={cx} cy={cy} r={box.width / 90} />;
                  })}
                  {planMarks.map((point, index) => {
                    const [cx, cy] = onPreview(point);
                    return (
                      <g key={index} className="camera__plan-mark">
                        <circle cx={cx} cy={cy} r={box.width / 36} />
                        <text x={cx} y={cy} dy={box.width / 110} textAnchor="middle">
                          {index + 1}
                        </text>
                      </g>
                    );
                  })}
                </svg>
              </>
            )}

            <div className="camera__row">
              <button type="button" className="btn btn--outline" onClick={useWholeRoom}>
                <Scan size={14} aria-hidden="true" />
                Зал целиком
              </button>
              <button
                type="button"
                className="btn btn--outline"
                onClick={() => {
                  setMarks([]);
                  setPlanMarks([]);
                }}
                disabled={!marks.length && !planMarks.length}
              >
                <RotateCcw size={14} aria-hidden="true" />
                Отметить заново
              </button>
            </div>
            <p className="camera__hint">
              Кадров в записи: {video.frames.length}, длительность {formatNumber(video.duration, 0)} с.
            </p>
            <div className="camera__row">
              <button type="button" className="btn btn--steel-light" onClick={analyze} disabled={!ready || busy !== null}>
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
