// EnterGYM Web Push Subscription Handler
//
// IMPORTANT: This file no longer calls Notification.requestPermission()
// automatically on page load. It only:
//   1. Silently re-syncs an EXISTING subscription to the server (no popup).
//   2. Exposes window.EnterGYMPush.requestAndSubscribe() so the
//      notification permission card can trigger the actual browser
//      prompt only after the user opts in.

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

function getVapidKey() {
    const metaEl = document.getElementById('vapid-meta');
    return metaEl ? metaEl.dataset.key : '';
}

async function fetchWithTimeout(url, options = {}, timeout = 10000) {
    const controller = new AbortController();

    const timer = setTimeout(() => controller.abort(), timeout);

    try {
        return await fetch(url, {
            ...options,
            signal: controller.signal
        });
    } finally {
        clearTimeout(timer);
    }
}
// ── Silent path: only touches an EXISTING subscription, never prompts ──
async function resyncExistingSubscription() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;

    const vapidKey = getVapidKey();
    if (!vapidKey) return;

    // If the user already denied, there's nothing to sync and we must
    // never call requestPermission() again.
    if (Notification.permission === 'denied') return;

    try {
        const reg = await navigator.serviceWorker.ready;
        const existing = await reg.pushManager.getSubscription();
        if (existing) {
            await syncSubscriptionToServer(existing);
        }
        // NOTE: if permission is 'granted' but there's no subscription yet
        // (e.g. cleared site data), we deliberately do nothing here — the
        // notification card handles (re)subscribing on user action.
    } catch (err) {
        console.error('EnterGYM: push resync error:', err);
    }
}

// ── Explicit path: called by the notification permission card AFTER ──
// ── the user has clicked "Enable Notifications" -> "Continue"        ──
async function requestAndSubscribe() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        return { status: 'unsupported' };
    }

    const vapidKey = getVapidKey();
    if (!vapidKey) return { status: 'unsupported' };

    try {
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') {
            return { status: permission }; // 'denied' or 'default'
        }

        const reg = await navigator.serviceWorker.ready;

        let subscription = await reg.pushManager.getSubscription();
        if (!subscription) {
            subscription = await reg.pushManager.subscribe({
                userVisibleOnly:      true,
                applicationServerKey: urlBase64ToUint8Array(vapidKey),
            });
        }

        await syncSubscriptionToServer(subscription);
        return { status: 'granted' };

    } catch (err) {
        console.error('EnterGYM: Push subscription error:', err);
        return { status: 'error', error: err };
    }
}

async function syncSubscriptionToServer(subscription) {
    await fetchWithTimeout('/push/subscribe/', {
        method:  'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken':  getCookie('csrftoken'),
        },
        body: JSON.stringify(subscription),
    });
}

// Expose the explicit, user-initiated entry point for other scripts.
window.EnterGYMPush = { requestAndSubscribe };

// Run only the SILENT resync path on load — never prompts.
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', resyncExistingSubscription);
} else {
    resyncExistingSubscription();
}