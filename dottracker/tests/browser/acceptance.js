// Browser acceptance checks U2, U4, U5, U6 and U7 from STAGING_ACCEPTANCE.md section 8.
// Run against a dotTracker you are allowed to change (it creates an invite, a geofence and a
// safety checklist):
//   npm install playwright@1 && npx playwright install chromium
//   DOTTRACKER_URL=http://localhost:8000 DOTTRACKER_EMAIL=admin@dottracker.local \
//   DOTTRACKER_PASSWORD=dottracker-demo node tests/browser/acceptance.js
// Exits 1 if any check fails. Map tiles are blocked so the run does not depend on OpenFreeMap.
const {chromium, devices} = require('playwright');

const BASE = (process.env.DOTTRACKER_URL || 'http://localhost:8000').replace(/\/$/, '');
const EMAIL = process.env.DOTTRACKER_EMAIL || 'admin@dottracker.local';
const PASSWORD = process.env.DOTTRACKER_PASSWORD || 'dottracker-demo';
const results = {};
const record = (id, pass, detail) => { results[id] = pass; console.log(`${pass ? 'PASS' : 'FAIL'} ${id} ${JSON.stringify(detail)}`); };
const wait = (p, ms) => p.waitForTimeout(ms);

async function newPage(browser, options = {viewport: {width: 1300, height: 820}}) {
  const context = await browser.newContext({ignoreHTTPSErrors: true, ...options});
  await context.route(/tiles\.openfreemap\.org/, route => route.abort());
  const page = await context.newPage();
  page.errors = [];
  page.on('pageerror', error => page.errors.push(error.message));
  return [context, page];
}

async function signIn(page) {
  await page.goto(BASE + '/');
  await page.fill('#email', EMAIL);
  await page.fill('#password', PASSWORD);
  await page.keyboard.press('Enter');
  await page.waitForSelector('#items .asset, #items .empty', {timeout: 20000});
  await wait(page, 2500);
}

const liveLabel = page => page.evaluate(() => document.querySelector('#liveIndicator')?.innerText.trim());

async function u2(browser) {
  const [context, page] = await newPage(browser);
  await signIn(page);
  const before = await liveLabel(page);
  await context.setOffline(true);
  await wait(page, 4000);
  const offline = await liveLabel(page);
  await context.setOffline(false);
  await wait(page, 10000);
  const after = await liveLabel(page);
  record('U2 connection state is truthful', before === 'Live connection' && offline !== 'Live connection' && after === 'Live connection', {before, offline, after});
  await context.close();
}

async function u4(browser) {
  const [context, page] = await newPage(browser);
  await signIn(page);
  await context.route(/\/api\//, route => route.fulfill({status: 503, contentType: 'application/json', body: '{"detail":"Database request failed"}'}));
  await page.click('#geoBtn');
  await page.click('#chooseCircle');
  await page.fill('#geoName', 'U4 outage check');
  await page.click('#modalSave');
  await wait(page, 1000);
  const formError = await page.textContent('#fenceSaveError');
  const toast = (await page.textContent('#toast')) || '';
  await wait(page, 16000);
  const mapStatus = await page.textContent('#mapStatus');
  record('U4 API unavailable shows errors, no false success',
    /failed|unavailable|error/i.test(formError) && !/created/i.test(toast) && /failed|unavailable|retrying/i.test(mapStatus),
    {formError, mapStatus});
  await context.close();
}

async function u5(browser) {
  const [context, page] = await newPage(browser);
  await signIn(page);
  await page.click('.tab[data-view="safety"]');
  await page.waitForSelector('#safetyForm');
  const count = () => page.evaluate(async () => (await (await fetch('/api/safety/submissions',
    {headers: {Authorization: 'Bearer ' + localStorage.getItem('dt_token')}})).json()).length);
  const before = await count();
  await context.setOffline(true);
  for (const select of await page.$$('#safetyForm select')) await select.selectOption('checked');
  await page.click('#safetyForm button[type=submit]');
  await wait(page, 1500);
  const pending = await page.textContent('#safetyStatus');
  await context.setOffline(false);
  await wait(page, 12000);
  await page.reload();
  await wait(page, 6000);
  const after = await count();
  record('U5 offline safety checklist is sent once', /1 checklists pending/.test(pending) && after === before + 1, {before, pending, after});
  await context.close();
}

async function u6(browser) {
  const [context, page] = await newPage(browser);
  await page.goto(BASE + '/');
  await page.waitForSelector('#email');
  const tabTo = async (selector, max = 80) => {
    for (let i = 0; i < max; i++) {
      if (await page.evaluate(s => document.activeElement?.matches(s), selector)) return true;
      await page.keyboard.press('Tab');
    }
    return false;
  };
  const steps = {};
  steps.email = await tabTo('#email', 20);
  await page.keyboard.type(EMAIL);
  await page.keyboard.press('Tab');
  steps.password = await page.evaluate(() => document.activeElement.id === 'password');
  await page.keyboard.type(PASSWORD);
  await page.keyboard.press('Enter');
  steps.signedIn = await page.waitForSelector('#items .asset, #items .empty', {timeout: 20000}).then(() => true, () => false);
  await wait(page, 2000);
  steps.teamTab = await tabTo('.tab[data-view="team"]');
  await page.keyboard.press('Enter');
  await wait(page, 800);
  steps.inviteEmail = await tabTo('#teamInviteEmail', 20);
  await page.keyboard.type(`keyboard-${Date.now()}@example.com`);
  steps.inviteButton = await tabTo('#createTeamInvite', 10);
  await page.keyboard.press('Enter');
  await wait(page, 1200);
  steps.inviteCreated = await page.evaluate(() => !!document.querySelector('#teamInviteLink')?.value);
  steps.geofenceButton = await tabTo('#geoBtn');
  await page.keyboard.press('Enter');
  await wait(page, 400);
  steps.circle = await tabTo('#chooseCircle', 10);
  await page.keyboard.press('Enter');
  await wait(page, 400);
  steps.nameFocused = await page.evaluate(() => document.activeElement.id === 'geoName');
  await page.keyboard.press('Control+A');
  await page.keyboard.type('Keyboard-only geofence');
  steps.save = await tabTo('#modalSave', 10);
  await page.keyboard.press('Enter');
  await wait(page, 1200);
  steps.geofenceCreated = /created/i.test((await page.textContent('#toast')) || '');
  record('U6 keyboard-only sign-in, invite and geofence', Object.values(steps).every(Boolean), steps);
  await context.close();
}

async function u7(browser) {
  for (const [label, options] of [['375px', {viewport: {width: 375, height: 780}, hasTouch: true, isMobile: true}], ['iPhone 13', devices['iPhone 13']]]) {
    const [context, page] = await newPage(browser, options);
    await signIn(page);
    const width = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth}));
    const lastTool = page.locator('.maptools .tool').last();
    await lastTool.scrollIntoViewIfNeeded();
    const box = await lastTool.boundingBox();
    const reachable = !!box && box.x >= 0 && box.x + box.width <= width.client;
    record(`U7 no horizontal scroll (${label})`, width.scroll <= width.client && reachable, {...width, lastToolbarButtonReachable: reachable});
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch();
  try {
    for (const check of [u2, u4, u5, u6, u7]) {
      try { await check(browser); } catch (error) { record(check.name.toUpperCase(), false, {error: error.message}); }
    }
  } finally {
    await browser.close();
  }
  const failed = Object.entries(results).filter(([, pass]) => !pass).map(([id]) => id);
  console.log(failed.length ? `\n${failed.length} check(s) failed` : '\nAll browser checks passed');
  process.exit(failed.length ? 1 : 0);
})();
