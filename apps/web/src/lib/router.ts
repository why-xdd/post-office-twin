import { useEffect, useState } from 'react';

export type Route =
  | { name: 'rack' }
  | { name: 'new' }
  | { name: 'import' }
  | { name: 'network' }
  | { name: 'plan'; id: string }
  | { name: 'tags'; id: string }
  | { name: 'report'; id: string }
  | { name: 'tag'; id: string; object: string };

const ID = '([A-Za-z0-9_-]+)';
// Идентификатор объекта задаёт заказчик: в нём бывают точки и другие знаки, поэтому он кодируется.
const OBJECT = '([^/?#]+)';

export function parse(hash: string): Route {
  let match = hash.match(new RegExp(`^#/tag/${ID}/${OBJECT}`));
  if (match) return { name: 'tag', id: match[1], object: decodeURIComponent(match[2]) };
  match = hash.match(new RegExp(`^#/plan/${ID}/report/?$`));
  if (match) return { name: 'report', id: match[1] };
  match = hash.match(new RegExp(`^#/plan/${ID}/tags/?$`));
  if (match) return { name: 'tags', id: match[1] };
  match = hash.match(new RegExp(`^#/plan/${ID}`));
  if (match) return { name: 'plan', id: match[1] };
  if (/^#\/new\/?$/.test(hash)) return { name: 'new' };
  if (/^#\/import\/?$/.test(hash)) return { name: 'import' };
  if (/^#\/network\/?$/.test(hash)) return { name: 'network' };
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

export function hashOf(route: Route): string {
  switch (route.name) {
    case 'plan':
      return `#/plan/${route.id}`;
    case 'tags':
      return `#/plan/${route.id}/tags`;
    case 'report':
      return `#/plan/${route.id}/report`;
    case 'tag':
      return `#/tag/${route.id}/${encodeURIComponent(route.object)}`;
    case 'new':
      return '#/new';
    case 'import':
      return '#/import';
    case 'network':
      return '#/network';
    default:
      return '#/';
  }
}

/** Полная ссылка для QR-бирки: её открывают с телефона, поэтому адрес — от текущего сайта. */
export function tagUrl(planId: string, objectId: string): string {
  return `${window.location.origin}${window.location.pathname}${hashOf({ name: 'tag', id: planId, object: objectId })}`;
}

export function navigate(route: Route): void {
  window.location.hash = hashOf(route);
}
