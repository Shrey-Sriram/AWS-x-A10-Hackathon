# Parcelsite: the stack behind one inspection

A manager uploads an exhibit, checks its mapped boundary, and confirms a lease condition. The server refreshes observations for the parcel, compares the readings with that condition, and logs an alert for review. Forecasts tell the manager where to look next. A separate studio helps explain or illustrate a proposed change.

| Layer | Technology | What Parcelsite uses it for |
|---|---|---|
| Browser | HTML, CSS, JavaScript, Leaflet, OpenStreetMap, model-viewer | Dashboard, clickable parcel boundaries, map tiles, interactive GLB terrain and objects |
| API and records | Python, FastAPI, Uvicorn, SQLite | Serve browser and JSON API; store accounts, sessions, parcels, leases, measurements, alerts and review history |
| Lease import | pypdf, DOCX XML, GeoJSON/KML parsers | Extract selectable text and explicit boundary coordinates; let users preview geometry and review proposed rules. No OCR for scans |
| Monitoring | Python scheduler, Open-Meteo, NASA GIBS | Refresh weather, soil and air-quality observations; compare dated satellite frames; evaluate confirmed rules |
| Predictive ML | scikit-learn, NumPy, joblib | Fit, save and load the four classifiers described below. WeatherBench 2 ERA5 supplies historical heat training data |
| Terrain | USGS 3DEP, Shapely, NumPy, trimesh, Pillow | Sample ground heights, clip a mesh to the parcel including holes, apply optional imagery and export a GLB |
| AWS integration | boto3, Bedrock, SageMaker AI | Request written briefs, image or mesh inference, and GPU training from the Python server or training launcher |
| Asynchronous terrain | SQS, Lambda, S3 | Queue a parcel job, build its terrain in a worker, store status, elevation and GLB outputs |
| Infrastructure | IAM, CloudFormation (SAM template), CloudWatch Logs, ECR | Grant permissions, deploy terrain resources, diagnose jobs, and supply the SageMaker training container |

## Seven model workflows: what each does

Counting one model per product task gives seven: three environmental random forests, one lease classifier, and three generative models. This counts the two soil/rain outputs as one fitted forest and the three Shap-E training rounds as versions of one model. CLIP and the mesh decoder are components of Shap-E.

### Trained for Parcelsite
- **1. Heat forest (80 trees).** Estimates the chance of crossing a selected temperature threshold within 72 hours. Helps a manager decide when to check a heat-sensitive lease condition.
- **2. Soil and rain forest (64 trees).** One multi-output classifier produces two scores: a soil threshold crossing within 72 hours and a heavy-rain screen over seven days. The rain result is experimental.
- **3. Satellite change forest (64 trees).** Estimates whether a regional image will show substantial change next week. The percentage that already changed comes from comparing two NASA images directly.
- **4. Lease clause classifier (logistic regression).** TF-IDF converts clause words and phrases into numeric features. The classifier suggests one of five categories. A separate parser proposes thresholds for a person to review.

### Pretrained models used in the app
- **5. Amazon Nova Lite.** Bedrock supplies a written management brief using saved readings, rules and alerts. Parcelsite does not train Nova or use it to decide whether a breach occurred.
- **6. FLUX.2-klein-4B.** A SageMaker inference endpoint turns a proposed feature description into an illustrative 2D image. Parcelsite does not train this model.
- **7. OpenAI Shap-E text300M.** A SageMaker endpoint turns a prompt into an illustrative 3D object. CLIP encodes text, a diffusion model samples the object representation, and a decoder produces the mesh. Our GPU experiment tunes only the text projection; a training checkpoint does not automatically update the serving endpoint.

### What uses no trained model?
The exhibit parser extracts coordinates. Shapely and mesh code clip measured elevation to the polygon. A rule comparison opens an alert when the observed value crosses a confirmed threshold. These steps remain inspectable even if all three generative services are unavailable.
