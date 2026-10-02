/** QR-код одной фигурой: тёмные модули — один путь, светлое поле — фон. Печатается чётко. */

import { encode } from 'uqr';
import { useMemo } from 'react';

export function Qr({ text, size = 120, label }: { text: string; size?: number; label: string }) {
  const { path, modules } = useMemo(() => {
    const result = encode(text, { ecc: 'M', border: 2 });
    let d = '';
    result.data.forEach((row, y) =>
      row.forEach((dark, x) => {
        if (dark) d += `M${x} ${y}h1v1h-1z`;
      }),
    );
    return { path: d, modules: result.size };
  }, [text]);
  return (
    <svg
      viewBox={`0 0 ${modules} ${modules}`}
      width={size}
      height={size}
      role="img"
      aria-label={label}
      shapeRendering="crispEdges"
      className="qr"
    >
      <rect width={modules} height={modules} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}
