// How things look: host colours and kit, project hues and room themes, and the room floor plan.

import { DESK_TOP, HALF, PI, RD, RW } from './env.js';
import { hash, hsl, mix } from './util.js';

// ------------------------------------------------------------------ looks: hosts → androids, projects → rooms
// Every android is the same robot; a host is told apart by its colour and one piece of kit.
const ACCESSORIES = { backpack: 'Backpack unit', antenna: 'Antenna unit', halo: 'Halo unit', crest: 'Crested unit' };
const KNOWN_HOSTS = { 'node-a': ['backpack', '#ff9340'], 'node-b': ['antenna', '#2dd4bf'], 'node-c': ['halo', '#a78bfa'], 'node-d': ['crest', '#facc15'] };
const hostLooks = new Map();
export function hostLook(name) {
  if (hostLooks.has(name)) return hostLooks.get(name);
  let look;
  if (KNOWN_HOSTS[name]) look = { acc: KNOWN_HOSTS[name][0], color: KNOWN_HOSTS[name][1] };
  else { const h = hash(name), keys = Object.keys(ACCESSORIES); look = { acc: keys[h % keys.length], color: hsl((h >>> 4) % 360, 72, 62) }; }
  look.label = ACCESSORIES[look.acc];
  hostLooks.set(name, look);
  return look;
}
export const AGENT_COLOR = { claude: '#ff8f6b', codex: '#7ce7ff' };
const PROJECT_HUES = [205, 28, 140, 265, 330, 175, 238, 300, 8, 100];   // at least 25° apart; no murky yellows
// a project keeps its hashed hue unless another room on the deck already has it; then it takes the next free one
export function projectLook(name, taken) {
  let i = hash(name) % PROJECT_HUES.length;
  for (let k = 0; k < PROJECT_HUES.length && taken.has(PROJECT_HUES[i]); k++) i = (i + 1) % PROJECT_HUES.length;
  const hue = PROJECT_HUES[i];
  taken.add(hue);
  return { hue, accent: hsl(hue, 85, 64), floor: hsl(hue, 22, 30), floor2: hsl(hue, 20, 25), wall: hsl(hue, 16, 42), rim: hsl(hue, 20, 26) };
}

