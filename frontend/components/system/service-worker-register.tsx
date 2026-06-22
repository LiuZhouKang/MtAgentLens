'use client';

import { useEffect } from 'react';

export default function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;

    const isLocalDevHost = ['localhost', '127.0.0.1'].includes(window.location.hostname);

    const syncServiceWorker = async () => {
      try {
        const registrations = await navigator.serviceWorker.getRegistrations();

        if (isLocalDevHost) {
          await Promise.all(registrations.map((registration) => registration.unregister()));
          return;
        }

        await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      } catch {
        // Ignore registration failures in local/dev environments.
      }
    };

    void syncServiceWorker();
  }, []);

  return null;
}
