const CACHE_NAME = 'depot-v2';
const ASSETS = [
  '/',
  '/index.html',
  '/mobile',
  '/mobile.html',
  '/manifest.json',
  '/icon.svg'
];

const API_CACHE = 'depot-api-v1';

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((k) => k !== CACHE_NAME && k !== API_CACHE).map((k) => caches.delete(k))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  
  if (url.pathname === '/upload') {
    e.respondWith(handleUpload(e.request));
    return;
  }

  if (e.request.method !== 'GET') return;

  e.respondWith(
    fetch(e.request).then((r) => {
      if (r.ok && url.pathname.startsWith('/')) {
        const clone = r.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(e.request, clone));
      }
      return r;
    }).catch(() => caches.match(e.request))
  );
});

async function handleUpload(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch (error) {
    const formData = await request.formData();
    const pending = await getPendingUploads();
    pending.push({
      id: Date.now(),
      files: Array.from(formData.getAll('file')).map(f => ({
        name: f.name,
        data: await blobToBase64(f)
      })),
      timestamp: Date.now()
    });
    await savePendingUploads(pending);
    return new Response(JSON.stringify({ success: true, offline: true }), {
      headers: { 'Content-Type': 'application/json' }
    });
  }
}

async function blobToBase64(blob) {
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result);
    reader.readAsDataURL(blob);
  });
}

async function getPendingUploads() {
  const cache = await caches.open(API_CACHE);
  const response = await cache.match('/pending-uploads');
  return response ? await response.json() : [];
}

async function savePendingUploads(pending) {
  const cache = await caches.open(API_CACHE);
  await cache.put('/pending-uploads', new Response(JSON.stringify(pending)));
}

self.addEventListener('sync', (e) => {
  if (e.tag === 'sync-uploads') {
    e.waitUntil(syncPendingUploads());
  }
});

async function syncPendingUploads() {
  const pending = await getPendingUploads();
  for (const upload of pending) {
    try {
      const formData = new FormData();
      for (const file of upload.files) {
        const blob = await fetch(file.data).then(r => r.blob());
        formData.append('file', new File([blob], file.name));
      }
      await fetch('/upload', { method: 'POST', body: formData });
    } catch (e) {
      console.log('Sync failed, will retry');
    }
  }
  await savePendingUploads([]);
}

async function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('depot-offline', 1);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);
    request.onupgradeneeded = (e) => {
      e.target.result.createObjectStore('notes');
      e.target.result.createObjectStore('files');
    };
  });
}

self.addEventListener('message', async (e) => {
  if (e.data.type === 'save-notes-offline') {
    const db = await openDB();
    const tx = db.transaction('notes', 'readwrite');
    tx.objectStore('notes').put(e.data.notes, 'current');
    tx.oncomplete = () => e.ports[0].postMessage({ success: true });
  }
  if (e.data.type === 'get-notes-offline') {
    const db = await openDB();
    const tx = db.transaction('notes', 'readonly');
    const store = tx.objectStore('notes');
    const request = store.get('current');
    request.onsuccess = () => e.ports[0].postMessage({ notes: request.result || { content: '', history: [] } });
  }
});