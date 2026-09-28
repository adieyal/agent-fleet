// highlight.js 11.12.0 core plus the reader's languages, bundled into highlight.min.js with:
//   npm i highlight.js@11.12.0 esbuild && npx esbuild build.js --bundle --format=esm --minify --outfile=highlight.min.js
import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import css from 'highlight.js/lib/languages/css';
import diff from 'highlight.js/lib/languages/diff';
import dockerfile from 'highlight.js/lib/languages/dockerfile';
import go from 'highlight.js/lib/languages/go';
import ini from 'highlight.js/lib/languages/ini';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import plaintext from 'highlight.js/lib/languages/plaintext';
import python from 'highlight.js/lib/languages/python';
import rust from 'highlight.js/lib/languages/rust';
import shell from 'highlight.js/lib/languages/shell';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';
Object.entries({ bash, css, diff, dockerfile, go, ini, javascript, json, markdown, plaintext, python, rust, shell, sql,
  typescript, xml, yaml }).forEach(([name, language]) => hljs.registerLanguage(name, language));
export default hljs;
