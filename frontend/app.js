const form = document.querySelector("#add");
const list = document.querySelector("#list");
const status = document.querySelector("#status");
async function draw() {
  const response = await fetch("/api/parcels");
  if (!response.ok) throw Error("Could not load parcels.");
  const parcels = await response.json();
  list.replaceChildren(...parcels.map(parcel => {
    const row = document.createElement("li");
    row.textContent = parcel.name + " · " + parcel.acres + " acres";
    return row;
  }));
  status.textContent = parcels.length ? "" : "No parcels added yet.";
}
form.onsubmit = async event => {
  event.preventDefault();
  const submit = form.querySelector("button");
  submit.disabled = true;
  try {
    const fields = new FormData(form);
    const response = await fetch("/api/parcels", {
      method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({name:fields.get("name").trim(), acres:Number(fields.get("acres"))})
    });
    if (!response.ok) throw Error("Enter a parcel name and a positive acreage.");
    form.reset();
    await draw();
  } catch (error) { status.textContent = error.message; }
  finally { submit.disabled = false; }
};
draw().catch(error => { status.textContent = error.message; });
