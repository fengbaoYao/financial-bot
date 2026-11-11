"use client";

import { useEffect, useId, useRef, useState, type CSSProperties } from "react";
// flubber has no bundled TypeScript declarations.
// @ts-expect-error - runtime interpolation helper
import { interpolate } from "flubber";
import styles from "./financial-bot-styles";
import type { BotState } from "@/lib/financial-bot/types";

type EyePose = { d: string; transform: string };
type AvatarPose = { body: string; bodyTransform: string; eyes: EyePose[] };
type AvatarProfile = { file: string; duration: number; speed: number; amp: number; blink: number };

const RAW_CACHE = new Map<string, string>();

const PROFILES: Record<string, AvatarProfile> = {
  idle: { file: "idle", duration: 620, speed: 0.8, amp: 4.2, blink: 4.9 },
  working: { file: "working", duration: 460, speed: 1.25, amp: 4.8, blink: 2.8 },
  waiting: { file: "waiting", duration: 540, speed: 0.45, amp: 2.4, blink: 5.4 },
  blocked: { file: "blocked", duration: 360, speed: 0, amp: 0, blink: 0 },
  thinking: { file: "thinking", duration: 380, speed: 0, amp: 0, blink: 0 },
  done: { file: "done", duration: 560, speed: 0.65, amp: 3.2, blink: 4.2 },
};

function fileForState(state: BotState): string {
  // Every state the product can show gets its own drawing: thinking while it is
  // queued, working while a tool runs, waiting when it needs the person,
  // blocked when it stopped or lost the server, done when it finished, and the
  // neutral idle drawing for a conversation that is not doing anything.
  if (state === "queued") return "thinking";
  if (state === "running") return "working";
  if (state === "waiting") return "waiting";
  if (state === "completed") return "done";
  if (state === "failed" || state === "partial") return "blocked";
  if (state === "cancelled" || state === "unknown") return "blocked";
  return "idle";
}

function hidden(element: Element): boolean {
  let node: Element | null = element;
  while (node && node.nodeType === 1) {
    const style = node.getAttribute("style") || "";
    if (/display\s*:\s*none/i.test(style)) return true;
    node = node.parentElement;
  }
  return false;
}

function parsePose(raw: string): AvatarPose {
  const doc = new DOMParser().parseFromString(raw, "image/svg+xml");
  const paths = Array.from(doc.querySelectorAll("path"));
  const bodyCandidates = paths
    .filter((path) => (path.getAttribute("class") || "").includes("_head") && !hidden(path) && (path.getAttribute("d") || "").trim())
    .sort((left, right) => (right.getAttribute("d") || "").length - (left.getAttribute("d") || "").length);
  // Some official snapshots have an unclassed visible outline plus classed
  // hidden templates; fall back to the first visible non-empty path.
  const fallbackBody = paths.find((path) => !hidden(path) && (path.getAttribute("d") || "").trim());
  const bodyPath = bodyCandidates[0] ?? fallbackBody;
  const bodyGroup = bodyPath?.closest("g");
  const eyes = paths
    .filter((path) => (path.getAttribute("class") || "").includes("_eye") && !hidden(path))
    .slice(0, 2)
    .map((path) => ({
      d: path.getAttribute("d") || "",
      transform: path.getAttribute("transform") || path.parentElement?.getAttribute("transform") || "",
    }));
  return {
    body: bodyPath?.getAttribute("d") || "",
    bodyTransform: bodyGroup?.getAttribute("transform") || "",
    eyes,
  };
}

function innerSvg(raw: string): string {
  const match = raw.match(/<svg[^>]*>([\s\S]*)<\/svg>/i);
  return match?.[1] ?? raw;
}

function scopeSvg(raw: string, prefix: string): string {
  const inner = innerSvg(raw);
  const ids = Array.from(inner.matchAll(/\bid="([^"]+)"/g), (item) => item[1]);
  let scoped = inner;
  for (const id of ids) {
    const next = `${prefix}-${id}`;
    scoped = scoped
      .split(`id="${id}"`).join(`id="${next}"`)
      .split(`url(#${id})`).join(`url(#${next})`)
      .split(`href="#${id}"`).join(`href="#${next}"`);
  }
  return scoped;
}

