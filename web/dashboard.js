let portfolioMode = "all";
let redrawPortfolio = null;
const modes = [
  ["all", "All parcels"],
  ["alerts", "Open violations"],
  ["risk", "High heat risk"],
  ["unread", "No readings"],
];
const palette = {
  Low: "#32734d",
  Medium: "#bf832c",
  High: "#b34e38",
  Unknown: "#78877c",
};

function newestObservation(parcel) {
  return parcel.observations.reduce(
    (latest, o) =>
      Date.parse(o.time) > Date.parse(latest?.time || "1970-01-01")
        ? o
        : latest,
    null,
  );
}
function localTime(time) {
  const date = new Date(time);
  return Number.isFinite(date.getTime())
    ? date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Unavailable";
}
function lastReading(parcel) {
  const latest = newestObservation(parcel);
  if (!latest) return "No readings yet";
  return (
    (Date.now() - Date.parse(latest.time) > 86400000 ? "Stale · " : "") +
    localTime(latest.time)
  );
}
function openEvents(parcel) {
  return parcel.events.filter((e) => !e.resolved);
}
function inMode(parcel, mode) {
  switch (mode) {
    case "alerts":
      return openEvents(parcel).length > 0;
    case "risk":
      return risk(parcel) === "High";
    case "unread":
      return !parcel.observations.length;
    default:
      return true;
  }
}
function eventEvidence(event, parcel) {
  const last = event.last_seen || event.time;
  const lease = parcel.leases.find((l) => l.time === event.rule.lease_ref);
  const reading = event.latest_value ?? event.value;
  return `<div class="item evidence" id="event-${esc(event.id)}" tabindex="-1">
    <small>${event.resolved ? "Resolved" : "Open violation"} · ${esc(localTime(last))}</small>
    <strong>${esc(event.rule.clause)}</strong>
    <p>${esc(metrics[event.rule.metric])}: <b>${esc(reading)}</b> · Rule triggers ${esc(event.rule.op)} ${esc(event.rule.limit)}</p>
    <small>${esc(event.source)}${lease ? " · " + esc(lease.name) : ""}</small>
    <p>${esc(event.action)}</p>${event.image ? `<img class="photo" src="${esc(event.image)}" alt="Changed pixels highlighted">` : ""}
    ${event.resolved ? `<small>Resolution · ${esc(event.resolution)}</small>` : `<button data-resolve="${esc(event.id)}">Resolve</button>`}
    </div>`;
}