// ------------------------------------------------------------------ the room (room-local tiles: x across the back wall, y towards the door)
// Furniture: [model, x, y, facing, height]; x/y is the footprint centre, facing is where the front points (0 = towards the door).
export const TERMINALS = [1.3, 3.0, 4.7];   // bash desks along the back wall
const BENCH = [5.0, 6.5];            // edit desks in the middle, worked from the far side
export const FURNITURE = [
  // (the desk chair's seat faces -z in the kit, unlike the rest of it)
  ...TERMINALS.flatMap(x => [['desk', x, 0.5, 0], ['computerScreen', x, 0.3, 0, DESK_TOP], ['computerKeyboard', x, 0.72, 0, DESK_TOP], ['chairDesk', x, 1.55, 0]]),
  ...BENCH.flatMap(x => [['desk', x, 4.6, PI], ['laptop', x, 4.55, PI, DESK_TOP], ['chairDesk', x, 3.65, PI]]),
  ['bookcaseOpen', 0.28, 2.3, HALF], ['bookcaseOpen', 0.28, 3.12, HALF],
  ['kitchenCabinetDrawer', 0.47, 5.2, HALF], ['kitchenCabinetDrawer', 0.47, 6.08, HALF],
  // '@role' is the room theme's model for that role (see THEMES); a theme can leave a role empty
  ['@sofa', 8.7, 7.5, 0], ['@sofa', 10.75, 7.5, 0], ['@rug', 9.7, 8.95, 0],
  ['@lamp', 11.6, 6.5, 0], ['@plant', 11.45, 9.35, 0], ['@corner', 0.55, 9.3, 0], ['@back', 6.05, 0.35, 0],
  ['kitchenCabinet', 0.47, 7.45, HALF], ['kitchenCabinet', 0.47, 8.33, HALF],   // kitchen corner: the coffee machine sits on top
  ['@armchair', 9.2, 5.0, 0],                                                    // reading armchair
  ['cardboardBoxClosed', 2.85, 9.5, 0.25],                                        // parcels waiting by the outbox
];
// Room themes: each project room gets one, from its name (see themeFor). A theme sets the floor pattern and palette,
// fills the furniture roles above, and adds decor and wall art in slots that stay clear of every station, so the
// androids behave the same in every room. Decor: [model, x, y, facing, height] ('rack' is a server rack built from
// primitives; height 'counter' or 'on:<model>' stacks on the kitchen counter or that model). Art: [wall, along, kind].
const RIGHT_SLOT = [11.45, 2.1], FRONT_L = [1.5, 9.5], FRONT_R = [7.0, 9.5], BENCH_RUG = [5.75, 4.3], WALL_GAP = [0.35, 4.15];
export const THEMES = {
  office: {
    label: 'open-plan office', floor: 'checker', pal: h => ({ floor: hsl(h, 22, 30), wall: hsl(h, 16, 42) }),
    roles: { sofa: 'loungeSofa', armchair: 'loungeChair', rug: 'rugRectangle', lamp: 'lampRoundFloor', plant: 'pottedPlant', corner: 'pottedPlant', back: 'pottedPlant' },
    decor: [['rugRectangle', ...BENCH_RUG, 0], ['trashcan', ...FRONT_R, 0], ['plantSmall1', ...WALL_GAP, HALF], ['coatRackStanding', ...RIGHT_SLOT, 0]],
    art: [['back', 6.05, 'poster'], ['left', 5.64, 'poster']],
  },
  lab: {
    label: 'lab', floor: 'tile', pal: h => ({ floor: hsl(h, 10, 44), wall: hsl(h, 12, 60) }),
    roles: { sofa: 'loungeSofa', armchair: 'loungeChair', rug: null, lamp: 'lampSquareFloor', plant: 'plantSmall2', corner: 'washer', back: 'plantSmall2' },
    decor: [['dryer', ...RIGHT_SLOT, -HALF], ['cardboardBoxOpen', ...FRONT_L, 0.3], ['trashcan', ...FRONT_R, 0], ['kitchenMicrowave', 0.42, 8.33, HALF, 'counter']],
    art: [['back', 6.05, 'chart'], ['left', 5.64, 'chart']],
  },
  library: {
    label: 'library', floor: 'planks', pal: h => ({ floor: mix(hsl(h, 30, 28), '#4a3526', 0.6), wall: mix(hsl(h, 20, 36), '#5b4331', 0.45) }),
    roles: { sofa: 'loungeSofa', armchair: 'loungeDesignChair', rug: 'rugRound', lamp: 'lampRoundFloor', plant: 'pottedPlant', corner: 'pottedPlant', back: 'bookcaseOpenLow' },
    decor: [['bookcaseOpen', ...RIGHT_SLOT, -HALF], ['books', 11.5, 1.95, -HALF, 0.47], ['books', 11.5, 2.25, -HALF, 0.9], ['books', 11.5, 2.0, -HALF, 1.32],
      ['books', 5.8, 0.35, 0, 'on:bookcaseOpenLow'], ['lampRoundTable', 6.35, 0.35, 0, 'on:bookcaseOpenLow'], ['rugRound', ...BENCH_RUG, 0], ['plantSmall3', ...WALL_GAP, HALF]],
    art: [['back', 6.05, 'painting'], ['left', 5.64, 'painting']],
  },
  lounge: {
    label: 'lounge and kitchen', floor: 'planks', pal: h => ({ floor: mix(hsl(h, 35, 36), '#7a5838', 0.45), wall: hsl(h, 32, 46) }),
    roles: { sofa: 'loungeDesignSofa', armchair: 'loungeChairRelax', rug: 'rugRounded', lamp: null, plant: 'plantSmall3', corner: 'kitchenFridge', back: 'pottedPlant' },
    decor: [['cabinetTelevision', 11.45, 6.5, -HALF], ['televisionModern', 11.45, 6.5, -HALF, 'on:cabinetTelevision'], ['toaster', 0.42, 8.33, HALF, 'counter'],
      ['rugRectangle', ...BENCH_RUG, 0], ['plantSmall1', ...RIGHT_SLOT, 0]],
    art: [['back', 6.05, 'canvas'], ['left', 5.64, 'painting']],
  },
  server: {
    label: 'server room', floor: 'grate', pal: h => ({ floor: hsl(h, 14, 19), wall: hsl(h, 16, 25) }),
    roles: { sofa: 'loungeSofa', armchair: 'loungeChair', rug: null, lamp: null, plant: null, corner: 'rack', back: 'rack' },
    decor: [['rack', ...RIGHT_SLOT, -HALF], ['trashcan', ...FRONT_R, 0], ['speakerSmall', ...FRONT_L, 0]],
    art: [['left', 5.64, 'panel'], ['left', 8.33, 'panel']],
  },
  studio: {
    label: 'studio', floor: 'carpet', pal: h => ({ floor: hsl(h, 30, 26), wall: hsl(h, 34, 38) }),
    roles: { sofa: 'loungeDesignSofa', armchair: 'loungeDesignChair', rug: 'rugRectangle', lamp: 'lampSquareFloor', plant: 'plantSmall1', corner: 'speaker', back: 'plantSmall2' },
    decor: [['televisionVintage', ...RIGHT_SLOT, -HALF], ['radio', 0.42, 8.33, HALF, 'counter'], ['speaker', ...FRONT_R, 0], ['rugSquare', ...BENCH_RUG, 0]],
    art: [['back', 6.05, 'canvas'], ['left', 5.64, 'canvas']],
  },
};
const THEME_KEYS = Object.keys(THEMES);
// a project keeps its hashed theme unless another room on the deck already has it (like the hue in projectLook)
export function themeFor(name, taken) {
  let i = hash(name + '#room') % THEME_KEYS.length;
  for (let k = 0; k < THEME_KEYS.length && taken.has(THEME_KEYS[i]); k++) i = (i + 1) % THEME_KEYS.length;
  taken.add(THEME_KEYS[i]);
  return THEME_KEYS[i];
}
export const KITCHEN = { x: 0.42, y: 7.45 };           // coffee machine
export const OUTBOX = { x: 3.75, y: 9.55, h: 0.95 };   // mail chute by the door: commits and pushes go in the slot on top
export const RACK = { x: 9.65, y: 0.3, h: 1.5 };       // build machine against the back wall, with a progress meter
const SEAT = 0.0;   // extra lift for androids sitting on a chair or sofa (the Sitting clip already bends to seat height)
// Spots per station, in order of preference: [x, y, facing, prop instance, sit]. Seats come first; the rest are
// standing places beside the station for when the seats are taken. Every android gets its own spot (see allocate).
// (the Sitting clip drops the hips backwards, so chair spots sit a little in front of the chair's centre)
// (seats within a station are laid out so their androids don't overlap on screen; see apart())
export const SPOTS = {
  terminal:   [...TERMINALS.map((x, i) => [x, 1.3, PI, i, SEAT]), [2.15, 2.75, PI, 0], [3.85, 2.75, PI, 1]],
  workbench:  [[4.95, 3.9, 0, 0, SEAT], [6.55, 3.9, 0, 1, SEAT], [3.6, 4.9, HALF, 0], [7.9, 4.9, -HALF, 1]],
  whiteboard: [[7.15, 1.35, PI, 0], [8.75, 1.35, PI, 0], [7.95, 2.75, PI, 0]],
  comms:      [[10.35, 2.2, PI * 0.95, 0], [9.95, 3.65, PI * 0.9, 0]],
  bookshelf:  [[1.35, 2.95, -HALF, 0], [1.35, 4.55, -HALF, 1]],
  cabinet:    [[1.6, 5.45, -0.76, 1], [1.6, 4.45, -0.67, 0]],   // behind the drawer as seen, turned to it, so it shows
  think:      [[3.2, 3.0, 0.6], [8.7, 3.3, -0.5], [3.0, 7.3, 0.9], [10.3, 6.3, -0.4], [4.8, 7.7, 0.3], [6.9, 6.4, -0.2]],
  lounge:     [[5.7, 7.7, 0.2], [6.1, 9.1, 0], [2.6, 7.7, 0.6], [7.3, 6.5, -0.4], [4.6, 6.9, 0.3]],
  dock:       [[8.7, 7.55, 0, 0, SEAT], [10.75, 7.55, 0, 1, SEAT], [8.1, 9.1, 0.3, 0], [9.75, 9.2, 0, 0], [11.35, 9.1, -0.3, 1]],
  kitchen:    [[1.55, 7.45, -HALF, 0], [1.6, 8.45, -HALF, 0], [2.45, 6.95, -HALF * 1.3, 0]],
  mail:       [[3.75, 8.75, 0, 0], [2.95, 8.5, 0.5, 0]],
  rack:       [[9.6, 1.4, PI, 0], [9.0, 2.35, PI * 0.85, 0]],
  press:      [[10.2, 3.4, HALF, 0], [10.2, 4.8, HALF, 0]],
  armchair:   [[9.2, 5.05, 0, 0, SEAT], [8.0, 6.1, 0.4, 0]],
};
// Two spots are far enough apart when their androids don't overlap on screen: side by side (across the screen) they
// need about a head's width, one behind the other (along the view) about an android's on-screen height.
export const APART_ACROSS = 1.25, APART_ALONG = 2.6;
export function apart(ax, ay, bx, by) {
  const u = ((ax - ay) - (bx - by)) / Math.SQRT2, v = ((ax + ay) - (bx + by)) / Math.SQRT2;
  return (u / APART_ACROSS) ** 2 + (v / APART_ALONG) ** 2 >= 1;
}
// furniture footprints (room tiles, padded by an android's radius) that spots and delegates must stay out of
const BLOCKS = [[0.3, 0.1, 5.6, 1.0], [0.3, 1.2, 5.6, 1.9], [4.3, 4.2, 7.2, 5.0], [0, 1.9, 0.6, 3.6], [0, 4.7, 1.0, 6.6],
  [7.6, 7.0, 11.8, 8.0], [10.9, 2.9, 11.8, 5.7], [10.3, 0.5, 11.2, 1.3], [6.8, 0, 9.1, 0.25],
  [0, 6.95, 1.0, 10], [8.55, 4.4, 9.85, 5.6], [1.0, 9.15, 4.15, 10], [9.2, 0, 10.1, 0.6],
  [11.0, 1.5, 12, 2.7], [6.7, 9.15, 7.3, 10], [11.0, 5.9, 12, 7.0]];   // (the last three: theme decor slots)
