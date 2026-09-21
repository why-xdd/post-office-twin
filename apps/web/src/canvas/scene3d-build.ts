/**
 * Сборка сцены three.js из описания плана.
 *
 * Здесь два качества рисования одной и той же геометрии. «Покрасивее» — физические материалы,
 * мягкие тени и отражения помещения: так объём читается как помещение, а не как набор коробок.
 * «Попроще» — плоская заливка без теней и отражений: то же самое, но работает на слабой машине со
 * встроенной видеокартой.
 *
 * Оба режима собираются одним кодом из одной геометрии, отличаются только материалы и свет.
 */

import * as THREE from 'three';
import type { Prism, Ribbon, Scene as PlanScene } from '../lib/scene3d';

export type Quality = 'rich' | 'plain';

/** Цвета мира «Абонентский шкаф»: сталь корпуса, бумага листа, латунь и сигнальный красный. */
const COLOR = {
  floor: 0xe7e1d2,
  grid: 0xc3bca8,
  wall: 0xd2ccbb,
  partition: 0xbdb6a2,
  equipment: 0xf7f4ea,
  operator: 0xa9b1b1,
  violation: 0x9e3119,
  caution: 0xb8862c,
  route: 0x2c5a74,
  figure: 0x5e6b6e,
  wheelchair: 0x845a08,
  edge: 0x41474a,
} as const;

export interface Built {
  root: THREE.Group;
  /** Объекты, по которым идёт наведение и выбор: ключ — идентификатор объекта плана. */
  pickable: THREE.Object3D[];
  dispose: () => void;
}

function material(quality: Quality, color: number, options: { rough?: number; metal?: number; opacity?: number } = {}) {
  const { rough = 0.75, metal = 0.02, opacity = 1 } = options;
  const transparent = opacity < 1;
  if (quality === 'plain') {
    return new THREE.MeshLambertMaterial({ color, transparent, opacity });
  }
  return new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: metal, transparent, opacity });
}

/** Пол по контуру помещения: произвольный многоугольник, а не обязательно прямоугольник. */
function floorMesh(scene: PlanScene, quality: Quality): THREE.Mesh | null {
  if (scene.floor.length < 3) return null;
  const shape = new THREE.Shape();
  scene.floor.forEach(([x, y], index) => (index === 0 ? shape.moveTo(x, y) : shape.lineTo(x, y)));
  shape.closePath();
  const geometry = new THREE.ShapeGeometry(shape);
  geometry.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geometry, material(quality, COLOR.floor, { rough: 0.95 }));
  mesh.receiveShadow = quality === 'rich';
  mesh.name = 'пол';
  return mesh;
}

function prismMesh(prism: Prism, quality: Quality): THREE.Mesh {
  const geometry = new THREE.BoxGeometry(prism.size[0], prism.height, prism.size[1]);
  const colour = prism.violation
    ? COLOR.violation
    : prism.caution
      ? COLOR.caution
      : prism.kind === 'equipment'
      ? COLOR.equipment
      : prism.kind === 'operator'
        ? COLOR.operator
        : prism.kind === 'partition'
          ? COLOR.partition
          : COLOR.wall;
  const mesh = new THREE.Mesh(geometry, material(quality, colour, { rough: prism.kind === 'equipment' ? 0.55 : 0.9 }));
  mesh.position.set(prism.centre[0], prism.base + prism.height / 2, -prism.centre[1]);
  // План смотрит осью Y вверх, сцена — осью Z на зрителя: поворот меняет знак.
  mesh.rotation.y = (-prism.rotationDeg * Math.PI) / 180;
  mesh.castShadow = quality === 'rich' && prism.kind !== 'wall';
  mesh.receiveShadow = quality === 'rich';
  mesh.userData = { id: prism.id, label: prism.label, kind: prism.kind, violation: prism.violation };
  mesh.name = prism.label;
  return mesh;
}

/** Рёбра объекта: белое на белом иначе не читается ни на проекторе, ни на снимке экрана. */
function edges(mesh: THREE.Mesh, colour: number, opacity: number): THREE.LineSegments {
  const geometry = new THREE.EdgesGeometry(mesh.geometry, 20);
  const line = new THREE.LineSegments(geometry, new THREE.LineBasicMaterial({ color: colour, transparent: true, opacity }));
  line.position.copy(mesh.position);
  line.rotation.copy(mesh.rotation);
  return line;
}


/** Метка нарушения: нарушение не должно читаться одним лишь цветом. */
function violationPin(prism: Prism, quality: Quality): THREE.Mesh {
  const geometry = new THREE.ConeGeometry(0.16, 0.42, 4);
  const mesh = new THREE.Mesh(geometry, material(quality, COLOR.violation, { rough: 0.4 }));
  mesh.position.set(prism.centre[0], prism.base + prism.height + 0.45, -prism.centre[1]);
  mesh.rotation.x = Math.PI;
  mesh.userData = { id: prism.id, label: prism.label, kind: 'pin', violation: true };
  return mesh;
}

