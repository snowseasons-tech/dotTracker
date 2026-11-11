# dotTracker mobile

iPhone/Android app that shares a vehicle's location with the existing dotTracker API, including
while the app is closed. It replaces the foreground-only web "Phone tracker" page for drivers.

It sends fixes to the existing `POST /api/ingest` endpoint with the tracker ID and credential an admin
creates under **Phone tracker** in the dashboard. No backend changes are needed.

## How it behaves
- Starts and stops only when the driver taps the button; stop asks for confirmation.
- Sends about every 15 s or 25 m. iPhone shows its blue location indicator while sharing.
- Fixes are queued on the phone and sent when signal returns (up to 2,000, oldest dropped first).
  The server accepts fixes up to 30 days old and ignores duplicates.
- If the server rejects the credential (revoked or rotated), sharing stops and the credential is cleared.
- The credential is kept in the iOS Keychain / Android Keystore. Server address must be HTTPS
  (plain http only for localhost).

## Develop
Needs Node 22+ for the Expo CLI (the repo machine currently has Node 20, which works but warns).

```bash
npm install
npm run typecheck
npm test
```

Background location does **not** work in Expo Go. Use a development build:

```bash
npx eas-cli login                      # your Expo account
npx eas-cli build --profile development --platform ios
```

EAS builds iOS in the cloud, so no Mac is needed. Installing on a real iPhone requires an Apple
Developer account ($99/yr) to register the device and sign the build. After install:

```bash
npx expo start --dev-client
```

## Before real drivers use it
- Replace `assets/icon.png`, `assets/splash` and the Android icons with approved American Tree art.
  The files there are Expo placeholders.
- Tell drivers in writing that location is shared while the app is on, and when to switch it off.
- Confirm the production server address and issue one tracker credential per vehicle.
- Test on a real iPhone: lock the screen, drive or walk 5 minutes, confirm the dot moves on the dashboard,
  then turn on airplane mode, move, turn it off and confirm the gap fills in.

## Status
Verified: TypeScript, upload/queue unit tests (offline, 401/404, 422, 500, queue cap), iOS JS bundle builds.
Not verified: running on a device, background tracking, EAS build, a live ingest call, battery impact.
