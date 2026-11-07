const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
let parcels = [],
  current = null,
  map = null,
  filter = "",
  level = "",
  query = "",
  timer;
const metrics = {
  temperature: "Temperature (°C)",
  wind: "Wind (km/h)",
  soil_moisture: "Soil moisture (m³/m³)",
  pm25: "PM2.5 (µg/m³)",
  change: "Image change (%)",
};
const risk = (p) =>
  p.risk === null
    ? "Unknown"
    : p.risk >= 0.65
      ? "High"
      : p.risk >= 0.3
        ? "Medium"
        : "Low";
function notice(msg) {
  clearTimeout(timer);
  $("notice").textContent = msg;
  timer = setTimeout(() => ($("notice").textContent = ""), 7500);
}
async function api(path, body) {
  const opts = {};
  if (body !== undefined) {
    opts.method = "POST";
    if (body instanceof FormData) opts.body = body;
    else {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch("/api/" + path, opts);
  const result = await r.json();
  if (!r.ok) {
    if (r.status === 401 && path !== "login") auth();
    throw Error(
      typeof result.detail === "string"
        ? result.detail
        : result.detail?.[0]?.msg || "Request failed",
    );
  }
  return result;
}
function run(fn) {
  return async (e) => {
    e?.preventDefault();
    const b = e?.submitter || e?.currentTarget;
    if (b) b.disabled = true;
    try {
      await fn(e);
    } catch (err) {
      notice(err.message);
    } finally {
      if (b) b.disabled = false;
    }
  };
}
function screen(html) {
  if (map) {
    map.remove();
    map = null;
  }
  $("main").innerHTML = html;
}
function modal(html) {
  $("dialogBody").innerHTML = html;
  if (!$("dialog").open) $("dialog").showModal();
}
$("close").onclick = () => $("dialog").close();
function auth(signup = false) {
  current = null;
  query = filter = level = "";
  portfolioMode = "all";
  $("nav").hidden = true;
  screen(
    '<section class="auth"><figure><figcaption>Southern Great Plains · NASA Terra / MODIS · June 2024</figcaption></figure><article><h1>Parcel intelligence</h1><p class="muted">Monitor lease obligations and environmental conditions across your land portfolio.</p><form id="auth"><label>Email<input name="email" type="email" autocomplete="username" required></label><label>Password<input name="password" type="password" minlength="10" autocomplete="' +
      (signup ? "new-password" : "current-password") +
      '" required></label><button class="primary">' +
      (signup ? "Create account" : "Sign in") +
      '</button></form><button id="mode">' +
      (signup ? "Use an existing account" : "Create an account") +
      "</button></article></section>",
  );
  $("mode").onclick = () => auth(!signup);
  $("auth").onsubmit = run(async (e) => {
    await api(
      signup ? "signup" : "login",
      Object.fromEntries(new FormData(e.target)),
    );
    await load();
  });
}
async function load(id = null) {
  parcels = await api("parcels");
  $("nav").hidden = false;
  if (id && parcels.some((p) => p.id === id))
    detail(parcels.find((p) => p.id === id));
  else portfolio();
}
function initMap(id, items, heat = true) {
  const m = L.map(id).setView([35, -99], 5);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(m);
  const bounds = L.latLngBounds([]);
  for (const p of items) {
    if (p.buffer)
      L.geoJSON(p.buffer, {
        style: {
          color: "#947432",
          weight: 1,
          dashArray: "4",
          fillOpacity: 0.09,
        },
      }).addTo(m);
    const group = L.geoJSON(p.geometry, {
      style: {
        color:
          heat && risk(p) === "Unknown"
            ? "#78877c"
            : heat && risk(p) === "High"
              ? "#bc4c35"
              : heat && risk(p) === "Medium"
                ? "#c38c32"
                : "#32734d",
        weight: 2,
        fillOpacity: 0.38,
      },
    }).addTo(m);
    if (p.name)
      group.bindTooltip(
        document
          .createTextNode(p.name)
          .textContent.replace(/[&<>]/g, (c) => esc(c)),
      );
    if (p.id)
      group.on("click", () =>
        detail(parcels.find((parcel) => parcel.id === p.id) || p),
      );
    bounds.extend(group.getBounds());
    for (const l of p.layers || [])
      L.geoJSON(l.geometry, {
        style: { color: "#6f63ac", weight: 2, fillOpacity: 0.25 },
      })
        .bindTooltip(esc(l.label))
        .addTo(m);
  }
  if (bounds.isValid()) m.fitBounds(bounds, { padding: [22, 22], maxZoom: 15 });
  return m;
}
function newParcel() {
  modal(
    '<h2>Add parcel</h2><form id="parcel"><label>Parcel name<input name="name" required maxlength="120"></label><div class="split"><label>Company<input name="company"></label><label>Renewal date<input type="date" name="renewal"></label></div><label>Import GeoJSON or KML<input id="geo" type="file" accept=".geojson,.json,.kml"></label><small>Or click at least three boundary points on the map. Additional imported polygons become land-use layers.</small><div id="drawMap" class="map mini"></div><div class="actions"><button type="button" id="undo">Undo point</button><small id="points">0 points</small></div><label>Buffer distance (meters)<input name="buffer_m" type="number" min="0" max="10000" value="0"></label><button class="primary">Save parcel</button></form>',
  );
  const m = initMap("drawMap", []),
    points = [];
  let layers = [],
    shapeLayer;
  const clean = () => {
    m.remove();
    $("dialog").removeEventListener("close", clean);
  };
  $("dialog").addEventListener("close", clean);
  function paint() {
    if (shapeLayer) m.removeLayer(shapeLayer);
    shapeLayer = L.polygon(points).addTo(m);
    $("points").textContent = points.length + " points";
  }
  m.on("click", (e) => {
    layers = [];
    points.push(e.latlng);
    paint();
  });
  $("undo").onclick = () => {
    layers = [];
    points.pop();
    paint();
  };
  $("geo").onchange = run(async (e) => {
    const fd = new FormData();
    fd.set("file", e.target.files[0]);
    layers = await api("map/import", fd);
    points.length = 0;
    if (shapeLayer) m.removeLayer(shapeLayer);
    shapeLayer = L.geoJSON(layers[0].geometry).addTo(m);
    m.fitBounds(shapeLayer.getBounds());
    $("points").textContent = layers.length + " polygon(s) imported";
  });
  $("parcel").onsubmit = run(async (e) => {
    if (!layers.length && points.length < 3)
      throw Error("Draw at least three points or import a boundary");
    const data = Object.fromEntries(new FormData(e.target));
    data.geometry = layers.length
      ? layers[0].geometry
      : shapeLayer.toGeoJSON().geometry;
    data.layers = layers.slice(1);
    const p = await api("parcels", data);
    $("dialog").close();
    await load(p.id);
  });
}
const item = (s) => '<div class="item">' + s + "</div>";
function detail(p) {
  if (current !== p.id) window.scrollTo(0, 0);
  current = p.id;
  const path = "parcels/" + p.id + "/";
  const reports = ["weekly", "violations", "scorecard", "geojson", "heatmap"];
  screen(
    '<div class="bar"><div><button id="backPortfolio" class="back-link">← Land portfolio</button><h1>' +
      esc(p.name) +
      "</h1><small>" +
      [
        p.company,
        p.stage,
        p.acres != null ? p.acres + " acres" : "",
        p.favorite ? "★ Starred" : "",
      ]
        .filter(Boolean)
        .map(esc)
        .join(" · ") +
      '</small><span class="pill">' +
      esc(p.status) +
      '</span></div><div class="actions"><button id="details">Edit lease details</button><button id="refresh" class="primary">Refresh sensors</button></div></div><div class="stats">' +
      [
        [
          "72-hour heat risk",
          p.risk === null ? "—" : Math.round(100 * p.risk) + "%",
        ],
        ["Open violations", p.events.filter((e) => !e.resolved).length],
        ["Renewal", esc(p.renewal) || "—"],
      ]
        .map(
          ([a, b]) =>
            '<div class="stat"><small>' + a + "</small><b>" + b + "</b></div>",
        )
        .join("") +
      '</div><section class="board"><div class="panel"><h2>Sensor readings</h2>' +
      sensorRows(p) +
      (p.outlook || [])
        .map((o) =>
          item(
            "<small>" +
              esc(o.label) +
              "</small><strong>" +
              Math.round(o.risk * 100) +
              "%</strong>",
          ),
        )
        .join("") +
      "<small>" +
      esc(p.feed_notice || "") +
      '</small></div><div class="panel"><h2>Parcel boundary</h2><div id="map" class="map mini"></div></div></section><section class="detail"><article class="panel"><div class="bar"><h2>Lease documents</h2><button id="upload">Upload lease</button></div>' +
      p.leases
        .slice()
        .reverse()
        .map((l) =>
          item(
            "<details><summary>" +
              esc(l.name) +
              "</summary>" +
              l.clauses
                .map((c) =>
                  item(
                    "<small>" +
                      esc(c.category) +
                      (c.page ? " · p. " + c.page : "") +
                      "</small>" +
                      esc(c.text),
                  ),
                )
                .join("") +
              "</details>",
          ),
        )
        .join("") +
      '</article><article class="panel"><div class="bar"><h2>Monitoring rules</h2><button id="rule">Add rule</button></div>' +
      p.rules
        .map((r) =>
          item(
            esc(r.clause) +
              "<small>" +
              metrics[r.metric] +
              " " +
              r.op +
              " " +
              r.limit +
              "</small>",
          ),
        )
        .join("") +
      '</article><article id="violations" class="panel"><h2>Violation log</h2>' +
      (p.events.length ? "" : "<small>No violations recorded.</small>") +
      p.events
        .slice()
        .reverse()
        .map((e) => eventEvidence(e, p))
        .join("") +
      '</article><article class="panel"><div class="bar"><h2>Satellite & aerial imagery</h2><button id="images">Compare images</button></div><button id="sat">Load satellite comparison</button>' +
      (p.latest_image
        ? "<small>" +
          esc(p.satellite?.dates?.join(" to ") || "Uploaded comparison") +
          (p.satellite?.regional_change_risk_7d != null
            ? " · Experimental regional RGB change, next 7 days: " +
              Math.round(p.satellite.regional_change_risk_7d * 100) +
              "%"
            : "") +
          '</small><img class="photo" src="' +
          p.latest_image +
          '" alt="Imagery difference overlay">'
        : "") +
      '</article><article class="panel"><div class="bar"><h2>Contact history</h2><button id="note">Add note</button></div>' +
      p.history
        .slice()
        .reverse()
        .map((n) => item(esc(n.text) + "<small>" + esc(n.time) + "</small>"))
        .join("") +
      '</article><article class="panel"><h2>Reports</h2><div class="actions">' +
      reports
        .map(
          (r, i) =>
            '<a class="link" href="/api/' +
            path +
            "reports/" +
            r +
            '">' +
            [
              "Weekly audit",
              "Violation CSV",
              "Scorecard",
              "Risk GeoJSON",
              "Heatmap PNG",
            ][i] +
            "</a>",
        )
        .join("") +
      "</div></article></section>",
  );
  $("backPortfolio").onclick = portfolio;
  map = initMap("map", [p]);
  $("refresh").onclick = run(async () => {
    const control = $("refresh");
    control.textContent = "Refreshing…";
    try {
      await api(path + "refresh", {});
    } finally {
      control.textContent = "Refresh sensors";
    }
    await load(p.id);
    notice("Sensor readings updated");
  });
  function form(title, fields, submit) {
    modal(
      "<h2>" +
        title +
        '</h2><form id="action">' +
        fields +
        '<button class="primary">Save</button></form>',
    );
    $("action").onsubmit = run(async (e) => {
      await submit(new FormData(e.target));
      $("dialog").close();
      await load(p.id);
    });
  }
  $("details").onclick = () =>
    form(
      "Lease details",
      '<label>Company<input name="company" value="' +
        esc(p.company) +
        '"></label><label>Contract status<select name="stage">' +
        ["Draft", "In review", "Signed", "Expired"]
          .map(
            (stage) =>
              "<option " +
              (stage === p.stage ? "selected" : "") +
              ">" +
              stage +
              "</option>",
          )
          .join("") +
        '</select></label><label>Renewal date<input type="date" name="renewal" value="' +
        esc(p.renewal) +
        '"></label><label>Portfolio bookmark<select name="favorite"><option value="false">Unstarred</option><option value="true" ' +
        (p.favorite ? "selected" : "") +
        ">Starred</option></select></label>",
      (fd) => api(path + "details", Object.fromEntries(fd)),
    );
  $("upload").onclick = () =>
    form(
      "Upload lease",
      '<label>Lease document<input type="file" name="file" accept=".pdf,.docx" required></label>',
      (fd) => api(path + "leases", fd),
    );
  $("rule").onclick = () => {
    form(
      "Confirm monitoring rule",
      '<label>Lease revision<select name="lease_ref" id="leaseRef"><option value="">Manual source</option>' +
        p.leases
          .map(
            (l) =>
              '<option value="' +
              esc(l.time) +
              '">' +
              esc(l.name) +
              " · " +
              esc(l.time.slice(0, 10)) +
              "</option>",
          )
          .join("") +
        '</select></label><label id="clauseSelect" hidden>Extracted clause<select id="suggestion"></select></label><label>Source clause<textarea id="ruleText" name="clause" required></textarea></label><label>Measurement<select name="metric">' +
        Object.entries(metrics)
          .map(([k, v]) => '<option value="' + k + '">' + v + "</option>")
          .join("") +
        '</select></label><div class="split"><label>Trigger<select name="op"><option>above</option><option>below</option></select></label><label>Limit<input type="number" name="limit" step="any" required></label></div>',
      (fd) => api(path + "rules", Object.fromEntries(fd)),
    );
    $("leaseRef").onchange = (e) => {
      const lease = p.leases.find((l) => l.time === e.target.value);
      $("clauseSelect").hidden = !lease;
      $("suggestion").innerHTML =
        '<option value="">Select a clause</option>' +
        (lease?.clauses || [])
          .map(
            (c, i) =>
              '<option value="' +
              i +
              '">' +
              esc(c.text.slice(0, 100)) +
              "</option>",
          )
          .join("");
      $("suggestion").onchange = (e) => {
        const c = lease?.clauses[e.target.value];
        if (c) {
          $("ruleText").value = c.text;
          if (c.suggestion) {
            const fields = $("action").elements;
            for (const k of ["metric", "op", "limit"])
              fields[k].value = c.suggestion[k];
          }
        }
      };
    };
  };
  $("images").onclick = () =>
    form(
      "Compare aligned imagery",
      '<small>Upload two images with the same bounds, orientation and dimensions.</small><label>Earlier image<input name="before" type="file" accept="image/*" required></label><label>Later image<input name="after" type="file" accept="image/*" required></label>',
      (fd) => api(path + "images", fd),
    );
  $("sat").onclick = run(async () => {
    await api(path + "satellite", {});
    await load(p.id);
  });
  $("note").onclick = () =>
    form(
      "Add contact note",
      '<label>Conversation or update<textarea name="text" required></textarea></label>',
      (fd) => api(path + "notes", Object.fromEntries(fd)),
    );
  document
    .querySelectorAll("[data-resolve]")
    .forEach(
      (b) =>
        (b.onclick = () =>
          form(
            "Resolve violation",
            '<label>Remediation taken<textarea name="text" required></textarea></label>',
            (fd) =>
              api(
                path + "events/" + b.dataset.resolve + "/resolve",
                Object.fromEntries(fd),
              ),
          )),
    );
}
function sensorRows(p) {
  const latest = {};
  for (const o of p.observations) {
    for (const [k, v] of Object.entries(o.values)) {
      if (!latest[k] || o.time > latest[k].time) latest[k] = { ...o, value: v };
    }
  }
  return (
    Object.entries(latest)
      .map(([k, o]) =>
        item(
          "<strong>" +
            metrics[k] +
            " · " +
            o.value +
            "</strong><small>" +
            esc(o.source) +
            " · " +
            esc(o.time) +
            "</small>",
        ),
      )
      .join("") ||
    '<p class="muted">Refresh sensors for readings at the parcel centroid.</p>'
  );
}
$("home").onclick = run(() => load());
$("logout").onclick = run(async () => {
  await api("logout", {});
  auth();
});
$("support").onclick = () => {
  modal(
    '<h2>Contact</h2><form id="contact"><label>Message to the workspace operator<textarea name="text" required></textarea></label><button class="primary">Save inquiry</button></form>',
  );
  $("contact").onsubmit = run(async (e) => {
    await api("contact", Object.fromEntries(new FormData(e.target)));
    $("dialog").close();
    notice("Inquiry saved");
  });
};
setInterval(() => {
  if (!$("nav").hidden && !$("dialog").open && !document.hidden) {
    const id = current;
    api("parcels")
      .then((rows) => {
        if ($("nav").hidden || current !== id || $("dialog").open) return;
        if (!id) {
          if (
            redrawPortfolio &&
            JSON.stringify(rows) !== JSON.stringify(parcels)
          ) {
            parcels = rows;
            redrawPortfolio();
          }
          return;
        }
        const old = parcels.find((p) => p.id === id),
          next = rows.find((p) => p.id === id);
        if (
          current === id &&
          next &&
          JSON.stringify(old) !== JSON.stringify(next)
        ) {
          parcels = rows;
          detail(next);
        }
      })
      .catch((e) => notice(e.message));
  }
}, 60000);
load().catch(() => auth());
