if ('serviceWorker' in navigator) {
    window.addEventListener('load', async () => {
        try {
            const registration = await navigator.serviceWorker.register('/sw.js');

            // Reload once so this page becomes controlled — but only once ever,
            // guarded so a stuck/uncontrolled SW can't loop reloads forever.
            if (!navigator.serviceWorker.controller && !sessionStorage.getItem('sw-reloaded')) {
                sessionStorage.setItem('sw-reloaded', '1');
                window.location.reload();
            }
        } catch (err) {
            console.error('❌ Service Worker registration failed', err);
        }
    });
}