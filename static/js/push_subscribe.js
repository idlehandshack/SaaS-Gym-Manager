// EnterGYM Web Push Subscription Handler

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64  = (base64String + padding)
        .replace(/-/g, '+')
        .replace(/_/g, '/');
    const raw = atob(base64);
    return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}

function getCookie(name) {
    return document.cookie
        .split(';')
        .map(c => c.trim())
        .find(c => c.startsWith(name + '='))
        ?.split('=')[1] ?? '';
}

async function subscribeToWebPush() {
    // Read VAPID key from meta element — injected for ALL authenticated users,
    // not just staff. If missing, the user is unauthenticated; skip silently.
    const metaEl = document.getElementById('vapid-meta');
    if (!metaEl) return;

    const vapidKey = metaEl.dataset.key;
    if (!vapidKey) return;

    // Check browser support
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        console.log('EnterGYM: Web push not supported on this browser.');
        return;
    }

    // Don't ask again if user already denied
    if (Notification.permission === 'denied') return;

    try {
        const reg = await navigator.serviceWorker.ready;

        // Check if already subscribed
        const existing = await reg.pushManager.getSubscription();
        if (existing) {
            // Already subscribed — make sure server has it (handles re-logins,
            // new devices, and subscription rotation)
            await syncSubscriptionToServer(existing);
            return;
        }

        // Ask permission and subscribe
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            console.log('EnterGYM: Notification permission denied.');
            return;
        }

        const subscription = await reg.pushManager.subscribe({
            userVisibleOnly:      true,
            applicationServerKey: urlBase64ToUint8Array(vapidKey),
        });

        await syncSubscriptionToServer(subscription);
        console.log('EnterGYM: Web push subscription saved!');

    } catch (err) {
        console.error('EnterGYM: Push subscription error:', err);
    }
}

async function syncSubscriptionToServer(subscription) {
    await fetch('/push/subscribe/', {
        method:  'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken':  getCookie('csrftoken'),
        },
        body: JSON.stringify(subscription),
    });
}

// Run when page loads
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', subscribeToWebPush);
} else {
    subscribeToWebPush();
}