/**
 * Объёмный вид плана.
 *
 * Показывает ровно то, что нарисовано в 2D: те же стены с проёмами, то же оборудование в тех же
 * местах, те же нарушения и те же типовые маршруты посетителей. Объём нужен не ради красоты, а
 * чтобы увидеть то, чего не видно сверху: перекрывает ли стеллаж табло, проходит ли человек между
 * кассой и скамьёй, не висит ли камера над дверью.
 *
 * Качества рисования два. «Покрасивее» — материалы, мягкие тени и отражения помещения.
 * «Попроще» — плоская заливка без теней: запасной вариант для слабой машины и чужого проектора.
 * Выбор запоминается, а при отсутствии WebGL или слишком редких кадрах сервис переходит на простое
 * сам и говорит об этом.
 */

import { Building2, Camera, DoorOpen, Grid3x3, TriangleAlert } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import type { EquipmentTypeInfo } from '../lib/plan';
import { buildScene } from '../lib/scene3d';
import { useWorkspace } from '../state/store';
import { build, lights, type Quality } from './scene3d-build';
import './plan3d.css';

const QUALITY_KEY = 'ops-plan-3d-quality';
/** Ниже этой частоты кадров подробный режим бесполезен: переходим на простой. */
const SLOW_FPS = 22;
const FRAMES_BEFORE_VERDICT = 90;

export type Shot = 'iso' | 'top' | 'entrance';

const SHOTS: { id: Shot; label: string; hint: string }[] = [
  { id: 'iso', label: 'Изометрия', hint: 'Общий вид помещения' },
  { id: 'top', label: 'Сверху', hint: 'Как на плане, но с высотами' },
  { id: 'entrance', label: 'От входа', hint: 'Взгляд посетителя от двери' },
];

/** Расстояние, при котором помещение целиком помещается в кадр: иначе стены уезжают за край. */
function fitDistance(radius: number, camera: THREE.PerspectiveCamera): number {
  const vertical = Math.tan((camera.fov * Math.PI) / 360);
  const horizontal = vertical * camera.aspect;
  return (radius * 1.12) / Math.max(0.05, Math.min(vertical, horizontal));
}

function readQuality(): Quality {
  try {
    return localStorage.getItem(QUALITY_KEY) === 'plain' ? 'plain' : 'rich';
  } catch {
    // Приватный режим браузера запрещает хранилище — это не повод падать.
    return 'rich';
  }
}

function webglWorks(): boolean {
  try {
    const probe = document.createElement('canvas');
    return Boolean(probe.getContext('webgl2') ?? probe.getContext('webgl'));
  } catch {
    return false;
  }
}

interface Props {
  types: Map<string, EquipmentTypeInfo>;
}

