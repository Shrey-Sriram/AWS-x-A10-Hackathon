function aiGallery(pid, outputs) {
  return (
    outputs
      .map((result) => {
        const path =
          "/api/parcels/" +
          encodeURIComponent(pid) +
          "/ai/" +
          encodeURIComponent(result.id) +
          "/image";
        const meta = esc(result.model) + " · " + esc(localTime(result.time));
        const remove = `<button data-delete-output="${esc(result.id)}">Delete saved output</button>`;
        if (result.kind === "concept3d")
          return `<article class="ai-output"><strong>3D concept</strong><p>${esc(result.prompt)}</p><small>${meta}</small><button data-scene="${esc(result.id)}">Open 3D concept</button>${remove}</article>`;
        if (result.kind === "concept")
          return `<figure class="ai-output"><figcaption><strong>AI-generated concept</strong><small>${meta}</small></figcaption><img class="photo" loading="lazy" src="${path}" alt="${esc(result.prompt)}"><p>${esc(result.prompt)}</p><a class="link" download="parcelsite-concept.png" href="${path}">Download PNG</a>${remove}</figure>`;
        return `<article class="ai-output"><strong>AI-generated brief</strong><small>${meta}</small><p>${esc(result.prompt)}</p><div class="brief-text">${esc(result.text)}</div><details><summary>Source records (${result.sources.length})</summary><ol>${result.sources.map((record, i) => `<li><b>[S${i + 1}]</b> ${esc(record)}</li>`).join("")}</ol></details>${remove}</article>`;
      })
      .join("") || "<small>No saved concepts or briefs.</small>"
  );
}
function setupStudio(parcel) {
  const path = "parcels/" + parcel.id + "/ai";
  const gallery = $("aiGallery");
  async function refresh() {
    gallery.innerHTML = aiGallery(parcel.id, await api(path));
  }
  refresh().catch((error) => (gallery.textContent = error.message));
  function compose(kind) {
    const image = kind === "concept";
    modal(`<h2>${image ? "Generate a 2D concept" : "Generate a parcel brief"}</h2>
      <form id="aiForm"><label>${image ? "Image description" : "Briefing question"}<textarea required maxlength="3000" name="prompt"></textarea></label>
      ${image ? `<label>Aspect ratio<select name="aspect_ratio"><option>16:9</option><option>1:1</option><option>9:16</option></select></label><small>SageMaker sends your description to FLUX.2-klein-4B. Generated concepts are saved in this parcel’s studio.</small>` : "<small>Bedrock receives the question and a snapshot of this parcel’s status, confirmed rules, recent readings and open alerts.</small>"}
      <p id="aiStatus" role="status" aria-live="polite"></p><button class="primary">${image ? "Generate concept" : "Generate brief"}</button></form>`);
    const form = $("aiForm"),
      status = $("aiStatus");
    form.elements.prompt.value = image
      ? ""
      : "Summarize current conditions, open violations and missing readings. Cite the source records.";
    form.onsubmit = async (e) => {
      e.preventDefault();
      const button = e.submitter;
      if (button.disabled) return;
      button.disabled = true;
      status.textContent = image
        ? "Generating with SageMaker…"
        : "Generating with Amazon Bedrock…";
      try {
        await api(path, {
          ...Object.fromEntries(new FormData(form)),
          kind,
          request_id: crypto.randomUUID(),
        });
        await refresh();
        if (form.isConnected && $("dialog").open) $("dialog").close();
        notice(image ? "Concept saved" : "Brief saved");
      } catch (error) {
        status.textContent = error.message;
      } finally {
        button.disabled = false;
      }
    };
  }
  gallery.onclick = run(async (event) => {
    const remove = event.target.closest("[data-delete-output]");
    if (remove) {
      if (!confirm("Delete this saved studio output?")) return;
      await api(path + "/" + remove.dataset.deleteOutput, undefined, "DELETE");
      await refresh();
      return;
    }
    const button = event.target.closest("[data-scene]");
    if (button) openStudioScene(parcel, refresh, button.dataset.scene);
  });
  $("generateConcept").onclick = () => openStudioScene(parcel, refresh);
  $("generateBrief").onclick = () => compose("brief");
}
