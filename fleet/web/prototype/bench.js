// Workbench prototype (l2.png): the baked scene from art/build.sh with its live pieces on top.
// Baked meshes are unlit: albedo x (base lightmap + warm layers scaled by each group's level).
// Everything that carries state stays dynamic: plan-wall tiles, criteria lights, desk lamps,
// the lantern, and the robots with their action bubbles.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { SMAAPass } from 'three/addons/postprocessing/SMAAPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import * as glyph from './glyphs.js';

const WORLD = '/assets/world/workbench/';
const ROBOT = '/assets/world/robot/';
const params = new URLSearchParams(location.search);
const STILL = params.has('still');            // no motion: screenshots and reduced motion
if (params.has('shot')) document.body.classList.add('shot');

// The l2 view: orthographic, looking down PITCH degrees, turned YAW from square-on to the back wall.
const VIEW = { target: [6.1, 3.7, 2.05], pitch: 37, yaw: 23, height: 5.1 };
const HOSTS = { teal: '#27b3b8', blue: '#2e62dc', olive: '#7a8a32' };
const CAST = [
  { desk: 'desk1', host: 'teal', action: 'type', icon: 'search' },
  { desk: 'desk2', host: 'blue', action: 'write', icon: 'pencil', prop: 'prop_pencil' },
  { desk: 'desk3', host: 'olive', action: 'hold', icon: 'flask', prop: 'prop_flask' },
];
// l2's plan wall: done tiles show a tick; the active row glows. Rows count up from the bottom.
const DONE = [[5, 1], [4, 1], [4, 5], [2, 6], [1, 6], [1, 8], [0, 4]];
const ACTIVE = [[3, 0], [3, 1], [3, 2], [3, 3], [3, 4], [3, 5]];
const CRITERIA_ON = [true, true, true, false, false];

const bl = (x, y, z) => new THREE.Vector3(x, z, -y);   // Blender (Z up) to three (Y up)

