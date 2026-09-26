// What an agent is doing: event classification and the phrases it mumbles (its glyph bubble's tooltip; see glyphs.js).

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
    case 'failed': return 'failed';
    case 'stalled': return 'stalled';
  }
  return activityOf(job.activity);
}
export const isActive = status => status === 'running' || status === 'working';

// What an event has an android doing: the coarse tool kind refined with the same parse the mumbling uses (shellClass),
// so a bubble saying "committing my work" goes with an android carrying a box to the outbox. null (an error): carry on.
const GIT_ACTS = { commit: 'ship', push: 'ship', diff: 'review', log: 'review', show: 'review', blame: 'review', status: 'review' };
const SHELL_ACTS = { test: 'test', lint: 'test', typecheck: 'test', install: 'build', build: 'build', serve: 'build', make: 'build',
  docker: 'build', packages: 'build', curl: 'web', ssh: 'web', copy: 'web', gh: 'web', fleet: 'web', grep: 'search', list: 'search',
  db: 'search', sleep: 'wait', ps: 'wait' };
export const DOC_FILE = /\.(md|mdx|markdown|rst|txt)$/i;
function shellActivity(command) {
  const c = shellClass(command);
  return c.cls === 'git' ? GIT_ACTS[c.sub] || 'type' : SHELL_ACTS[c.cls] || 'type';
}
export function activityOf(ev) {
  if (!ev || ev.kind === 'text') return 'think';
  if (ev.kind !== 'tool') return null;
  const s = ev.summary || '';
  switch (ev.name) {
    case 'Bash': case 'shell': return shellActivity(s);
    case 'BashOutput': case 'AskUserQuestion': return 'wait';
    case 'Read': case 'Skill': return 'read';
    case 'Edit': case 'MultiEdit': case 'Write': case 'NotebookEdit': return DOC_FILE.test(s) ? 'doc' : 'edit';
    case 'apply_patch': return s && s.split(', ').every(p => DOC_FILE.test(p)) ? 'doc' : 'edit';
    case 'Grep': case 'Glob': return 'search';
    case 'WebFetch': case 'WebSearch': case 'web_search': return 'web';
    case 'TodoWrite': case 'TaskCreate': case 'TaskUpdate': case 'EnterPlanMode': case 'ExitPlanMode': return 'plan';
    case 'Task': case 'Agent': return 'delegate';
  }
  switch (ev.tool) {
    case 'bash': return shellActivity(s);
    case 'edit': return DOC_FILE.test(s) ? 'doc' : 'edit';
    case 'read': case 'search': case 'web': case 'think': case 'plan': case 'delegate': return ev.tool;
  }
  return 'type';
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
// strip wrappers so "cd x && timeout 60 uv run pytest -q" reads as "pytest -q"
function mainCommand(command) {
  const parts = String(command || '').split(/\s*(?:&&|\|\||;|\|)\s*/).map(s => s.trim()).filter(Boolean);
  const pick = parts.find(s => !/^(cd|export|source|set|\.)\b/.test(s)) || parts[0] || '';
  let words = pick.split(/\s+/);
  const skip = () => {
    for (;;) {
      if (/^\w+=/.test(words[0] || '')) words = words.slice(1);
      else if (['sudo', 'exec', 'nice', 'time', 'command', 'env'].includes(words[0])) words = words.slice(1);
      else if (words[0] === 'timeout') words = words.slice(words[1] && /^\d/.test(words[1]) ? 2 : 1);
      else if (words[0] === 'uv' && words[1] === 'run') words = words.slice(2);
      else if (['npx', 'pnpx', 'bunx'].includes(words[0])) words = words.slice(words[1] === '-y' ? 2 : 1);
      else if (/^python[\d.]*$/.test(words[0] || '') && words[1] === '-m') words = words.slice(2);
      else return;
    }
  };
  skip();
  return words;
}
const GIT_PHRASES = { status: "checking what's changed", diff: 'looking over the diff', log: 'reading the git history', commit: 'committing my work',
  add: 'staging changes', push: 'pushing my branch', pull: 'pulling the latest', fetch: 'fetching the latest', checkout: 'switching branches',
  switch: 'switching branches', worktree: 'setting up a worktree', rebase: 'rebasing', merge: 'merging branches', stash: 'stashing changes',
  show: 'looking at a commit', grep: 'searching the repo', blame: 'working out who wrote this', branch: 'looking at branches',
  reset: 'resetting a branch', restore: 'restoring a file', 'cherry-pick': 'cherry-picking a commit', clone: 'cloning a repo' };
// What a shell command is: { cls, prog, sub, arg, w }. The one parse both the mumbling and the androids' activities use,
// so what an agent says and what it does agree. For git and npm-style runners `sub` is the subcommand or script.
function shellClass(command) {
  const wrapped = String(command || '').match(/^\s*(?:ba|z)?sh\s+-l?c\s+(['"])([\s\S]*)\1\s*$/);   // codex: bash -lc '…'
  if (wrapped) return shellClass(wrapped[2]);
  const w = mainCommand(command), prog = baseName(w[0] || ''), arg = w.slice(1).find(x => !x.startsWith('-')) || '';
  const is = (cls, sub = w[1] || '') => ({ cls, prog, sub, arg, w });
  if (!prog) return is('none');
  if (prog === 'git') return is('git', w.find((x, i) => i > 0 && !x.startsWith('-')) || '');
  if (/^(pytest|vitest|jest|mocha|tox|nox)$/.test(prog) || /\b(test|pytest|vitest|jest)\b/.test(w.slice(0, 3).join(' '))) return is('test');
  if (/^(npm|pnpm|yarn|bun)$/.test(prog)) {
    if (/^(i|install|ci|add)$/.test(w[1] || '')) return is('install');
    const script = w[1] === 'run' ? w[2] || '' : w[1] || '';
    return is(/build/.test(script) ? 'build' : /lint/.test(script) ? 'lint' : /format|prettier/.test(script) ? 'format' : /dev|start|serve/.test(script) ? 'serve' : 'script', script);
  }
  if (prog === 'make') return is('make');
  if (/^(eslint|ruff|flake8|pylint|mypy|pyright|tsc)$/.test(prog)) return is(prog === 'tsc' || prog === 'mypy' || prog === 'pyright' ? 'typecheck' : 'lint');
  if (/^(prettier|black|isort)$/.test(prog)) return is('format');
  if (/^(ls|tree|find|fd|du|stat)$/.test(prog)) return is('list');
  if (/^(cat|head|tail|less|bat|sed|awk|jq|wc)$/.test(prog)) return is('cat');
  if (/^(grep|rg|ag)$/.test(prog)) return is('grep');
  if (/^(curl|wget|http)$/.test(prog)) return is('curl');
  if (prog === 'ssh') return is('ssh');
  if (/^(scp|rsync)$/.test(prog)) return is('copy');
  if (/^docker(-compose)?$/.test(prog)) return is('docker');
  if (/^(psql|mysql|sqlite3)$/.test(prog)) return is('db');
  if (/^(python[\d.]*|node|ruby|deno|bun|bash|sh|zsh)$/.test(prog)) return is('script-run');
  if (prog === 'manage.py' || arg === 'manage.py') return is('django');
  if (/^(kill|pkill|killall)$/.test(prog)) return is('kill');
  if (prog === 'mkdir') return is('mkdir');
  if (prog === 'rm') return is('rm');
  if (/^(mv|cp|ln)$/.test(prog)) return is('move');
  if (prog === 'gh') return is('gh');
  if (prog === 'fleet') return is('fleet');
  if (prog === 'sleep') return is('sleep');
  if (/^(echo|printf)$/.test(prog)) return is('echo');
  if (/^(playwright|chromium|chrome)$/.test(prog)) return is('shots');
  if (/^(pip|pip3|uv|poetry)$/.test(prog)) return is('packages');
  if (/^(ps|top|htop|pgrep|lsof|ss|netstat)$/.test(prog)) return is('ps');
  if (/^(tmux|screen)$/.test(prog)) return is('tmux');
  return is('other');
}
const SHELL_PHRASES = { none: 'at the terminal', test: 'running the tests', install: 'installing dependencies', build: 'building it',
  lint: 'linting', typecheck: 'type-checking', format: 'tidying the formatting', serve: 'starting the dev server', copy: 'copying files across',
  db: 'querying the database', django: 'poking Django', kill: 'stopping a process', mkdir: 'making a folder', rm: 'cleaning up',
  move: 'shuffling files around', fleet: 'checking on the fleet', sleep: 'waiting a moment', echo: 'jotting something down',
  shots: 'taking screenshots', packages: 'sorting out python packages', ps: 'checking what’s running', tmux: 'juggling terminals' };
function shellPhrase(command) {
  const { cls, prog, sub, arg, w } = shellClass(command);
  switch (cls) {
    case 'git': return GIT_PHRASES[sub] || 'doing some git';
    case 'script': return sub ? `running ${sub}` : 'fiddling with npm';
    case 'make': return sub ? `running make ${sub}` : 'running make';
    case 'list': return `looking around${arg && !arg.startsWith('.') ? ' ' + baseName(arg) : ''}`;
    case 'cat': return arg && !/^['"\d]/.test(arg) ? `reading ${baseName(arg)}` : 'reading some output';
    case 'grep': return arg ? `searching for ${quoted(arg.replace(/^['"]|['"]$/g, ''))}` : 'searching';
    case 'curl': { const url = w.find(x => /^https?:/.test(x.replace(/^['"]/, ''))); return url ? `poking ${hostOf(url.replace(/^['"]|['"]$/g, ''))}` : 'poking a server'; }
    case 'ssh': return `hopping onto ${arg.split('@').pop() || 'another box'}`;
    case 'docker': return sub === 'compose' || prog === 'docker-compose' ? 'wrangling containers' : 'fiddling with docker';
    case 'script-run': return arg ? `running ${baseName(arg)}` : `trying something in ${prog.replace(/[\d.]+$/, '')}`;
    case 'gh': return sub === 'pr' ? 'checking the pull request' : sub === 'issue' ? 'looking at issues' : 'talking to GitHub';
    case 'other': return `running ${prog}`;
  }
  return SHELL_PHRASES[cls];
}
function toolPhrase(ev) {
  const s = ev.summary || '', name = ev.name || '';
  if (ev.intent) return fromIntent(ev.intent);
  switch (name) {
    case 'Bash': case 'shell': case 'BashOutput': return name === 'BashOutput' ? 'checking on a background job' : shellPhrase(s);
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
    case 'bash': return shellPhrase(s);
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
