import assert from 'node:assert/strict';
import test from 'node:test';
import { api } from '../src/api.js';

async function capture(operation) {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({url, body: JSON.parse(options.body)});
    return new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}});
  };
  try { await operation(); return calls; }
  finally { globalThis.fetch = previous; }
}

test('learning selection sends exact large signed decimal strings', async () => {
  const ids = ['9007199254740993', '-9007199254740993', '-1001234567890'];
  const calls = await capture(() => api.createEnableSelectionAction(ids, 42));
  assert.equal(calls[0].url, '/api/v1/knowledge/enable-selection-action');
  assert.deepEqual(calls[0].body, {chat_ids: ids, limit: 42});
});

test('preference transport normalizes safe legacy IDs and preserves large strings', async () => {
  const calls = await capture(() => api.updatePreferences({
    group_page_size: 20,
    always_keep_chat_ids: [123, '-9007199254740993'],
    ignored_recommendation_chat_ids: ['9007199254740993', -1001234567890],
  }));
  assert.equal(calls[0].url, '/api/v1/preferences');
  assert.deepEqual(calls[0].body, {
    group_page_size: 20,
    always_keep_chat_ids: ['123', '-9007199254740993'],
    ignored_recommendation_chat_ids: ['9007199254740993', '-1001234567890'],
  });
});

test('keep handler transports one canonical ID without duplicates or precision loss', async () => {
  const calls = await capture(() => api.keepRecommendedGroup({always_keep_chat_ids: [123, '123', '-9007199254740993']}, '9007199254740993'));
  assert.deepEqual(calls[0].body.always_keep_chat_ids, ['123', '-9007199254740993', '9007199254740993']);
});

for (const value of [true, false, 1.5, 0, -0, '0', '-0', '+1', '01', '-01', '1.0', ' 1', '1 ', '1\n', '', 'private-fixture-value', 9007199254740992]) {
  test(`invalid ID refuses selection and preference transport (${typeof value})`, async () => {
    for (const operation of [
      () => api.createEnableSelectionAction([value]),
      () => api.updatePreferences({always_keep_chat_ids: [value]}),
      () => api.updatePreferences({ignored_recommendation_chat_ids: [value]}),
      () => api.keepRecommendedGroup({always_keep_chat_ids: []}, value),
    ]) {
      const calls = await capture(async () => {
        await assert.rejects(async () => operation(), {message: 'Chat ID không hợp lệ.'});
      });
      assert.deepEqual(calls, []);
    }
  });
}

test('modern selection refuses numeric IDs even when safe; only legacy preferences adapt them', async () => {
  const calls = await capture(async () => {
    await assert.rejects(async () => api.createEnableSelectionAction([123]), {message: 'Chat ID không hợp lệ.'});
  });
  assert.deepEqual(calls, []);
});
