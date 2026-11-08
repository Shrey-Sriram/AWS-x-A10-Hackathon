const get = id => document.getElementById(id);
const mode = new URLSearchParams(location.search).get("mode") === "map" ? "map" : "embedded";
const base = "/api/demos/willow-bend/" + mode;
const sample = "/examples/willow-bend/";
get(mode === "map" ? "mapMode" : "embedded").setAttribute("aria-current", "page");
get("pdf").href = sample + mode + ".pdf";
const map = L.map("bounds");
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {attribution: "© OpenStreetMap contributors"}).addTo(map);
let selected = false;
let layer;
function confirmField() {
  selected = true;
  layer.setStyle({color:"#235638", fillOpacity:0.35});
  get("select").hidden = true;
  get("source").textContent = mode === "map" ? "Boundary selected from the sample map. The exhibit contains the inspection clause." : "The exhibit supplied the outline, including its interior exclusion.";
  get("build").disabled = false;
}
async function preview() {
  const response = await fetch(base + "/preview");
  if (!response.ok) throw Error("Could not read the sample exhibit.");
  const info = await response.json();
  const outline = info.geometry || await fetch(sample + "boundary.geojson").then(r => r.json());
  layer = L.geoJSON(outline, {style:{color:"#647460", fillOpacity:0.12}}).addTo(map);
  map.fitBounds(layer.getBounds(), {padding:[18,18]});
  if (info.geometry) confirmField();
  else {
    get("source").textContent = "No coordinates in the exhibit. Select the outlined field on this sample map.";
    get("select").hidden = false;
    get("select").onclick = confirmField;
    layer.on("click", confirmField);
  }
}
async function build(shed) {
  const view = get("terrain");
  get("build").disabled = get("shed").disabled = true;
  get("status").textContent = "Clipping saved USGS elevation samples to the selected outline…";
  const url = base + "/scene.glb?selected=" + selected + "&shed=" + shed;
  try {
    await import("/vendor/model-viewer.min.js");
    const r = await fetch(url);
    if (!r.ok) throw Error("Could not build the demo scene.");
    const objectURL = URL.createObjectURL(await r.blob());
    const previous = view.getAttribute("src");
    await new Promise((resolve,reject) => {
      const events = new AbortController();
      view.addEventListener("load",()=>{events.abort(); resolve();},{signal:events.signal});
      view.addEventListener("error",()=>{events.abort(); reject(Error("The 3D viewer could not render the mesh."));},{signal:events.signal});
      get("download").href = objectURL;
      get("download").hidden = false;
      view.src = objectURL;
      if (previous) URL.revokeObjectURL(previous);
    });
    get("status").textContent = shed ? "Measured terrain with a saved Shap-E shed. Its size and placement are illustrative." : "Measured USGS terrain clipped to the parcel. Drag to rotate or scroll to zoom.";
    get("shed").disabled = shed;
  } catch(error) { get("status").textContent = error.message; }
  finally {get("build").disabled = false;}
}
get("build").onclick = ()=>build(false);
get("shed").onclick = ()=>build(true);
preview().catch(error => {get("source").textContent = error.message;});