const renderer = new THREE.WebGLRenderer({ antialias: false, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = Number(params.get('exposure') || 0.68);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFShadowMap;
document.body.prepend(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color('#d9e9f8');
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 200);
const clock = new THREE.Clock();
const mixers = [];
const tweens = [];

function placeCamera() {
  const p = THREE.MathUtils.degToRad(VIEW.pitch), y = THREE.MathUtils.degToRad(VIEW.yaw);
  const target = bl(...VIEW.target);
  const dir = bl(Math.sin(y) * Math.cos(p), -Math.cos(y) * Math.cos(p), Math.sin(p));
  camera.position.copy(target).addScaledVector(dir, 60);
  camera.lookAt(target);
  const aspect = innerWidth / innerHeight;
  camera.top = VIEW.height / 2;
  camera.bottom = -VIEW.height / 2;
  camera.left = -VIEW.height * aspect / 2;
  camera.right = VIEW.height * aspect / 2;
  camera.updateProjectionMatrix();
}

// --- baked materials -----------------------------------------------------------

function bakedMaterial(source, lightmaps, warm) {
  const m = new THREE.MeshBasicMaterial({ color: source.color, map: source.map, lightMap: lightmaps.base.texture,
    lightMapIntensity: lightmaps.base.scale * Math.PI });
  m.onBeforeCompile = shader => {
    let decl = '', sum = '';
    warm.forEach((w, i) => {
      shader.uniforms[`warmMap${i}`] = { value: w.texture };
      shader.uniforms[`warmK${i}`] = w.uniform;
      decl += `uniform sampler2D warmMap${i};\nuniform float warmK${i};\n`;
      sum += ` + texture2D( warmMap${i}, vLightMapUv ).rgb * warmK${i}`;
    });
    shader.fragmentShader = shader.fragmentShader
      .replace('#include <lightmap_pars_fragment>', `#include <lightmap_pars_fragment>\n${decl}`)
      .replace('reflectedLight.indirectDiffuse += lightMapTexel.rgb * lightMapIntensity * RECIPROCAL_PI;',
        `reflectedLight.indirectDiffuse += ( lightMapTexel.rgb * lightMapIntensity${sum} ) * RECIPROCAL_PI;`);
  };
  m.customProgramCacheKey = () => `baked-warm-${warm.length}`;
  return m;
}

async function loadLightmaps(manifest) {
  const loader = new THREE.TextureLoader();
  const out = {};
  await Promise.all(Object.entries(manifest.lightmap.layers).map(async ([name, layer]) => {
    const t = await loader.loadAsync(WORLD + layer.file);
    t.channel = manifest.lightmap.uv_channel;
    t.colorSpace = THREE.SRGBColorSpace;
    t.flipY = false;
    out[name] = { texture: t, scale: layer.scale };
  }));
  return out;
}

// --- warm light groups: a level per group drives its lightmap layer, bulb and a live light ---

const warmth = {};  // group -> { level, target, uniform, bulbs: [], light }

function warmGroup(name, scale) {
  const w = { level: 0, target: 0, scale, uniform: { value: 0 }, bulbs: [], light: null };
  warmth[name] = w;
  return w;
}

function setWarm(name, on, instant = STILL) {
  const w = warmth[name];
  w.target = on ? 1 : 0;
  if (instant) w.level = w.target;
}

function updateWarmth(dt) {
  for (const w of Object.values(warmth)) {
    w.level += (w.target - w.level) * Math.min(1, dt * 3);
    w.uniform.value = w.level * w.scale * Math.PI;
    for (const b of w.bulbs) {
      b.material.emissiveIntensity = 0.15 + 5 * w.level;
      b.material.color.set(w.level > 0.5 ? '#fff4d8' : '#6b6a70');
    }
    if (w.light) w.light.intensity = w.light.userData.full * w.level;
  }
}

// --- plan wall ---------------------------------------------------------------------

const tiles = {};  // "r,c" -> { pivot, mesh, decal, state }
const TICK = glyph.tick();
const TICK_LIT = glyph.tick('#7a4a10');

function setupTile(mesh, r, c) {
  const box = new THREE.Box3().setFromObject(mesh);
  const centre = box.getCenter(new THREE.Vector3());
  const pivot = new THREE.Group();
  pivot.position.copy(centre);
  mesh.parent.add(pivot);
  pivot.attach(mesh);
  mesh.material = mesh.material.clone();
  const size = box.getSize(new THREE.Vector3());
  const decal = new THREE.Mesh(new THREE.PlaneGeometry(size.x * 0.8, size.y * 0.8),
    new THREE.MeshBasicMaterial({ map: TICK, transparent: true, depthWrite: false, toneMapped: false }));
  decal.position.set(0, 0, size.z / 2 + 0.002);
  decal.visible = false;
  pivot.add(decal);
  const tile = { pivot, mesh, decal, state: 'todo', r, c };
  tiles[`${r},${c}`] = tile;
  mesh.userData.tile = tile;
  return tile;
}

function paintTile(tile) {
  const lit = tile.state === 'active';
  tile.decal.visible = tile.state !== 'todo';
  tile.decal.material.map = lit ? TICK_LIT : TICK;
  // amber, not white: a saturated emissive at moderate strength survives tone mapping
  tile.mesh.material.emissive.set(lit ? '#ff9418' : '#000000');
  tile.mesh.material.emissiveIntensity = lit ? 1.5 : 0;
  tile.mesh.material.color.set(lit ? '#ffc767' : '#eeedf0');
}

function flipTile(tile, state) {
  const next = state || (tile.state === 'todo' ? 'done' : 'todo');
  if (STILL) {
    tile.state = next;
    paintTile(tile);
    return;
  }
  let swapped = false;
  tween(0.5, t => {
    tile.pivot.rotation.x = Math.PI * 2 * easeInOut(t);   // a full turn: it lands face-out again
    if (!swapped && t >= 0.5) {
      swapped = true;
      tile.state = next;
      paintTile(tile);
    }
  });
}

// --- the lantern ----------------------------------------------------------------------

const lantern = { group: null, raised: false, parts: [] };

function setupLantern(root) {
  const anchor = root.getObjectByName('lantern_anchor');
  const body = root.getObjectByName('lantern');
  const cord = root.getObjectByName('lantern_cord');
  const group = new THREE.Group();
  group.position.copy(anchor.getWorldPosition(new THREE.Vector3()));
  scene.add(group);
  group.attach(cord);
  group.attach(body);
  body.material = body.material.clone();
  body.material.color.set('#c21bd0');
  body.material.emissive.set('#e021e6');
  body.material.emissiveIntensity = 3;
  const glyphSprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: glyph.question(), toneMapped: false,
    depthTest: false }));
  glyphSprite.scale.setScalar(0.22);
  glyphSprite.position.copy(body.position);
  glyphSprite.renderOrder = 2;
  group.add(glyphSprite);
  const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: glyph.halo('rgba(236, 72, 226, 0.55)'),
    blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false }));
  halo.scale.setScalar(1.5);
  halo.position.copy(body.position).add(new THREE.Vector3(0, 0, -0.25));
  group.add(halo);
  const light = new THREE.PointLight('#ff4fe8', 1.2, 2.5, 2);
  light.position.copy(body.position);
  group.add(light);
  Object.assign(lantern, { group, parts: [cord, body, glyphSprite, halo, light] });
}

