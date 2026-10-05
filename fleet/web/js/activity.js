// Render recorded activities and the phrases an agent mumbles (its glyph bubble's tooltip; see glyphs.js).

import { hash, trunc } from './util.js';

// Interactive CLI sessions (Claude Code / Codex run by a person) sit on the deck beside fleet jobs. Their entity
// keeps the session in `e.job` like a job, with kind 'session'; status is 'working' or 'idle' (waiting on the human).
export const isSession = e => e.kind === 'session';
export const working = j => j.status === 'running' || j.status === 'working';
export const shortId = id => String(id).slice(0, 8);

// status first: an idle session waits on its human; working ones act on their events like running jobs
export function activityFor(job) {
  switch (job.status) {
    case 'idle': return 'await';
    case 'queued': return 'idle';
    case 'done': case 'cancelled': return 'dock';
  }
  return activityOf(job.activity);
}
export const isActive = status => status === 'running' || status === 'working';

// Execution classifies observations before the deck receives them.
export function activityOf(ev) {
  if (!ev || ev.kind === 'text') return 'think';
  return ev.activity_class ?? null;
}

// ------------------------------------------------------------------ mumbling: tool events → what an agent would mutter
// The phrases say what the agent is doing in plain English; the log and panel keep the raw commands.
// Claude's shell calls carry their own description (ev.intent); everything else is phrased from the tool and its argument.
const MUMBLE_LEADS = ['', '', '', 'now ', 'ok, ', 'hmm, ', 'right, ', 'just '];
const DOUBLED_VERBS = new Set(['run', 'rerun', 'get', 'set', 'put', 'stop', 'cut', 'sit', 'plan', 'begin', 'commit', 'drop', 'ship',
  'split', 'strip', 'swap', 'grab', 'skim', 'scan', 'trim', 'tag', 'map', 'zip', 'dig', 'pin', 'log', 'submit', 'admit', 'rip', 'snip', 'wrap', 'step', 'prep', 'chop', 'pop']);
function gerund(verb) {
  const v = verb.toLowerCase();
  if (DOUBLED_VERBS.has(v)) return v + v.slice(-1) + 'ing';
  if (v.endsWith('ie')) return v.slice(0, -2) + 'ying';
  if (v.endsWith('e') && !v.endsWith('ee') && v.length > 2) return v.slice(0, -1) + 'ing';
  return v + 'ing';
}
// "Run database migrations" → "running database migrations"
function fromIntent(text) {
  const words = String(text).trim().replace(/[.:]+$/, '').split(/\s+/);
  if (!words[0]) return '';
  const first = /^[A-Za-z]+$/.test(words[0]) && !/ing$/i.test(words[0]) ? gerund(words[0]) : words[0].toLowerCase();
  return (first + ' ' + words.slice(1).join(' ')).trim();
}
const baseName = p => String(p || '').replace(/\/+$/, '').split('/').pop() || String(p || '');
function hostOf(url) { try { return new URL(url).hostname.replace(/^www\./, ''); } catch (err) { return trunc(url, 40); } }
function quoted(s) { return `“${trunc(s, 40)}”`; }
function globPhrase(pattern) {
  const p = String(pattern || '');
  const ext = p.match(/\*\.(\{[^}]+\}|[\w.]+)$/);
  const dir = p.split(/[*{]/)[0].replace(/\/+$/, '');
  const what = ext ? `.${ext[1].replace(/[{}]/g, '').split(',').join('/.')} files` : `files like ${trunc(p, 40)}`;
  return `looking for ${what}${dir ? ' in ' + baseName(dir) : ''}`;
}
const SHELL_PHRASES = { test: 'running the tests', build: 'building', search: 'searching',
  web: 'working online', wait: 'waiting', ship: 'shipping changes', review: 'reviewing changes',
  type: 'working at the terminal' };
function shellPhrase(event) {
  return SHELL_PHRASES[event.activity_class] ?? 'activity unknown';
}
function toolPhrase(ev) {
  const s = ev.summary || '', name = ev.name || '';
  if (ev.intent) return fromIntent(ev.intent);
  switch (name) {
    case 'Bash': case 'shell': case 'BashOutput': return name === 'BashOutput' ? 'checking on a background job' : shellPhrase(ev);
    case 'Read': return `reading ${baseName(s)}`;
    case 'Edit': case 'MultiEdit': return `editing ${baseName(s)}`;
    case 'Write': return `writing ${baseName(s)}`;
    case 'NotebookEdit': return `editing ${baseName(s)}`;
    case 'apply_patch': return `editing ${s.split(', ').map(baseName).slice(0, 2).join(' and ')}`;
    case 'Grep': return `searching for ${quoted(s)}`;
    case 'Glob': return globPhrase(s);
    case 'WebFetch': return `reading ${hostOf(s)}`;
    case 'WebSearch': case 'web_search': return `searching the web for ${quoted(s)}`;
    case 'TodoWrite': case 'TaskCreate': case 'TaskUpdate': return 'updating my todo list';
    case 'Task': case 'Agent': return s ? `asking a helper to ${fromIntent(s).replace(/^(\w+)ing\b/, (m, v) => v)}` : 'asking a helper';
    case 'Skill': return `reading up on ${trunc(s, 40)}`;
    case 'AskUserQuestion': return 'waiting on a question for you';
    case 'ExitPlanMode': case 'EnterPlanMode': return 'thinking through a plan';
  }
  switch (ev.tool) {
    case 'bash': return shellPhrase(ev);
    case 'edit': return s ? `editing ${baseName(s)}` : 'editing';
    case 'read': return s ? `reading ${baseName(s)}` : 'reading';
    case 'search': return s ? `searching for ${quoted(s)}` : 'searching';
    case 'web': return /^https?:/.test(s) ? `reading ${hostOf(s)}` : `looking up ${quoted(s)}`;
    case 'think': return s && s !== 'thinking…' ? s : 'thinking';
    case 'plan': return 'updating my todo list';
    case 'delegate': return 'handing something off';
  }
  return name ? `using ${name.replace(/^mcp__.+__/, '').replace(/_/g, ' ')}` : 'working on it';
}
export function mumble(ev) {
  const phrase = toolPhrase(ev).trim();
  if (ev.tool === 'think') return phrase;   // already the agent's own words
  const lead = MUMBLE_LEADS[hash(ev.summary + (ev.ts || '')) % MUMBLE_LEADS.length];
  return trunc(lead + phrase, 110) + '…';
}
