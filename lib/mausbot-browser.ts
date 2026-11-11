type BrowserAction = {
  action?: string;
  tabId?: string;
  url?: string;
  x?: number;
  y?: number;
  text?: string;
  key?: string;
  deltaY?: number;
};

const SERVICE = process.env.MAUSBOT_BROWSER_SERVICE_URL || "http://127.0.0.1:18850";

async function requestService(path: string, init?: RequestInit) {
  let response: Response;
  try {
    response = await fetch(`${SERVICE}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(path === "/state" ? 8000 : 25_000),
    });
  } catch {
    throw new Error("浏览器服务暂不可用");
  }
  const payload = await response.json().catch(() => ({ detail: "浏览器服务返回了无效响应" }));
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload && typeof payload.detail === "string"
      ? payload.detail : "浏览器操作失败";
    throw new Error(detail);
  }
  return payload;
}

export function getBrowserState() {
  return requestService("/state");
}

export function runBrowserAction(input: BrowserAction) {
  return requestService("/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
}
