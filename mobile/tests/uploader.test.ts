import { test } from 'node:test';
import assert from 'node:assert/strict';
import { enqueueAndFlush, toPayload, MAX_QUEUE, type Credentials, type Fix, type QueueStore } from '../src/uploader';

const creds: Credentials = { serverUrl: 'https://tracker.test/', deviceId: 'truck-1', token: 'x'.repeat(20) };
const mem = (initial: Fix[] = []): QueueStore & { q: Fix[] } => {
  const s = { q: initial, async load() { return s.q; }, async save(q: Fix[]) { s.q = q; } };
  return s;
};
const fix = (t: number): Fix => ({ lat: 39.7, lng: -104.9, speedMps: 10, heading: 90, timestamp: t });
const reply = (status: number, body: unknown = { accepted: true }) =>
  (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

test('converts m/s to km/h and rejects out-of-range heading', () => {
  const p = toPayload({ ...fix(1000), speedMps: 10, heading: -1 }, creds);
  assert.equal(p.speed, 36);
  assert.equal(p.heading, null);
  assert.equal(p.recorded_at, new Date(1000).toISOString());
});

test('sends queued fixes oldest first and empties the queue', async () => {
  const store = mem([fix(2000)]);
  const seen: string[] = [];
  const send = (async (_u: string, init: RequestInit) => {
    seen.push(JSON.parse(init.body as string).recorded_at);
    return new Response('{"accepted":true}', { status: 200 });
  }) as unknown as typeof fetch;
  const r = await enqueueAndFlush(store, creds, [fix(1000)], send);
  assert.equal(r.state, 'ok');
  assert.deepEqual(seen, [new Date(1000).toISOString(), new Date(2000).toISOString()]);
  assert.equal(store.q.length, 0);
});

test('keeps fixes when offline and retries later', async () => {
  const store = mem();
  const offline = (async () => { throw new Error('Network request failed'); }) as unknown as typeof fetch;
  const r = await enqueueAndFlush(store, creds, [fix(1), fix(2)], offline);
  assert.equal(r.state, 'retry');
  assert.equal(store.q.length, 2);
  const r2 = await enqueueAndFlush(store, creds, [], reply(200));
  assert.equal(r2.state, 'ok');
  assert.equal(store.q.length, 0);
});

test('401 and 404 report rejected and keep the queue', async () => {
  for (const status of [401, 404]) {
    const store = mem();
    const r = await enqueueAndFlush(store, creds, [fix(1)], reply(status, { detail: 'no' }));
    assert.equal(r.state, 'rejected');
    assert.equal(store.q.length, 1);
  }
});

test('422 drops the invalid fix and continues; 500 retries', async () => {
  const s1 = mem();
  assert.equal((await enqueueAndFlush(s1, creds, [fix(1)], reply(422, { detail: 'old' }))).state, 'ok');
  assert.equal(s1.q.length, 0);
  const s2 = mem();
  assert.equal((await enqueueAndFlush(s2, creds, [fix(1)], reply(500))).state, 'retry');
  assert.equal(s2.q.length, 1);
});

test('queue is capped, dropping the oldest fixes', async () => {
  const store = mem();
  const many = Array.from({ length: MAX_QUEUE + 5 }, (_, i) => fix(i));
  const offline = (async () => { throw new Error('x'); }) as unknown as typeof fetch;
  await enqueueAndFlush(store, creds, many, offline);
  assert.equal(store.q.length, MAX_QUEUE);
  assert.equal(store.q[0].timestamp, 5);
});
