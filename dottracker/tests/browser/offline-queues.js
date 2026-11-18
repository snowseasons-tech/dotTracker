// Offline queue checks: observation pins, temporary checkpoints, safety checklists and dispatch status.
// Each queue is written while the browser is offline, sent when it reconnects, and must reach the
// server exactly once, even when the same item is replayed afterwards. The failed-send check keeps
// every queued item for the next attempt.
// Run against a dotTracker you are allowed to change (it creates assets, pins, checklists and a route):
//   npm install playwright@1 && npx playwright install chromium
//   DOTTRACKER_URL=http://localhost:8000 DOTTRACKER_EMAIL=admin@dottracker.local \
//   DOTTRACKER_PASSWORD=dottracker-demo node tests/browser/offline-queues.js
// Exits 1 if any check fails.
const {chromium} = require('playwright');

const BASE = (process.env.DOTTRACKER_URL || 'http://localhost:8000').replace(/\/$/, '');
const EMAIL = process.env.DOTTRACKER_EMAIL || 'admin@dottracker.local';
const PASSWORD = process.env.DOTTRACKER_PASSWORD || 'dottracker-demo';
const TAG = `oq${Date.now()}`;
const results = [];
const check = (name, ok, detail = '') => {
  results.push(!!ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
};

async function api(path, options = {}, token) {
  const headers = {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})};
  const response = await fetch(BASE + '/api' + path, {...options, headers});
  return {status: response.status, body: await response.json().catch(() => ({}))};
}

async function signIn(page) {
  await page.goto(BASE + '/');
  await page.fill('#email', EMAIL);
  await page.fill('#password', PASSWORD);
  await page.click('#loginBtn');
  await page.waitForSelector('#changePasswordPhone', {state: 'attached', timeout: 20000});
  await page.waitForFunction(() => document.querySelector('#userEmail').textContent.length > 0, null, {timeout: 20000});
}

// Places a pin at the centre of the map and fills the observation form, leaving it to the caller to save.
async function openPin(page, note, kind = 'observation') {
  await page.click('#dropPin');
  const box = await page.locator('#map').boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await page.waitForFunction(() => document.querySelector('#modalback').style.display === 'grid', null, {timeout: 10000});
  await page.selectOption('#obsKind', kind);
  await page.fill('#obsNote', note);
}

const queueLength = page => page.evaluate(() => JSON.parse(localStorage.getItem('dt_pending_observations') || '[]').length);
const waitForQueue = (page, length) => page.waitForFunction(n => JSON.parse(localStorage.getItem('dt_pending_observations') || '[]').length === n, length, {timeout: 15000}).then(() => true, () => false);
const countObservations = async (token, note) => (await api('/observations', {}, token)).body.filter(row => row.note === note).length;
const countCheckpoints = async (token, note) => (await api('/v1/checkpoints?west=-180&south=-90&east=180&north=90', {}, token)).body.filter(row => row.note === note).length;

