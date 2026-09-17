/** Знак продукта: дверца ячейки, на которой лежит план помещения. */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true" focusable="false">
      <rect x="1.5" y="1.5" width="25" height="25" rx="2" fill="var(--steel-600)" />
      <rect x="1.5" y="1.5" width="25" height="25" rx="2" fill="none" stroke="var(--steel-400)" />
      <rect x="5" y="6.5" width="18" height="15" fill="var(--sheet)" />
      <path d="M5 6.5h18v15H5z M14 6.5v8.5 M5 15h6" fill="none" stroke="var(--ink)" strokeWidth="1.4" />
      <rect x="15.8" y="16.6" width="4.6" height="2.8" fill="var(--ink)" />
      <rect x="11" y="3.1" width="6" height="1.8" rx="0.4" fill="var(--steel-300)" />
    </svg>
  );
}
