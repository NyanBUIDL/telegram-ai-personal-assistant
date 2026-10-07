import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { inspect } from 'node:util';
import { fixtureMessages, withFixtureCleanup, publicFixtureMessage, navigateToDashboard } from './helpers/native-fixture.js';

function controlledChild() {
  return spawn(process.execPath, ['-e', `
    require('node:readline').createInterface({ input: process.stdin }).on('line', line => {
      if (line === 'exit') process.exit(2);
      else console.log(JSON.stringify({ type: line }));
    });
    console.log(JSON.stringify({ type: 'ready' }));
  `], { windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] });
}

test('expired message waiter cannot consume the later stop acknowledgement', async () => {
  const child = controlledChild();
  const next = fixtureMessages(child, 200);
  try {
    assert.deepEqual(await next(), { type: 'ready' });
    await assert.rejects(next(), { message: 'native_fixture_timeout' });
    child.stdin.write('stopped\n');
    assert.deepEqual(await next(), { type: 'stopped' });
  } finally { child.stdin.end(); }
});

test('actual child exit rejects both outstanding and later message waits', async () => {
  const child = controlledChild();
  const next = fixtureMessages(child, 200);
  try {
    await next();
    const pending = next();
    child.stdin.write('exit\n');
    await assert.rejects(pending, { message: 'native_fixture_exited' });
    await assert.rejects(next(), { message: 'native_fixture_exited' });
  } finally { child.stdin.end(); }
});

test('spawn failure returns a fixed code without executable path', async () => {
  const child = spawn('synthetic-private-missing-executable', [], { stdio: ['pipe', 'pipe', 'pipe'] });
  // Prevent the old harness from producing an unhandled platform error in RED.
  child.on('error', () => {});
  await assert.rejects(fixtureMessages(child, 200)(), { message: 'native_fixture_spawn_failed' });
});

test('cleanup failure preserves the original body error and separate cleanup error', async () => {
  const body = new Error('body_failed'), cleanup = new Error('owned_stop_failed');
  await assert.rejects(withFixtureCleanup(async () => { throw body; }, async () => { throw cleanup; }), error => {
    assert.equal(error.message, 'native_fixture_body_and_cleanup_failed');
    assert.deepEqual(error.errors, [body, cleanup]);
    return true;
  });
});

test('cleanup failure still fails an otherwise passing body', async () => {
  const cleanup = new Error('owned_stop_failed');
  await assert.rejects(withFixtureCleanup(async () => {}, async () => { throw cleanup; }), error => error === cleanup);
});

test('clean teardown retains an original body error', async () => {
  const body = new Error('body_failed');
  await assert.rejects(withFixtureCleanup(async () => { throw body; }, async () => {}), error => error === body);
});

test('unexpected late ticket is rejected before any assertion can print its URL', () => {
  const canary = 'synthetic-private-ticket-canary';
  for (const type of ['ready', 'dialog', 'status', 'telegram', 'telegram_resumed', 'stopped']) {
    assert.throws(() => publicFixtureMessage({ type: 'ticket', url: `http://127.0.0.1/#launch_ticket=${canary}` }, type), error => {
      assert.equal(error.message, 'native_fixture_message_invalid');
      assert.equal(inspect(error).includes(canary), false);
      assert.equal(error.cause, undefined);
      return true;
    });
  }
});

test('public message projection excludes unrelated private fields', () => {
  assert.deepEqual(publicFixtureMessage({ type: 'stopped', clean: true, owned_handle_released: true, url: 'synthetic-private-ticket-canary' }, 'stopped'), {
    type: 'stopped', clean: true, owned_handle_released: true,
  });
});

test('failed navigation reporter error never contains ticket URL or original cause', async () => {
  const canary = 'synthetic-private-ticket-canary';
  const url = `http://127.0.0.1/#launch_ticket=${canary}`;
  const page = { goto: async destination => { throw new Error(`navigation failed: ${destination}`); } };
  await assert.rejects(navigateToDashboard(page, url), error => {
    assert.equal(error.message, 'native_dashboard_navigation_failed');
    assert.equal(inspect(error).includes(canary), false);
    assert.equal(error.cause, undefined);
    return true;
  });
});

test('allowlisted worker failure retains its fixed cause without unrelated ticket data', () => {
  const canary = 'synthetic-private-ticket-canary';
  assert.throws(() => publicFixtureMessage({ type: 'failure', code: 'storage_access_denied', url: canary }, 'ready'), error => {
    assert.equal(error.message, 'native_fixture_failed:storage_access_denied');
    assert.equal(inspect(error).includes(canary), false);
    assert.equal(error.cause, undefined);
    return true;
  });
});

test('unknown private failure code is replaced by a generic fixed error', () => {
  const canary = 'synthetic-private-ticket-canary';
  assert.throws(() => publicFixtureMessage({ type: 'failure', code: canary }, 'ready'), error => {
    assert.equal(error.message, 'native_fixture_message_invalid');
    assert.equal(inspect(error).includes(canary), false);
    assert.equal(error.cause, undefined);
    return true;
  });
});