export function Plan3D({ types }: Props) {
  const doc = useWorkspace((s) => s.doc);
  const report = useWorkspace((s) => s.report);
  const selection = useWorkspace((s) => s.selection);
  const select = useWorkspace((s) => s.select);

  const holder = useRef<HTMLDivElement>(null);
  const engine = useRef<{
    renderer: THREE.WebGLRenderer;
    scene: THREE.Scene;
    camera: THREE.PerspectiveCamera;
    controls: OrbitControls;
    content: THREE.Group | null;
    disposeContent: (() => void) | null;
    pickable: THREE.Object3D[];
    /** Габариты собранной сцены: по ним ставится камера, чтобы помещение влезало целиком. */
    sphere: THREE.Sphere | null;
    /** Кадр выставлен для этого движка: движок пересоздаётся, и камера каждый раз новая. */
    framed: boolean;
    flight: { to: THREE.Vector3; at: THREE.Vector3; started: number } | null;
  } | null>(null);

  const [quality, setQuality] = useState<Quality>(readQuality);
  const [supported] = useState(webglWorks);
  const [hovered, setHovered] = useState<{ label: string; violation: boolean } | null>(null);
  const [downgraded, setDowngraded] = useState(false);

  const scene = useMemo(() => (doc ? buildScene(doc, types, report) : null), [doc, types, report]);

  /** Ракурс: камера перелетает плавно, чтобы на защите не терялась ориентация. */
  const flyTo = (shot: Shot, instant = false) => {
    const parts = engine.current;
    if (!parts || !scene || !parts.sphere) return;
    const centre = parts.sphere.center;
    const distance = fitDistance(parts.sphere.radius, parts.camera);
    let target = centre.clone();
    let place: THREE.Vector3;
    if (shot === 'top') {
      // Взгляд сверху обязан совпасть с чертежом: ось Y плана смотрит вверх экрана.
      parts.camera.up.set(0, 0, -1);
      place = new THREE.Vector3(centre.x, centre.y + distance, centre.z + 0.01);
    } else if (shot === 'entrance') {
      parts.camera.up.set(0, 1, 0);
      // Взгляд посетителя: чуть позади фигуры у входа, на высоте глаз.
      const door = scene.figures[0]?.centre ?? scene.centre;
      const away = new THREE.Vector3(centre.x - door[0], 0, centre.z + door[1]).normalize();
      place = new THREE.Vector3(door[0] - away.x * 1.4, 1.65, -door[1] - away.z * 1.4);
      target = new THREE.Vector3(centre.x, 1.2, centre.z);
    } else {
      parts.camera.up.set(0, 1, 0);
      place = new THREE.Vector3(
        centre.x + distance * 0.52,
        centre.y + distance * 0.58,
        centre.z + distance * 0.62,
      );
    }
    if (instant) {
      parts.camera.position.copy(place);
      parts.controls.target.copy(target);
      parts.controls.update();
      return;
    }
    parts.flight = { to: place, at: target, started: performance.now() };
  };

  // Постановка сцены: создаётся один раз на качество, потому что материалы у режимов разные.
  useEffect(() => {
    if (!supported || !holder.current || !scene) return;
    const box = holder.current;
    const renderer = new THREE.WebGLRenderer({ antialias: quality === 'rich', powerPreference: 'high-performance' });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, quality === 'rich' ? 2 : 1));
    renderer.setSize(box.clientWidth, box.clientHeight);
    if (quality === 'rich') {
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 0.92;
    }
    box.appendChild(renderer.domElement);

    const world = new THREE.Scene();
    world.background = new THREE.Color(quality === 'rich' ? 0xe9e6dc : 0xf4f1e8);
    if (quality === 'rich') {
      // Отражения помещения дают материалам объём без единой картинки в поставке.
      const environment = new THREE.PMREMGenerator(renderer);
      world.environment = environment.fromScene(new RoomEnvironment(), 0.06).texture;
      // Отражения дают материалам объём, но в полную силу они забивают тени: приглушаем.
      world.environmentIntensity = 0.4;
      environment.dispose();
      world.fog = new THREE.Fog(0xe9e6dc, scene.radius * 4, scene.radius * 9);
    }
    for (const light of lights(scene, quality)) world.add(light);

    const camera = new THREE.PerspectiveCamera(50, box.clientWidth / box.clientHeight, 0.1, scene.radius * 14 + 60);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(scene.centre[0], 0, -scene.centre[1]);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.maxPolarAngle = Math.PI / 2 - 0.02;
    controls.minDistance = 1.5;
    controls.maxDistance = scene.radius * 6 + 20;
    controls.update();

    engine.current = {
      renderer,
      scene: world,
      camera,
      controls,
      content: null,
      disposeContent: null,
      pickable: [],
      sphere: null,
      framed: false,
      flight: null,
    };

    const resize = () => {
      if (!box.clientWidth || !box.clientHeight) return;
      camera.aspect = box.clientWidth / box.clientHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(box.clientWidth, box.clientHeight);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(box);

    let frames = 0;
    let elapsed = 0;
    let last = performance.now();
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      const now = performance.now();
      const delta = now - last;
      last = now;

      const parts = engine.current;
      if (!parts) return;
      if (parts.flight) {
        // Перелёт камеры: доля пути с замедлением к концу, 700 мс.
        const share = Math.min(1, (now - parts.flight.started) / 700);
        const eased = 1 - Math.pow(1 - share, 3);
        parts.camera.position.lerp(parts.flight.to, eased * 0.35);
        parts.controls.target.lerp(parts.flight.at, eased * 0.35);
        if (share >= 1) parts.flight = null;
      }
      parts.controls.update();
      parts.renderer.render(parts.scene, parts.camera);

      if (quality === 'rich' && frames < FRAMES_BEFORE_VERDICT) {
        frames += 1;
        elapsed += delta;
        if (frames === FRAMES_BEFORE_VERDICT && elapsed > 0) {
          const fps = (frames * 1000) / elapsed;
          if (fps < SLOW_FPS) {
            setDowngraded(true);
            setQuality('plain');
          }
        }
      }
    };
    raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      engine.current?.disposeContent?.();
      controls.dispose();
      renderer.dispose();
      world.environment?.dispose();
      box.removeChild(renderer.domElement);
      engine.current = null;
    };
  }, [supported, quality, scene?.radius, scene?.centre[0], scene?.centre[1]]);

  // Содержимое пересобирается при каждой правке плана: сцена всегда равна плану, а не его прошлому.
  useEffect(() => {
    const parts = engine.current;
    if (!parts || !scene) return;
    parts.disposeContent?.();
    if (parts.content) parts.scene.remove(parts.content);
    const built = build(scene, quality);
    parts.scene.add(built.root);
    parts.content = built.root;
    parts.disposeContent = built.dispose;
    parts.pickable = built.pickable;
    // Габариты берутся у собранной сцены, но без сетки пола: она нарочно шире помещения и
    // иначе отодвигала бы камеру от того, ради чего её ставят.
    const box = new THREE.Box3();
    for (const child of built.root.children) {
      if (child instanceof THREE.GridHelper) continue;
      box.expandByObject(child);
    }
    parts.sphere = box.isEmpty() ? null : box.getBoundingSphere(new THREE.Sphere());
    if (!parts.framed) {
      flyTo('iso', true);
      parts.framed = true;
    }
  }, [scene, quality]);

  // Выбранный объект подсвечивается тем же способом, что и на плане.
  useEffect(() => {
    const parts = engine.current;
    if (!parts?.content) return;
    parts.content.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || !mesh.userData?.id) return;
      const chosen = selection?.id === mesh.userData.id;
      const material = mesh.material as THREE.MeshStandardMaterial;
      if (!('emissive' in material)) return;
      material.emissive?.setHex(chosen ? 0x8e6621 : 0x000000);
      material.emissiveIntensity = chosen ? 0.55 : 0;
    });
  }, [selection, scene, quality]);

  const pick = (event: React.PointerEvent<HTMLDivElement>, choose: boolean) => {
    const parts = engine.current;
    if (!parts || !holder.current) return;
    const rect = holder.current.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    const caster = new THREE.Raycaster();
    caster.setFromCamera(pointer, parts.camera);
    const hit = caster.intersectObjects(parts.pickable, false)[0];
    const data = hit?.object.userData as { id?: string; label?: string; kind?: string; violation?: boolean } | undefined;
    if (!data?.id) {
      setHovered(null);
      return;
    }
    setHovered({ label: data.label ?? data.id, violation: Boolean(data.violation) });
    if (choose && (data.kind === 'equipment' || data.kind === 'pin')) {
      select({ kind: 'equipment', id: data.id.replace(':operator', '') });
    }
  };

  /** Клавиатура: стрелки вращают и наклоняют, плюс и минус приближают. */
  const steer = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const parts = engine.current;
    if (!parts) return;
    const step = event.shiftKey ? 0.22 : 0.08;
    const offset = parts.camera.position.clone().sub(parts.controls.target);
    const spherical = new THREE.Spherical().setFromVector3(offset);
    switch (event.key) {
      case 'ArrowLeft':
        spherical.theta -= step;
        break;
      case 'ArrowRight':
        spherical.theta += step;
        break;
      case 'ArrowUp':
        spherical.phi = Math.max(0.12, spherical.phi - step);
        break;
      case 'ArrowDown':
        spherical.phi = Math.min(Math.PI / 2 - 0.02, spherical.phi + step);
        break;
      case '+':
      case '=':
        spherical.radius = Math.max(1.5, spherical.radius * 0.88);
        break;
      case '-':
        spherical.radius = Math.min(parts.controls.maxDistance, spherical.radius * 1.14);
        break;
      default:
        return;
    }
    event.preventDefault();
    parts.flight = null;
    parts.camera.position.copy(parts.controls.target.clone().add(new THREE.Vector3().setFromSpherical(spherical)));
    parts.controls.update();
  };

  const change = (next: Quality) => {
    setDowngraded(false);
    setQuality(next);
    try {
      localStorage.setItem(QUALITY_KEY, next);
    } catch {
      // Без хранилища выбор просто не переживёт перезагрузку страницы.
    }
  };

  if (!doc || !scene) return null;

  if (!supported) {
    return (
      <div className="plan3d plan3d--absent" role="status">
        <Building2 size={22} aria-hidden="true" />
        <p>Браузер не поддерживает трёхмерную графику (WebGL). План целиком доступен в виде сверху — вкладка «План».</p>
      </div>
    );
  }

  return (
    <div className="plan3d">
      <div
        ref={holder}
        className="plan3d__stage"
        role="application"
        tabIndex={0}
        aria-label="Объёмный вид плана отделения. Стрелки вращают камеру, плюс и минус приближают"
        onKeyDown={steer}
        onPointerMove={(event) => pick(event, false)}
        onPointerDown={(event) => pick(event, true)}
        onPointerLeave={() => setHovered(null)}
      />

      <div className="plan3d__shots" role="group" aria-label="Ракурсы камеры">
        {SHOTS.map((shot) => (
          <button key={shot.id} type="button" className="plan3d__shot" onClick={() => flyTo(shot.id)} title={shot.hint}>
            {shot.id === 'top' ? <Grid3x3 size={15} aria-hidden="true" /> : shot.id === 'entrance' ? <DoorOpen size={15} aria-hidden="true" /> : <Camera size={15} aria-hidden="true" />}
            {shot.label}
          </button>
        ))}
      </div>

      <div className="plan3d__quality" role="group" aria-label="Качество изображения">
        <button
          type="button"
          className={`plan3d__mode${quality === 'rich' ? ' plan3d__mode--on' : ''}`}
          onClick={() => change('rich')}
          aria-pressed={quality === 'rich'}
        >
          Покрасивее
        </button>
        <button
          type="button"
          className={`plan3d__mode${quality === 'plain' ? ' plan3d__mode--on' : ''}`}
          onClick={() => change('plain')}
          aria-pressed={quality === 'plain'}
        >
          Попроще
        </button>
      </div>

      <div className="plan3d__legend">
        <p>
          {scene.violations > 0 ? (
            <>
              <TriangleAlert size={14} aria-hidden="true" /> Красным и меткой сверху — объекты, участвующие в нарушениях: {scene.violations}
            </>
          ) : (
            'Нарушений на плане нет'
          )}
        </p>
        <p>Синие полосы — типовые маршруты посетителей. Фигуры у входа: человек 1,7 м и кресло-коляска 0,9 м.</p>
      </div>

      {downgraded && (
        <p className="plan3d__notice" role="status">
          Машина не тянет подробный режим — включено «Попроще». Вернуть можно кнопкой выше.
        </p>
      )}

      {hovered && (
        <p className={`plan3d__tip${hovered.violation ? ' plan3d__tip--bad' : ''}`} role="status">
          {hovered.label}
        </p>
      )}
    </div>
  );
}
