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
        if (result.kind === "concept")
          return `<figure class="ai-output"><figcaption><strong>AI-generated concept</strong><small>${meta}</small></figcaption><img class="photo" loading="lazy" src="${path}" alt="${esc(result.prompt)}"><p>${esc(result.prompt)}</p><a class="link" download="parcelsite-concept.png" href="${path}">Download PNG</a></figure>`;
        return `<article class="ai-output"><strong>AI-generated brief</strong><small>${meta}</small><p>${esc(result.prompt)}</p><div class="brief-text">${esc(result.text)}</div><details><summary>Source records (${result.sources.length})</summary><ol>${result.sources.map((record, i) => `<li><b>[S${i + 1}]</b> ${esc(record)}</li>`).join("")}</ol></details></article>`;
      })
      .join("") || "<small>No saved concepts or briefs.</small>"
  );
}
function setupBedrock(parcel) {
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
      ${image ? `<div class="split"><label>Stability AI model<select name="model"><option value="core">Stable Image Core</option><option value="ultra">Stable Image Ultra</option></select></label><label>Aspect ratio<select name="aspect_ratio"><option>16:9</option><option>1:1</option><option>9:16</option></select></label></div><small>Bedrock receives your description. Generated concepts are saved in this parcel’s studio.</small>` : "<small>Bedrock receives the question and a snapshot of this parcel’s status, confirmed rules, recent readings and open alerts.</small>"}
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
      status.textContent = "Generating with Amazon Bedrock…";
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
  $("generateConcept").onclick = () => compose("concept");
  $("generateBrief").onclick = () => compose("brief");
}
