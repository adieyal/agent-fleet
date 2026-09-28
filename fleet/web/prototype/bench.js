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
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import * as glyph from './glyphs.js';
import { ViewController, ZOOM } from './camera.js';

const WORLD = '/assets/world/workbench/';
const ROBOT = '/assets/world/robot/';
const params = new URLSearchParams(location.search);
const STILL = params.has('still');            // no motion: screenshots and reduced motion
if (params.has('shot')) document.body.classList.add('shot');

// The l2 view: orthographic, looking down PITCH degrees, turned YAW from square-on to the back wall.
// Fitted to landmarks in l2.png (bench corners weighted most) by art/scripts/fit_camera.py.
const VIEW = { target: [6.269, 4.229, 1.698], pitch: 44.5, yaw: 21.25, height: 5.486 };
const HOSTS = { teal: '#27b3b8', blue: '#2e62dc', olive: '#7a8a32' };
const AO_STRENGTH = 0.9;
// A light grade in linear light before tone mapping: more contrast around mid-grey, and mid-tones cooled
// slightly: l2's ambient is a cool lilac-grey, and its warmth comes only from the lamps and wall lights,
// which are bright enough to sit outside the mid-tone mask.
const GradeShader = {
  uniforms: { tDiffuse: { value: null }, contrast: { value: 1.18 }, warm: { value: new THREE.Vector3(0.96, 0.99, 1.06) } },
  vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: `uniform sampler2D tDiffuse; uniform float contrast; uniform vec3 warm; varying vec2 vUv;
    void main() {
      vec4 c = texture2D(tDiffuse, vUv);
      vec3 g = 0.18 * pow(max(c.rgb, 0.0) / 0.18, vec3(contrast));
      float lum = dot(g, vec3(0.2126, 0.7152, 0.0722));
      float mid = smoothstep(0.02, 0.25, lum) * (1.0 - smoothstep(0.6, 1.5, lum));
      gl_FragColor = vec4(g * mix(vec3(1.0), warm, mid), c.a);
    }`,
};
const CAST = [
  { desk: 'desk1', host: 'teal', action: 'Type', icon: 'search' },
  { desk: 'desk2', host: 'blue', action: 'Write', icon: 'pencil', prop: 'prop_pencil' },
  { desk: 'desk3', host: 'olive', action: 'Hold', icon: 'flask', prop: 'prop_flask' },
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

// The view starts on l2's framing; the controller zooms, pans and turns it within limits.
const REDUCED_MOTION = STILL || matchMedia('(prefers-reduced-motion: reduce)').matches;
if (params.has('maxzoom')) ZOOM.max = Number(params.get('maxzoom'));  // for art/scripts/zoom_sharpness.py
const view = new ViewController(camera, renderer.domElement,
  { target: bl(...VIEW.target), pitch: VIEW.pitch, yaw: VIEW.yaw, height: VIEW.height },
  { min: new THREE.Vector3(-1, 0, -6), max: new THREE.Vector3(12.5, 2.5, 3.5) },  // over the room (x, height, -y)
  REDUCED_MOTION);

// --- baked materials -----------------------------------------------------------

function bakedMaterial(source, lightmaps, warm) {
  const m = new THREE.MeshBasicMaterial({ color: source.color, map: source.map, lightMap: lightmaps.base.texture,
    lightMapIntensity: lightmaps.base.scale * Math.PI, aoMap: lightmaps.ao, aoMapIntensity: AO_STRENGTH });
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
  // short-range baked occlusion, multiplied over the lightmaps: contact shadows under furniture and in corners
  out.ao = await loader.loadAsync(WORLD + manifest.lightmap.ao.file);
  Object.assign(out.ao, { channel: manifest.lightmap.uv_channel, colorSpace: THREE.NoColorSpace, flipY: false });
  return out;
}

// --- warm light groups: a level per group drives its lightmap layer, bulb and a live light ---

const warmth = {};  // group -> { level, target, uniform, bulbs: [], light }

// Desk lamps are pushed past their baked strength: l2's working desks glow amber around the lamp.
const WARM_GAIN = { desk: 1.4, corner: 1.4 };  // much higher and white paper under the lamp blooms

function warmGroup(name, scale) {
  const gain = WARM_GAIN[name.replace(/\d+$/, '')] ?? 1;
  const w = { level: 0, target: 0, scale: scale * gain, uniform: { value: 0 }, bulbs: [], light: null };
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
    if (w.halo) w.halo.material.opacity = w.level;
  }
}

// --- plan wall ---------------------------------------------------------------------

const tiles = {};  // "r,c" -> { pivot, mesh, decal, state }
const TICK = glyph.tick();
const LAMP_HALO = glyph.halo('rgba(255, 170, 80, 0.95)');
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
  tile.mesh.material.color.set(lit ? '#ffc767' : '#f7f6f8');
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

