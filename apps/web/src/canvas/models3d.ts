/**
 * Модели оборудования для объёмного вида: GLB из public/models, по одной на тип.
 *
 * Модель сделана в номинальных габаритах типа (они записаны в extras как `nominal`: ширина,
 * глубина, высота) и растягивается под габариты конкретного объекта на плане. Сторона к
 * посетителю у модели — −Z сцены, то есть +Y плана, противоположная месту сотрудника.
 * Сцена сначала строится брусками; модели загружаются следом и встают на их место, а брусок
 * остаётся невидимой мишенью для щелчка. Нет модели или сеть подвела — остаётся брусок.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { Prism } from '../lib/scene3d';

const MODEL_TYPES = new Set([
  'cashier_window',
  'pvz_counter',
  'self_service_kiosk',
  'payment_terminal_stand',
  'package_drop_box',
  'info_display',
  'media_panel',
  'waiting_seat',
  'atm',
  'parcel_locker',
  'back_office_workplace',
  'printer',
  'storage_rack',
  'goods_rack',
  'weighing_station',
  'info_stand',
  'safe',
  'trash_bin',
  'potted_plant',
  'fitting_room',
  'camera',
]);

interface Template {
  scene: THREE.Object3D;
  nominal: [number, number, number];
}

const loader = new GLTFLoader();
const templates = new Map<string, Promise<Template | null>>();

export function hasModel(typeCode: string | undefined): boolean {
  return Boolean(typeCode && MODEL_TYPES.has(typeCode));
}

function template(code: string): Promise<Template | null> {
  const known = templates.get(code);
  if (known) return known;
  const pending = loader
    .loadAsync(`${import.meta.env.BASE_URL}models/${code}.glb`)
    .then((gltf) => {
      let nominal: [number, number, number] | null = null;
      gltf.scene.traverse((node) => {
        const value = node.userData?.nominal;
        if (!nominal && Array.isArray(value) && value.length === 3) nominal = value as [number, number, number];
        if (node instanceof THREE.Mesh) {
          node.castShadow = true;
          node.receiveShadow = true;
        }
      });
      return nominal ? { scene: gltf.scene, nominal } : null;
    })
    .catch(() => null);
  templates.set(code, pending);
  return pending;
}

export interface ModelSlot {
  prism: Prism;
  /** Брусок: остаётся мишенью для щелчка, но перестаёт быть виден. */
  block: THREE.Mesh;
  /** Что ещё рисовалось вместо модели: рёбра, стекло над стойкой. */
  extras: THREE.Object3D[];
  /** Место сотрудника на плане: модель поворачивается к нему служебной стороной. */
  operator?: [number, number];
}

/** Расстояние от точки до ближайшего ребра многоугольника. */
function toBoundary([px, py]: [number, number], polygon: [number, number][]): number {
  let best = Infinity;
  for (let i = 0; i < polygon.length; i += 1) {
    const [ax, ay] = polygon[i];
    const [bx, by] = polygon[(i + 1) % polygon.length];
    const dx = bx - ax;
    const dy = by - ay;
    const lengthSq = dx * dx + dy * dy || 1;
    const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / lengthSq));
    best = Math.min(best, Math.hypot(px - (ax + dx * t), py - (ay + dy * t)));
  }
  return best;
}

/**
 * Развернуть ли модель на 180°. Лицевая сторона модели — +Y её собственных осей. У окна и
 * стойки лицо к посетителю, значит, спиной к месту сотрудника. У банкомата, терминала, стенда
 * места сотрудника нет: лицом от ближайшей стены, иначе экран упрётся в стену.
 */
function facesBackwards(slot: ModelSlot, floor: [number, number][]): boolean {
  const { prism } = slot;
  const angle = (prism.rotationDeg * Math.PI) / 180;
  // Местная ось +Y объекта на плане. Поворот на плане идёт по часовой стрелке (как в
  // equipmentCorners и месте сотрудника), поэтому X со знаком плюс.
  const fy: [number, number] = [Math.sin(angle), Math.cos(angle)];
  if (slot.operator) {
    const toOperator = (slot.operator[0] - prism.centre[0]) * fy[0] + (slot.operator[1] - prism.centre[1]) * fy[1];
    return toOperator > 0;
  }
  if (floor.length < 3) return false;
  const half = prism.size[1] / 2;
  const front: [number, number] = [prism.centre[0] + fy[0] * half, prism.centre[1] + fy[1] * half];
  const back: [number, number] = [prism.centre[0] - fy[0] * half, prism.centre[1] - fy[1] * half];
  return toBoundary(front, floor) + 0.1 < toBoundary(back, floor);
}

const VIOLATION = new THREE.Color(0xd04a2f);
const CAUTION = new THREE.Color(0xd59b2c);

/**
 * Ставит модели на место брусков. Возвращает уборку для материалов, перекрашенных под
 * нарушение; геометрия и материалы шаблонов общие и живут, пока открыта страница.
 */
export async function dressWithModels(
  root: THREE.Group,
  slots: ModelSlot[],
  floor: [number, number][],
  isCurrent: () => boolean,
): Promise<() => void> {
  const tinted: THREE.Material[] = [];
  const loaded = await Promise.all(slots.map((slot) => template(slot.prism.typeCode ?? '')));
  if (!isCurrent()) return () => undefined;
  slots.forEach((slot, index) => {
    const model = loaded[index];
    if (!model) return;
    const { prism } = slot;
    const instance = model.scene.clone(true);
    instance.scale.set(
      prism.size[0] / model.nominal[0],
      prism.height / model.nominal[2],
      prism.size[1] / model.nominal[1],
    );
    instance.position.set(prism.centre[0], prism.base, -prism.centre[1]);
    instance.rotation.y = (-prism.rotationDeg * Math.PI) / 180 + (facesBackwards(slot, floor) ? Math.PI : 0);
    instance.userData = slot.block.userData;
    const signal = prism.violation ? VIOLATION : prism.caution ? CAUTION : null;
    if (signal) {
      // Нарушение видно цветом и на модели: материал подкрашивается, а не подменяется.
      instance.traverse((node) => {
        if (!(node instanceof THREE.Mesh)) return;
        const source = node.material as THREE.MeshStandardMaterial;
        const copy = source.clone();
        copy.color.lerp(signal, prism.violation ? 0.55 : 0.35);
        copy.emissive = signal.clone();
        copy.emissiveIntensity = prism.violation ? 0.18 : 0.1;
        tinted.push(copy);
        node.material = copy;
      });
    }
    root.add(instance);
    slot.block.visible = false;
    for (const extra of slot.extras) extra.visible = false;
  });
  return () => {
    for (const material of tinted) material.dispose();
  };
}
