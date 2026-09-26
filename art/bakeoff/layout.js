// Lays a bake-off variant out like l2.png: the bench, three seated robots (typing, writing, holding a
// flask) and planters, from the prototype's l2 camera, over a flat floor and back wall. Everything is
// unlit: no lights, shadow maps, reflections or post-processing. Lamp pools are additive glow sprites.
//   ?variant=A   pre-lit glTFs (art/bakeoff/A), drawn with MeshBasicMaterial
//   ?variant=B1  Blender sprites (art/bakeoff/B1), drawn on a 2D canvas over the same floor and wall
//   ?variant=none  the floor and wall alone
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';

const params = new URLSearchParams(location.search);
const VARIANT = params.get('variant') || 'A';
const ROOT = '/art/bakeoff/';
const VIEW = { target: [6.269, 4.229, 1.698], pitch: 44.5, yaw: 21.25, height: 5.486 };  // as the prototype
const HOSTS = { teal: '#27b3b8', blue: '#2e62dc', olive: '#7a8a32' };
// l2's arrangement, in the workbench's Blender coordinates (metres, Z up)
const BENCH = { x0: 3.8, deskW: 1.8, y: 4.35, deskD: 0.8, deskH: 0.74, seatH: 0.47 };
const CAST = [
  { pose: 'type', clip: 'Type', desk: 1, host: 'teal' },
  { pose: 'write', clip: 'Write', desk: 2, host: 'blue', prop: 'prop_pencil' },
  { pose: 'hold', clip: 'Hold', desk: 3, host: 'olive', prop: 'prop_flask' },
];
const PLANTS = [[-0.55, 5.55], [8.45, 5.15], [9.95, 3.9], [2.3, 5.4]];
const seat = d => [BENCH.x0 + (d - 1) * BENCH.deskW + BENCH.deskW / 2 - 0.25,
  BENCH.y + BENCH.deskD / 2 + 0.34 - 0.2, BENCH.seatH];
const lamp = d => [BENCH.x0 + (d - 1) * BENCH.deskW + 0.18, BENCH.y + 0.02, BENCH.deskH + 0.215];
const bl = (x, y, z) => new THREE.Vector3(x, z, -y);  // Blender (Z up) to three (Y up)

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(1);
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;
document.body.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color('#dcebf8');
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 200);
{
  const p = THREE.MathUtils.degToRad(VIEW.pitch), y = THREE.MathUtils.degToRad(VIEW.yaw);
  const target = bl(...VIEW.target);
  camera.position.copy(target).add(bl(Math.sin(y) * Math.cos(p), -Math.cos(y) * Math.cos(p), Math.sin(p)).multiplyScalar(60));
  camera.lookAt(target);
  const h = VIEW.height / 2, w = h * innerWidth / innerHeight;
  Object.assign(camera, { left: -w, right: w, top: h, bottom: -h });
  camera.updateProjectionMatrix();
}
const mixers = [];
const clock = new THREE.Clock();

function flat(color, w, h, pos, rotX) {
  const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ color }));
  m.rotation.x = rotX;
  m.position.copy(pos);
  scene.add(m);
}
// the room around the objects: flat, so the objects carry the look
flat('#d3d1db', 13.5, 9.5, bl(5.75, 1.25, 0), -Math.PI / 2);
flat('#b7b1b4', 13.5, 3.2, bl(5.75, 6.0, 1.6), 0);

function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  r.addColorStop(0, 'rgba(255, 170, 80, 0.6)');
  r.addColorStop(1, 'rgba(255, 170, 80, 0)');
  g.fillStyle = r;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const GLOW = glowTexture();
function lampGlows(active) {
  for (const d of [1, 2, 3]) {
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: GLOW, blending: THREE.AdditiveBlending,
      depthWrite: false, transparent: true, opacity: active ? 1 : 0 }));
    s.position.copy(bl(...lamp(d)));
    s.scale.setScalar(0.6);
    s.renderOrder = 5;
    scene.add(s);
  }
}

// --- A: pre-lit glTFs, unlit ---------------------------------------------------------------------

function unlit(root, tint) {
  root.traverse(o => {
    if (!o.isMesh) return;
    const src = o.material;
    if (o.name.endsWith('contact_shadow')) {
      o.material = new THREE.MeshBasicMaterial({ map: src.map, transparent: true, depthWrite: false,
        polygonOffset: true, polygonOffsetFactor: -2 });
      o.renderOrder = 1;
      return;
    }
    const m = new THREE.MeshBasicMaterial({ map: src.map, color: src.color.clone() });
    if (src.name === 'robot_body' && tint) m.color.set(tint);
    if (src.name === 'robot_eye') m.color.copy(src.emissive).multiplyScalar(1.2);
    if (src.name === 'robot_glass') Object.assign(m, { transparent: true, opacity: 0.6 });
    o.material = m;
  });
}