// The robot is the deck's RobotExpressive, restyled by art/scripts/build_robot.py. Its body holds the end of
// `Sitting`; its arms play one of the arm-only clips (Rest, Type, Write, Hold) built on that seated pose.
const ARM_TRACK = /^(UpperArm|LowerArm)[LR]\./;  // glTF node names lose their dots in three

function trackSubset(clip, keep, name) {
  return new THREE.AnimationClip(name, clip.duration, clip.tracks.filter(t => keep(t.name)));
}

function setupRobots(robotGltf, robotManifest, root) {
  const [sx, sy, sz] = robotManifest.runtime.seat_point;
  const seatPoint = bl(sx, sy, sz);  // where the seated robot rests, in its own coordinates
  const all = Object.fromEntries(robotGltf.animations.map(a => [a.name, a]));
  const clips = {
    body: trackSubset(all.Sitting, n => !ARM_TRACK.test(n), 'Seated'),
    ...Object.fromEntries(['Rest', 'Type', 'Write', 'Hold'].map(n => [n, trackSubset(all[n], n2 => ARM_TRACK.test(n2), n)])),
  };
  for (const cast of CAST) {
    const seat = root.getObjectByName(`seat_${cast.desk}`);
    const robot = SkeletonUtils.clone(robotGltf.scene);
    seat.getWorldQuaternion(robot.quaternion);
    // put the robot's seat point on the chair's seat
    seat.getWorldPosition(robot.position).sub(seatPoint.clone().applyQuaternion(robot.quaternion));
    robot.traverse(o => {
      if (!o.isMesh) return;
      o.castShadow = true;
      o.frustumCulled = false;  // skinned hands: their bounds are the rest pose's
      o.material = o.material.clone();
      o.material.envMapIntensity = 1.0;
      if (o.material.name === 'robot_body') o.material.color.set(HOSTS[cast.host]);
      if (o.material.name === 'robot_eye') o.material.emissiveIntensity = 1.4;
      if (o.material.name === 'robot_glass') Object.assign(o.material, { transparent: true, opacity: 0.75 });
      if (o.material.name === 'robot_liquid') o.material.emissiveIntensity = 0.6;
    });
    for (const name of ['prop_pencil', 'prop_flask']) robot.getObjectByName(name).visible = name === cast.prop;
    // the robot carries both agent faces and every host accessory: show the eyes and no accessory
    for (const name of ['robot_band', 'acc_backpack', 'acc_antenna', 'acc_halo', 'acc_crest', 'prop_laptop', 'prop_paper']) robot.getObjectByName(name).visible = false;  // the bench has its own
    scene.add(robot);
    const mixer = new THREE.AnimationMixer(robot);
    const seated = mixer.clipAction(clips.body);
    seated.setLoop(THREE.LoopOnce, 1);
    seated.clampWhenFinished = true;
    seated.play();
    seated.time = clips.body.duration;  // already seated when the page opens
    const bubble = new THREE.Sprite(new THREE.SpriteMaterial({ map: glyph.bubble(cast.icon, HOSTS[cast.host]),
      toneMapped: false, depthTest: false }));
    bubble.scale.set(0.42, 0.48, 1);
    seat.getWorldPosition(bubble.position).add(new THREE.Vector3(0, 0.95, 0));
    bubble.renderOrder = 3;
    scene.add(bubble);
    const r = { ...cast, robot, mixer, clips, seated, bubble, current: null, busy: true };
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
  if (STILL) {
    r.mixer.setTime(0.35);
    r.seated.time = r.clips.body.duration;
    r.mixer.update(0);
  }
}

function setBusy(r, busy) {
  r.busy = busy;
  play(r, busy ? r.action : 'Rest');
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

// The satin floor of l2: a half-resolution mirror, blurred and laid over the baked floor at low opacity,
// so lamps, lit tiles and furniture leave soft reflections.
const GlossShader = {
  name: 'FloorGloss',
  uniforms: { color: { value: null }, tDiffuse: { value: null }, textureMatrix: { value: null },
              strength: { value: 0.2 }, texel: { value: new THREE.Vector2() } },
  vertexShader: `uniform mat4 textureMatrix; varying vec4 vUv;
    void main() { vUv = textureMatrix * vec4(position, 1.0); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
  fragmentShader: `uniform vec3 color; uniform sampler2D tDiffuse; uniform float strength; uniform vec2 texel; varying vec4 vUv;
    void main() {
      vec2 uv = vUv.xy / vUv.w;
      vec3 c = vec3(0.0); float w = 0.0;
      for (int x = -2; x <= 2; x++) for (int y = -2; y <= 2; y++) {
        float k = exp(-float(x * x + y * y) / 3.0);
        c += texture2D(tDiffuse, uv + vec2(float(x), float(y)) * texel).rgb * k; w += k;
      }
      gl_FragColor = vec4(c / w * color, strength);
    }`,
};

function floorGloss(root) {
  const floor = root.getObjectByName('floor');
  const box = new THREE.Box3().setFromObject(floor);
  const size = box.getSize(new THREE.Vector3());
  const res = new THREE.Vector2(innerWidth, innerHeight).multiplyScalar(0.5 * renderer.getPixelRatio());
  const mirror = new Reflector(new THREE.PlaneGeometry(size.x, size.z), {
    shader: GlossShader, textureWidth: res.x, textureHeight: res.y, color: '#fff2e6', multisample: 0 });
  mirror.material.uniforms.texel.value.set(2.5 / res.x, 2.5 / res.y);
  Object.assign(mirror.material, { transparent: true, depthWrite: false });
  mirror.rotation.x = -Math.PI / 2;
  mirror.position.set((box.min.x + box.max.x) / 2, box.max.y + 0.001, (box.min.z + box.max.z) / 2);
  mirror.renderOrder = -1;
  // sprites (bubbles, the lantern's glyph and halo) face the camera and would reflect as floating blobs;
  // the lantern's reflection lands far from it in this view and reads as a second, stray attention light
  const render = mirror.onBeforeRender;
  mirror.onBeforeRender = (...args) => {
    const hidden = [];
    scene.traverse(o => {
      if ((o.isSprite || lantern.parts.includes(o)) && o.visible) { o.visible = false; hidden.push(o); }
    });
    render.apply(mirror, args);
    hidden.forEach(o => { o.visible = true; });
  };
  scene.add(mirror);
  return mirror;
}

function footprints(root) {
  const mat = new THREE.MeshBasicMaterial({ map: glyph.footprint(), transparent: true, opacity: 0.75,
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
  const [manifest, gltf, robotGltf, robotManifest, hdr] = await Promise.all([
    fetch(WORLD + 'manifest.json').then(r => r.json()),
    new GLTFLoader().loadAsync(WORLD + 'workbench.glb'),
    new GLTFLoader().loadAsync(ROBOT + 'robot.glb'),
    fetch(ROBOT + 'manifest.json').then(r => r.json()),
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
        // the lamp's glow as seen from above (its bulb faces down, under the shade): l2's amber halo
        const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: LAMP_HALO, blending: THREE.AdditiveBlending,
          depthWrite: false, toneMapped: false, transparent: true }));
        halo.scale.setScalar(1.1);
        halo.position.copy(light.position);
        scene.add(halo);
        warmth[g].halo = halo;
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
  setupRobots(robotGltf, robotManifest, root);
  robots.forEach(r => setWarm(r.desk, true, true));
  shadowCatchers(root);
  footprints(root);
  floorGloss(root);

  const sun = new THREE.DirectionalLight('#fff3e2', 1.6);
  sun.position.copy(bl(2.5, -2.5, 9));
  sun.target.position.copy(bl(6.5, 3.5, 0));
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 30 });
  sun.shadow.radius = 4;
  sun.shadow.bias = -0.0005;
  scene.add(sun, sun.target);
  // soft sky/floor fill for the live objects (baked surfaces ignore lights), so they sit in the room's brightness
  scene.add(new THREE.HemisphereLight('#eef2ff', '#b8a898', 0.9));

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  // a high threshold: only emissive light (bulbs, lit tiles, the lantern) blooms, not white paper
  composer.addPass(new UnrealBloomPass(new THREE.Vector2(innerWidth, innerHeight), 0.6, 0.55, 2.4));
  composer.addPass(new ShaderPass(GradeShader));
  composer.addPass(new SMAAPass());
  composer.addPass(new OutputPass());
  addEventListener('resize', () => {
    renderer.setSize(innerWidth, innerHeight);
    composer.setSize(innerWidth, innerHeight);
    view.apply();
  });
  document.getElementById('resetView').addEventListener('click', () => view.reset());

  raiseAttention();
  interact();
  let frames = 0;
  renderer.setAnimationLoop(() => {
    const dt = Math.min(clock.getDelta(), 0.05);
    if (!STILL) mixers.forEach(m => m.update(dt));
    runTweens(STILL ? 1 : dt);
    updateWarmth(STILL ? 1 : dt);
    view.update(dt);
    composer.render();
    if (++frames === 3) Object.assign(window.bench, { ready: true, readyAt: performance.now() });
  });
}

function interact() {
  const ray = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  // on release, and only if the press wasn't a drag: dragging pans or turns the view instead
  renderer.domElement.addEventListener('pointerup', e => {
    if (view.dragged || e.button !== 0) return;
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
  addEventListener('keydown', e => {
    if (e.key === 'a') raiseAttention();
    if (e.key === 'r') view.reset();
  });
}

// A small handle for tests and for driving the prototype from the console.
window.bench = {
  ready: false,
  flipTile: (r, c, state) => flipTile(tiles[`${r},${c}`], state),
  setCriteria: (i, on) => setWarm(`criteria${i}`, on),
  setBusy: (desk, busy) => setBusy(robots.find(r => r.desk === desk), busy),
  raiseAttention,
  answerAttention,
  view: () => view.state(),
  resetView: () => view.reset(),
  setView: v => view.set(v),
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
