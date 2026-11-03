(function () {
  const live = (window.FINANCIAL_BOT_LIVE_APP || "").replace(/\/$/, "");
  const note = document.getElementById("liveNote");
  function target(kind) {
    if (!live) return null;
    if (kind === "use") return live + "/bot?mode=use&lang=zh";
    if (kind === "work") return live + "/work?lang=zh";
    if (kind === "home") return live + "/landing?lang=zh";
    return live;
  }
  document.querySelectorAll("[data-nav]").forEach((el) => {
    const kind = el.getAttribute("data-nav");
    const href = target(kind);
    if (href) el.setAttribute("href", href);
  });
  if (note) {
    if (live) {
      note.hidden = false;
      note.innerHTML = "完整 Next 工作台已连接：<code>" + live + "</code>。「开始使用 / 经典研究台」将进入与本地一致的路由。";
    } else {
      note.hidden = false;
      note.textContent = "完整 Next /bot 与 /work 暂未挂到公网时，按钮进入 Pages 上的层级说明页；API 仍可通过旧版对话壳调用。";
    }
  }
  // decorative candles
  const g = document.getElementById("candleGroup");
  if (g) {
    let x = 20, d = "";
    for (let i = 0; i < 48; i++) {
      const h = 20 + ((i * 37) % 70);
      const y = 160 - h;
      const up = i % 3 !== 0;
      const color = up ? "#0f8a7b" : "#be5b36";
      d += `<rect x="${x}" y="${y}" width="8" height="${h}" fill="${color}" opacity=".55"/>`;
      d += `<line x1="${x + 4}" y1="${y - 8}" x2="${x + 4}" y2="${y + h + 8}" stroke="${color}" stroke-width="1" opacity=".45"/>`;
      x += 24;
    }
    g.innerHTML = d;
  }
})();
