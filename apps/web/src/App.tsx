import { ErrorBoundary, isStaleChunk } from './components/ErrorBoundary';
import { useRoute } from './lib/router';
import { ImportDrawing } from './screens/ImportDrawing';
import { NewPlan } from './screens/NewPlan';
import { Rack } from './screens/Rack';
import { Tag } from './screens/Tag';
import { Tags } from './screens/Tags';
import { Network } from './screens/Network';
import { Report } from './screens/Report';
import { Workspace } from './screens/Workspace';

export function App() {
  const route = useRoute();
  if (route.name === 'plan')
    return (
      <ErrorBoundary key={route.id} fallback={(error) => <Crash error={error} />}>
        <Workspace key={route.id} planId={route.id} />
      </ErrorBoundary>
    );
  if (route.name === 'tag') return <Tag key={`${route.id}/${route.object}`} planId={route.id} objectId={route.object} />;
  if (route.name === 'report') return <Report key={route.id} planId={route.id} />;
  if (route.name === 'tags') return <Tags key={route.id} planId={route.id} />;
  if (route.name === 'network') return <Network />;
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
