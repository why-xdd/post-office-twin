/** «ОПС-1» → «ОПС-1 · копия 2», если такое название уже есть среди сохранённых планов. */
export function uniqueTitle(base: string, taken: string[]): string {
  const names = new Set(taken);
  if (!names.has(base)) return base;
  let number = 2;
  while (names.has(`${base} · копия ${number}`)) number += 1;
  return `${base} · копия ${number}`;
}
