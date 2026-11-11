"use client";

import { useEffect, useRef, useState } from "react";
import styles from "./financial-bot-styles";
import { GrokAvatar } from "./grok-avatar";
import { greetingFor, optionsFor, successPathsFor, type OnboardOption, type ResearchPersona } from "@/lib/financial-bot/onboarding";
import type { BotProfile, OnboardPlan } from "@/lib/financial-bot/client";

export type { ResearchPersona } from "@/lib/financial-bot/onboarding";

const LETTERS = ["A", "B", "C", "D", "E", "F"];

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function AskCard({
  index,
  title,
  note,
  options,
  answer,
  answerLetter,
  busy,
}: {
  index: number;
  title: string;
  note?: string;
  options: Array<{ label: string; onPick: () => void }>;
  answer: string | null;
  answerLetter?: string;
  busy?: boolean;
}) {
  return (
    <div className={styles.askCard} data-open={answer ? "false" : "true"}>
      <p className={styles.askTitle}>{title}</p>
      {note ? <p className={styles.askNote}>{note}</p> : null}
      <div className={styles.askOptions} data-collapsed={answer ? "true" : "false"} aria-hidden={answer ? "true" : undefined}>
        {options.map((option, position) => (
          <button
            key={option.label}
            type="button"
            className={styles.askOption}
            onClick={option.onPick}
            disabled={busy}
            tabIndex={answer ? -1 : 0}
          >
            <span className={styles.askLetter}>{LETTERS[position]}</span>
            <span className={styles.askLabel}>{option.label}</span>
          </button>
        ))}
      </div>
      {answer ? (
        <div className={styles.askAnswer}>
          <span className={styles.askLetter} data-answered="true">{answerLetter ?? LETTERS[0]}</span>
          <span className={styles.askLabel}>{answer}</span>
        </div>
      ) : null}
      <span className={styles.askIndex} aria-hidden="true">{String(index).padStart(2, "0")}</span>
    </div>
  );
}

export type OnboardingRecord = {
  botName: string;
  greeting: string;
  steps: Array<{ question: string; answer: string; letter: string }>;
};

/** Read-only replay of the first conversation, shown once the chat has work. */
export function OnboardingReplay({
  record,
  en,
}: {
  record: OnboardingRecord | null;
  en: boolean;
}) {
  if (!record || (!record.greeting && !record.steps.length)) return null;
  return (
    <section className={styles.welcomeThread} aria-label={en ? "Conversation start" : "对话开头"}>
      <div className={styles.welcomeRow}>
        <GrokAvatar name={record.botName} state="idle" size="small" />
        <div className={styles.welcomeBubble}>
          <strong>{record.botName}</strong>
          <p>{record.greeting}</p>
        </div>
      </div>
      {record.steps.map((step, index) => (
        <div key={`${index}-${step.answer}`} className={styles.welcomeRow}>
          <span className={styles.welcomeSpacer} aria-hidden="true" />
          <div className={styles.askCard} data-open="false">
            <p className={styles.askTitle}>{step.question}</p>
            <div className={styles.askAnswer}>
              <span className={styles.askLetter} data-answered="true">{step.letter}</span>
              <span className={styles.askLabel}>{step.answer}</span>
            </div>
            <span className={styles.askIndex} aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
          </div>
        </div>
      ))}
    </section>
  );
}

type Stage = "status" | "greeting" | "ask1" | "ask2";

