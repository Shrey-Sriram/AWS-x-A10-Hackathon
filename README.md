# parcelsite

parcelsite turns a land lease into a parcel monitoring workspace. An operator draws a boundary, uploads a lease, confirms measurable obligations, and follows environmental readings and alerts through to an audit export.

## Run locally

Tested on macOS with Python 3.13. From the repository root:

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Open [the dashboard](http://127.0.0.1:8000), create an account, and select **Add parcel**. The checked-in model weights load at startup. Weather, air quality, satellite imagery and map tiles need internet access. The API reference is available at [/docs](http://127.0.0.1:8000/docs).

1. Draw at least three points or import a GeoJSON/KML polygon. For a multi-feature file, put the parcel first and name subsequent polygons for their land uses. Set the boundary buffer in meters.
2. Upload a text-based PDF or DOCX. Open **Add rule**, select a lease revision and extracted clause, and review its measurement, direction and limit. Uploading another revision preserves the previous text.
3. Select **Refresh sensors** for weather, near-surface soil and PM2.5 readings. **Load satellite comparison** compares two dated Terra MODIS images of the same area. **Compare images** also accepts two co-registered aerial images. The overlay highlights changed pixels.
4. Open violations record the source, observation time, rule, value and suggested response. Resolve an event with a field note. Download a weekly audit, violation CSV, parcel scorecard, georeferenced risk layers or PNG heatmap.

A background thread refreshes registered parcels every 15 minutes and checks satellite imagery once per acquisition date while the server runs. Use one server worker with this embedded scheduler. Set `LEASESENSE_MONITOR=0` for manual refresh. Account and parcel data are stored in `data/leasesense.sqlite` by default. Override the path with `LEASESENSE_DB`. Set `HTTPS_ONLY=1` when serving through HTTPS. The contact form saves inquiries to the local operator database.

## Engineering walkthrough

I use the parcel ID to connect a lease revision, its geometry, the operator's confirmed rules, and dated measurements. Keeping the original clause alongside the numerical limit gives the operator a way to trace an alert back to the lease. The clause classifier routes text into five categories. A unit-aware parser proposes numerical rules for review.

I evaluate the current reading directly against the confirmed rule. I use a separate model for the forward-looking question, such as whether the temperature will cross a parcel's work limit in the next 72 hours. The dashboard presents that probability alongside the open violation count. The two measures can move independently.

I use aligned satellite scenes to flag visible change and preserve an annotated frame with imagery alerts. Cloud cover, illumination and seasonal vegetation can all change RGB values. A field inspection or higher-resolution aerial evidence is needed to distinguish vegetation clearing, earthworks and construction.

The interface uses Leaflet. FastAPI serves the same-origin dashboard and API, and SQLite transactions keep each account's parcel records and audit entries together. Model fitting and inference share feature-building code. Local credentials, operator records and training caches are excluded from Git.

## Model training and evaluation

| Model | Training target | Chronological holdout |
| --- | --- | --- |
| Heat risk, RandomForestClassifier | Any 6-hour temperature over a selected 25, 30 or 35 °C limit during the following 72 hours. Inputs include location, season, hour, current temperature and four prior threshold checks. | 75,400 training rows and 24,150 test rows across 50 US grid cells. Average precision 0.951 with 13.8% positive examples. Brier score 0.026 versus 0.076 for persistence. |
| Soil and heavy-rain risk, multi-output RandomForestClassifier | A 72-hour topsoil threshold crossing and a seven-day inspection signal based on at least 25 mm of rain in a future 24-hour period. | Six US sites. 34,848 training rows from 2019–2022 and 8,592 test rows from 2023. Soil average precision 0.955 at 29.8% prevalence. Heavy-rain average precision 0.081 at 6.1% prevalence. |
| Regional imagery risk, RandomForestClassifier | Whether at least 25% of the regional frame exceeds the RGB change threshold next week. | Four sites, 332 training pairs and 116 held-out pairs. Average precision 0.742 at 66.4% prevalence. |
| Lease clause routing, TF-IDF and logistic regression | Size/boundary, operational restriction, term/deadline, environmental obligation, or other. | Fitted on 25 authored example clauses. Accuracy on an independent lease corpus is unmeasured. |

The weather and imagery outcomes are derived from observations. The training inputs contain no adjudicated lease violations. The heat dataset uses coarse WeatherBench 2 ERA5 grid cells. The soil model uses ERA5-Land soil and temperature with ERA5 rainfall. Both evaluations hold out later dates at the same locations. New sites, arbitrary lease thresholds and actual site-level readings require independent validation. Soil training covers below 0.12/0.18 and above 0.25/0.32 m³/m³. Operational soil inputs approximate the top 7 cm from forecast-model layers. The heavy-rain screen has weak predictive performance and measures a weather precursor to erosion. The satellite model's small evaluation set and cloud effects limit the interpretation of its probabilities.

Reproduce each fit from the repository root:

```sh
python -m ml.clauses
python -m ml.train
python -m ml.soil
python -m ml.imagery
```

Each script saves its model under `ml/`. The environmental training scripts also save evaluation JSON with data sources, time windows and row counts. The imagery script caches valid frames under `data/training-imagery/`. Public providers may revise records or omit individual acquisitions.

## Verification

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
```

The integration tests exercise document extraction, rule evaluation, duplicate feed handling, authenticated isolation, KML geometry, image evidence, resolution and report downloads.

## Credits and data

The July 2025 pitch credits Bobby Bilo, Yohan Kim, Hasan Onallah and Arnav Nair. This repository contains a new implementation of the pitch workflow.

Weather data comes from [WeatherBench 2](https://weatherbench2.readthedocs.io/en/latest/data-guide.html), [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api), [weather forecasts](https://open-meteo.com/en/docs) and [air quality](https://open-meteo.com/en/docs/air-quality-api). Satellite imagery, including the sign-in image, comes from [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/). Map tiles use [OpenStreetMap contributors](https://www.openstreetmap.org/copyright). Leaflet's license is included in `web/vendor/`.
