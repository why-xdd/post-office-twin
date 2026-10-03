import { describe, expect, it } from 'vitest';
import { hashOf, parse } from './router';

describe('router', () => {
  it('keeps old plan links working', () => {
    expect(parse('#/plan/abc123')).toEqual({ name: 'plan', id: 'abc123' });
    expect(parse('#/')).toEqual({ name: 'rack' });
  });

  it('round-trips tag links with unusual object ids', () => {
    const route = { name: 'tag', id: 'abc', object: 'eq.window/1 №2' } as const;
    expect(parse(hashOf(route))).toEqual(route);
  });

  it('survives a damaged tag link', () => {
    expect(parse('#/tag/abc/eq_win_1%E0%A4%A')).toEqual({ name: 'tag', id: 'abc', object: 'eq_win_1%E0%A4%A' });
  });

  it('opens the printable report', () => {
    expect(parse('#/plan/abc/report')).toEqual({ name: 'report', id: 'abc' });
  });

  it('opens the tag sheet of a plan', () => {
    expect(parse('#/plan/abc/tags')).toEqual({ name: 'tags', id: 'abc' });
  });
});