function raiseAttention() {
  lantern.raised = true;
  lantern.parts.forEach(p => { p.visible = true; });
  if (STILL) return;
  // one swing on arrival, then steady
  tween(2.4, t => { lantern.group.rotation.z = 0.35 * Math.sin(t * Math.PI * 3) * (1 - t) ** 2; });
}

function answerAttention() {
  lantern.raised = false;
  lantern.parts.forEach(p => { p.visible = false; });
}

// --- robots ----------------------------------------------------------------------------

const robots = [];

function setupRobots(robotGltf, root) {
  for (const cast of CAST) {
    const seat = root.getObjectByName(`seat_${cast.desk}`);
    const robot = SkeletonUtils.clone(robotGltf.scene);
    seat.getWorldPosition(robot.position);
    seat.getWorldQuaternion(robot.quaternion);
    robot.traverse(o => {
      if (!o.isMesh) return;
      o.castShadow = true;
      o.material = o.material.clone();
      o.material.envMapIntensity = 0.6;
      if (o.material.name === 'robot_body') o.material.color.set(HOSTS[cast.host]);
      if (o.material.name === 'robot_eye') o.material.emissiveIntensity = 1.1;
      if (o.material.name === 'robot_glass') Object.assign(o.material, { transparent: true, opacity: 0.55 });
    });
    for (const name of ['prop_pencil', 'prop_flask']) robot.getObjectByName(name).visible = name === cast.prop;
    scene.add(robot);
    const mixer = new THREE.AnimationMixer(robot);
    const clips = Object.fromEntries(robotGltf.animations.map(a => [a.name, a]));
    const bubble = new THREE.Sprite(new THREE.SpriteMaterial({ map: glyph.bubble(cast.icon, HOSTS[cast.host]),
      toneMapped: false, depthTest: false }));
    bubble.scale.set(0.42, 0.48, 1);
    bubble.position.copy(robot.position).add(new THREE.Vector3(0, 1.12, 0));
    bubble.renderOrder = 3;
    scene.add(bubble);
    const r = { ...cast, robot, mixer, clips, bubble, current: null, busy: true };
    robot.traverse(o => { o.userData.robot = r; });
    robots.push(r);
    mixers.push(mixer);
    play(r, cast.action);
  }
}

function play(r, name) {
  const next = r.mixer.clipAction(r.clips[name]);
  if (r.current && r.current !== next) r.current.fadeOut(STILL ? 0 : 0.4);
  next.reset().fadeIn(STILL ? 0 : 0.4).play();
  r.current = next;
  if (STILL) r.mixer.setTime(0.35);
}

function setBusy(r, busy) {
  r.busy = busy;
  play(r, busy ? r.action : 'idle');
  r.bubble.visible = busy;
  r.robot.getObjectByName('prop_pencil').visible = busy && r.prop === 'prop_pencil';
  r.robot.getObjectByName('prop_flask').visible = busy && r.prop === 'prop_flask';
  setWarm(r.desk, busy);   // the lamp follows activity
}

// --- small helpers -------------------------------------------------------------------------

