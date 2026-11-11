import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AccessibilityInfo, Alert, Animated, AppState, Easing, KeyboardAvoidingView, Linking, Platform, Pressable,
  ScrollView, StyleSheet, Text, TextInput, useColorScheme, View,
} from 'react-native';
import { SafeAreaProvider, useSafeAreaInsets } from 'react-native-safe-area-context';
import {
  clearCredentials, loadCredentials, loadStatus, queueStore, saveCredentials, type Status,
} from './src/storage';
import { isTracking, startTracking, stopTracking } from './src/tracking';

// ---------- theme ----------
const light = {
  bg: '#f4f6f2', card: '#ffffff', border: '#dfe5e0', text: '#14261c', muted: '#5a6b60',
  brand: '#1f6b3f', onBrand: '#ffffff', danger: '#b3261e', warn: '#8a5a00', warnBg: '#fff4d6',
  errBg: '#fde8e6', input: '#ffffff',
};
const dark: typeof light = {
  bg: '#0e1612', card: '#17221b', border: '#2a3a30', text: '#eaf2ec', muted: '#9bb0a2',
  brand: '#3fae6b', onBrand: '#06170d', danger: '#ff8a80', warn: '#ffd27a', warnBg: '#3a2f12',
  errBg: '#3d1c19', input: '#101a14',
};

// Plain http is allowed only for local development; real fleets must use HTTPS.
function validServer(url: string): boolean {
  return /^https:\/\/[^\s/]+/i.test(url) || /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?/i.test(url);
}

