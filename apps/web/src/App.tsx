import { useRoute } from './lib/router';
import { Rack } from './screens/Rack';
import { Workspace } from './screens/Workspace';

export function App() {
  const route = useRoute();
  if (route.name === 'plan') return <Workspace key={route.id} planId={route.id} />;
  return <Rack />;
}
