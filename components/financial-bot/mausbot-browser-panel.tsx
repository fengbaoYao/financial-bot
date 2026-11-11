"use client";

import { FormEvent, KeyboardEvent, MouseEvent, useCallback, useEffect, useRef, useState } from "react";
import styles from "./mausbot-browser-panel.module.css";

type BrowserTab = { id: string; title: string; url: string; active: boolean };
type BrowserState = {
  ok: boolean;
  running: boolean;
  tabs: BrowserTab[];
  activeTabId: string | null;
  screenshot: string | null;
  viewport?: { width: number; height: number };
  error?: string | null;
};

const EMPTY: BrowserState = { ok: true, running: false, tabs: [], activeTabId: null, screenshot: null };

export default function MausbotBrowserPanel({ zh }: { zh: boolean }) {
  const [state, setState] = useState<BrowserState>(EMPTY);
  const [address, setAddress] = useState("");
  const [pageText, setPageText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const pending = useRef(false);
  const addressEditing = useRef(false);
  const lastPageLocation = useRef({ tabId: "", url: "" });

  const refresh = useCallback(async () => {
    if (pending.current) return;
    pending.current = true;
    try {
      const response = await fetch("/api/mausbot/browser", { cache: "no-store" });
      const data = await response.json() as BrowserState & { detail?: string };
      if (!response.ok) throw new Error(data.detail || (zh ? "浏览器状态读取失败" : "Could not read browser state"));
      setState(data);
      const activeTab = data.tabs?.find((tab) => tab.active);
      const nextLocation = { tabId: activeTab?.id || "", url: activeTab?.url || "" };
      const locationChanged = nextLocation.tabId !== lastPageLocation.current.tabId || nextLocation.url !== lastPageLocation.current.url;
      if (locationChanged && !addressEditing.current) {
        setAddress(nextLocation.url);
        lastPageLocation.current = nextLocation;
      }
      setError(data.error || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : (zh ? "浏览器连接失败" : "Browser connection failed"));
    } finally {
      pending.current = false;
    }
  }, [zh]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => { void refresh(); }, 1600);
    return () => window.clearInterval(timer);
  }, [refresh, tick]);

  const act = useCallback(async (action: string, extra: Record<string, unknown> = {}) => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/mausbot/browser", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...extra }),
      });
      const data = await response.json() as BrowserState & { detail?: string };
      if (!response.ok) throw new Error(data.detail || (zh ? "操作失败" : "Action failed"));
      setState(data);
      setError(data.error || "");
      const activeTab = data.tabs?.find((tab) => tab.active);
      if (activeTab) {
        const location = { tabId: activeTab.id, url: activeTab.url || "" };
        setAddress(location.url);
        lastPageLocation.current = location;
      } else if (action === "stop") {
        setState(EMPTY);
        setAddress("");
        lastPageLocation.current = { tabId: "", url: "" };
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : (zh ? "浏览器操作失败" : "Browser action failed"));
    } finally {
      setBusy(false);
      setTick((value) => value + 1);
    }
  }, [busy, zh]);

  const navigateAddress = (nextAddress: string) => {
    addressEditing.current = false;
    setAddress(nextAddress);
    void act("navigate", { url: nextAddress });
  };

  const submitAddress = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submitted = new FormData(event.currentTarget).get("address");
    navigateAddress(typeof submitted === "string" ? submitted : address);
  };

  const submitPageText = (event: FormEvent) => {
    event.preventDefault();
    if (!pageText) return;
    void act("type", { text: pageText });
    setPageText("");
  };

  const clickScreen = (event: MouseEvent<HTMLImageElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const width = state.viewport?.width || 1280;
    const height = state.viewport?.height || 800;
    void act("click", {
      x: Math.round(((event.clientX - rect.left) / rect.width) * width),
      y: Math.round(((event.clientY - rect.top) / rect.height) * height),
    });
    event.currentTarget.parentElement?.focus();
  };

  const keyScreen = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === "Escape" || event.key === "Tab" || event.key === "Backspace" ||
        event.key.startsWith("Arrow") || event.key === "Delete" || event.key === "Home" || event.key === "End" ||
        event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      void act("key", { key: event.key });
    }
  };

  return (
    <section className={styles.panel} aria-label={zh ? "网页浏览器" : "Web browser"}>
      <header className={styles.header}>
        <strong>{zh ? "网页浏览器" : "Web browser"}</strong>
        <span className={state.running ? styles.online : styles.offline}>
          {state.running ? (zh ? "运行中" : "Running") : (zh ? "未启动" : "Stopped")}
        </span>
      </header>

      {state.running ? (
        <>
          <form className={styles.address} onSubmit={submitAddress}>
            <button type="button" aria-label={zh ? "后退" : "Back"} title={zh ? "后退" : "Back"} disabled={busy} onClick={() => void act("back")}>←</button>
            <button type="button" aria-label={zh ? "前进" : "Forward"} title={zh ? "前进" : "Forward"} disabled={busy} onClick={() => void act("forward")}>→</button>
            <button type="button" aria-label={zh ? "刷新" : "Refresh"} title={zh ? "刷新" : "Refresh"} disabled={busy} onClick={() => void act("reload")}>↻</button>
            <input name="address" value={address} onFocus={() => { addressEditing.current = true; }} onBlur={() => { addressEditing.current = false; }} onChange={(event) => setAddress(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); navigateAddress(event.currentTarget.value); } }} placeholder="https://example.com" aria-label={zh ? "网址" : "Address"} />
            <button type="button" disabled={busy} onClick={() => navigateAddress(address)}>{zh ? "打开" : "Go"}</button>
            <button type="button" disabled={busy || state.tabs.length >= 3} onClick={() => void act("newTab")} aria-label={zh ? "新建标签页" : "New tab"}>＋</button>
          </form>

          <div className={styles.tabs} role="tablist" aria-label={zh ? "标签页" : "Tabs"}>
            {state.tabs.map((tab) => (
              <div className={styles.tabItem} key={tab.id} data-active={tab.active || undefined}>
                <button type="button" role="tab" aria-selected={tab.active} title={tab.url || "about:blank"} onClick={() => void act("select", { tabId: tab.id })}>
                  {tab.title || (tab.url && tab.url !== "about:blank" ? new URL(tab.url).hostname : (zh ? "新标签页" : "New tab"))}
                </button>
                <button type="button" aria-label={zh ? "关闭标签页" : "Close tab"} onClick={() => void act("close", { tabId: tab.id })}>×</button>
              </div>
            ))}
          </div>

          <div className={styles.screen} tabIndex={0} onKeyDown={keyScreen} aria-label={zh ? "浏览器画面；点击后可用键盘控制" : "Browser view; click and use your keyboard"}>
            {state.screenshot ? (
              <img src={`data:image/jpeg;base64,${state.screenshot}`} alt={zh ? "实时浏览器画面" : "Live browser view"} onClick={clickScreen} draggable={false} />
            ) : (
              <span>{busy ? (zh ? "正在加载…" : "Loading…") : (zh ? "等待页面画面…" : "Waiting for the page…")}</span>
            )}
          </div>

          <form className={styles.pageInput} onSubmit={submitPageText}>
            <input value={pageText} onChange={(event) => setPageText(event.target.value)} placeholder={zh ? "输入文字到当前网页" : "Type into the current page"} aria-label={zh ? "网页输入" : "Type into page"} />
            <button type="submit" disabled={busy || !pageText}>{zh ? "输入" : "Type"}</button>
            <button type="button" disabled={busy} onClick={() => void act("key", { key: "Enter" })}>↵</button>
          </form>
        </>
      ) : (
        <div className={styles.empty}>
          <p>{zh ? "启动隔离的浏览器会话。最多 3 个标签页，关闭或空闲 30 分钟后会释放。" : "Start an isolated browser session. Up to 3 tabs; it closes when stopped or idle for 30 minutes."}</p>
          <button type="button" disabled={busy} onClick={() => void act("start")}>{busy ? (zh ? "正在启动…" : "Starting…") : (zh ? "启动浏览器" : "Start browser")}</button>
        </div>
      )}

      <div className={styles.footer}>
        <span>{zh ? "只允许访问公开 HTTP(S) 网站" : "Public HTTP(S) sites only"}</span>
        {state.running ? <button type="button" disabled={busy} onClick={() => void act("stop")}>{zh ? "关闭浏览器" : "Stop browser"}</button> : null}
      </div>
      {error ? <p className={styles.error} role="status">{error}</p> : null}
    </section>
  );
}
