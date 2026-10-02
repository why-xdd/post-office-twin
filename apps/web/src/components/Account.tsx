/**
 * Вход сотрудника: кнопка в рейке и лист со входом. Без входа сервис работает как раньше —
 * вход только добавляет действия по роли и подпись в журнале.
 */

import { LoaderCircle, LogIn, LogOut, UserRound } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../lib/api';
import { useSession } from '../state/session';
import './account.css';

const ROLE_NOTES: Record<string, string> = {
  head: 'Сводка «Сегодня», обращения, осмотры',
  operator: 'Паспорта и сообщения о неисправностях',
  engineer: 'Обращения в работу, отметка осмотров',
  designer: 'Планировка, проверка норм, ассистент',
};

export function Account({ surface = 'steel' }: { surface?: 'steel' | 'label' }) {
  const user = useSession((s) => s.user);
  const loaded = useSession((s) => s.loaded);
  const refresh = useSession((s) => s.refresh);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!loaded) void refresh();
  }, [loaded, refresh]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div className={`account account--${surface}`} ref={root}>
      <button
        type="button"
        className="account__button"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
        title={user ? `${user.name} — ${user.role_label}` : 'Войти как сотрудник'}
      >
        {user ? <UserRound size={16} aria-hidden="true" /> : <LogIn size={16} aria-hidden="true" />}
        <span className="account__name">{user ? shortName(user.name) : 'Войти'}</span>
      </button>
      {open && <AccountSheet onDone={() => setOpen(false)} />}
    </div>
  );
}

function shortName(full: string): string {
  const [last, first, middle] = full.split(' ');
  if (!first) return full;
  return `${last} ${first[0]}.${middle ? ` ${middle[0]}.` : ''}`;
}

function AccountSheet({ onDone }: { onDone: () => void }) {
  const user = useSession((s) => s.user);
  const demo = useSession((s) => s.demo);
  const roles = useSession((s) => s.roles);
  const login = useSession((s) => s.login);
  const demoLogin = useSession((s) => s.demoLogin);
  const logout = useSession((s) => s.logout);
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();

  const attempt = async (what: string, action: () => Promise<void>) => {
    setBusy(what);
    setError(null);
    try {
      await action();
      onDone();
    } catch (reason) {
      setError(reason instanceof ApiError ? reason.message : 'Сервер не ответил. Попробуйте ещё раз.');
    } finally {
      setBusy(null);
    }
  };

  if (user) {
    return (
      <div className="account__sheet" role="dialog" aria-labelledby={titleId}>
        <p id={titleId} className="account__title">
          {user.name}
        </p>
        <p className="account__role">{user.role_label}</p>
        <p className="account__note">{ROLE_NOTES[user.role]}</p>
        {error && <p className="account__error">{error}</p>}
        <button type="button" className="btn btn--steel account__wide" onClick={() => void attempt('logout', logout)} disabled={busy !== null}>
          {busy === 'logout' ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <LogOut size={15} aria-hidden="true" />}
          Выйти
        </button>
      </div>
    );
  }

  return (
    <div className="account__sheet" role="dialog" aria-labelledby={titleId}>
      <p id={titleId} className="account__title">
        Вход сотрудника
      </p>
      <p className="account__note">Без входа план можно смотреть, править и проверять. Вход добавляет действия по должности.</p>
      <form
        className="account__form"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim() || !password) {
            setError('Введите логин и пароль.');
            return;
          }
          void attempt('form', () => login(name.trim(), password));
        }}
      >
        <label>
          <span>Логин</span>
          <input value={name} onChange={(e) => setName(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} />
        </label>
        <label>
          <span>Пароль</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && (
          <p className="account__error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn--label account__wide" disabled={busy !== null}>
          {busy === 'form' ? <LoaderCircle size={15} className="spin" aria-hidden="true" /> : <LogIn size={15} aria-hidden="true" />}
          Войти
        </button>
      </form>
      {demo && roles.length > 0 && (
        <div className="account__demo">
          <p className="account__demo-title">Демо-сотрудники — войти одной кнопкой</p>
          <ul>
            {roles.map((item) => (
              <li key={item.role}>
                <button type="button" onClick={() => void attempt(item.role, () => demoLogin(item.role))} disabled={busy !== null}>
                  <b>{item.label}</b>
                  <span>{ROLE_NOTES[item.role]}</span>
                  {busy === item.role && <LoaderCircle size={14} className="spin" aria-hidden="true" />}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
