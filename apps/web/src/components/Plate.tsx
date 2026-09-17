import type { ReactNode } from 'react';
import './plate.css';

interface PlateProps {
  children: ReactNode;
  /** Выбитая табличка на стали или штамп на бумажной этикетке. */
  surface?: 'steel' | 'label';
  size?: 'sm' | 'md' | 'lg';
  title?: string;
}

/** Табличка с адресом ячейки. */
export function Plate({ children, surface = 'steel', size = 'md', title }: PlateProps) {
  return (
    <span className={`plate plate--${surface} plate--${size}`} title={title}>
      {children}
    </span>
  );
}
