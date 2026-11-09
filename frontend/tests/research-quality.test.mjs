import test from 'node:test';
import assert from 'node:assert/strict';
import { researchOutputNotice } from '../lib/research-quality.ts';

test('unvalidated visible output is explicitly flagged before display', () => {
  const result = { payload: { _meta: { output_fallback: 'text', structured_output_validated: false } } };
  assert.match(researchOutputNotice(result, 'zh'), /未通过.*校验/);
  assert.match(researchOutputNotice(result, 'en'), /not.*validated/i);
});

test('validated answers have no fallback notice', () => {
  assert.equal(researchOutputNotice({ payload: { _meta: {} } }, 'zh'), null);
  assert.equal(researchOutputNotice({ payload: {} }, 'zh'), null);
});
