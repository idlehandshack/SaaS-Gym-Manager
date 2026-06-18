import { useEffect, useRef } from 'react';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import { Platform } from 'react-native';
import Constants from 'expo-constants';

const BACKEND_URL = 'https://entergym.onrender.com';

// ✅ Read from expoConfig.extra — works in dev APK, prod APK, and Expo Go
const extra = Constants.expoConfig?.extra ?? {};
const API_KEY = (extra.apiKey as string) 
             || process.env.EXPO_PUBLIC_API_KEY 
             || '';
console.log('API_KEY length:', API_KEY.length);
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: true,
    shouldSetBadge: true,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
});

async function ensureAndroidChannel() {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync('entergym_orders', {
    name: 'New Orders',
    importance: Notifications.AndroidImportance.MAX,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: '#ff4d00',
  });
  await Notifications.setNotificationChannelAsync('entergym_expiry', {
    name: 'Membership Reminders',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: '#ff4d00',
  });
}

async function getDeviceToken(): Promise<string | null> {
  if (!Device.isDevice) {
    console.warn('[Notif] Not a physical device — skipping token fetch');
    return null;
  }

  const { status: existingStatus } = await Notifications.getPermissionsAsync();
  let finalStatus = existingStatus;

  if (existingStatus !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync();
    finalStatus = status;
  }

  if (finalStatus !== 'granted') {
    console.warn('[Notif] Permission denied');
    return null;
  }

  try {
    const tokenData = await Notifications.getDevicePushTokenAsync();
    console.log('[Notif] FCM token obtained:', tokenData.data?.slice(0, 20) + '...');
    return tokenData.data;
  } catch (err) {
    console.warn('[Notif] Failed to get FCM token:', err);
    return null;
  }
}

async function registerTokenWithBackend(token: string) {
  // ✅ Add this log so you can confirm in Metro what key is being sent
  console.log('[Notif] Registering with API_KEY present:', API_KEY.length > 0);

  try {
    const deviceName =
      `${Device.brand ?? ''} ${Device.modelName ?? ''}`.trim() || 'Unknown';

    const res = await fetch(`${BACKEND_URL}/devices/register/`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Api-Key': API_KEY,
      },
      body: JSON.stringify({ token, device_name: deviceName }),
    });

    if (!res.ok) {
      console.warn('[Notif] Backend returned HTTP', res.status);
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    if (!data.ok) {
      console.warn('[Notif] Backend rejected token:', data.error);
    } else {
      console.log('[Notif] Token registered successfully, created:', data.created);
    }
  } catch (err) {
    console.warn('[Notif] Could not register token:', err);
  }
}

export function useOwnerNotifications() {
  const notificationListener = useRef<Notifications.Subscription | null>(null);
  const responseListener = useRef<Notifications.Subscription | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      await ensureAndroidChannel();
      const token = await getDeviceToken();
      if (token && !cancelled) {
        await registerTokenWithBackend(token);
      }
    })();

    notificationListener.current = Notifications.addNotificationReceivedListener(
      (_notification) => {}
    );
    responseListener.current = Notifications.addNotificationResponseReceivedListener(
      (_response) => {}
    );

    return () => {
      cancelled = true;
      notificationListener.current?.remove();
      responseListener.current?.remove();
    };
  }, []);
}