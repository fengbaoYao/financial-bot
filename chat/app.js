
(() => {
  const apiBase = String(window.FINANCIAL_BOT_API_BASE || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const messages = $("messages");
  const apiChip = $("apiChip");
  $("apiBase").textContent = apiBase || "(missing)";

  function addMsg(role, text) {
    const el = document.createElement("div");
    el.className = `msg ${role}`;
    el.textContent = text;
    messages.appendChild(el);
    messages.scrollTop = messages.scrollHeight;
    return el;
  }

  async function api(path, init) {
    const res = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init && init.headers ? init.headers : {}),
      },
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = text; }
    if (!res.ok) {
      const detail = data && data.detail ? data.detail : text || res.status;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function refreshStatus() {
    try {
      const health = await api("/healthz");
      $("health").textContent = JSON.stringify(health);
      const caps = await api("/api/agent/capabilities");
      $("caps").textContent = `quick=${caps.quick_available} deep=${caps.deep_available}` +
        (caps.deep_unavailable_reason ? ` (${caps.deep_unavailable_reason})` : "");
      apiChip.textContent = "API online";
      apiChip.className = "chip ok";
    } catch (err) {
      $("health").textContent = String(err.message || err);
      $("caps").textContent = "unavailable";
      apiChip.textContent = "API offline";
      apiChip.className = "chip bad";
    }
  }

  $("newChat").addEventListener("click", () => {
    messages.innerHTML = "";
    addMsg("sys", "新对话已清空。后端会话若需隔离，请在完整产品版中使用 /bot。");
  });

  $("composer").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("input");
    const q = input.value.trim();
    if (!q) return;
    input.value = "";
    addMsg("user", q);
    const thinking = addMsg("bot", "研究中…");
    const btn = e.target.querySelector('button[type="submit"]');
    btn.disabled = true;
    try {
      // Prefer financial-bot task API if present; fall back to classic agent query.
      let answered = false;
      try {
        const created = await api("/api/tasks", {
          method: "POST",
          body: JSON.stringify({
            message: q,
            research_mode: "quick",
            model_approved: true,
          }),
        });
        const taskId = created && created.task && created.task.id;
        if (taskId) {
          // poll briefly for a result message
          let finalText = "";
          for (let i = 0; i < 40; i++) {
            await new Promise((r) => setTimeout(r, 1500));
            const task = await api(`/api/tasks/${encodeURIComponent(taskId)}`);
            const status = task && task.task && task.task.status;
            const msgs = await api(`/api/sessions/${encodeURIComponent(task.task.session_id)}/messages?limit=20`).catch(() => null);
            if (msgs && Array.isArray(msgs.messages)) {
              const botMsgs = msgs.messages.filter((m) => m.role === "assistant" || m.role === "bot");
              if (botMsgs.length) finalText = botMsgs[botMsgs.length - 1].content || JSON.stringify(botMsgs[botMsgs.length - 1]);
            }
            if (status === "completed" || status === "failed" || status === "cancelled") {
              answered = true;
              thinking.textContent = finalText || `任务结束：${status}`;
              break;
            }
            thinking.textContent = `研究中… (${status || "running"} #${i + 1})`;
          }
          if (!answered) {
            thinking.textContent = finalText || "任务仍在运行；请稍后在完整工作台查看。";
            answered = true;
          }
        }
      } catch (taskErr) {
        // fall through to classic endpoint
        console.warn("task api failed", taskErr);
      }
      if (!answered) {
        const data = await api("/api/agent/query", {
          method: "POST",
          body: JSON.stringify({ query: q, mode: "quick", model_approved: true }),
        });
        const text = data.answer || data.message || data.summary || JSON.stringify(data, null, 2);
        thinking.textContent = text;
      }
    } catch (err) {
      thinking.textContent = `请求失败：${err.message || err}`;
    } finally {
      btn.disabled = false;
    }
  });

  addMsg("sys", "静态前端已连接硅谷 API。Deep 研究在公测中暂停；Quick 可用。");
  refreshStatus();
})();
