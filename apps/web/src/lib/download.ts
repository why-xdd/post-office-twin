/** Сохранение файла из браузера: план уезжает к заказчику как обычный JSON. */
export function downloadJson(name: string, data: unknown): void {
  const safe = name.replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'plan';
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${safe}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
