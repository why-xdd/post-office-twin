/**
 * Материалы объёмного вида: из чего сделано то, что стоит в отделении.
 *
 * Банкомат металлический, скамья тёплая матовая, окно обслуживания со стеклом, табло светится.
 * Без этого объём читается как набор одинаковых белых коробок, и по нему нельзя понять, что
 * перед тобой. Отделка берётся по типу оборудования из того же справочника, что и высоты, —
 * ничего не выдумывается на месте.
 *
 * Текстуры рисуются здесь же на холсте: в поставке нет ни одного файла картинки, сервис остаётся
 * автономным, а пол при этом выглядит полом — с швами плитки, а не сплошной заливкой.
 */

import * as THREE from 'three';

export interface Finish {
  color: number;
  /** Шероховатость: 0 — зеркало, 1 — матовое. */
  rough: number;
  /** Металличность: 1 — металл. */
  metal: number;
  /** Светящаяся поверхность (табло, терминал). */
  glow?: number;
  /** Верхняя часть объекта — стекло: окна обслуживания, витрины. */
  glass?: boolean;
}

const STEEL: Finish = { color: 0xb9bfc0, rough: 0.34, metal: 0.82 };
const PAINTED: Finish = { color: 0xeae5d8, rough: 0.62, metal: 0.05 };
const DARK_PANEL: Finish = { color: 0x39413f, rough: 0.45, metal: 0.35, glow: 0.35 };
const WOOD: Finish = { color: 0xc9a274, rough: 0.72, metal: 0 };
const FABRIC: Finish = { color: 0x9aa6a4, rough: 0.94, metal: 0 };
const COUNTER: Finish = { color: 0xf1ece0, rough: 0.5, metal: 0.05, glass: true };

/** Из чего сделан каждый тип оборудования перечня ОПС. */
const FINISHES: Record<string, Finish> = {
  cashier_window: COUNTER,
  pvz_counter: COUNTER,
  atm: STEEL,
  parcel_locker: { color: 0xa8b0b2, rough: 0.4, metal: 0.7 },
  safe: { color: 0x6f7679, rough: 0.38, metal: 0.85 },
  self_service_kiosk: DARK_PANEL,
  payment_terminal_stand: DARK_PANEL,
  info_display: { color: 0x2a3033, rough: 0.28, metal: 0.4, glow: 0.7 },
  media_panel: { color: 0x2a3033, rough: 0.28, metal: 0.4, glow: 0.7 },
  waiting_seat: FABRIC,
  storage_rack: { color: 0xb2b7b3, rough: 0.55, metal: 0.55 },
  goods_rack: WOOD,
  sorting_table: WOOD,
  back_office_workplace: WOOD,
  weighing_station: STEEL,
  printer: { color: 0xdcdcd6, rough: 0.5, metal: 0.2 },
  label_printer: { color: 0xdcdcd6, rough: 0.5, metal: 0.2 },
  mailbox: { color: 0x2f5c7a, rough: 0.5, metal: 0.4 },
  trash_bin: { color: 0x8d9391, rough: 0.6, metal: 0.3 },
  plant: { color: 0x5c7a4a, rough: 0.9, metal: 0 },
  camera: { color: 0x51585a, rough: 0.4, metal: 0.5 },
  fire_alarm: { color: 0xd8d4c8, rough: 0.7, metal: 0.1 },
  security_alarm: { color: 0xd8d4c8, rough: 0.7, metal: 0.1 },
  info_stand: PAINTED,
  fitting_room: PAINTED,
};

export function finishFor(typeCode: string | undefined): Finish {
  return (typeCode && FINISHES[typeCode]) || PAINTED;
}

/** Общий холст для процедурных текстур. */
function canvas(size: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const element = document.createElement('canvas');
  element.width = size;
  element.height = size;
  const context = element.getContext('2d');
  if (!context) throw new Error('Браузер не дал холст для текстуры');
  return [element, context];
}

function grain(context: CanvasRenderingContext2D, size: number, strength: number): void {
  const image = context.getImageData(0, 0, size, size);
  for (let index = 0; index < image.data.length; index += 4) {
    const shift = (Math.random() - 0.5) * strength;
    image.data[index] += shift;
    image.data[index + 1] += shift;
    image.data[index + 2] += shift;
  }
  context.putImageData(image, 0, 0);
}

/** Пол: керамогранит со швами. Один квадрат текстуры — метр плана. */
export function floorTexture(anisotropy: number): THREE.CanvasTexture {
  const size = 512;
  const [element, context] = canvas(size);
  context.fillStyle = '#e7e1d2';
  context.fillRect(0, 0, size, size);
  const tile = size / 2; // плитка 0,5 × 0,5 м
  context.strokeStyle = 'rgba(120, 112, 96, 0.35)';
  context.lineWidth = 2;
  for (let offset = 0; offset <= size; offset += tile) {
    context.beginPath();
    context.moveTo(offset, 0);
    context.lineTo(offset, size);
    context.moveTo(0, offset);
    context.lineTo(size, offset);
    context.stroke();
  }
  // Лёгкие пятна на плитке: пол не идеальный, и глаз это ловит.
  for (let spot = 0; spot < 60; spot += 1) {
    const x = Math.random() * size;
    const y = Math.random() * size;
    const radius = 6 + Math.random() * 26;
    const shade = context.createRadialGradient(x, y, 0, x, y, radius);
    shade.addColorStop(0, `rgba(160, 150, 130, ${0.05 + Math.random() * 0.07})`);
    shade.addColorStop(1, 'rgba(160, 150, 130, 0)');
    context.fillStyle = shade;
    context.fillRect(x - radius, y - radius, radius * 2, radius * 2);
  }
  grain(context, size, 10);
  const texture = new THREE.CanvasTexture(element);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.anisotropy = anisotropy;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Стена: матовая краска с мелким зерном. */
export function wallTexture(anisotropy: number): THREE.CanvasTexture {
  const size = 256;
  const [element, context] = canvas(size);
  context.fillStyle = '#d2ccbb';
  context.fillRect(0, 0, size, size);
  grain(context, size, 14);
  const texture = new THREE.CanvasTexture(element);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(3, 3);
  texture.anisotropy = anisotropy;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/** Мягкая тень под объектом: пятно с размытым краем, которое «сажает» вещь на пол. */
export function contactShadowTexture(): THREE.CanvasTexture {
  const size = 128;
  const [element, context] = canvas(size);
  const shade = context.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  shade.addColorStop(0, 'rgba(28, 32, 35, 0.55)');
  shade.addColorStop(0.55, 'rgba(28, 32, 35, 0.22)');
  shade.addColorStop(1, 'rgba(28, 32, 35, 0)');
  context.fillStyle = shade;
  context.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(element);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}