function portfolio() {
  current = null;
  const counts = () => [
    parcels.length,
    parcels.reduce((sum, p) => sum + openEvents(p).length, 0),
    parcels.filter((p) => inMode(p, "risk")).length,
    parcels.filter((p) => inMode(p, "unread")).length,
  ];
  screen(`<section class="portfolio-hero"><div><h1>Land portfolio</h1><p id="acreage"></p></div><button id="new" class="primary">Add parcel</button></section>
    <div class="portfolio-counts" aria-label="Filter portfolio">${modes.map(([key, label]) => `<button class="metric" data-mode="${key}" aria-pressed="${key === portfolioMode}"><span>${label}</span><b></b></button>`).join("")}</div>
    <div class="portfolio-board"><section class="panel map-panel"><h2>Risk map</h2><div id="map" class="map"></div><div class="legend" aria-label="72-hour heat risk"><b>Heat risk</b>${Object.entries(
      palette,
    )
      .map(
        ([k, color]) =>
          `<span><i style="background:${color}"></i>${k === "Unknown" ? "Unassessed" : k}</span>`,
      )
      .join("")}</div></section>
    <section class="panel queue"><h2>Open violations</h2><div id="queue"></div></section></div>
    <section class="panel directory"><div class="bar"><h2>Parcel directory</h2><small id="resultCount" role="status"></small></div>
    <div class="directory-filters"><input id="search" type="search" placeholder="Find a parcel, company or renewal date" aria-label="Search parcels">
    <select id="status" aria-label="Status filter"><option value="">All statuses</option>${["Unassessed", "Awaiting readings", "Compliant", "Flagged", "Critical"].map((x) => `<option>${x}</option>`).join("")}</select>
    <select id="riskFilter" aria-label="Risk filter"><option value="">All risk levels</option>${Object.keys(
      palette,
    )
      .map((x) => `<option>${x}</option>`)
      .join("")}</select><button id="reset">Clear filters</button></div>
    <div id="list"></div></section>`);
  $("new").onclick = newParcel;
  const clear = () => {
    portfolioMode = "all";
    query = filter = level = "";
    $("search").value = $("status").value = $("riskFilter").value = "";
    draw();
  };
  let mapKey = null;
  function draw() {
    const totalAcres = parcels.reduce((sum, p) => sum + (p.acres || 0), 0);
    $("acreage").textContent = parcels.length
      ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(
          totalAcres,
        ) +
        " recorded acres across " +
        parcels.length +
        " " +
        (parcels.length === 1 ? "parcel" : "parcels")
      : "Start monitoring a parcel by drawing or importing its boundary.";
    const totals = counts();
    document
      .querySelectorAll(".metric b")
      .forEach((node, i) => (node.textContent = totals[i]));
    const visible = parcels
      .filter(
        (p) =>
          inMode(p, portfolioMode) &&
          (!filter || p.status === filter) &&
          (!level || risk(p) === level) &&
          [
            p.name,
            p.company,
            p.stage,
            p.renewal,
            p.favorite ? "starred" : "",
            p.acres,
          ]
            .join(" ")
            .toLowerCase()
            .includes(query.trim().toLowerCase()),
      )
      .sort(
        (a, b) =>
          openEvents(b).length - openEvents(a).length ||
          Number(b.favorite) - Number(a.favorite) ||
          a.name.localeCompare(b.name),
      );
    document
      .querySelectorAll("[data-mode]")
      .forEach((b) =>
        b.setAttribute(
          "aria-pressed",
          String(b.dataset.mode === portfolioMode),
        ),
      );
    $("reset").disabled =
      !query && !filter && !level && portfolioMode === "all";
    $("resultCount").textContent =
      visible.length + " of " + parcels.length + " shown";
    $("list").innerHTML = visible.length
      ? `<div class="directory-head" aria-hidden="true"><span>Parcel</span><span>Status</span><span>72-hour heat risk</span><span>Latest reading</span></div>` +
        visible
          .map(
            (p) =>
              `<button class="directory-row" data-parcel="${p.id}"><span><strong>${esc(p.name)}</strong><small>${esc([p.company, p.acres != null ? p.acres + " acres" : ""].filter(Boolean).join(" · "))}</small></span><span><span class="status-chip" data-state="${esc(p.status)}">${esc(p.status)}</span></span><span class="risk-cell">${p.risk == null ? "Unassessed" : esc(Math.round(100 * p.risk) + "% · " + risk(p))}</span><span class="date-cell">${esc(lastReading(p))}</span></button>`,
          )
          .join("")
      : `<p class="empty-directory">${parcels.length ? "No parcels match these filters." : "Your parcels will appear here after you add a boundary."}</p>`;
    $("list")
      .querySelectorAll("[data-parcel]")
      .forEach(
        (b) =>
          (b.onclick = () =>
            detail(visible.find((p) => p.id === b.dataset.parcel))),
      );
    const events = visible
      .flatMap((p) => openEvents(p).map((e) => ({ p, e })))
      .sort(
        (a, b) =>
          Date.parse(b.e.last_seen || b.e.time) -
          Date.parse(a.e.last_seen || a.e.time),
      );
    $("queue").innerHTML =
      events
        .map(
          ({ p, e }, i) =>
            `<button class="queue-row" data-index="${i}"><small>${esc(p.name)} · ${esc(localTime(e.last_seen || e.time))}</small><strong>${esc(e.rule.clause)}</strong><span>View evidence →</span></button>`,
        )
        .join("") ||
      `<p class="muted">${visible.length ? "No open violations for the parcels shown." : "No parcels in this view."}</p>`;
    $("queue")
      .querySelectorAll("[data-index]")
      .forEach(
        (b) =>
          (b.onclick = () => {
            const { p, e } = events[Number(b.dataset.index)];
            detail(p);
            const target = $("event-" + e.id);
            target?.focus();
            target?.scrollIntoView({ block: "center" });
          }),
      );
    const nextKey = JSON.stringify(
      visible.map((p) => [
        p.id,
        p.name,
        risk(p),
        p.geometry,
        p.layers,
        p.buffer,
      ]),
    );
    if (nextKey !== mapKey) {
      if (map) map.remove();
      map = initMap("map", visible);
      mapKey = nextKey;
    }
  }
  $("search").value = query;
  $("status").value = filter;
  $("riskFilter").value = level;
  $("search").oninput = (e) => {
    query = e.target.value;
    draw();
  };
  $("status").onchange = (e) => {
    filter = e.target.value;
    draw();
  };
  $("riskFilter").onchange = (e) => {
    level = e.target.value;
    draw();
  };
  $("reset").onclick = clear;
  document.querySelectorAll("[data-mode]").forEach(
    (b) =>
      (b.onclick = () => {
        portfolioMode = b.dataset.mode;
        draw();
      }),
  );
  redrawPortfolio = draw;
  draw();
}
