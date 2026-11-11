import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const source = await readFile(new URL('../components/research-trace.tsx', import.meta.url), 'utf8');
let compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.ESNext } }).outputText;
compiled = compiled.replace(/from "([^"]+)"/g, (_, name) => {
  let url;
  if (name === '@/components/language-provider') url = 'data:text/javascript,' + encodeURIComponent('export const useLanguage = () => ({locale: "en"});');
  else if (name === '@/lib/research-export') url = 'data:text/javascript,' + encodeURIComponent('export function researchExport() { throw new Error("Export is outside this display test"); }');
  else if (name === '@/lib/research-activity') url = new URL('../lib/research-activity.ts', import.meta.url).href;
  else url = import.meta.resolve(name);
  return `from ${JSON.stringify(url)}`;
});
export const { ResearchTools, ResponseTools } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
export const fixtureSteps = [
  { tool: 'search_stocks', status: 'completed' },
  { tool: 'get_sec_company_data', status: 'completed' },
  { tool: 'get_stock_news', status: 'completed' },
];

test('checking is an open, accessible progress control with icons and a logical rail', () => {
  const html = renderToStaticMarkup(React.createElement(ResearchTools, { steps: fixtureSteps, phase: 'Checking facts and sources' }));
  assert.match(html, /<details[^>]*class="research-tool-activity agent-run-timeline"[^>]*\bopen\b/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /Checking facts and sources/);
  assert.match(html, /Retrieve SEC filings and facts/);
  assert.match(html, /agent-run-rail/);
  assert.match(html, /agent-run-icon/);
  assert.match(html, /<svg/);
  assert.doesNotMatch(html, /Writing|checked before saving/);
});
test('progress remains visible before the first tool without presenting an answer', () => {
  const html = renderToStaticMarkup(React.createElement(ResearchTools, { steps: [], phase: 'Preparing research' }));
  assert.match(html, /Preparing research/);
  assert.doesNotMatch(html, /markdown-body|research-streaming-answer/);
});
test('completed tool history is grouped, collapsed and has no busy indicator', () => {
  const html = renderToStaticMarkup(React.createElement(ResponseTools, { response: { research_steps: fixtureSteps } }));
  assert.match(html, /Tools used/);
  assert.match(html, /<details[^>]*class="research-tool-activity agent-run-timeline"/);
  assert.match(html, /3 tools · 3 groups/);
  assert.match(html, /agent-run-item/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b|research-stream-dot|Checking facts|aria-live/);
});

test('repeated tool calls merge into one group with a visible count', () => {
  const repeated = [
    { tool: 'web_search', status: 'completed' },
    { tool: 'web_search', status: 'completed' },
    { tool: 'web_search', status: 'completed' },
    { tool: 'read_public_source', status: 'completed' },
  ];
  const html = renderToStaticMarkup(React.createElement(ResearchTools, { steps: repeated }));
  assert.match(html, /4 tools · 2 groups/);
  assert.match(html, /×3/);
  assert.equal((html.match(/class="agent-run-item"/g) ?? []).length, 2);
  assert.equal((html.match(/Search public sources/g) ?? []).length, 1);
});
test('saved work duration stays with the collapsed tool history', () => {
  const response = JSON.parse(JSON.stringify({ research_steps: fixtureSteps, payload: { _meta: { latency_seconds: 78.2 } } }));
  const html = renderToStaticMarkup(React.createElement(ResponseTools, { response }));
  assert.match(html, /Worked for 1m 18s/);
  assert.match(html, /3 tools/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b|research-stream-dot/);
});
test('very fast replies show an honest duration without an empty tool disclosure', () => {
  const html = renderToStaticMarkup(React.createElement(ResponseTools, { response: { payload: { _meta: { latency_seconds: 0.15 } } } }));
  assert.match(html, /Worked for &lt;1s/);
  assert.doesNotMatch(html, /<details/);
});

test('a completed tool with a reason code is shown as a warning, not a green check', () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchTools, {
      steps: [{ tool: 'search_stocks', status: 'completed', reasonCode: 'no_matches' }],
    }),
  );
  assert.match(html, /data-status="warning"/);
  assert.match(html, /No match/);
  assert.doesNotMatch(html, /data-status="completed"/);
});

test('the connector into the running step is marked as a lead-in', () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchTools, {
      steps: [
        { tool: 'search_stocks', status: 'completed' },
        { tool: 'get_stock_news', status: 'running' },
      ],
      phase: 'Retrieving company news',
    }),
  );
  assert.match(html, /is-lead-in/);
  assert.match(html, /data-status="running"/);
  assert.match(html, /执行中|Running/);
});

test('a few failed source reads do not mark the whole tool group unavailable', () => {
  const steps = [
    { tool: 'read_public_source', status: 'completed', endpoint: 'example.com' },
    { tool: 'read_public_source', status: 'completed', endpoint: 'example.org' },
    { tool: 'read_public_source', status: 'unavailable', endpoint: 'example.net' },
  ];
  const html = renderToStaticMarkup(React.createElement(ResearchTools, { steps }));
  assert.match(html, /data-status="warning"/);
  assert.match(html, /Partial 2\/3/);
  assert.doesNotMatch(html, /data-status="unavailable"/);
  assert.match(html, /example\.com/);
  assert.doesNotMatch(html, /Source unavailable/);
});

test('unconfigured providers are skipped rather than shown as an outage', () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchTools, {
      steps: [{ tool: 'get_stock_news', status: 'unavailable', reasonCode: 'not_configured' }],
    }),
  );
  assert.match(html, /data-status="skipped"/);
  assert.match(html, /Not configured \(skipped\)/);
  assert.doesNotMatch(html, /Source unavailable/);
});

test('running tool shows the current interface with an endpoint animation', () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchTools, {
      steps: [{ tool: 'read_public_source', status: 'running', endpoint: 'www.cninfo.com.cn' }],
      phase: 'Reading sources',
    }),
  );
  assert.match(html, /agent-run-endpoint/);
  assert.match(html, /www\.cninfo\.com\.cn/);
  assert.match(html, /Fetching/);
});

test('completed tool history lists interfaces and failure counts', () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchTools, {
      steps: [
        { tool: 'web_search', status: 'completed', endpoint: 'brave_search' },
        { tool: 'web_search', status: 'unavailable', endpoint: 'duckduckgo_html' },
      ],
    }),
  );
  assert.match(html, /Interfaces/);
  assert.match(html, /brave_search/);
  assert.match(html, /1 failed/);
});

test('live endpoint history shows the recent interfaces while work continues', () => {
  const html = renderToStaticMarkup(
    React.createElement(ResearchTools, {
      steps: [{ tool: 'read_public_source', status: 'running', endpoint: 'www.cninfo.com.cn', endpoints: ['example.com', 'www.cninfo.com.cn'] }],
      phase: 'Reading sources',
    }),
  );
  assert.match(html, /www\.cninfo\.com\.cn/);
  assert.match(html, /agent-run-endpoint-history/);
  assert.match(html, /example\.com/);
});
