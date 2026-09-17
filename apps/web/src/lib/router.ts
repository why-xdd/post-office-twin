import { useEffect, useState } from 'react';

export type Route = { name: 'rack' } | { name: 'new' } | { name: 'plan'; id: string };

function parse(hash: string): Route {
  const match = hash.match(/^#\/plan\/([A-Za-z0-9_-]+)/);
  if (match) return { name: 'plan', id: match[1] };
  if (/^#\/new\/?$/.test(hash)) return { name: 'new' };
  return { name: 'rack' };
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash));
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);
  return route;
}

export function navigate(route: Route): void {
  window.location.hash = route.name === 'plan' ? `#/plan/${route.id}` : route.name === 'new' ? '#/new' : '#/';
}
