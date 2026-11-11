import * as Location from 'expo-location';
import * as TaskManager from 'expo-task-manager';
import { clearCredentials, loadCredentials, queueStore, saveStatus } from './storage';
import { enqueueAndFlush, type Fix } from './uploader';

export const TASK = 'dottracker-background-location';

// defineTask must run at module load (index.ts imports this file) so iOS/Android can
// start the task headlessly after the app has been suspended or relaunched.
TaskManager.defineTask(TASK, async ({ data, error }) => {
  if (error) {
    await saveStatus({ message: `Location error: ${error.message}`, at: Date.now(), pending: 0, kind: 'error' });
    return;
  }
  const locations = (data as { locations?: Location.LocationObject[] } | undefined)?.locations ?? [];
  const creds = await loadCredentials();
  if (!creds || locations.length === 0) return;

  const fixes: Fix[] = locations.map((l) => ({
    lat: l.coords.latitude,
    lng: l.coords.longitude,
    speedMps: l.coords.speed,
    heading: l.coords.heading,
    timestamp: l.timestamp,
  }));
  const result = await enqueueAndFlush(queueStore, creds, fixes);

  if (result.state === 'rejected') {
    // The credential was revoked or rotated: stop rather than keep draining the battery.
    await stopTracking();
    await clearCredentials();
    await saveStatus({
      message: 'Tracker credential rejected. Ask an administrator for the current credential.',
      at: Date.now(),
      pending: result.pending,
      kind: 'error',
    });
  } else if (result.state === 'retry') {
    await saveStatus({
      message: `Offline or server unavailable. ${result.pending} fixes saved to send later.`,
      at: Date.now(),
      pending: result.pending,
      kind: 'warn',
    });
  } else {
    const last = locations[locations.length - 1];
    await saveStatus({
      message: 'Location received by dispatch',
      at: Date.now(),
      pending: 0,
      kind: 'ok',
      accuracyM: last.coords.accuracy ?? undefined,
      speedKmh: last.coords.speed && last.coords.speed > 0 ? last.coords.speed * 3.6 : 0,
    });
  }
});

export async function startTracking(): Promise<{ ok: true } | { ok: false; reason: string }> {
  const fg = await Location.requestForegroundPermissionsAsync();
  if (fg.status !== 'granted') return { ok: false, reason: 'Location permission was denied.' };
  // iOS shows the "Always" choice only after the user has granted "While Using" first.
  const bg = await Location.requestBackgroundPermissionsAsync();
  if (bg.status !== 'granted') {
    return { ok: false, reason: 'Choose "Always" for location so tracking continues in the background.' };
  }
  if (await Location.hasStartedLocationUpdatesAsync(TASK)) return { ok: true };
  await Location.startLocationUpdatesAsync(TASK, {
    accuracy: Location.Accuracy.High,
    timeInterval: 15000, // Android; iOS delivers on distance/accuracy
    distanceInterval: 25, // metres; keeps parked vehicles quiet
    activityType: Location.ActivityType.AutomotiveNavigation,
    pausesUpdatesAutomatically: false,
    showsBackgroundLocationIndicator: true, // iOS: visible blue bar, so drivers know it is on
    foregroundService: {
      notificationTitle: 'dotTracker is sharing this vehicle’s location',
      notificationBody: 'Tap to open. Stop sharing from inside the app.',
    },
  });
  return { ok: true };
}

export async function stopTracking(): Promise<void> {
  if (await Location.hasStartedLocationUpdatesAsync(TASK)) {
    await Location.stopLocationUpdatesAsync(TASK);
  }
}

export function isTracking(): Promise<boolean> {
  return Location.hasStartedLocationUpdatesAsync(TASK);
}
