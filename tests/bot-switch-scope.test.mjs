import test from 'node:test';
import assert from 'node:assert/strict';
import { isCurrentBotResponse } from '../lib/financial-bot/bot-switch-scope.ts';

test('late responses cannot populate a newly selected assistant', () => {
  assert.equal(isCurrentBotResponse('old-bot', 'new-bot'), false);
  assert.equal(isCurrentBotResponse('new-bot', 'new-bot'), true);
  assert.equal(isCurrentBotResponse('old-bot', null), false);
});