function easeInOut(t) { return t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2; }

function tween(seconds, step) {
  tweens.push({ t: 0, seconds, step });
}

function runTweens(dt) {
  for (let i = tweens.length - 1; i >= 0; i--) {
    const tw = tweens[i];
    tw.t = Math.min(1, tw.t + dt / tw.seconds);
    tw.step(tw.t);
    if (tw.t >= 1) tweens.splice(i, 1);
  }
}

function shadowCatchers(root) {
  // Baked surfaces are unlit, so the robots' shadows land on transparent copies of the floor and desk tops.
  const catcher = new THREE.ShadowMaterial({ color: '#2d3a58', opacity: 0.22, depthWrite: false,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
  root.traverse(o => {
    if (o.isMesh && /^(floor|desk\d_top|qdesk_top)$/.test(o.name)) {
      const c = new THREE.Mesh(o.geometry, catcher);
      o.getWorldPosition(c.position);
      o.getWorldQuaternion(c.quaternion);
      c.receiveShadow = true;
      scene.add(c);
    }
  });
}

function footprints(root) {
  const mat = new THREE.MeshBasicMaterial({ map: glyph.footprint(), transparent: true, opacity: 0.45,
    depthWrite: false, polygonOffset: true, polygonOffsetFactor: -4 });
  const geo = new THREE.PlaneGeometry(0.11, 0.22).rotateX(-Math.PI / 2);
  for (let i = 0; root.getObjectByName(`footprint_${i}`); i++) {
    const a = root.getObjectByName(`footprint_${i}`);
    const f = new THREE.Mesh(geo, mat);
    a.getWorldPosition(f.position);
    f.quaternion.copy(a.getWorldQuaternion(new THREE.Quaternion()));
    f.translateX(i % 2 ? 0.09 : -0.09);
    f.position.y += 0.006;
    scene.add(f);
  }
}

// --- load and wire up ------------------------------------------------------------------------

async function main() {
  const [manifest, gltf, robotGltf, hdr] = await Promise.all([
    fetch(WORLD + 'manifest.json').then(r => r.json()),
    new GLTFLoader().loadAsync(WORLD + 'workbench.glb'),
    new GLTFLoader().loadAsync(ROBOT + 'robot.glb'),
    new HDRLoader().loadAsync(WORLD + 'environment.hdr'),
  ]);
  hdr.mapping = THREE.EquirectangularReflectionMapping;
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromEquirectangular(hdr).texture;
  scene.environmentIntensity = 0.55;
  scene.environmentRotation.y = THREE.MathUtils.degToRad(manifest.environment.rotation_deg);

  const lightmaps = await loadLightmaps(manifest);
  const groups = Object.keys(manifest.warm_groups);
  const warm = groups.map(g => ({ texture: lightmaps[g].texture, uniform: warmGroup(g, lightmaps[g].scale).uniform }));
  const root = gltf.scene;
  scene.add(root);
  root.updateMatrixWorld(true);
  const bakedCache = new Map();
  root.traverse(o => {
    if (!o.isMesh) return;
    if (o.userData.fleet === 'baked') {
      if (!bakedCache.has(o.material)) bakedCache.set(o.material, bakedMaterial(o.material, lightmaps, warm));
      o.material = bakedCache.get(o.material);
    } else if (o.userData.fleet === 'dynamic') {
      o.castShadow = true;
    }
  });

  // warm groups: bulbs glow with their level; desk lamps also light the robots live
  for (const [g, names] of Object.entries(manifest.warm_groups)) {
    for (const n of names) {
      const bulb = root.getObjectByName(n);
      bulb.material = bulb.material.clone();
      bulb.material.emissive.set('#ffc46b');
      warmth[g].bulbs.push(bulb);
      if (n.endsWith('_lamp_bulb')) {
        const light = new THREE.PointLight('#ffc27a', 0, 1.6, 2);
        bulb.getWorldPosition(light.position);
        light.position.y -= 0.05;
        light.userData.full = 0.5;
        scene.add(light);
        warmth[g].light = light;
      }
    }
  }
  for (const i of [0, 1, 2]) {
    const ind = root.getObjectByName(`lift_indicator_${i}`);
    ind.material = ind.material.clone();
    ind.material.emissiveIntensity = i < 2 ? 2.5 : 0.3;
  }
  const tileMeshes = [];  // collected first: setupTile reparents, which must not happen mid-traverse
  root.traverse(o => { if (/^plan_tile_r\d_c\d$/.test(o.name)) tileMeshes.push(o); });
  for (const o of tileMeshes) {
    const [, r, c] = /^plan_tile_r(\d)_c(\d)$/.exec(o.name);
    setupTile(o, Number(r), Number(c));
  }
  for (const [r, c] of DONE) tiles[`${r},${c}`].state = 'done';
  for (const [r, c] of ACTIVE) tiles[`${r},${c}`].state = 'active';
  Object.values(tiles).forEach(paintTile);
  CRITERIA_ON.forEach((on, i) => setWarm(`criteria${i}`, on, true));
  setWarm('corner', true, true);

  setupLantern(root);
  setupRobots(robotGltf, root);
  robots.forEach(r => setWarm(r.desk, true, true));
  shadowCatchers(root);
  footprints(root);

  const sun = new THREE.DirectionalLight('#fff3e2', 1.6);
  sun.position.copy(bl(2.5, -2.5, 9));
  sun.target.position.copy(bl(6.5, 3.5, 0));
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 30 });
  sun.shadow.radius = 4;
  sun.shadow.bias = -0.0005;
  scene.add(sun, sun.target);

  placeCamera();
  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  // a high threshold: only emissive light (bulbs, lit tiles, the lantern) blooms, not white paper
  composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.6, 0.55, 2.4));
  composer.addPass(new SMAAPass());
  composer.addPass(new OutputPass());
  addEventListener('resize', () => {
    renderer.setSize(innerWidth, innerHeight);
    composer.setSize(innerWidth, innerHeight);
    placeCamera();
  });

  raiseAttention();
  interact();
  let frames = 0;
  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.05);
    if (!STILL) mixers.forEach(m => m.update(dt));
    runTweens(STILL ? 1 : dt);
    updateWarmth(STILL ? 1 : dt);
    composer.render();
    if (++frames === 3) window.bench.ready = true;
  });
}

