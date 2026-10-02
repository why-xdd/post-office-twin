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

/** Таблица для Excel: точка с запятой и BOM — так русский Excel открывает файл без мастера импорта. */
export function downloadCsv(name: string, rows: (string | number | null | undefined)[][]): void {
  const safe = name.replace(/[\\/:*?"<>|]+/g, ' ').trim() || 'table';
  const cell = (value: string | number | null | undefined) => {
    const text = value == null ? '' : typeof value === 'number' ? String(value).replace('.', ',') : value;
    return /[;"\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const body = rows.map((row) => row.map(cell).join(';')).join('\r\n');
  const url = URL.createObjectURL(new Blob(['\uFEFF' + body], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = `${safe}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
