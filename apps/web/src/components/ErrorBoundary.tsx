/**
 * Ловушка ошибок отрисовки: вместо пустого экрана — что случилось и как продолжить.
 *
 * Самый частый случай на бесплатном хостинге — после новой выкладки браузер просит старый
 * кусок приложения, которого уже нет. Тогда помогает обычная перезагрузка страницы, и об
 * этом стоит сказать прямо.
 */

import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** Что показать вместо сломавшейся части; retry сбрасывает ошибку и пробует снова. */
  fallback: (error: Error, retry: () => void) => ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Ошибка отрисовки', error, info.componentStack);
  }

  retry = () => this.setState({ error: null });

  render() {
    return this.state.error ? this.props.fallback(this.state.error, this.retry) : this.props.children;
  }
}

/** Устаревший кусок приложения после новой выкладки. */
export function isStaleChunk(error: Error): boolean {
  return /dynamically imported module|Loading chunk|Importing a module script failed/i.test(error.message);
}
