import { createInterface } from 'node:readline';

// Private stdout may carry an ephemeral ticket. Never include its contents in errors.
export function fixtureMessages(child, timeoutMs = 30000) {
  const messages = [], waiters = [];
  let ended = null;
  function finish(code) {
    ended = new Error(code);
    for (const waiter of waiters.splice(0)) {
      clearTimeout(waiter.timeout);
      waiter.reject(ended);
    }
  }
  child.on('error', () => finish('native_fixture_spawn_failed'));
  // close follows stdout drain, so an emitted stopped acknowledgement remains readable.
  child.on('close', () => finish(ended?.message || 'native_fixture_exited'));
  createInterface({ input: child.stdout }).on('line', line => {
    let value; try { value = JSON.parse(line); } catch { return; }
    if (waiters.length) {
      const waiter = waiters.shift(); clearTimeout(waiter.timeout); waiter.done(value);
    } else messages.push(value);
  });
  child.stderr.on('data', () => {});
  return async () => {
    if (messages.length) return messages.shift();
    if (ended) throw ended;
    return new Promise((done, reject) => {
      const waiter = { done, reject, timeout: null };
      waiter.timeout = setTimeout(() => {
        const index = waiters.indexOf(waiter);
        if (index !== -1) waiters.splice(index, 1);
        reject(new Error('native_fixture_timeout'));
      }, timeoutMs);
      waiters.push(waiter);
    });
  };
}

export async function withFixtureCleanup(body, cleanup) {
  let bodyError, result;
  try { result = await body(); } catch (error) { bodyError = error; }
  try { await cleanup(); } catch (cleanupError) {
    if (bodyError) throw new AggregateError([bodyError, cleanupError], 'native_fixture_body_and_cleanup_failed');
    throw cleanupError;
  }
  if (bodyError) throw bodyError;
  return result;
}

export function publicFixtureMessage(value, expectedType) {
  const failureCodes = new Set([
    'windows_fixture_owner_unavailable', 'storage_access_denied', 'native_worker_failed',
    'native_worker_early_exit', 'readiness_timeout', 'ticket_failed', 'owned_stop_timeout',
  ]);
  if (value?.type === 'failure' && failureCodes.has(value.code)) {
    throw new Error(`native_fixture_failed:${value.code}`);
  }
  const fields = {
    ready: ['origin', 'hidden', 'profile_id'], ticket: ['url'],
    dialog: ['count', 'empty', 'launcher_visible'], status: ['count', 'launcher_visible'],
    telegram: ['empty', 'worker_stopped', 'retained_context'],
    telegram_resumed: ['ready'], stopped: ['clean', 'owned_handle_released'],
  }[expectedType];
  if (!fields || value?.type !== expectedType) throw new Error('native_fixture_message_invalid');
  const result = { type: expectedType };
  for (const field of fields) {
    const item = value[field];
    const valid = ['origin', 'profile_id', 'url'].includes(field) ? typeof item === 'string'
      : field === 'count' ? Number.isSafeInteger(item) && item >= 0 : typeof item === 'boolean';
    if (!valid) throw new Error('native_fixture_message_invalid');
    result[field] = item;
  }
  return result;
}

export async function navigateToDashboard(page, url) {
  try { await page.goto(url); } catch { throw new Error('native_dashboard_navigation_failed'); }
}
