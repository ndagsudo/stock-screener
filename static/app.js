// 銘柄詳細ページの簡易チャート描画（外部ライブラリなし・Canvas直描画）。
// データが空/欠損の場合は「データなし」を表示して落ちないようにする。
(function () {
  function drawLineChart(canvas, series, opts) {
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = (canvas.width = canvas.clientWidth || 600);
    const h = canvas.height = opts.height || 220;
    ctx.clearRect(0, 0, w, h);

    if (!series || series.length === 0) {
      ctx.fillStyle = "#6b7280";
      ctx.font = "14px sans-serif";
      ctx.fillText("データなし", 10, h / 2);
      return;
    }

    const values = series.map((p) => p.value).filter((v) => v !== null && v !== undefined);
    if (values.length === 0) {
      ctx.fillStyle = "#6b7280";
      ctx.fillText("データなし", 10, h / 2);
      return;
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const pad = 30;
    const range = max - min || 1;

    ctx.strokeStyle = opts.color || "#1f4e79";
    ctx.lineWidth = 2;
    ctx.beginPath();
    series.forEach((p, i) => {
      if (p.value === null || p.value === undefined) return;
      const x = pad + (i / Math.max(series.length - 1, 1)) * (w - pad * 2);
      const y = h - pad - ((p.value - min) / range) * (h - pad * 2);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();

    ctx.fillStyle = "#1f2733";
    ctx.font = "12px sans-serif";
    ctx.fillText(opts.title || "", 4, 14);
    ctx.fillStyle = "#6b7280";
    ctx.fillText(String(Math.round(max)), 4, pad);
    ctx.fillText(String(Math.round(min)), 4, h - pad + 12);
  }

  function initPriceChart() {
    const canvas = document.getElementById("priceChart");
    if (!canvas) return;
    let prices = [];
    try {
      prices = JSON.parse(canvas.dataset.prices || "[]");
    } catch (e) {
      prices = [];
    }
    const series = prices.map((p) => ({ value: p.adj_close ?? p.close }));
    drawLineChart(canvas, series, { title: "株価推移", color: "#1f4e79" });
  }

  function initFinancialsChart() {
    const canvas = document.getElementById("financialsChart");
    if (!canvas) return;
    let fins = [];
    try {
      fins = JSON.parse(canvas.dataset.financials || "[]");
    } catch (e) {
      fins = [];
    }
    const series = fins.map((f) => ({ value: f.operating_profit }));
    drawLineChart(canvas, series, { title: "営業利益推移", color: "#c0392b" });
  }

  function initCopyButtons() {
    document.querySelectorAll(".copy-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const targetId = btn.getAttribute("data-copy-target");
        const target = document.getElementById(targetId);
        if (!target) return;
        const text = target.textContent || "";
        const done = function () {
          const original = btn.textContent;
          btn.textContent = "コピーしました";
          setTimeout(function () {
            btn.textContent = original;
          }, 1500);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () {
            fallbackCopy(text, done);
          });
        } else {
          fallbackCopy(text, done);
        }
      });
    });
  }

  function fallbackCopy(text, done) {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {
      document.execCommand("copy");
      done();
    } catch (e) {
      // コピーに失敗しても手動選択できるようテキストは表示済みのため何もしない
    }
    document.body.removeChild(textarea);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initPriceChart();
    initFinancialsChart();
    initCopyButtons();
  });
})();
