"use client";

import { useId } from "react";

import type { BotState } from "@/lib/financial-bot/types";
import { botCopy } from "@/lib/financial-bot/state";
import styles from "./financial-bot-styles";

export function BotCompanion({
  state,
  locale,
  compact = false,
}: {
  state: BotState;
  locale: "zh" | "en";
  compact?: boolean;
}) {
  const copy = botCopy(state, locale);
  const rawId = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const faceId = `bot-face-${rawId}`;
  const bodyId = `bot-body-${rawId}`;
  return (
    <section
      className={`${styles.bot} ${styles[`bot_${state}`] ?? ""} ${compact ? styles.botCompact : ""}`}
      data-state={state}
      role="status"
      aria-live="polite"
      aria-label={`${copy.title}：${copy.detail}`}
    >
      <div className={styles.botStage} aria-hidden="true">
        <svg viewBox="0 0 120 120" className={styles.botSvg}>
          <defs>
            <radialGradient id={faceId} cx="50%" cy="38%" r="70%">
              <stop offset="0%" stopColor="#fdfaf4" />
              <stop offset="100%" stopColor="#e7dfd2" />
            </radialGradient>
            <linearGradient id={bodyId} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#2a2723" />
              <stop offset="100%" stopColor="#121110" />
            </linearGradient>
          </defs>
          <g className={styles.orbit}>
            <circle cx="60" cy="60" r="48" className={styles.orbitRing} />
            <circle cx="60" cy="12" r="3.2" className={styles.orbitDotA} />
            <circle cx="103" cy="78" r="2.6" className={styles.orbitDotB} />
            <circle cx="20" cy="84" r="2.2" className={styles.orbitDotC} />
          </g>
          <path d="M60 20 L60 32" className={styles.antenna} />
          <circle cx="60" cy="17" r="5" className={styles.antennaTip} />
          <rect x="18" y="30" width="84" height="72" rx="26" fill={`url(#${bodyId})`} />
          <rect x="26" y="38" width="68" height="54" rx="20" fill={`url(#${faceId})`} />
          <g className={styles.eyes}>
            <rect x="43" y="58" width="9" height="12" rx="4.5" fill="#161513" />
            <rect x="68" y="58" width="9" height="12" rx="4.5" fill="#161513" />
            <circle cx="45.6" cy="60.6" r="1.7" fill="#fdfaf4" />
            <circle cx="70.6" cy="60.6" r="1.7" fill="#fdfaf4" />
          </g>
          <path d="M52 78 Q60 84 68 78" className={styles.mouth} />
          <g className={styles.stateMark}>
            <path className={styles.check} d="M80 84 L86 90 L98 74" />
            <path className={styles.crack} d="M90 38 L84 56 L92 64 L86 80" />
            <path className={styles.slash} d="M38 84 L82 40" />
            <text className={styles.question} x="83" y="43">?</text>
          </g>
        </svg>
      </div>
      <div className={styles.botText}>
        <span className={styles.botEyebrow}>{locale === "en" ? "Financial Bot companion" : "研究助手 · 形象"}</span>
        <strong>{copy.title}</strong>
        <span>{copy.detail}</span>
      </div>
    </section>
  );
}
