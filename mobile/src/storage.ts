import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import type { Credentials, Fix, QueueStore } from './uploader';

const CREDS_KEY = 'dottracker.creds';
const QUEUE_KEY = 'dottracker.queue';
const STATUS_KEY = 'dottracker.status';

// The tracker credential is a secret, so it goes in the iOS Keychain / Android Keystore.
// It must persist (unlike the web page) because tracking continues while the app is closed.
export async function saveCredentials(c: Credentials): Promise<void> {
  await SecureStore.setItemAsync(CREDS_KEY, JSON.stringify(c));
}
export async function loadCredentials(): Promise<Credentials | null> {
  const raw = await SecureStore.getItemAsync(CREDS_KEY);
  return raw ? (JSON.parse(raw) as Credentials) : null;
}
export async function clearCredentials(): Promise<void> {
  await SecureStore.deleteItemAsync(CREDS_KEY);
}

export const queueStore: QueueStore = {
  async load() {
    const raw = await AsyncStorage.getItem(QUEUE_KEY);
    return raw ? (JSON.parse(raw) as Fix[]) : [];
  },
  async save(queue) {
    await AsyncStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
  },
};

export type Status = {
  message: string;
  at: number;
  pending: number;
  kind: 'ok' | 'warn' | 'error';
  accuracyM?: number;
  speedKmh?: number;
};
export async function saveStatus(s: Status): Promise<void> {
  await AsyncStorage.setItem(STATUS_KEY, JSON.stringify(s));
}
export async function loadStatus(): Promise<Status | null> {
  const raw = await AsyncStorage.getItem(STATUS_KEY);
  return raw ? (JSON.parse(raw) as Status) : null;
}