export const blocked = (x, y) => x < 0.6 || y < 0.8 || x > RW - 0.6 || y > RD - 0.45 || BLOCKS.some(([x0, y0, x1, y1]) => x > x0 - 0.3 && x < x1 + 0.3 && y > y0 - 0.3 && y < y1 + 0.3);
// free floor for androids whose station is full, sessions waiting on their human, and delegates with nowhere beside their partner
export const OVERFLOW = [[3.4, 4.3, 0.4], [8.3, 3.6, -0.4], [2.4, 8.0, 0.5], [6.9, 8.0, -0.3], [9.7, 6.3, -0.6], [4.4, 6.1, 0.2],
  [6.0, 6.35, 0], [2.4, 3.9, 0.6], [10.3, 5.9, -0.8], [6.0, 2.7, 0], [4.7, 2.6, 0.3], [2.2, 6.6, 0.5]].filter(([x, y]) => !blocked(x, y));
// every clear floor point on a 0.8-tile grid, for when the overflow list runs out
export const FLOOR = [];
for (let x = 0.8; x < RW; x += 0.8) for (let y = 1.0; y < RD; y += 0.8) if (!blocked(x, y)) FLOOR.push([x, y, 0]);
export const DOOR_X0 = 4.6, DOOR_X1 = 6.4;
export const AISLES = [2.55, 6.5, 8.75];    // walkways across the room (y) and the columns that join them (x)
export const CROSSINGS = [3.2, 7.55];
export const FACE_VIEWER = PI / 4;          // the camera looks along (-1,-1,-1): an android facing +x+z looks out at you
// What an android does for each activity: the table lives with the shared behaviour (behaviour.js)
export { ACTS, stationOf } from './behaviour.js';
export const TOOL_ICON = { bash: '$', edit: '✎', read: '▤', search: '⌕', web: '◎', think: '∴', plan: '☰', delegate: '⇄', other: '•' };
