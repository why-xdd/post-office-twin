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
import { dressWithModels } from './models3d';
import { plural } from '../lib/planFacts';
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
    flight: { from: THREE.Vector3; fromAt: THREE.Vector3; to: THREE.Vector3; at: THREE.Vector3; started: number } | null;
  } | null>(null);

  const [quality, setQuality] = useState<Quality>(readQuality);
  const [supported] = useState(webglWorks);
  const [hovered, setHovered] = useState<{ label: string; violation: boolean } | null>(null);
  const [downgraded, setDowngraded] = useState(false);

  const scene = useMemo(() => (doc ? buildScene(doc, types, report) : null), [doc, types, report]);

  /** Ракурс: камера перелетает плавно, чтобы не терялась ориентация в помещении. */
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
      // Взгляд посетителя: глаза на высоте 1,65 м чуть впереди фигуры у входа. Позади неё
      // полупрозрачная фигура закрывала середину кадра.
      const door = scene.figures[0]?.centre ?? scene.centre;
      const away = new THREE.Vector3(centre.x - door[0], 0, centre.z + door[1]).normalize();
      // Камера не должна оказаться внутри оборудования у входа: берётся первое свободное место
      // на линии взгляда — впереди фигуры, на ней или чуть позади.
      const blocked = (x: number, y: number) =>
        scene.prisms.some((prism) => {
          if (prism.kind !== 'equipment' || prism.base > 1.6) return false;
          const angle = (prism.rotationDeg * Math.PI) / 180;
          const dx = x - prism.centre[0];
          const dy = y - prism.centre[1];
          const u = dx * Math.cos(angle) - dy * Math.sin(angle);
          const v = dx * Math.sin(angle) + dy * Math.cos(angle);
          return Math.abs(u) < prism.size[0] / 2 + 0.35 && Math.abs(v) < prism.size[1] / 2 + 0.35;
        });
      const step = [0.35, 0.8, 0, -0.6, 1.3].find((k) => !blocked(door[0] + away.x * k, door[1] - away.z * k)) ?? 0.35;
      place = new THREE.Vector3(door[0] + away.x * step, 1.65, -door[1] + away.z * step);
      target = new THREE.Vector3(centre.x, 1.3, centre.z);
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
    parts.flight = {
      from: parts.camera.position.clone(),
      fromAt: parts.controls.target.clone(),
      to: place,
      at: target,
      started: performance.now(),
    };
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
      // В three r18x мягкий PCF убран: обычный PCF теперь сглаживает тени сам.
      renderer.shadowMap.type = THREE.PCFShadowMap;
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
        // Перелёт камеры за 700 мс с замедлением к концу. Положение считается от начальной точки,
        // а не от текущей: на медленном кадре камера всё равно приходит точно в цель, иначе вид
        // «От входа» оставался выше уровня глаз.
        const share = Math.min(1, (now - parts.flight.started) / 700);
        const eased = 1 - Math.pow(1 - share, 3);
        parts.camera.position.lerpVectors(parts.flight.from, parts.flight.to, eased);
        parts.controls.target.lerpVectors(parts.flight.fromAt, parts.flight.at, eased);
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
    const built = build(scene, quality, parts.renderer.capabilities.getMaxAnisotropy());
    parts.scene.add(built.root);
    parts.content = built.root;
    parts.disposeContent = built.dispose;
    parts.pickable = built.pickable;
    // Модели оборудования догружаются следом; если план успели поменять — их уже не ставим.
    if (built.slots.length) {
      let undress: (() => void) | null = null;
      void dressWithModels(built.root, built.slots, scene.floor, () => engine.current?.content === built.root).then((cleanup) => {
        undress = cleanup;
      });
      parts.disposeContent = () => {
        undress?.();
        built.dispose();
      };
    }
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

  // Рамка выбора: латунный контур вокруг объекта и метка на полу. Свечение материала ниже не
  // видно у моделей из Blender — они заменяют брусок, — поэтому выбор показывает отдельная рамка.
  useEffect(() => {
    const parts = engine.current;
    if (!parts?.content || !scene || selection?.kind !== 'equipment') return;
    const prism = scene.prisms.find((p) => p.id === selection.id && p.kind === 'equipment');
    if (!prism) return;
    const pad = 0.06;
    const [w, d] = [prism.size[0] + pad * 2, prism.size[1] + pad * 2];
    const marker = new THREE.Group();
    marker.position.set(prism.centre[0], 0, -prism.centre[1]);
    marker.rotation.y = (-prism.rotationDeg * Math.PI) / 180;
    const outline = new THREE.EdgesGeometry(new THREE.BoxGeometry(w, prism.height + pad, d));
    const lineMaterial = new THREE.LineBasicMaterial({ color: 0xd0a453, depthTest: false, transparent: true, opacity: 0.95 });
    const frame = new THREE.LineSegments(outline, lineMaterial);
    frame.position.y = prism.base + prism.height / 2;
    frame.renderOrder = 10;
    const floorGeometry = new THREE.PlaneGeometry(w + 0.2, d + 0.2);
    const floorMaterial = new THREE.MeshBasicMaterial({ color: 0xd0a453, transparent: true, opacity: 0.28, depthWrite: false });
    const floor = new THREE.Mesh(floorGeometry, floorMaterial);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = 0.012;
    marker.add(frame, floor);
    parts.scene.add(marker);
    return () => {
      parts.scene.remove(marker);
      outline.dispose();
      floorGeometry.dispose();
      lineMaterial.dispose();
      floorMaterial.dispose();
    };
  }, [selection, scene, quality]);

  // Выбранный объект подсвечивается тем же способом, что и на плане.
  useEffect(() => {
    const parts = engine.current;
    if (!parts?.content) return;
    parts.content.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh || !mesh.userData?.id) return;
      const chosen = selection?.id === mesh.userData.id;
      const material = mesh.material as THREE.MeshStandardMaterial;
      if (!('emissive' in material) || !material.emissive) return;
      // Свои свечения (экраны табло) запоминаются до первой подсветки и возвращаются после неё.
      if (material.userData.glow === undefined) {
        material.userData.glow = { hex: material.emissive.getHex(), intensity: material.emissiveIntensity };
      }
      const own = material.userData.glow as { hex: number; intensity: number };
      material.emissive.setHex(chosen ? 0x8e6621 : own.hex);
      material.emissiveIntensity = chosen ? 0.55 : own.intensity;
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
          <button
            key={shot.id}
            type="button"
            className="plan3d__shot"
            onClick={() => flyTo(shot.id)}
            title={shot.hint}
            aria-label={shot.label}
          >
            {shot.id === 'top' ? <Grid3x3 size={15} aria-hidden="true" /> : shot.id === 'entrance' ? <DoorOpen size={15} aria-hidden="true" /> : <Camera size={15} aria-hidden="true" />}
            {/* На узком экране остаются значки: подписи ракурсов не помещаются рядом с качеством. */}
            <span className="plan3d__shot-label">{shot.label}</span>
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
              {/* Здесь считаются объекты, а не проблемы: одна проблема может касаться двух объектов. */}
              <TriangleAlert size={14} aria-hidden="true" /> Красным с меткой сверху —{' '}
              {scene.violations} {plural(scene.violations, 'объект', 'объекта', 'объектов')}, из-за которых есть проблемы
            </>
          ) : (
            'Проблем на плане нет'
          )}
          {scene.cautions > 0 && ` · янтарным — ${scene.cautions} ${plural(scene.cautions, 'объект', 'объекта', 'объектов')} с замечаниями`}
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