function interact() {
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  renderer.domElement.addEventListener('pointerdown', e => {
    pointer.set((e.clientX / innerWidth) * 2 - 1, -(e.clientY / innerHeight) * 2 + 1);
    ray.setFromCamera(pointer, camera);
    for (const hit of ray.intersectObjects(scene.children, true)) {
      const o = hit.object;
      if (!o.visible) continue;
      if (o.userData.tile) return flipTile(o.userData.tile);
      if (o.userData.robot) return setBusy(o.userData.robot, !o.userData.robot.busy);
      const crit = /^criteria_light_(\d)$/.exec(o.name);
      if (crit) return setWarm(`criteria${crit[1]}`, warmth[`criteria${crit[1]}`].target < 0.5);
      if (lantern.parts.includes(o)) return answerAttention();
      if (o.userData.fleet === 'baked') return;
    }
  });
  addEventListener('keydown', e => { if (e.key === 'a') raiseAttention(); });
}

// A small handle for tests and for driving the prototype from the console.
window.bench = {
  ready: false,
  flipTile: (r, c, state) => flipTile(tiles[`${r},${c}`], state),
  setCriteria: (i, on) => setWarm(`criteria${i}`, on),
  setBusy: (desk, busy) => setBusy(robots.find(r => r.desk === desk), busy),
  raiseAttention,
  answerAttention,
  state: () => ({
    tiles: Object.fromEntries(Object.entries(tiles).map(([k, t]) => [k, t.state])),
    warmth: Object.fromEntries(Object.entries(warmth).map(([k, w]) => [k, w.target])),
    robots: robots.map(r => ({ desk: r.desk, busy: r.busy, action: r.current?.getClip().name })),
    lantern: lantern.raised,
  }),
};

main().catch(err => {
  console.error(err);
  document.body.insertAdjacentHTML('beforeend', `<pre style="position:fixed;top:8px;left:8px;color:#a00">${err}</pre>`);
});
