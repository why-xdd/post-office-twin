import { describe, expect, it } from 'vitest';
import { uniqueTitle } from './titles';

describe('uniqueTitle', () => {
  it('keeps a new title as is', () => {
    expect(uniqueTitle('ОПС-1', ['ОПС-2'])).toBe('ОПС-1');
  });

  it('numbers repeated uploads', () => {
    expect(uniqueTitle('ОПС-1', ['ОПС-1'])).toBe('ОПС-1 · копия 2');
    expect(uniqueTitle('ОПС-1', ['ОПС-1', 'ОПС-1 · копия 2'])).toBe('ОПС-1 · копия 3');
  });
});
