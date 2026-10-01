import { ErrorBoundary, isStaleChunk } from './components/ErrorBoundary';
import { useRoute } from './lib/router';
import { ImportDrawing } from './screens/ImportDrawing';
import { NewPlan } from './screens/NewPlan';
import { Rack } from './screens/Rack';
import { Workspace } from './screens/Workspace';

export function App() {
  const route = useRoute();
  if (route.name === 'plan')
    return (
      <ErrorBoundary key={route.id} fallback={(error) => <Crash error={error} />}>
        <Workspace key={route.id} planId={route.id} />
      </ErrorBoundary>
    );
  if (route.name === 'new') return <NewPlan />;
  if (route.name === 'import') return <ImportDrawing />;
  return <Rack />;
}

/** Экран вместо упавшего редактора: правки не потеряны — черновик плана хранится в браузере. */
function Crash({ error }: { error: Error }) {
  const stale = isStaleChunk(error);
  return (
    <main className="crash" role="alert">
      <h1>{stale ? 'Сервис обновился' : 'Редактор остановился из-за ошибки'}</h1>
      <p>
        {stale
          ? 'Пока страница была открыта, вышла новая версия. Перезагрузите страницу: несохранённые правки вернутся из черновика.'
          : 'Несохранённые правки остались в черновике и вернутся после перезагрузки. Если ошибка повторится, откройте план из списка.'}
      </p>
      <div className="crash__actions">
        <button type="button" className="btn btn--label" onClick={() => window.location.reload()}>
          Перезагрузить страницу
        </button>
        <a className="btn btn--outline" href="#/">
          К списку планов
        </a>
      </div>
    </main>
  );
}