/** Лента маршрута: полоса по полу, ширина и плотность — по доле посетителей. */
function ribbonMesh(ribbon: Ribbon, quality: Quality): THREE.Mesh | null {
  const width = 0.22 + 0.5 * Math.min(1, ribbon.share || 0.2);
  const shapes: THREE.Shape[] = [];
  for (let index = 0; index + 1 < ribbon.points.length; index += 1) {
    const [x1, y1] = ribbon.points[index];
    const [x2, y2] = ribbon.points[index + 1];
    const length = Math.hypot(x2 - x1, y2 - y1);
    if (length < 0.01) continue;
    const ux = (x2 - x1) / length;
    const uy = (y2 - y1) / length;
    const nx = (-uy * width) / 2;
    const ny = (ux * width) / 2;
    const shape = new THREE.Shape();
    shape.moveTo(x1 + nx, y1 + ny);
    shape.lineTo(x2 + nx, y2 + ny);
    shape.lineTo(x2 - nx, y2 - ny);
    shape.lineTo(x1 - nx, y1 - ny);
    shape.closePath();
    shapes.push(shape);
  }
  if (!shapes.length) return null;
  const geometry = new THREE.ShapeGeometry(shapes);
  geometry.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geometry, material(quality, COLOR.route, { opacity: 0.45, rough: 1 }));
  mesh.position.y = 0.012;
  mesh.userData = { id: `route:${ribbon.label}`, label: ribbon.label, kind: 'route' };
  mesh.renderOrder = 1;
  return mesh;
}

/** Фигуры масштаба: человек и габарит кресла-коляски. */
function figureMesh(centre: [number, number], radius: number, height: number, colour: number, quality: Quality) {
  const geometry = new THREE.CylinderGeometry(radius, radius, height, quality === 'rich' ? 24 : 10);
  const mesh = new THREE.Mesh(geometry, material(quality, colour, { opacity: 0.55, rough: 0.9 }));
  mesh.position.set(centre[0], height / 2, -centre[1]);
  mesh.castShadow = quality === 'rich';
  return mesh;
}

/** Сетка метрового шага по полу: без неё объём теряет масштаб. */
function grid(scene: PlanScene, quality: Quality): THREE.GridHelper {
  const size = Math.ceil(scene.radius * 2) + 4;
  const helper = new THREE.GridHelper(size, size, COLOR.grid, COLOR.grid);
  helper.position.set(scene.centre[0], 0.004, -scene.centre[1]);
  const grid_material = helper.material as THREE.Material;
  grid_material.transparent = true;
  grid_material.opacity = quality === 'rich' ? 0.5 : 0.35;
  return helper;
}

/** Собирает сцену целиком. Возвращает корневую группу и способ освободить память. */
export function build(scene: PlanScene, quality: Quality): Built {
  const root = new THREE.Group();
  const pickable: THREE.Object3D[] = [];
  const created: (THREE.BufferGeometry | THREE.Material)[] = [];

  const remember = (object: THREE.Mesh) => {
    created.push(object.geometry);
    created.push(object.material as THREE.Material);
  };

  const floor = floorMesh(scene, quality);
  if (floor) {
    root.add(floor);
    remember(floor);
  }
  root.add(grid(scene, quality));

  for (const ribbon of scene.ribbons) {
    const mesh = ribbonMesh(ribbon, quality);
    if (!mesh) continue;
    root.add(mesh);
    remember(mesh);
  }

  for (const prism of scene.prisms) {
    const mesh = prismMesh(prism, quality);
    root.add(mesh);
    remember(mesh);
    if (prism.kind === 'equipment' || prism.kind === 'operator') {
      const line = edges(mesh, COLOR.edge, prism.violation ? 0.45 : 0.32);
      root.add(line);
      created.push(line.geometry, line.material as THREE.Material);
    }
    if (prism.kind === 'equipment' || prism.kind === 'operator') pickable.push(mesh);
    if (prism.violation && prism.kind === 'equipment') {
      const pin = violationPin(prism, quality);
      root.add(pin);
      remember(pin);
      pickable.push(pin);
    }
  }

  for (const figure of scene.figures) {
    const colour = figure.id.includes('wheelchair') ? COLOR.wheelchair : COLOR.figure;
    const mesh = figureMesh(figure.centre, figure.radius, figure.height, colour, quality);
    mesh.userData = { id: figure.id, label: figure.label, kind: 'figure' };
    root.add(mesh);
    remember(mesh);
    pickable.push(mesh);
  }

  return {
    root,
    pickable,
    dispose: () => {
      for (const item of created) item.dispose();
    },
  };
}

/** Освещение: у подробного режима — солнце с мягкой тенью, у простого — ровный свет без теней. */
export function lights(scene: PlanScene, quality: Quality): THREE.Object3D[] {
  const made: THREE.Object3D[] = [];
  if (quality === 'plain') {
    made.push(new THREE.AmbientLight(0xffffff, 1.7));
    const flat = new THREE.DirectionalLight(0xffffff, 0.9);
    flat.position.set(scene.centre[0] + 6, 12, -scene.centre[1] + 8);
    made.push(flat);
    return made;
  }
  made.push(new THREE.HemisphereLight(0xf7f4ea, 0x6b7274, 0.42));
  const sun = new THREE.DirectionalLight(0xfff4e0, 2.1);
  sun.position.set(scene.centre[0] + scene.radius, scene.radius * 2.2 + 6, -scene.centre[1] + scene.radius);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  sun.shadow.bias = -0.0008;
  sun.shadow.normalBias = 0.02;
  const reach = scene.radius * 1.8 + 4;
  const camera = sun.shadow.camera as THREE.OrthographicCamera;
  camera.left = -reach;
  camera.right = reach;
  camera.top = reach;
  camera.bottom = -reach;
  camera.near = 0.5;
  camera.far = scene.radius * 6 + 20;
  camera.updateProjectionMatrix();
  sun.target.position.set(scene.centre[0], 0, -scene.centre[1]);
  made.push(sun, sun.target);
  const fill = new THREE.DirectionalLight(0xdfe6ea, 0.22);
  fill.position.set(scene.centre[0] - scene.radius, scene.radius + 4, -scene.centre[1] - scene.radius);
  made.push(fill);
  return made;
}