(async () => {
  const adminToken = (await api('/auth/login', {method: 'POST', body: JSON.stringify({email: EMAIL, password: PASSWORD})})).body.access_token;
  const browser = await chromium.launch();
  const context = await browser.newContext({permissions: ['clipboard-read', 'clipboard-write']});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await signIn(page);

  // 1. An observation pin written offline reaches the server once, and replaying it does not duplicate it.
  const pinNote = `pin ${TAG}`;
  await context.setOffline(true);
  await openPin(page, pinNote);
  await page.click('#modalSave');
  check('pin is queued in the browser while offline', await waitForQueue(page, 1));
  const queued = await page.evaluate(() => JSON.parse(localStorage.getItem('dt_pending_observations')));
  check('queued pin is not on the server while offline', (await countObservations(adminToken, pinNote)) === 0);
  await context.setOffline(false);
  check('queue empties after reconnect', await waitForQueue(page, 0));
  check('server stores the pin exactly once', (await countObservations(adminToken, pinNote)) === 1);
  await page.evaluate(items => {
    localStorage.setItem('dt_pending_observations', JSON.stringify(items));
    window.dispatchEvent(new Event('online'));
  }, queued);
  check('replaying an already-sent pin empties the queue', await waitForQueue(page, 0));
  check('replayed pin is not duplicated', (await countObservations(adminToken, pinNote)) === 1);

  // 2. A failed send keeps every queued pin, in order, until a later attempt succeeds.
  const first = `failed a ${TAG}`;
  const second = `failed b ${TAG}`;
  await context.setOffline(true);
  await openPin(page, first);
  await page.click('#modalSave');
  await openPin(page, second);
  await page.click('#modalSave');
  check('two pins are queued while offline', await waitForQueue(page, 2));
  await page.route('**/api/observations', route => route.request().method() === 'POST'
    ? route.fulfill({status: 503, contentType: 'application/json', body: '{"detail":"unavailable"}'})
    : route.continue());
  await context.setOffline(false);
  await page.waitForTimeout(2000);
  check('a failed send keeps both pins queued', (await queueLength(page)) === 2);
  check('a failed send stores neither pin', (await countObservations(adminToken, first)) === 0 && (await countObservations(adminToken, second)) === 0);
  await page.unroute('**/api/observations');
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  check('a later attempt sends both pins and empties the queue', await waitForQueue(page, 0));
  check('both pins are stored exactly once', (await countObservations(adminToken, first)) === 1 && (await countObservations(adminToken, second)) === 1);

  // 3. A temporary checkpoint written offline is sent once, as a checkpoint.
  const checkpointNote = `checkpoint ${TAG}`;
  await context.setOffline(true);
  await openPin(page, checkpointNote, 'mobile_dot_checkpoint');
  await page.click('#modalSave');
  check('checkpoint is queued while offline', await waitForQueue(page, 1));
  await context.setOffline(false);
  check('checkpoint queue empties after reconnect', await waitForQueue(page, 0));
  check('server stores the checkpoint exactly once', (await countCheckpoints(adminToken, checkpointNote)) === 1);

  // 4. A safety checklist submitted offline is sent once, and replay does not resend it.
  const safetyNote = `safety ${TAG}`;
  await page.click('[data-view="safety"]');
  await page.waitForSelector('#safetyForm', {state: 'visible', timeout: 15000});
  await context.setOffline(true);
  for (const select of await page.locator('#safetyForm select').all()) await select.selectOption('checked');
  await page.fill('#safetyForm textarea[name="notes"]', safetyNote);
  await page.click('#safetyForm button[type="submit"]');
  await page.waitForFunction(() => document.querySelector('#safetyStatus').textContent.includes('1 checklists pending sync'), null, {timeout: 10000}).then(() => true, () => false);
  check('checklist shows as pending while offline', (await page.textContent('#safetyStatus')).includes('1 checklists pending sync'));
  await context.setOffline(false);
  check('checklist queue empties after reconnect', await page.waitForFunction(() => document.querySelector('#safetyStatus').textContent.includes('0 checklists pending sync'), null, {timeout: 15000}).then(() => true, () => false));
  const submissions = (await api('/safety/submissions', {}, adminToken)).body.filter(row => row.notes === safetyNote);
  check('server stores the checklist exactly once', submissions.length === 1, `found ${submissions.length}`);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForTimeout(1500);
  check('replaying the checklist does not resend it', (await api('/safety/submissions', {}, adminToken)).body.filter(row => row.notes === safetyNote).length === 1);

  // 5. A dispatch status change made offline is sent once with its idempotency key.
  const asset = (await api('/assets', {method: 'POST', body: JSON.stringify({name: `Dispatch ${TAG}`, kind: 'vehicle'})}, adminToken)).body;
  const route = {departure_minute: 480, crew_equipment: [], jobs: [{id: 'job-a'}, {id: 'job-b'}],
    travel_minutes: [[0, 10, 10], [null, 0, 5], [null, 5, 0]]};
  check('dispatch route is published', (await api(`/assets/${asset.id}/dispatch-route`, {method: 'PUT', body: JSON.stringify(route)}, adminToken)).status === 200);
  // The page lists assets as they were when it loaded, so reload to include the new asset.
  await page.reload();
  await page.waitForSelector('#changePasswordPhone', {state: 'attached', timeout: 20000});
  await page.waitForTimeout(1000);
  await page.click('[data-view="dispatch"]');
  await page.waitForSelector('#dispatchAsset', {state: 'attached', timeout: 15000});
  await page.selectOption('#dispatchAsset', String(asset.id));
  await page.waitForFunction(() => document.querySelectorAll('#dispatchStops select').length === 2, null, {timeout: 15000}).then(() => true, () => false);
  check('route is cached while online', (await page.locator('#dispatchStops select').count()) === 2);
  await context.setOffline(true);
  await page.locator('#dispatchStops select').first().selectOption('completed');
  await page.waitForFunction(() => document.querySelector('#dispatchStatus').textContent.includes('1 changes pending sync'), null, {timeout: 10000}).then(() => true, () => false);
  check('status change is pending while offline', (await page.textContent('#dispatchStatus')).includes('1 changes pending sync'));
  check('server has no status change while offline', (await api(`/assets/${asset.id}/dispatch-route`, {}, adminToken)).body.progress?.['job-a'] === undefined
    && (await api(`/assets/${asset.id}/dispatch-route`, {}, adminToken)).body.progress?.['job-b'] === undefined);
  await context.setOffline(false);
  check('dispatch queue empties after reconnect', await page.waitForFunction(() => document.querySelector('#dispatchStatus').textContent.includes('0 changes pending sync'), null, {timeout: 15000}).then(() => true, () => false));
  const afterSync = (await api(`/assets/${asset.id}/dispatch-route`, {}, adminToken)).body;
  const completed = Object.values(afterSync.progress || {}).filter(status => status === 'completed').length;
  check('server records the status change exactly once', completed === 1, `completed jobs on server: ${completed}`);
  const revision = afterSync.revision;
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForTimeout(1500);
  const replayed = (await api(`/assets/${asset.id}/dispatch-route`, {}, adminToken)).body;
  check('replaying the status change leaves the server unchanged', Object.values(replayed.progress || {}).filter(status => status === 'completed').length === 1 && replayed.revision === revision);

  check('no uncaught page errors', errors.length === 0, errors.join(' | ').slice(0, 300));
  await browser.close();
  const failed = results.filter(r => !r).length;
  console.log(`\n${results.length - failed}/${results.length} checks passed`);
  process.exit(failed ? 1 : 0);
})().catch(error => { console.error('SCRIPT ERROR', error); process.exit(2); });
