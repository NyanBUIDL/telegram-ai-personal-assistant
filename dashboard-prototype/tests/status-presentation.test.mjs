import test from 'node:test';
import assert from 'node:assert/strict';
import { formatDate, statusTone } from '../src/format.js';

test('invalid dates remain safe to render', () => {
  for (const value of [null, undefined, '', 'not-a-date', Infinity]) assert.equal(formatDate(value), '—');
  assert.notEqual(formatDate('2026-10-10T00:00:00Z'), '—');
});

test('status tones use exact outcomes, never success substrings', () => {
  for (const value of ['completed_with_warning', 'uncertain', 'pause_requested', 'stale', 'unknown']) assert.equal(statusTone(value), 'yellow', value);
  for (const value of ['completed', 'ok', 'executed']) assert.equal(statusTone(value), 'success', value);
  for (const value of ['failed', 'denied', 'cancelled']) assert.equal(statusTone(value), 'magenta', value);
  for (const value of ['not_completed', 'online_but_unverified', 'successful_guess']) assert.notEqual(statusTone(value), 'success', value);
});

test('connection evidence expires at 60s and rejects absent, invalid or future times', async () => {
  const { connectionPresentation } = await import('../src/statusPresentation.js');
  const now = Date.parse('2026-10-10T00:01:00Z');
  for (const checked_at of [null, undefined, '', 'bad', '2026-10-10T00:01:01Z']) {
    const result = connectionPresentation({ state: 'ready', checked_at }, now);
    assert.equal(result.online, false); assert.equal(result.state, 'unknown');
  }
  assert.equal(connectionPresentation({ state: 'ready', checked_at: '2026-10-10T00:00:01Z' }, now).online, true);
  const expired = connectionPresentation({ status: 'ok', checked_at: '2026-10-10T00:00:00Z' }, now);
  assert.equal(expired.online, false); assert.equal(expired.stale, true);
  assert.equal(connectionPresentation({ state: 'unknown', checked_at: '2026-10-10T00:01:00Z' }, now).online, false);
  assert.equal(connectionPresentation(null, now).online, false);
  assert.equal(connectionPresentation({ state: 'disconnected', checked_at: null }, now).state, 'disconnected');
  assert.equal(connectionPresentation({ state: 'degraded', checked_at: null }, now).online, false);
  const failed = connectionPresentation({ state: 'ready', checked_at: '2026-10-10T00:01:00Z' }, now, true);
  assert.equal(failed.online, false); assert.match(failed.label, /DỮ LIỆU CŨ/);
});

test('percentage preserves real zero and does not invent unknown measurements', async () => {
  const { formatPercent } = await import('../src/statusPresentation.js');
  assert.equal(formatPercent(0), '0.0%'); assert.equal(formatPercent(2.25), '2.3%');
  for (const value of [null, undefined, '', '0', NaN, Infinity, -1]) assert.equal(formatPercent(value), 'Chưa biết');
});
