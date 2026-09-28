// Replays recordings of 2D canvas calls onto an OffscreenCanvas and sends each result back as an ImageBitmap (see
// raster.js). Images come as URLs, fetched and decoded here, or as transferred ImageBitmaps.
//
// message in: { id, w, h, calls, sources: [[key, ImageBitmap]], drop: [keys no longer needed] }
// message out: { id, bitmap } (transferred), or { id, error }

const held = new Map();   // image key (a URL, or a number for a transferred bitmap) -> Promise of ImageBitmap

self.onmessage = async ({ data: { id, w, h, calls, sources, drop } }) => {
  for (const key of drop) { const b = held.get(key); held.delete(key); if (b) b.then(x => x.close(), () => {}); }
  for (const [key, bitmap] of sources) held.set(key, Promise.resolve(bitmap));
  try {
    const images = new Map();
    for (const call of calls) for (const a of call) {
      if (a && typeof a === 'object' && 'src' in a && !images.has(a.src)) images.set(a.src, load(a.src));
    }
    for (const [key, p] of images) images.set(key, await p);
    const c = new OffscreenCanvas(w, h), g = c.getContext('2d');
    replay(g, calls, images);
    const bitmap = c.transferToImageBitmap();
    self.postMessage({ id, bitmap }, [bitmap]);
  } catch (e) {
    self.postMessage({ id, error: String(e.message || e) });
  }
};

function load(key) {
  if (!held.has(key)) {
    if (typeof key !== 'string') return Promise.reject(new Error('image ' + key + ' was never sent'));
    held.set(key, fetch(key).then(r => (r.ok ? r.blob() : Promise.reject(new Error('missing ' + key)))).then(b => createImageBitmap(b))
      .catch(e => { held.delete(key); throw e; }));
  }
  return held.get(key);
}

function replay(g, calls, images) {
  const refs = new Map();
  const val = a => (a && typeof a === 'object' ? ('src' in a ? images.get(a.src) : refs.get(a.ref)) : a);
  for (const [name, ...args] of calls) {
    if (name === '=') g[args[0]] = val(args[1]);
    else if (name === 'createPattern') refs.set(args[0], g.createPattern(val(args[1]), args[2]));
    else if (name === 'createRadialGradient' || name === 'createLinearGradient') refs.set(args[0], g[name](...args.slice(1)));
    else if (name === 'addColorStop') refs.get(args[0]).addColorStop(args[1], args[2]);
    else g[name](...args.map(val));
  }
}
