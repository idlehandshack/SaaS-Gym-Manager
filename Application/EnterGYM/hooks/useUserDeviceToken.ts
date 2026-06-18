// hooks/useUserDeviceToken.ts
import { useEffect, useState } from 'react';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';

export function useUserDeviceToken() {
  const [token, setToken] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      if (!Device.isDevice) return;

      const { status: existing } = await Notifications.getPermissionsAsync();
      let status = existing;
      if (status !== 'granted') {
        const res = await Notifications.requestPermissionsAsync();
        status = res.status;
      }
      if (status !== 'granted') return;

      try {
        const tokenData = await Notifications.getDevicePushTokenAsync();
        setToken(tokenData.data);
      } catch (e) {
        console.warn('[UserDevice] token fetch failed', e);
      }
    })();
  }, []);

  return token;
}