// Sends GPS fixes to the existing dotTracker POST /api/ingest endpoint.
// Fixes are queued first so a dead zone never loses data; the API accepts fixes up
// to 30 days old and de-duplicates identical ones, so replaying the queue is safe.

export type Fix = {
  lat: number;
  lng: number;
  speedMps: number | null;
  heading: number | null;
  timestamp: number; // epoch ms
};

export type Credentials = { serverUrl: string; deviceId: string; token: string };

export type QueueStore = {
  load(): Promise<Fix[]>;
  save(queue: Fix[]): Promise<void>;
};

export type FlushResult =
  | { state: 'ok'; sent: number; pending: number }
  | { state: 'retry'; sent: number; pending: number; reason: string }
  | { state: 'rejected'; pending: number }; // server refused the tracker credential

export const MAX_QUEUE = 2000; // ~8 hours at one fix per 15 s; oldest are dropped first

// The API stores speed in km/h and heading in [0, 360).
export function toPayload(fix: Fix, c: Credentials) {
  const kmh = fix.speedMps !== null && Number.isFinite(fix.speedMps) ? fix.speedMps * 3.6 : 0;
  const heading =
    fix.heading !== null && Number.isFinite(fix.heading) && fix.heading >= 0 && fix.heading < 360
      ? fix.heading
      : null;
  return {
    device_id: c.deviceId,
    token: c.token,
    lat: fix.lat,
    lng: fix.lng,
    speed: Math.min(1000, Math.max(0, kmh)),
    heading,
    recorded_at: new Date(fix.timestamp).toISOString(),
  };
}

export function ingestUrl(serverUrl: string): string {
  return serverUrl.replace(/\/+$/, '') + '/api/ingest';
}

// Serialises flushes: the background task and the foreground screen can both fire.
let chain: Promise<unknown> = Promise.resolve();

export function enqueueAndFlush(
  store: QueueStore,
  creds: Credentials,
  fixes: Fix[],
  send: typeof fetch = fetch,
): Promise<FlushResult> {
  const run = chain.then(() => flushLocked(store, creds, fixes, send));
  chain = run.catch(() => undefined);
  return run;
}

async function flushLocked(
  store: QueueStore,
  creds: Credentials,
  fixes: Fix[],
  send: typeof fetch,
): Promise<FlushResult> {
  let queue = [...(await store.load()), ...fixes].sort((a, b) => a.timestamp - b.timestamp);
  if (queue.length > MAX_QUEUE) queue = queue.slice(queue.length - MAX_QUEUE);
  let sent = 0;

  while (queue.length > 0) {
    const fix = queue[0];
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const res = await send(ingestUrl(creds.serverUrl), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(toPayload(fix, creds)),
        signal: controller.signal,
      });
      if (res.status === 401 || res.status === 404) {
        await store.save(queue);
        return { state: 'rejected', pending: queue.length };
      }
      if (res.status === 422) {
        queue.shift(); // this fix is invalid (e.g. older than 30 days); never retry it
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const receipt = (await res.json()) as { accepted?: boolean };
      if (receipt.accepted !== true) throw new Error('Fix not accepted');
      queue.shift();
      sent += 1;
    } catch (e) {
      await store.save(queue);
      return { state: 'retry', sent, pending: queue.length, reason: String((e as Error).message ?? e) };
    } finally {
      clearTimeout(timer);
    }
  }
  await store.save(queue);
  return { state: 'ok', sent, pending: 0 };
}
