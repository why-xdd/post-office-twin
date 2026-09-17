/**
 * Поля свойств. Изменение применяется по Enter или при уходе фокуса — так правка остаётся
 * одной записью в истории, а не десятком по нажатию клавиш.
 */

import { useEffect, useId, useState, type ReactNode } from 'react';
import './fields.css';

interface FieldShellProps {
  label: string;
  hint?: string;
  children: (id: string) => ReactNode;
  wide?: boolean;
}

function FieldShell({ label, hint, children, wide }: FieldShellProps) {
  const id = useId();
  return (
    <div className={`field${wide ? ' field--wide' : ''}`}>
      <label className="field__label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint && <span className="field__hint">{hint}</span>}
    </div>
  );
}

interface NumberFieldProps {
  label: string;
  value: number | null | undefined;
  unit?: string;
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  onCommit: (value: number | null) => void;
  allowEmpty?: boolean;
}

export function NumberField({ label, value, unit, min, max, step = 0.05, hint, onCommit, allowEmpty }: NumberFieldProps) {
  const format = (v: number | null | undefined) => (v === null || v === undefined ? '' : String(Number(v.toFixed(3))).replace('.', ','));
  const [text, setText] = useState(format(value));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setText(format(value));
    setError(null);
  }, [value]);

  const apply = () => {
    const trimmed = text.trim();
    if (trimmed === '') {
      if (allowEmpty) {
        setError(null);
        if (value !== null && value !== undefined) onCommit(null);
        return;
      }
      setError('Укажите число');
      return;
    }
    const parsed = Number(trimmed.replace(',', '.'));
    if (!Number.isFinite(parsed)) {
      setError('Нужно число, например 1,2');
      return;
    }
    if (min !== undefined && parsed < min) {
      setError(`Не меньше ${String(min).replace('.', ',')}`);
      return;
    }
    if (max !== undefined && parsed > max) {
      setError(`Не больше ${String(max).replace('.', ',')}`);
      return;
    }
    setError(null);
    if (parsed !== value) onCommit(parsed);
  };

  return (
    <FieldShell label={label} hint={error ?? hint}>
      {(id) => (
        <span className={`field__control${error ? ' field__control--error' : ''}`}>
          <input
            id={id}
            inputMode="decimal"
            value={text}
            aria-invalid={error ? true : undefined}
            onChange={(event) => setText(event.target.value)}
            onBlur={apply}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                apply();
                (event.target as HTMLInputElement).blur();
              }
              if (event.key === 'Escape') {
                setText(format(value));
                setError(null);
                (event.target as HTMLInputElement).blur();
              }
              if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
                event.preventDefault();
                const current = Number(text.replace(',', '.')) || 0;
                const next = Number((current + (event.key === 'ArrowUp' ? step : -step)).toFixed(3));
                setText(format(next));
              }
            }}
          />
          {unit && <span className="field__unit">{unit}</span>}
        </span>
      )}
    </FieldShell>
  );
}

interface TextFieldProps {
  label: string;
  value: string | undefined;
  placeholder?: string;
  hint?: string;
  wide?: boolean;
  onCommit: (value: string) => void;
}

export function TextField({ label, value, placeholder, hint, wide, onCommit }: TextFieldProps) {
  const [text, setText] = useState(value ?? '');
  useEffect(() => setText(value ?? ''), [value]);
  const apply = () => {
    if (text !== (value ?? '')) onCommit(text.trim());
  };
  return (
    <FieldShell label={label} hint={hint} wide={wide}>
      {(id) => (
        <span className="field__control">
          <input
            id={id}
            value={text}
            placeholder={placeholder}
            onChange={(event) => setText(event.target.value)}
            onBlur={apply}
            onKeyDown={(event) => {
              if (event.key === 'Enter') (event.target as HTMLInputElement).blur();
              if (event.key === 'Escape') {
                setText(value ?? '');
                (event.target as HTMLInputElement).blur();
              }
            }}
          />
        </span>
      )}
    </FieldShell>
  );
}

interface SelectFieldProps {
  label: string;
  value: string | undefined;
  options: { value: string; label: string }[];
  hint?: string;
  wide?: boolean;
  emptyLabel?: string;
  onCommit: (value: string) => void;
}

export function SelectField({ label, value, options, hint, wide, emptyLabel, onCommit }: SelectFieldProps) {
  return (
    <FieldShell label={label} hint={hint} wide={wide}>
      {(id) => (
        <span className="field__control field__control--select">
          <select id={id} value={value ?? ''} onChange={(event) => onCommit(event.target.value)}>
            {emptyLabel !== undefined && <option value="">{emptyLabel}</option>}
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </span>
      )}
    </FieldShell>
  );
}

export function ReadonlyField({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="field">
      <span className="field__label">{label}</span>
      <span className="field__readonly">{value}</span>
    </div>
  );
}
