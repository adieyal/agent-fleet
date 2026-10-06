// Where everything sits on the canvas. Stage columns and bands are computed from records;
// regions, views and drafted stages carry their own shared positions; epics, documents and
// cards outside the workflow are placed by the person's own layout.

export const FX = 60, FY = 120, CW = 250, CG = 24, CARD_W = 234, CARD_H = 112;
export const BANDS = ['now', 'next', 'later'];
export const BAND_NAMES = { now: 'Now', next: 'Next', later: 'Later' };

export function columnsOf(model) {
  return model.workflow.stages.map((stage) => stage.id).concat(['done']);
}

function onBoard(item) {
  return item.stage && !item.region;
}

export function bandLayout(model) {
  const cols = columnsOf(model);
  let y = FY + 40 + 80;
  const tops = {}, heights = {};
  for (const band of BANDS) {
    const most = Math.max(1, ...cols.map((col) => model.items.filter((item) => onBoard(item) && item.stage === col
      && (item.band || 'later') === band).length));
    tops[band] = y;
    heights[band] = 40 + most * CARD_H + 8;
    y += heights[band];
  }
  return { tops, heights, h: y - FY + 16 };
}

export function columnRects(model) {
  const bands = bandLayout(model);
  return columnsOf(model).map((id, index) => ({ id, x: FX + 110 + index * (CW + CG), y: FY + 40, w: CW, h: bands.h - 56 }));
}

export function frameRect(model) {
  const count = model.workflow.stages.length + 1;
  return { x: FX, y: FY, w: 126 + count * (CW + CG) - CG, h: bandLayout(model).h };
}

export function bandAt(model, y) {
  const bands = bandLayout(model);
  return BANDS.find((band) => y >= bands.tops[band] && y < bands.tops[band] + bands.heights[band]) || null;
}

export function inside(point, rect) {
  return point.x >= rect.x && point.x <= rect.x + rect.w && point.y >= rect.y && point.y <= rect.y + rect.h;
}

function regionSlot(model, region, item) {
  const rect = region.rect;
  const per = Math.max(1, Math.floor((rect.w - 24) / (CW)));
  const peers = model.items.filter((other) => other.region === region.id);
  const slot = Math.max(0, peers.findIndex((other) => other.id === item.id));
  return { x: rect.x + 24 + (slot % per) * CW, y: rect.y + 70 + Math.floor(slot / per) * (CARD_H + 8) };
}

export function docsTop(model) {
  const frame = frameRect(model);
  return frame.y + frame.h + 80;
}

export function itemPosition(model, layout, item, drag) {
  if (drag && drag.kind === 'task' && drag.id === item.id && drag.x !== undefined) return { x: drag.x, y: drag.y };
  if (item.region) {
    const region = model.regions.find((entry) => entry.id === item.region);
    if (region) return regionSlot(model, region, item);
  }
  if (!item.stage) {
    const saved = layout['task:' + item.id];
    if (saved) return { x: saved.x, y: saved.y };
    const loose = model.items.filter((other) => !other.stage && !other.region);
    const index = Math.max(0, loose.findIndex((other) => other.id === item.id));
    const frame = frameRect(model);
    return { x: frame.x + frame.w + 80 + (index % 2) * (CW), y: FY + 40 + Math.floor(index / 2) * (CARD_H + 8) };
  }
  const column = columnRects(model).find((col) => col.id === item.stage);
  const bands = bandLayout(model);
  const band = item.band || 'later';
  const peers = model.items.filter((other) => onBoard(other) && other.stage === item.stage && (other.band || 'later') === band);
  const index = Math.max(0, peers.findIndex((other) => other.id === item.id));
  if (!column) return { x: FX, y: FY };
  return { x: column.x + 8, y: bands.tops[band] + 36 + index * CARD_H };
}

export function docPosition(model, layout, id, drag) {
  if (drag && drag.kind === 'doc' && drag.id === id && drag.x !== undefined) return { x: drag.x, y: drag.y };
  const saved = layout['doc:' + id];
  if (saved) return { x: saved.x, y: saved.y };
  const top = docsTop(model);
  return { spec: { x: 60, y: top }, star: { x: 660, y: top }, report: { x: 1140, y: top } }[id];
}

export function epicPosition(model, layout, epic, index, drag) {
  if (drag && drag.kind === 'epic' && drag.id === epic.id && drag.x !== undefined) return { x: drag.x, y: drag.y };
  const saved = layout['epic:' + epic.id];
  if (saved) return { x: saved.x, y: saved.y };
  return { x: 60 + index * 520, y: docsTop(model) + 380 };
}

export function epicRect(model, layout, epic, index, zoom) {
  const at = epicPosition(model, layout, epic, index, null);
  const full = zoom >= 0.5;
  return { x: at.x, y: at.y, w: 480, h: 168 + (full ? 30 + epic.children.length * 40 + 52 : 0) };
}

export function regionRect(region, drag) {
  if (drag && drag.kind === 'zone' && drag.id === region.id && drag.x !== undefined) {
    return { x: drag.x, y: drag.y, w: region.rect.w, h: region.rect.h };
  }
  return region.rect;
}

export function viewPosition(view, drag) {
  if (drag && drag.kind === 'widget' && drag.id === view.id && drag.x !== undefined) return { x: drag.x, y: drag.y };
  return { x: view.x, y: view.y };
}

export function blockPosition(block, drag) {
  if (drag && drag.kind === 'block' && drag.id === block.id && drag.x !== undefined) return { x: drag.x, y: drag.y };
  return { x: block.x, y: block.y };
}

export const VIEW_WIDTH = { swimlanes: 780, board: 440, table: 640, progress: 520, metric: 240, attention: 380, doc: 420,
  agents: 440, charter: 420, note: 300 };