function ago(at: number, now: number): string {
  const s = Math.max(0, Math.round((now - at) / 1000));
  if (s < 10) return 'just now';
  if (s < 60) return `${s} seconds ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  return new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

type Errors = { server?: string; id?: string; token?: string };

function Root() {
  const insets = useSafeAreaInsets();
  const scheme = useColorScheme();
  const c = scheme === 'dark' ? dark : light;
  const s = useMemo(() => makeStyles(c), [c]);

  const [serverUrl, setServerUrl] = useState('');
  const [deviceId, setDeviceId] = useState('');
  const [token, setToken] = useState('');
  const [hasSaved, setHasSaved] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [active, setActive] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [pending, setPending] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [reduceMotion, setReduceMotion] = useState(false);
  const pulse = useRef(new Animated.Value(0)).current;

  const refresh = useCallback(async () => {
    setActive(await isTracking());
    setStatus(await loadStatus());
    setPending((await queueStore.load()).length);
    setNow(Date.now());
  }, []);

  useEffect(() => {
    (async () => {
      const saved = await loadCredentials();
      if (saved) {
        setServerUrl(saved.serverUrl);
        setDeviceId(saved.deviceId);
        setHasSaved(true);
      }
      await refresh();
    })();
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion);
    const rm = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduceMotion);
    const sub = AppState.addEventListener('change', (st) => st === 'active' && refresh());
    const timer = setInterval(refresh, 5000);
    return () => { rm.remove(); sub.remove(); clearInterval(timer); };
  }, [refresh]);

  // A slow pulse on the live dot; skipped entirely when Reduce Motion is on.
  useEffect(() => {
    if (!active || reduceMotion) { pulse.setValue(0); return; }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 1100, easing: Easing.out(Easing.quad), useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 900, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [active, reduceMotion, pulse]);

  function validate(): Errors {
    const e: Errors = {};
    if (!validServer(serverUrl.trim())) e.server = 'Enter your dotTracker address, starting with https://';
    if (!deviceId.trim()) e.id = 'Enter the tracker ID your administrator gave you.';
    const t = token.trim();
    if ((!hasSaved || t) && t.length < 16) e.token = 'The credential is at least 16 characters.';
    return e;
  }

  async function onStart() {
    setNotice(null);
    const e = validate();
    setErrors(e);
    if (Object.keys(e).length) return;
    setBusy(true);
    try {
      const id = deviceId.trim();
      const stored = await loadCredentials();
      const secret = token.trim() || (stored && stored.deviceId === id ? stored.token : '');
      if (secret.length < 16) {
        setErrors({ token: 'Enter the tracker credential for this ID.' });
        return;
      }
      await saveCredentials({ serverUrl: serverUrl.trim(), deviceId: id, token: secret });
      setHasSaved(true);
      setToken('');
      const result = await startTracking();
      if (!result.ok) setNotice(result.reason);
    } catch {
      setNotice('Could not start sharing. Please try again.');
    } finally {
      setBusy(false);
      await refresh();
    }
  }

  function onStop() {
    Alert.alert('Stop sharing location?', 'Dispatch will no longer see this vehicle until you start again.', [
      { text: 'Keep sharing', style: 'cancel' },
      {
        text: 'Stop sharing', style: 'destructive',
        onPress: async () => { setBusy(true); await stopTracking(); setBusy(false); await refresh(); },
      },
    ]);
  }

  function onForget() {
    Alert.alert('Remove saved credential?', 'Sharing will stop, and you will need the credential again to restart.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Remove', style: 'destructive',
        onPress: async () => {
          await stopTracking(); await clearCredentials();
          setHasSaved(false); setToken(''); await refresh();
        },
      },
    ]);
  }

  const kind = !active ? 'idle' : status?.kind === 'error' ? 'error' : pending > 0 && status?.kind !== 'ok' ? 'warn' : 'ok';
  const banner =
    status?.kind === 'error' ? { text: status.message, bg: c.errBg, fg: c.danger }
    : active && status?.kind === 'warn' ? { text: status.message, bg: c.warnBg, fg: c.warn }
    : notice ? { text: notice, bg: c.errBg, fg: c.danger }
    : null;
  const dotColor = kind === 'ok' ? c.brand : kind === 'warn' ? c.warn : kind === 'error' ? c.danger : c.muted;

  return (
    <KeyboardAvoidingView style={s.root} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView
        contentContainerStyle={[s.body, { paddingTop: insets.top + 20, paddingBottom: insets.bottom + 28 }]}
        keyboardShouldPersistTaps="handled"
      >
        <Text style={s.brand} accessibilityRole="header">dotTracker</Text>
        <Text style={s.tag}>Fleet location sharing</Text>

        <View style={s.hero} accessible accessibilityLabel={active ? 'Location sharing is on' : 'Location sharing is off'}>
          <View style={s.dotWrap}>
            {active && !reduceMotion ? (
              <Animated.View
                style={[s.halo, { backgroundColor: dotColor, opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.35, 0] }),
                  transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 2.4] }) }] }]}
              />
            ) : null}
            <View style={[s.dot, { backgroundColor: dotColor }]} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={s.heroTitle}>{active ? 'Sharing location' : 'Not sharing'}</Text>
            <Text style={s.heroSub}>
              {active
                ? status ? `Last update ${ago(status.at, now)}` : 'Waiting for the first GPS fix…'
                : 'Dispatch cannot see this vehicle.'}
            </Text>
          </View>
        </View>

        {banner ? (
          <View style={[s.banner, { backgroundColor: banner.bg }]} accessibilityRole="alert">
            <Text style={[s.bannerText, { color: banner.fg }]}>{banner.text}</Text>
            {notice && /Always/.test(notice) ? (
              <Pressable onPress={() => Linking.openSettings()} accessibilityRole="button">
                <Text style={[s.bannerLink, { color: banner.fg }]}>Open Settings</Text>
              </Pressable>
            ) : null}
          </View>
        ) : null}

        {active ? (
          <View style={s.stats}>
            <View style={s.stat}>
              <Text style={s.statValue}>{status?.accuracyM != null ? `±${Math.round(status.accuracyM)} m` : '—'}</Text>
              <Text style={s.statLabel}>GPS accuracy</Text>
            </View>
            <View style={s.stat}>
              <Text style={s.statValue}>{status?.speedKmh != null ? `${Math.round(status.speedKmh * 0.621371)} mph` : '—'}</Text>
              <Text style={s.statLabel}>Speed</Text>
            </View>
            <View style={s.stat}>
              <Text style={s.statValue}>{pending}</Text>
              <Text style={s.statLabel}>Waiting to send</Text>
            </View>
          </View>
        ) : null}

        <View style={s.card}>
          <Text style={s.cardTitle}>Vehicle setup</Text>
          <Field s={s} c={c} label="Server address" value={serverUrl} onChange={setServerUrl} disabled={active}
            placeholder="https://tracker.example.com" error={errors.server} keyboardType="url" />
          <Field s={s} c={c} label="Tracker ID" value={deviceId} onChange={setDeviceId} disabled={active}
            placeholder="Given by your administrator" error={errors.id} />
          <Field s={s} c={c} label="Tracker credential" value={token} onChange={setToken} disabled={active}
            placeholder={hasSaved ? 'Saved on this phone — leave blank to keep' : 'Shown once when the tracker is assigned'}
            error={errors.token} secure={!showToken}
            trailing={
              <Pressable onPress={() => setShowToken((v) => !v)} accessibilityRole="button" hitSlop={10}
                accessibilityLabel={showToken ? 'Hide credential' : 'Show credential'}>
                <Text style={s.trail}>{showToken ? 'Hide' : 'Show'}</Text>
              </Pressable>
            }
          />
        </View>

        {active ? (
          <Pressable style={({ pressed }) => [s.btn, s.stop, pressed && s.pressed]} onPress={onStop} disabled={busy}
            accessibilityRole="button" accessibilityLabel="Stop sharing location">
            <Text style={s.btnText}>Stop sharing</Text>
          </Pressable>
        ) : (
          <Pressable style={({ pressed }) => [s.btn, s.go, (pressed || busy) && s.pressed]} onPress={onStart} disabled={busy}
            accessibilityRole="button" accessibilityLabel="Start sharing location">
            <Text style={s.btnTextGo}>{busy ? 'Starting…' : 'Start sharing'}</Text>
          </Pressable>
        )}

        <Text style={s.note}>
          While sharing, your location is sent about every 15 seconds or 25 meters, even with the app closed. A blue
          indicator shows on iPhone whenever it is on. Share only while driving for work, and stop when your shift ends.
        </Text>
        {hasSaved ? (
          <Pressable onPress={onForget} accessibilityRole="button" hitSlop={8}>
            <Text style={s.link}>Remove saved credential from this phone</Text>
          </Pressable>
        ) : null}
      </ScrollView>
      <StatusBar style={scheme === 'dark' ? 'light' : 'dark'} />
    </KeyboardAvoidingView>
  );
}

function Field(props: {
  s: ReturnType<typeof makeStyles>; c: typeof light; label: string; value: string; onChange: (v: string) => void;
  disabled?: boolean; placeholder?: string; error?: string; secure?: boolean; keyboardType?: 'url';
  trailing?: React.ReactNode;
}) {
  const { s, c } = props;
  return (
    <View style={{ marginTop: 14 }}>
      <Text style={s.label}>{props.label}</Text>
      <View style={[s.inputRow, props.error ? { borderColor: c.danger } : null, props.disabled ? { opacity: 0.6 } : null]}>
        <TextInput
          style={s.input} value={props.value} onChangeText={props.onChange} editable={!props.disabled}
          placeholder={props.placeholder} placeholderTextColor={c.muted} secureTextEntry={props.secure}
          autoCapitalize="none" autoCorrect={false} keyboardType={props.keyboardType}
          accessibilityLabel={props.label} accessibilityHint={props.error}
        />
        {props.trailing}
      </View>
      {props.error ? <Text style={s.error} accessibilityRole="alert">{props.error}</Text> : null}
    </View>
  );
}

export default function App() {
  return (
    <SafeAreaProvider>
      <Root />
    </SafeAreaProvider>
  );
}

function makeStyles(c: typeof light) {
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: c.bg },
    body: { paddingHorizontal: 20 },
    brand: { fontSize: 30, fontWeight: '800', color: c.brand, letterSpacing: -0.5 },
    tag: { color: c.muted, marginTop: 2, marginBottom: 18, fontSize: 15 },
    hero: { flexDirection: 'row', alignItems: 'center', gap: 16, backgroundColor: c.card, borderRadius: 16,
      padding: 18, borderWidth: 1, borderColor: c.border },
    dotWrap: { width: 22, height: 22, alignItems: 'center', justifyContent: 'center' },
    dot: { width: 14, height: 14, borderRadius: 7 },
    halo: { position: 'absolute', width: 14, height: 14, borderRadius: 7 },
    heroTitle: { fontSize: 20, fontWeight: '700', color: c.text },
    heroSub: { color: c.muted, marginTop: 2, fontSize: 14 },
    banner: { marginTop: 12, borderRadius: 12, padding: 14 },
    bannerText: { fontSize: 14, lineHeight: 20, fontWeight: '600' },
    bannerLink: { marginTop: 8, fontWeight: '700', textDecorationLine: 'underline' },
    stats: { flexDirection: 'row', gap: 10, marginTop: 12 },
    stat: { flex: 1, backgroundColor: c.card, borderRadius: 14, paddingVertical: 14, paddingHorizontal: 10,
      alignItems: 'center', borderWidth: 1, borderColor: c.border },
    statValue: { fontSize: 18, fontWeight: '700', color: c.text },
    statLabel: { fontSize: 12, color: c.muted, marginTop: 3, textAlign: 'center' },
    card: { marginTop: 16, backgroundColor: c.card, borderRadius: 16, padding: 18, borderWidth: 1, borderColor: c.border },
    cardTitle: { fontSize: 16, fontWeight: '700', color: c.text },
    label: { fontWeight: '600', color: c.text, marginBottom: 6, fontSize: 14 },
    inputRow: { flexDirection: 'row', alignItems: 'center', backgroundColor: c.input, borderWidth: 1,
      borderColor: c.border, borderRadius: 10, paddingHorizontal: 12 },
    input: { flex: 1, paddingVertical: 13, fontSize: 16, color: c.text },
    trail: { color: c.brand, fontWeight: '700', paddingLeft: 10 },
    error: { color: c.danger, marginTop: 6, fontSize: 13 },
    btn: { marginTop: 20, borderRadius: 14, minHeight: 54, alignItems: 'center', justifyContent: 'center' },
    go: { backgroundColor: c.brand },
    stop: { backgroundColor: c.danger },
    pressed: { opacity: 0.8 },
    btnText: { color: '#fff', fontSize: 17, fontWeight: '700' },
    btnTextGo: { color: c.onBrand, fontSize: 17, fontWeight: '700' },
    note: { marginTop: 18, color: c.muted, fontSize: 13, lineHeight: 19 },
    link: { marginTop: 16, color: c.brand, textDecorationLine: 'underline', fontSize: 14 },
  });
}
