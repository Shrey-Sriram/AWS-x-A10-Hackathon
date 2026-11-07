from io import BytesIO

from PIL import Image, ImageDraw
from shapely.geometry import shape


def heatmap(parcel):
    width, height = 900, 640
    canvas = Image.new("RGB", (width, height), "#f5f6ed")
    draw = ImageDraw.Draw(canvas)
    main = shape(parcel["geometry"])
    shapes = [(shape(a["geometry"]), "#8175a5") for a in parcel["layers"]]
    if parcel["buffer"]:
        shapes.insert(0, (shape(parcel["buffer"]), "#cfbc80"))
    p = parcel["risk"]
    color = (
        "#9caba0"
        if p is None
        else "#be5741"
        if p >= 0.65
        else "#d59538"
        if p >= 0.30
        else "#5c9b6f"
    )
    bounds = [s.bounds for s, _ in shapes] + [main.bounds]
    west = min(b[0] for b in bounds)
    east = max(b[2] for b in bounds)
    north = max(b[3] for b in bounds)
    south = min(b[1] for b in bounds)
    # Local metric projection prevents latitude-dependent distortion in the image.
    from math import cos, radians

    xratio = max(0.001, cos(radians((north + south) / 2)))
    scale = min(
        (width - 80) / ((east - west) * xratio), (height - 130) / (north - south)
    )

    def ring(coords):
        return [
            (
                width / 2 + (x - (west + east) / 2) * scale * xratio,
                height / 2 + 10 - (y - (north + south) / 2) * scale,
            )
            for x, y, *_ in coords
        ]

    ordered = (
        [a for a in shapes if a[1] == "#cfbc80"]
        + [(main, color)]
        + [a for a in shapes if a[1] != "#cfbc80"]
    )
    for geom, fill in ordered:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            draw.polygon(
                ring(poly.exterior.coords), fill=fill, outline="#294333", width=2
            )
            for hole in poly.interiors:
                draw.polygon(ring(hole.coords), fill="#f5f6ed")
    draw.text((24, 16), parcel["name"], fill="#20382a", font_size=23)
    label = "Unassessed" if p is None else f"{p:.0%} chance of threshold crossing"
    draw.text(
        (24, height - 38),
        "72-hour heat risk: "
        + label
        + "   |   Ochre: buffer   |   Purple: land-use layer",
        fill="#20382a",
        font_size=15,
    )
    out = BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
