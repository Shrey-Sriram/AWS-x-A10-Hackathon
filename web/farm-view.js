async function loadViewer(view, url, status, loaded) {
  view.addEventListener("error", () => status.textContent = "Could not display the model. Try downloading the GLB.");
  view.addEventListener("load", () => status.textContent = loaded());
  try {
    await import("/vendor/model-viewer.min.js");
    if (view.isConnected) view.src = url;
  } catch {
    status.textContent = "3D rendering is unavailable. Download the GLB to view it.";
  }
}
async function openFarmView() {
  const url = "/models/caldwell-farm-3d.glb";
  modal(`<section class="farm-view"><h2>Caldwell field study</h2>
    <p>Agricultural land near Caldwell, Idaho · 43.63225° N, 116.66050° W</p>
    <model-viewer id="farmModel" camera-controls touch-action="pan-y" alt="Farm buildings on measured terrain"
    camera-target="130m 4m -108m" camera-orbit="30deg 76deg 16%" min-camera-orbit="auto 8deg 12%" max-camera-orbit="auto 87deg 150%"></model-viewer>
    <div class="actions"><button id="farmOverview">Whole parcel</button><button id="farmYard">Farmyard</button><a class="link" download href="${url}">Download 3D model</a></div>
    <small>Imagery: Esri World Imagery and contributors · Terrain: USGS 3DEP · Building heights estimated</small>
    <small id="farmStatus" role="status">Loading 3D view…</small></section>`);
  const view = $("farmModel");
  $("farmOverview").onclick = () => { view.cameraTarget="auto auto auto"; view.cameraOrbit="20deg 54deg 83%"; };
  $("farmYard").onclick = () => { view.cameraTarget="130m 4m -108m"; view.cameraOrbit="30deg 76deg 16%"; };
  await loadViewer(view, url, $("farmStatus"), () => "Drag to rotate. Scroll or pinch to zoom.");
}
async function terrainControls(path, view, url) {
  const button = $("loadTerrain"), feedback = $("terrainFeedback");
  function active() {
    return view.isConnected && $("dialog").open && $("loadTerrain") === button;
  }
  async function show(load = false) {
    button.disabled = true;
    try {
      let info = await api(path + "/terrain", load ? {} : undefined);
      const wasPending = info.state === "pending";
      while (info.state === "pending" && active()) {
        feedback.textContent = "Preparing measured terrain…";
        await new Promise(resolve => setTimeout(resolve, 2500));
        if (!active()) return;
        info = await api(path + "/terrain");
      }
      if (!active()) return;
      if (info.state === "ready") {
        button.hidden = true;
        const details = [info.source];
        if (info.grid_spacing_m != null) details.push("Output grid ≈ " + info.grid_spacing_m + " m");
        if (info.relief_m != null) details.push("Elevation change " + info.relief_m + " m");
        feedback.textContent = details.join(" · ");
        if (load || wasPending) view.src = url + "?terrain=" + Date.now();
      } else {
        feedback.textContent = info.message || "Uses the parcel bounds to request USGS elevation data.";
        button.disabled = false;
      }
    } catch (error) {
      feedback.textContent = error.message;
      button.disabled = false;
    }
  }
  button.onclick = () => show(true);
  await show();
}
async function openStudioScene(parcel, refresh, id) {
  const path = "parcels/" + encodeURIComponent(parcel.id);
  const url = "/api/" + path + (id ? "/ai/" + encodeURIComponent(id) + "/model.glb" : "/scene.glb");
  modal(`<section class="farm-view"><h2>${esc(parcel.name)}</h2>
    <model-viewer id="studioScene" loading="eager" camera-controls camera-orbit="25deg 65deg 86%" touch-action="pan-y" alt="Interactive parcel concept"></model-viewer>
    <small id="sceneStatus" role="status">Loading parcel…</small>
    <a class="link" href="${url}" download="parcel-concept.glb">Download GLB</a>
    ${id ? "" : '<details><summary>Attach a site image</summary><label>North-up aerial image (JPEG or PNG, under 10 MB)<input id="siteImage" type="file" accept="image/jpeg,image/png"></label><small>Crop the image to the parcel’s bounding rectangle before uploading.</small><small id="imageFeedback" role="status"></small></details>'}
    ${id ? "" : '<div><button id="loadTerrain" disabled>Load measured terrain</button> <small id="terrainFeedback" role="status"></small></div>'}
    <form id="sceneForm"><label>Proposed feature<textarea required maxlength="3000" name="prompt" placeholder="A timber equipment shed with a pitched roof"></textarea></label>
    <small>The feature is placed at an illustrative size and location.</small><button class="primary">Generate 3D concept</button><p id="generationStatus" role="status"></p></form></section>`);
  const view=$("studioScene"), form=$("sceneForm"), status=$("generationStatus");
  if (!id) void terrainControls(path, view, url);
  if (!id) $("siteImage").onchange = async ({target}) => {
    if (!target.files[0]) return;
    const feedback=$("imageFeedback"), fd=new FormData();
    fd.set("file",target.files[0]); target.disabled=true;
    try { await api(path+"/site-image",fd); feedback.textContent="Image saved."; view.src=url+"?revision="+Date.now(); }
    catch(error){ feedback.textContent=error.message; }
    finally {target.disabled=false;}
  };
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button=form.querySelector("button");
    if (button.disabled) return;
    button.disabled=true; status.textContent="Generating the feature with SageMaker…";
    try {
      const result=await api(path+"/ai",{kind:"concept3d",request_id:crypto.randomUUID(),prompt:form.elements.prompt.value});
      await refresh();
      if (view.isConnected && $("dialog").open) await openStudioScene(parcel,refresh,result.id);
    } catch(error){status.textContent=error.message;}
    finally {button.disabled=false;}
  };
  await loadViewer(view,url,$("sceneStatus"),()=>{
    const materials=view.model.materials.map(m=>m.name||"");
    const credits=[];
    if(materials.some(n=>n.includes("USGS 3DEP"))) credits.push("Terrain: USGS 3DEP");
    if(materials.some(n=>n.startsWith("Esri World Imagery"))) credits.push("Imagery: Esri World Imagery and contributors");
    else if(materials.some(n=>n.startsWith("Uploaded site image"))) credits.push("Uploaded site image");
    if(parcel.acres!=null) credits.push(parcel.acres+" acres");
    return [...credits,id?"Saved 3D concept":"Parcel boundary","Drag to rotate. Scroll or pinch to zoom."].join(" · ");
  });
}
