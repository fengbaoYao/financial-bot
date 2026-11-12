import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { isTranscriptNearBottom, scrollTranscriptToBottom } from "../lib/bot-transcript-scroll.ts";

const shell = readFileSync(new URL("../components/financial-bot/mausbot-shell.tsx", import.meta.url), "utf8");

test("transcript updates do not force readers away from their current position", () => {
  const start = shell.indexOf("const el = transcriptRef.current;");
  const end = shell.indexOf("}, [visibleMessages, streaming, queued.length, liveStatus, turnEndNotice]);", start);
  assert.notEqual(start, -1, "transcript scroll effect exists");
  assert.notEqual(end, -1, "transcript scroll effect dependencies exist");
  const effect = shell.slice(start, end);
  assert.match(effect, /scrollTranscriptToBottom\(el, shouldFollowTranscriptRef\.current\)/, "scrolling is gated by whether the reader is following the bottom");
  assert.doesNotMatch(effect, /el\.scrollTop\s*=\s*el\.scrollHeight/, "unconditional bottom jumps are removed");
  assert.match(shell, /onScroll=\{\(event\) => \{\s*shouldFollowTranscriptRef\.current = isTranscriptNearBottom\(event\.currentTarget\);/, "manual scrolling updates the follow state");
});

test("reader stays at their current position during stream updates", () => {
  const transcript = { scrollTop: 180, scrollHeight: 1200, clientHeight: 600 };
  assert.equal(isTranscriptNearBottom(transcript), false);
  assert.equal(scrollTranscriptToBottom(transcript, false), false);
  assert.equal(transcript.scrollTop, 180);
});

test("a reader already at the bottom continues following new messages", () => {
  const transcript = { scrollTop: 530, scrollHeight: 1200, clientHeight: 600 };
  assert.equal(isTranscriptNearBottom(transcript), true);
  assert.equal(scrollTranscriptToBottom(transcript, true), true);
  assert.equal(transcript.scrollTop, 1200);
});

test("messages without actual model details do not show an empty model label", () => {
  const labeler = shell.slice(shell.indexOf("function modelProvenanceLabel"), shell.indexOf("function wireToChatMsg"));
  assert.match(labeler, /: "";/, "no-provenance state produces no label");
  assert.match(shell, /!mine && !m\.pending && provenanceLabel/, "empty labels are not rendered");
});

test("live execution status keeps one stable transcript position", () => {
  assert.doesNotMatch(shell, /liveInCapsule/, "status is not moved between the activity capsule and a standalone line");
  assert.match(shell, /\{liveText\s*\?\s*<LiveLine text=\{liveText\} zh=\{zh\} \/>\s*:\s*null\}/, "live status always uses its dedicated row");
});
