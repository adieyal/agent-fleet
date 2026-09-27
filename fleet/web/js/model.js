// Shared deck state: hosts, entities, selection, connection status and the deck log.

// ------------------------------------------------------------------ state
export let hosts = [];
export const ents = new Map();        // "host:id" → android entity
export const offFloor = new Map();    // "host:id" → work with no android: a failed or stalled job, or any in a background room
export const workOf = key => ents.get(key) || offFloor.get(key);   // what the side panel shows for a key

export let selectedKey = null;
export let everLoaded = false;
export let live = { ok: false, at: 0, err: null };
export const feed = [];
export const seenEvents = new Set();
export let feedSeeded = false;

export function setHosts(value) { hosts = value; }
export function setEverLoaded(value) { everLoaded = value; }
export function setSelectedKey(value) { selectedKey = value; }
export function setFeedSeeded(value) { feedSeeded = value; }
export function setLive(value) { live = value; }
