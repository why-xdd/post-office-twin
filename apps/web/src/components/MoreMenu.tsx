/**
 * Выпадающее меню редких действий. Стрелки ходят по пунктам, Esc и щелчок мимо закрывают:
 * так ведёт себя меню в любой настольной программе.
 */

import { useEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from 'react';
import './more-menu.css';

export interface MoreMenuItem {
  icon: ReactNode;
  label: string;
  /** Короткая пометка справа: формат файла или клавиша. */
  note?: string;
  run: () => void;
}

interface MoreMenuProps {
  id?: string;
  items: MoreMenuItem[];
  /** Обёртка вместе с кнопкой меню: щелчок внутри неё меню не закрывает. */
  container: RefObject<HTMLElement | null>;
  /** refocus — вернуть фокус на кнопку меню: после выбора с клавиатуры он не должен теряться. */
  onClose: (refocus: boolean) => void;
}

export function MoreMenu({ id, items, container, onClose }: MoreMenuProps) {
  const menu = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    menu.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
    const onPointer = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) close.current(false);
    };
    document.addEventListener('pointerdown', onPointer);
    return () => document.removeEventListener('pointerdown', onPointer);
  }, [container]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const entries = [...(menu.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]') ?? [])];
    const at = entries.indexOf(document.activeElement as HTMLButtonElement);
    const move = (to: number) => {
      event.preventDefault();
      entries[(to + entries.length) % entries.length]?.focus();
    };
    if (event.key === 'ArrowDown') move(at + 1);
    else if (event.key === 'ArrowUp') move(at - 1);
    else if (event.key === 'Home') move(0);
    else if (event.key === 'End') move(entries.length - 1);
    else if (event.key === 'Escape') {
      // Esc закрывает только меню: выделение на плане и остальное состояние остаются.
      event.preventDefault();
      event.stopPropagation();
      close.current(true);
    } else if (event.key === 'Tab') close.current(false);
  };

  return (
    <div id={id} ref={menu} className="more-menu" role="menu" onKeyDown={onKeyDown}>
      {items.map((item) => (
        <button
          key={item.label}
          type="button"
          role="menuitem"
          className="more-menu__item"
          onClick={() => {
            close.current(true);
            item.run();
          }}
        >
          {item.icon}
          <span>{item.label}</span>
          {item.note && <small>{item.note}</small>}
        </button>
      ))}
    </div>
  );
}
