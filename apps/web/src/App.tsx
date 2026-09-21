import { useRoute } from './lib/router';
import { ImportDrawing } from './screens/ImportDrawing';
import { NewPlan } from './screens/NewPlan';
import { Rack } from './screens/Rack';
import { Workspace } from './screens/Workspace';

export function App() {
  const route = useRoute();
  if (route.name === 'plan') return <Workspace key={route.id} planId={route.id} />;
  if (route.name === 'new') return <NewPlan />;
  if (route.name === 'import') return <ImportDrawing />;
  return <Rack />;
}
