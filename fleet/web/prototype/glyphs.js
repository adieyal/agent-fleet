// Canvas-drawn glyphs for the workbench prototype: tile ticks, the lantern's question mark,
// action bubbles above the robots, and footprints. Each returns a THREE.CanvasTexture.
import * as THREE from 'three';

function canvas(w, h, draw) {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const g = c.getContext('2d');
  g.lineCap = 'round';
  g.lineJoin = 'round';
  draw(g, w, h);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  return t;
}

export function tick(color = '#5a5a62') {
  return canvas(128, 128, g => {
    g.strokeStyle = color;
    g.lineWidth = 13;
    g.beginPath();
    g.moveTo(34, 66);
    g.lineTo(56, 88);
    g.lineTo(96, 42);
    g.stroke();
  });
}

export function question(color = '#ffffff') {
  return canvas(128, 128, g => {
    g.fillStyle = color;
    g.font = 'bold 96px system-ui, sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.fillText('?', 64, 70);
  });
}

// Action icons, drawn in a 100 x 100 box.
const ICONS = {
  search(g) {
    g.lineWidth = 9;
    g.beginPath();
    g.arc(42, 42, 22, 0, Math.PI * 2);
    g.stroke();
    g.lineWidth = 12;
    g.beginPath();
    g.moveTo(59, 59);
    g.lineTo(80, 80);
    g.stroke();
  },
  pencil(g) {
    g.lineWidth = 8;
    g.beginPath();
    g.moveTo(26, 74);
    g.lineTo(30, 58);
    g.lineTo(66, 22);
    g.lineTo(78, 34);
    g.lineTo(42, 70);
    g.closePath();
    g.stroke();
    g.beginPath();
    g.moveTo(58, 30);
    g.lineTo(70, 42);
    g.stroke();
  },
  flask(g) {
    g.lineWidth = 8;
    g.beginPath();
    g.moveTo(40, 18);
    g.lineTo(60, 18);
    g.moveTo(43, 20);
    g.lineTo(43, 44);
    g.lineTo(24, 78);
    g.lineTo(76, 78);
    g.lineTo(57, 44);
    g.lineTo(57, 20);
    g.stroke();
    g.globalAlpha = 0.45;
    g.beginPath();
    g.moveTo(33, 62);
    g.lineTo(67, 62);
    g.lineTo(76, 78);
    g.lineTo(24, 78);
    g.closePath();
    g.fill();
  },
};

// A white speech bubble with a tail, holding one action icon in the robot's host colour.
export function bubble(icon, color) {
  return canvas(160, 184, g => {
    g.shadowColor = 'rgba(40, 50, 70, .25)';
    g.shadowBlur = 10;
    g.shadowOffsetY = 3;
    g.fillStyle = '#ffffff';
    g.beginPath();
    g.roundRect(14, 10, 132, 132, 30);
    g.moveTo(66, 140);
    g.lineTo(80, 164);
    g.lineTo(94, 140);
    g.fill();
    g.shadowColor = 'transparent';
    g.strokeStyle = color;
    g.fillStyle = color;
    g.translate(30, 26);
    ICONS[icon](g);
  });
}

export function footprint() {
  return canvas(64, 128, g => {
    g.fillStyle = '#44474f';
    g.beginPath();
    g.ellipse(32, 44, 17, 30, 0, 0, Math.PI * 2);
    g.fill();
    g.beginPath();
    g.ellipse(32, 100, 13, 17, 0, 0, Math.PI * 2);
    g.fill();
  });
}

export function halo(color) {
  return canvas(128, 128, g => {
    const r = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    r.addColorStop(0, color);
    r.addColorStop(1, 'rgba(0, 0, 0, 0)');
    g.fillStyle = r;
    g.fillRect(0, 0, 128, 128);
  });
}