export function WelcomeThread({
  en,
  bot,
  watchingEnabled = true,
  plan,
  persona,
  onPersona,
  onStart,
  onCustom,
  onSkip,
  onRecord,
}: {
  en: boolean;
  bot: BotProfile | null;
  /** The watch list is off: drop the "watch this question" template and its copy. */
  watchingEnabled?: boolean;
  plan?: OnboardPlan | null;
  persona: ResearchPersona | null;
  onPersona: (persona: ResearchPersona | null) => void;
  onStart: (prompt: string, meta?: { kind?: "ask" | "track"; pathId?: string }) => void;
  onCustom: () => void;
  onSkip: () => void;
  onRecord?: (record: OnboardingRecord) => void;
}) {
  // A model-written opening replaces the generic list when it arrives; the
  // capability-based options stay as the fallback so the page never waits.
  const [lockedPlan] = useState<OnboardPlan | null>(plan ?? null);
  const tailored = plan && plan.options.length >= 2 ? plan : lockedPlan && lockedPlan.options.length >= 2 ? lockedPlan : null;
  const options: OnboardOption[] = tailored
    ? tailored.options.map((option) => ({
        persona: "pro",
        label: option.label,
        ack: option.ack || (en ? "Okay — let's start there." : "好，就从这里开始。"),
        templates: option.templates.length
          ? option.templates.map((template) => ({ label: template.label, prompt: template.prompt }))
          : [{ label: en ? "I'll type it" : "我自己输入", custom: true }],
      }))
    : optionsFor(bot, en);
  const [stage, setStage] = useState<Stage>("status");
  const [answer, setAnswer] = useState<OnboardOption | null>(
    () => (tailored ? null : options.find((option) => option.persona === persona) ?? null),
  );
  const [answerLetter, setAnswerLetter] = useState<string | null>(null);
  const [modeAnswer, setModeAnswer] = useState<string | null>(null);
  const [modeLetter, setModeLetter] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const timers = useRef<number[]>([]);

  const later = (fn: () => void, ms: number) => {
    const timer = window.setTimeout(fn, prefersReducedMotion() ? Math.min(ms, 60) : ms);
    timers.current.push(timer);
    return timer;
  };

  useEffect(() => () => {
    timers.current.forEach((timer) => window.clearTimeout(timer));
  }, []);

  // A short "working" beat before the greeting, so the first message does not
  // pop out of nowhere when an empty chat mounts.
  useEffect(() => {
    if (stage !== "status") return;
    later(() => setStage("greeting"), 620);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  useEffect(() => {
    if (stage !== "greeting") return;
    // A returning visitor already picked a mode for this Bot.
    later(() => setStage(answer ? "ask2" : "ask1"), 420);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const botName = bot?.name?.trim() || (en ? "Research Bot" : "研究助手");

  const questionOne = en ? "What should we look at first?" : "今天想先看哪一块？";
  const questionTwo = en ? "Which template should we start with?" : "想从哪个模板开始？";

  function pickMode(option: OnboardOption, letter: string) {
    setAnswer(option);
    setAnswerLetter(letter);
    if (!tailored) onPersona(option.persona);
    onRecord?.({
      botName,
      greeting: greetingFor(bot, en),
      steps: [{ question: questionOne, answer: option.label, letter }],
    });
    later(() => setStage("ask2"), 260);
  }

  return (
    <section className={styles.welcomeThread} aria-live="polite" aria-label={en ? "Welcome conversation" : "欢迎对话"}>
      {stage === "status" ? (
        <div className={styles.welcomeStatus} role="status">
          <GrokAvatar name={botName} state="running" size="small" />
          <span>{en ? "Saying hi" : "正在跟你打招呼"}</span>
          <i className={styles.welcomeStatusDot} aria-hidden="true" />
        </div>
      ) : null}

      {stage !== "status" ? (
        <div className={styles.welcomeRow}>
          <GrokAvatar name={botName} state="idle" size="small" />
          <div className={`${styles.welcomeBubble} ${styles.enter}`}>
            <strong>{botName}</strong>
            <p>{tailored?.greeting || greetingFor(bot, en)}</p>
          </div>
        </div>
      ) : null}

      {stage === "ask1" && !answer ? (
        <div className={`${styles.welcomeRow} ${styles.enter}`}>
          <span className={styles.welcomeSpacer} aria-hidden="true" />
          <div className={`${styles.successPaths} ${styles.delegatePaths}`} role="group" aria-label={en ? "Delegate a task" : "委派一件事"}>
            <div className={styles.successPathsHead}>
              <strong>{en ? "Hand me one of these" : "先交给我一件"}</strong>
              <span>{watchingEnabled
                ? (en ? "Two quick asks, plus one lasting watch." : "两条随手问，外加一条长期盯梢。")
                : (en ? "Two quick asks." : "两条随手问。")}</span>
            </div>
            <div className={styles.successPathGrid}>
              {successPathsFor(en).filter((path) => watchingEnabled || path.id !== "track").map((path) => (
                <button
                  key={path.id}
                  type="button"
                  className={styles.successPathCard}
                  disabled={starting}
                  data-path={path.id}
                  onClick={() => {
                    setStarting(true);
                    onRecord?.({
                      botName,
                      greeting: greetingFor(bot, en),
                      steps: [{ question: en ? "Delegated" : "委派任务", answer: path.label, letter: "★" }],
                    });
                    const kind = path.kind || (path.id === "track" ? "track" : "ask");
                    later(() => onStart(path.prompt, { kind, pathId: path.id }), 220);
                  }}
                >
                  <strong>{path.label}</strong>
                  <span>{path.blurb}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {stage === "ask1" && !answer ? (
        <div className={`${styles.welcomeRow} ${styles.enter}`}>
          <span className={styles.welcomeSpacer} aria-hidden="true" />
          <details className={styles.welcomeMoreTemplates}>
            <summary>{en ? "More templates by type" : "更多：按类型挑模板"}</summary>
            <AskCard
              index={1}
              title={en ? "Pick a focus" : "按类型挑"}
              options={[
                ...options.map((option, position) => ({
                  label: option.label,
                  onPick: () => pickMode(option, LETTERS[position]),
                })),
                { label: en ? "I'll just type" : "我自己打字", onPick: onSkip },
              ]}
              answer={null}
              answerLetter={undefined}
              busy={false}
            />
          </details>
        </div>
      ) : null}

      {stage === "ask2" ? (
        <div className={`${styles.welcomeRow} ${styles.enter}`}>
          <span className={styles.welcomeSpacer} aria-hidden="true" />
          <AskCard
            index={1}
            title={questionOne}
            options={[
              ...options.map((option, position) => ({
                label: option.label,
                onPick: () => pickMode(option, LETTERS[position]),
              })),
              { label: en ? "I'll just type" : "我自己打字", onPick: onSkip },
            ]}
            answer={answer?.label ?? null}
            answerLetter={answerLetter ?? undefined}
            busy={Boolean(answer)}
          />
        </div>
      ) : null}

      {stage === "ask2" && answer ? (
        <div className={`${styles.welcomeRow} ${styles.enter}`}>
          <span className={styles.welcomeSpacer} aria-hidden="true" />
          <div className={styles.welcomeBubble}>
            <p>{answer.ack}</p>
          </div>
        </div>
      ) : null}

      {stage === "ask2" && answer ? (
        <div className={`${styles.welcomeRow} ${styles.enter}`}>
          <span className={styles.welcomeSpacer} aria-hidden="true" />
          <AskCard
            index={2}
            title={questionTwo}
            note={
              starting
                ? (en ? "Starting the research run…" : "正在开始研究…")
                : (en ? "You can also type anything." : "也可以直接打字提问。")
            }
            options={answer.templates.map((template, position) => ({
              label: template.label,
              onPick: () => {
                setModeAnswer(template.label);
                setModeLetter(LETTERS[position]);
                if (answer) {
                  onRecord?.({
                    botName,
                    greeting: greetingFor(bot, en),
                    steps: [
                      { question: questionOne, answer: answer.label, letter: answerLetter ?? LETTERS[0] },
                      { question: questionTwo, answer: template.label, letter: LETTERS[position] },
                    ],
                  });
                }
                if (template.custom) {
                  later(onCustom, 240);
                  return;
                }
                if (template.prompt) {
                  const prompt = template.prompt;
                  setStarting(true);
                  later(() => onStart(prompt), 300);
                }
              },
            }))}
            answer={modeAnswer}
            answerLetter={modeLetter ?? undefined}
            busy={starting || Boolean(modeAnswer)}
          />
        </div>
      ) : null}
    </section>
  );
}