function easeOut(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

export function GrokAvatar({
  name,
  state,
  size = "small",
  muted = false,
  title,
}: {
  name: string;
  state: BotState;
  size?: "small" | "large";
  muted?: boolean;
  title?: string;
}) {
  const prefix = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const file = fileForState(state);
  const profile = PROFILES[file] ?? PROFILES.idle;
  const svgRef = useRef<SVGSVGElement | null>(null);
  const bodyRef = useRef<SVGGElement | null>(null);
  const bodyPathRef = useRef<SVGPathElement | null>(null);
  const eyeGroupRefs = useRef<(SVGGElement | null)[]>([]);
  const eyePathRefs = useRef<(SVGPathElement | null)[]>([]);
  const currentPose = useRef<AvatarPose | null>(null);
  const animationRef = useRef<number>(0);
  const driftRef = useRef<number>(0);
  const [special, setSpecial] = useState("");
  const [specialVisible, setSpecialVisible] = useState(false);
  const light = file === "blocked" || file === "thinking";

  useEffect(() => {
    let cancelled = false;
    const path = `/avatars/svg/${file}.svg`;
    const apply = async () => {
      let raw = RAW_CACHE.get(path);
      if (!raw) {
        const loaded = await fetch(path).then((response) => response.text());
        raw = loaded;
        RAW_CACHE.set(path, loaded);
      }
      if (cancelled) return;

      if (file === "blocked" || file === "thinking") {
        cancelAnimationFrame(animationRef.current);
        cancelAnimationFrame(driftRef.current);
        setSpecial(scopeSvg(raw, prefix));
        setSpecialVisible(true);
        return;
      }

      setSpecial("");
      setSpecialVisible(false);
      const target = parsePose(raw);
      if (!target.body) return;
      const from = currentPose.current;
      const svg = svgRef.current;
      if (!svg || !bodyPathRef.current) {
        currentPose.current = target;
        return;
      }

      const bodyPath = bodyPathRef.current;
      const bodyGroup = bodyRef.current;
      const eyePaths = eyePathRefs.current;
      const applyTargetTransforms = () => {
        if (bodyGroup) bodyGroup.setAttribute("transform", target.bodyTransform);
        target.eyes.forEach((eye, index) => {
          eyePathRefs.current[index]?.setAttribute("transform", eye.transform);
        });
      };

      if (!from || from.body === target.body) {
        bodyPath.setAttribute("d", target.body);
        applyTargetTransforms();
        target.eyes.forEach((eye, index) => eyePaths[index]?.setAttribute("d", eye.d));
        currentPose.current = target;
        startDrift();
        return;
      }

      const bodyMorph = interpolate(from.body, target.body);
      const eyeMorphs = target.eyes.map((eye, index) => {
        const previous = from.eyes[index]?.d;
        return previous ? interpolate(previous, eye.d) : null;
      });
      applyTargetTransforms();
      const started = performance.now();
      const duration = profile.duration;

      const step = (now: number) => {
        const t = Math.min(1, (now - started) / duration);
        const eased = easeOut(t);
        bodyPath.setAttribute("d", bodyMorph(eased));
        target.eyes.forEach((eye, index) => {
          const morph = eyeMorphs[index];
          if (morph) eyePaths[index]?.setAttribute("d", morph(eased));
        });
        if (t < 1) {
          animationRef.current = requestAnimationFrame(step);
        } else {
          currentPose.current = target;
          startDrift();
        }
      };
      cancelAnimationFrame(animationRef.current);
      cancelAnimationFrame(driftRef.current);
      animationRef.current = requestAnimationFrame(step);
    };

    void apply().catch(() => {
      if (!cancelled) setSpecial("");
    });
    return () => {
      cancelled = true;
      cancelAnimationFrame(animationRef.current);
      cancelAnimationFrame(driftRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file, prefix, profile.duration]);

  function startDrift() {
    cancelAnimationFrame(driftRef.current);
    if (!profile.amp) return;
    const groups = eyeGroupRefs.current.filter(Boolean) as SVGGElement[];
    if (!groups.length) return;
    const started = performance.now();
    const step = (now: number) => {
      const t = (now - started) / 1000;
      const x = Math.sin(t * profile.speed) * profile.amp;
      const y = Math.cos(t * profile.speed * 0.77) * profile.amp * 0.45;
      const blinkPhase = (t + 0.4) % profile.blink;
      const blink = blinkPhase > profile.blink - 0.16 ? 0.08 : 1;
      groups.forEach((group, index) => {
        const offset = index === 0 ? -1.4 : 2.1;
        group.style.transform = `translate(${(x + offset).toFixed(2)}px, ${(y + (index === 0 ? 0.6 : -0.8)).toFixed(2)}px) scaleY(${blink})`;
      });
      driftRef.current = requestAnimationFrame(step);
    };
    driftRef.current = requestAnimationFrame(step);
  }

  const style = {
    "--avatar-hue": "205",
    "--fg": "#0a0a0c",
    "--bg": light ? "#f7f3ec" : "#0a0a0c",
  } as CSSProperties;

  return (
    <span
      className={`${styles.botAvatar} ${styles[`botAvatar_${size}`]} ${styles[`botAvatar_${state}`]} ${light ? styles.grokAvatarLight : ""} ${muted ? styles.botAvatarMuted : ""}`}
      style={style}
      title={title}
      aria-hidden="true"
    >
      <svg
        ref={svgRef}
        className={styles.grokAvatarSvg}
        viewBox="-15 -15 259 259"
        xmlns="http://www.w3.org/2000/svg"
        style={{ opacity: special ? 0 : 1 }}
      >
        <g ref={bodyRef}>
          <path ref={bodyPathRef} className={styles.grokHead} />
        </g>
        {[0, 1].map((index) => (
          <g
            key={index}
            className={styles.grokEyeGroup}
            ref={(node) => { eyeGroupRefs.current[index] = node; }}
          >
            <path
              ref={(node) => { eyePathRefs.current[index] = node; }}
              className={styles.grokEye}
            />
          </g>
        ))}
      </svg>
      {special ? (
        <svg
          className={`${styles.grokAvatarSvg} ${styles.grokAvatarSpecial}`}
          viewBox="-15 -15 259 259"
          xmlns="http://www.w3.org/2000/svg"
          style={{ opacity: specialVisible ? 1 : 0 }}
          dangerouslySetInnerHTML={{ __html: special }}
        />
      ) : null}
      <span className={styles.srOnly}>{name}</span>
    </span>
  );
}
