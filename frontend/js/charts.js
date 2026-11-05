(() => {
  const instances = {};
  const colors = { temperature: "#55c7ee", tds: "#43d3a7", turbidity: "#f2c65f", legacy_cloudiness: "#8294a8" };
  function destroy(id) {
    if (instances[id]) { instances[id].destroy(); delete instances[id]; }
  }
  function draw(id, canvasId, points, sensor, unit, emptyMessage = "No historical data is available for the selected range.", additionalSeries = [], historicalLabel = null) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) return;
    destroy(id);
    const parent = canvas.parentElement;
    const emptyState = parent.querySelector(".empty-state");
    if (emptyState) emptyState.remove();
    canvas.style.display = "";
    const labels = { temperature: `Temperature (${unit})`, tds: "TDS (ppm)", turbidity: "Turbidity (NTU)", legacy_cloudiness: "Legacy relative sensor index (0-100; not NTU)" };
    const safeSeries = [{ label: historicalLabel || labels[sensor] || unit, points, color: colors[sensor], dashed: false }, ...additionalSeries].map(series => ({
      ...series,
      points: Array.isArray(series.points) ? series.points.filter(p => p && p.value !== null && p.value !== undefined &&
        Number.isFinite(Number(p.value)) && typeof p.timestamp === "string" && Number.isFinite(Date.parse(p.timestamp))) : []
    }));
    const timestamps = [...new Set(safeSeries.flatMap(series => series.points.map(point => point.timestamp)))]
      .sort((a, b) => Date.parse(a) - Date.parse(b));
    if (!timestamps.length) {
      canvas.style.display = "none";
      const message = document.createElement("div");
      message.className = "empty-state";
      const detail = document.createElement("p");
      detail.textContent = emptyMessage;
      message.append(detail);
      parent.append(message);
      return;
    }
    instances[id] = new Chart(canvas, {
      type: "line",
      data: {
        labels: timestamps.map(timestamp => new Date(timestamp).toLocaleString([], {month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})),
        datasets: safeSeries.filter(series => series.points.length).map(series => {
          const byTimestamp = new Map(series.points.map(point => [point.timestamp, Number(point.value)]));
          return { label: series.label, data: timestamps.map(timestamp => byTimestamp.get(timestamp) ?? null), borderColor: series.color,
            backgroundColor: `${series.color}20`, fill: !series.dashed, tension: .32, pointRadius: 2.5,
            pointHoverRadius: 5, borderWidth: 2, borderDash: series.dashed ? [6, 4] : [] };
        })
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        plugins: { legend: { display: safeSeries.filter(series => series.points.length).length > 1, labels: { color: "#a8bdcf" } },
          tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}` } } },
        scales: {
          x: { ticks: { color: "#8fa8bd", maxTicksLimit: 7 }, grid: { color: "#263b4e" } },
          y: { title: { display: true, text: unit, color: "#a8bdcf" }, ticks: { color: "#8fa8bd" }, grid: { color: "#263b4e" } }
        }
      }
    });
  }
  window.LUWCharts = { draw, destroyAll: () => Object.keys(instances).forEach(destroy) };
})();
