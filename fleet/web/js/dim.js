// Dimming background rooms. Rooms share instanced and merged meshes, so a room can't be dimmed through its own
// materials; instead every material in the scene gets a small shader addition that desaturates and darkens whatever
// it draws inside a dimmed room's footprint (floor, walls, furniture, androids). Anything outside the footprint, such
// as an attention lantern hanging past the room's corner, is untouched. Sprites and particles keep their own shaders.
// A dimmed room whose work is running is lit warm: it darkens towards amber instead of grey.

import * as THREE from 'three';
import { scene } from './scene.js';

const MAX = 32;   // dimmed rooms at once; more than this and the rest stay bright
const uniforms = {
  fleetDimRects: { value: Array.from({ length: MAX }, () => new THREE.Vector4()) },
  fleetDimAmount: { value: new Float32Array(MAX) },
  fleetDimWarm: { value: new Float32Array(MAX) },
  fleetDimCount: { value: 0 },
};

const WORLD = `
{
  vec4 fleetWorld = vec4( transformed, 1.0 );
#ifdef USE_INSTANCING
  fleetWorld = instanceMatrix * fleetWorld;
#endif
  vFleetXZ = ( modelMatrix * fleetWorld ).xz;
}`;
const DIM = `
{
  float fleetDim = 0.0, fleetWarm = 0.0;
  for ( int i = 0; i < ${MAX}; i ++ ) {
    if ( i >= fleetDimCount ) break;
    vec4 r = fleetDimRects[ i ];
    if ( vFleetXZ.x >= r.x && vFleetXZ.x <= r.z && vFleetXZ.y >= r.y && vFleetXZ.y <= r.w ) {
      fleetDim = max( fleetDim, fleetDimAmount[ i ] );
      fleetWarm = max( fleetWarm, fleetDimWarm[ i ] );
    }
  }
  float fleetGrey = dot( gl_FragColor.rgb, vec3( 0.299, 0.587, 0.114 ) );
  vec3 fleetTint = mix( vec3( 0.5 ), vec3( 0.72, 0.5, 0.3 ), fleetWarm );
  gl_FragColor.rgb = mix( gl_FragColor.rgb, vec3( fleetGrey ) * fleetTint, fleetDim * 0.85 );
}`;

function patch(material) {
  if (material.userData.fleetDim || material.isShaderMaterial || material.isSpriteMaterial) return;
  material.userData.fleetDim = true;
  material.onBeforeCompile = shader => {
    const vertex = shader.vertexShader.replace('#include <project_vertex>', '#include <project_vertex>' + WORLD);
    const fragment = shader.fragmentShader.replace('#include <dithering_fragment>', '#include <dithering_fragment>' + DIM);
    if (vertex === shader.vertexShader) return;
    shader.vertexShader = 'varying vec2 vFleetXZ;\n' + vertex;
    if (fragment === shader.fragmentShader) return;   // e.g. points: positioned, but drawn undimmed
    shader.fragmentShader = `uniform vec4 fleetDimRects[ ${MAX} ];\nuniform float fleetDimAmount[ ${MAX} ];\nuniform float fleetDimWarm[ ${MAX} ];\nuniform int fleetDimCount;\nvarying vec2 vFleetXZ;\n` + fragment;
    Object.assign(shader.uniforms, uniforms);
  };
  material.needsUpdate = true;
}

// new meshes (androids, rebuilt rooms) appear with each state document: patch whatever is new
export function patchScene() {
  scene.traverse(o => {
    if (!o.material) return;
    for (const m of Array.isArray(o.material) ? o.material : [o.material]) patch(m);
  });
}

// rects: [x0, z0, x1, z1, amount, warm] for every room with any dimming
export function setDimmed(rects) {
  const n = Math.min(MAX, rects.length);
  for (let i = 0; i < n; i++) {
    const [x0, z0, x1, z1, amount, warm] = rects[i];
    uniforms.fleetDimRects.value[i].set(x0, z0, x1, z1);
    uniforms.fleetDimAmount.value[i] = amount;
    uniforms.fleetDimWarm.value[i] = warm;
  }
  uniforms.fleetDimCount.value = n;
}