async function variantA() {
  const load = f => new GLTFLoader().loadAsync(ROOT + 'A/' + f);
  const [manifest, bench, plant, robot, robotShadow] = await Promise.all([
    fetch(ROOT + 'A/manifest.json').then(r => r.json()), load('bench.glb'), load('plant.glb'), load('robot.glb'),
    load('robot_shadow.glb')]);
  unlit(bench.scene);
  scene.add(bench.scene);
  for (const [x, y] of PLANTS) {
    const p = plant.scene.clone();
    unlit(p);
    p.position.copy(bl(x, y, 0));
    scene.add(p);
  }
  const armTrack = /^(UpperArm|LowerArm)[LR]\./;
  const clip = (name, keep) => {
    const c = robot.animations.find(a => a.name === name);
    return new THREE.AnimationClip(name, c.duration, c.tracks.filter(t => keep(armTrack.test(t.name))));
  };
  const seated = clip('Sitting', arm => !arm);
  const seatPoint = bl(...manifest.objects.robot.seat_point);
  for (const cast of CAST) {
    const r = SkeletonUtils.clone(robot.scene);
    unlit(r, HOSTS[cast.host]);
    for (const n of ['prop_pencil', 'prop_flask']) r.getObjectByName(n).visible = n === cast.prop;
    const s = seat(cast.desk);
    r.position.copy(bl(...s)).sub(seatPoint);
    scene.add(r);
    const shadow = robotShadow.scene.clone();
    unlit(shadow);
    shadow.position.copy(bl(s[0], s[1], 0));
    scene.add(shadow);
    const mixer = new THREE.AnimationMixer(r);
    const sit = mixer.clipAction(seated);
    sit.setLoop(THREE.LoopOnce, 1);
    sit.clampWhenFinished = true;
    sit.play();
    sit.time = seated.duration;
    mixer.clipAction(clip(cast.clip, arm => arm)).play();
    mixer.update(0.3);
    mixers.push(mixer);
  }
  lampGlows(true);
}

// --- B1: sprites on a canvas over the flat room --------------------------------------------------

function load(src) {
  return new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
}

async function variantB1() {
  const manifest = await fetch(ROOT + 'B1/manifest.json').then(r => r.json());
  const o = manifest.objects;
  const img = async entry => ({ ...entry, image: await load(ROOT + 'B1/' + entry.file) });
  const bench = await img(o.bench['1x']);
  const plant = await img(o.plant['1x']);
  const robots = await Promise.all(CAST.map(async c => ({ ...c, sheet: await img(o.robot[c.pose]['1x']) })));
  lampGlows(true);
  const canvas = document.createElement('canvas');
  canvas.width = innerWidth;
  canvas.height = innerHeight;
  document.body.appendChild(canvas);
  const g = canvas.getContext('2d');
  const back = new THREE.Vector3().subVectors(camera.position, bl(...VIEW.target)).normalize();
  const screen = world => {
    const v = bl(...world).project(camera);
    return [(v.x + 1) / 2 * innerWidth, (1 - v.y) / 2 * innerHeight];
  };
  // each sprite: its image anchored by ref_px at its world ref point; drawn far to near
  const items = [
    { at: bench.ref_world, depthAt: [6.5, 4.35, 0.5], draw: (x, y) => g.drawImage(bench.image, x - bench.ref_px[0], y - bench.ref_px[1]) },
    ...PLANTS.map(([px, py]) => ({ at: [px, py, 0], depthAt: [px, py, 0.5],
      draw: (x, y) => g.drawImage(plant.image, x - plant.ref_px[0], y - plant.ref_px[1]) })),
    ...robots.map(r => ({ at: r.sheet.ref_world, depthAt: [r.sheet.ref_world[0], r.sheet.ref_world[1] - 0.6, 0.5], robot: r,
      draw: (x, y, t) => {
        const [fw, fh] = r.sheet.size;
        const f = Math.floor(t * r.sheet.fps) % r.sheet.frames;
        g.drawImage(r.sheet.image, f * fw, 0, fw, fh, x - r.sheet.ref_px[0], y - r.sheet.ref_px[1], fw, fh);
      } })),
  ];
  // robots are cut by the desk already (rendered with the bench as a holdout): they go over the bench
  items.forEach(i => { i.depth = bl(...i.depthAt).dot(back) + (i.robot ? 10 : 0); });
  items.sort((a, b) => a.depth - b.depth);
  const t0 = performance.now();
  (function paint() {
    g.clearRect(0, 0, canvas.width, canvas.height);
    const t = (performance.now() - t0) / 1000 + 0.3;
    for (const i of items) i.draw(...screen(i.at), t);
    requestAnimationFrame(paint);
  })();
  // the lamp glows go above the sprites: a transparent three canvas stacked over the sprite canvas
  const glowScene = new THREE.Scene();
  scene.children.filter(c => c.isSprite).forEach(s => glowScene.add(s));
  const over = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  over.setPixelRatio(1);
  over.setSize(innerWidth, innerHeight);
  over.outputColorSpace = THREE.SRGBColorSpace;
  over.domElement.style.pointerEvents = 'none';
  document.body.appendChild(over.domElement);
  over.setAnimationLoop(() => over.render(glowScene, camera));
}

async function main() {
  if (VARIANT === 'A') await variantA();
  else if (VARIANT === 'B1') await variantB1();
  let frames = 0;
  renderer.setAnimationLoop(() => {
    const dt = clock.getDelta();
    mixers.forEach(m => m.update(dt));
    renderer.render(scene, camera);
    if (++frames === 3) window.layoutReady = true;
  });
}

main().catch(e => { console.error(e); document.body.insertAdjacentHTML('beforeend', `<pre>${e}</pre>`); });
