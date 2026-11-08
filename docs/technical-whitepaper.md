# Parcelsite
## From a lease clause to a field inspection

**Technical whitepaper | November 2026 | Hackathon prototype**

A land manager needs to know which parcel requires attention, which lease condition applies, and what evidence to collect. Parcelsite links the text of a lease to a mapped parcel, dated environmental readings, and a record of the manager's response.

### A decision the system supports
Suppose a lease requires work to stop above 35 °C. A reading near the parcel reaches 37 °C. The manager sees the field, the clause, and the reading together and contacts the tenant to establish whether work took place. Recording the tenant's response closes the loop. The temperature crossing establishes a reason to check. Establishing a breach requires additional facts.

### What the prototype demonstrates
A user can import a selectable-text lease exhibit, preview its boundary, confirm a measurable rule, refresh environmental observations, and resolve an alert with a note. The same parcel can be explored in 3D using measured ground elevations. A separate studio produces written briefs and illustrative concepts.

### Scope of the evidence
The evaluation covers software workflows, historical environmental prediction tasks, and a small text-to-3D experiment. The Idaho demonstration parcels are fictional. Commercial demand, inspection time saved, and accuracy on adjudicated lease violations have yet to be measured. Idaho's grazing program documents lease monitoring and site inspections as real management duties [1].

---
## Lease ingestion and parcel records

### Reading the exhibit
FastAPI accepts a text-based PDF or Word document. The PDF reader extracts text page by page. The boundary parser looks for one embedded WGS84 GeoJSON Polygon or MultiPolygon, or a labeled table of longitude and latitude in decimal degrees. A reviewer previews the shape before saving. Multiple boundary candidates require clarification. A scanned exhibit needs OCR before upload. An address or acreage alone cannot define the perimeter.

### Alternative boundary sources
Users can import GeoJSON or KML or draw points on the map. In a file with multiple features, the first polygon represents the parcel and additional polygons may represent land-use layers. The parcel identifier connects the boundary, lease revisions, rules, observations, events, and saved outputs. The signed document or survey remains the reference for boundary accuracy.

### Turning clauses into rules
A TF-IDF and logistic regression classifier routes text into five categories: boundary or size, operations, dates or term, environmental obligations, and other. A parser proposes a measurable quantity, direction, and limit where it recognizes one. The operator reviews the proposal against the original clause before activation. The classifier was fitted on 25 authored examples. Its accuracy on a representative corpus of real leases is unknown.

### Following a result back to its source
The lease revision, clause, observed metric, unit, comparison, limit, value, and observation time let a manager explain why an alert opened. A resolution note records the follow-up. Reports include a weekly audit, violation CSV, scorecard, risk GeoJSON, and heatmap. The UI uses “violation” for rule-triggered events, so a field reviewer should distinguish an automated crossing from a verified contractual finding.

---
## Application architecture and AWS services

The browser uses JavaScript and Leaflet for the portfolio, maps, and parcel review. A bundled model-viewer component displays GLB models. FastAPI serves the frontend and API from the same origin. SQLite stores accounts, sessions, parcel records, measurements, and review history. The demo application runs locally with AWS providing specific remote services.

### AWS service inventory
- **Amazon Bedrock.** Generates on-demand briefs from a snapshot of confirmed rules, recent readings, status, and open alerts. The configured default is Amazon Nova Lite through the Converse API. The user can inspect the source snapshot.
- **Amazon SageMaker AI.** Hosts inference endpoints for Shap-E text-to-3D concepts and FLUX.2-klein-4B image concepts. Managed PyTorch training jobs fine-tune the Shap-E text projection using a GPU.
- **Amazon SQS and AWS Lambda.** SQS accepts the selected parcel's terrain request. Lambda obtains the elevation grid, clips the surface to the geometry, and exports the model. A dead-letter queue handles repeatedly failed deliveries.
- **Amazon S3.** Stores terrain grids, GLBs, job status, training inputs, and model artifacts. The terrain result bucket blocks public access. Completed terrain is also cached in the local application database.
- **AWS CloudFormation and AWS IAM.** A SAM-format CloudFormation template deploys the terrain resources. IAM roles grant the app, worker, and training jobs access to the operations they require.
- **Amazon CloudWatch Logs and Amazon ECR.** CloudWatch captures worker and training logs. SageMaker pulls the PyTorch training image from an AWS-managed ECR repository.

### Runtime dependencies
Monitoring uses Open-Meteo and NASA GIBS. The map uses OpenStreetMap tiles. USGS supplies measured terrain. A user may attach site imagery. Aerial texture and elevation are separate inputs, so a photograph alone does not establish building height. Credentialed AWS requests originate on the server.

---
## Environmental monitoring and evaluation

The embedded scheduler refreshes registered parcels about every 15 minutes after a sweep and checks for a new satellite date. Manual refresh is also available. The three random forests estimate future environmental outcomes. The current observation is evaluated separately against the manager's confirmed rule.

### Heat: will the site cross its limit?
The forest estimates whether any six-hour temperature sample will cross a selected limit within 72 hours. Training used WeatherBench 2 ERA5 temperatures from 50 US grid cells. The chronological split contained 75,400 training rows and 24,150 test rows. Average precision was 0.951 at a positive rate of 0.138. Brier score was 0.026 versus 0.076 for persistence, where lower is better. Coarse grid temperatures can differ from conditions at a work site.

### Soil and rain: which conditions warrant inspection?
One multi-output forest estimates a soil threshold crossing within 72 hours and a separate seven-day heavy-rain signal. Training used 34,848 rows from six sites in 2019-2022 and held out 8,592 rows from 2023. Soil average precision was 0.955 at 0.298 prevalence. Heavy-rain average precision was 0.081 at 0.061 prevalence. The weak rain result limits its usefulness for prioritizing visits. Rainfall alone cannot establish erosion.

### Satellite change: what looked different?
Parcelsite compares NASA Terra MODIS true-color images seven days apart over a common extent. A pixel is marked when its mean absolute RGB channel difference exceeds 35 on the 0-255 scale. The displayed percentage is the share of image pixels marked, including the area around the parcel. A separate forest forecasts whether at least 25% of a regional frame will cross that threshold next week. Its four-site evaluation had 332 training pairs and 116 held-out pairs, with average precision of 0.742 at 0.664 prevalence. Clouds, illumination, and harvest can produce similar changes.

All three holdouts test later dates at training locations. Performance on new properties and the rate of useful inspection alerts require separate field validation [2-4].

---
## Measured parcel terrain

### From geographic coordinates to a mesh
The app projects the lease boundary into local east/south meters using WGS84 scale factors at the parcel centroid. The worker requests a floating-point elevation grid over the bounding rectangle from the USGS 3DEP service. It samples at a spacing appropriate to the parcel size and caps the number of grid cells along the long dimension. The output spacing describes the exported grid. Native survey resolution varies with source coverage [5].

Each grid cell is intersected with the parcel polygon. Triangulation preserves interior exclusions and separate polygons. Interpolated ground heights provide the vertical coordinates. Illustrative sidewalls show the cutaway edge. With no measured grid, the app displays a flat footprint. The dashboard's separately labeled Caldwell study is a demonstration asset.

### Willow Bend Field
Willow Bend is a fictional 17.38-acre Idaho parcel with a northeast notch and an interior cutout. The live SQS and Lambda workflow produced a terrain surface with 5,642 triangles, approximately five-meter sample spacing, and 14.0 meters of modeled relief. The highest-to-lowest difference describes the ground clipped to the fictional boundary. It does not measure the survey accuracy of that boundary.

### Storage and failure handling
The worker writes the elevation grid and GLB before marking a job ready. The authenticated API reads the result and caches it for repeat viewing. The frontend shows the source, output spacing, and relief. Missing coverage or failed requests are surfaced to the user. An uploaded north-up image must be cropped to the bounding rectangle for the texture to align.

### Geometry checks
The workflow suite exercises document ingestion, parcel ownership, monitoring rules and reports. Mesh checks compare the exported ground with the source geometry, including holes and disjoint plots. Two mock PDF exhibits reproduced their local projected boundary to within one millimeter in the export comparison. This tests software conversion only. Seven automated tests passed for the published terrain workflow.

---
## Text-to-3D training and inference

The studio accepts prompts such as “a wooden equipment shed with a pitched roof.” The SageMaker Shap-E endpoint samples an object, decodes a mesh, and returns it for placement in the parcel scene. Size and placement are illustrative. PDF boundary ingestion and measured terrain use geometry and elevation directly. They require no generative training.

### Data and optimization
Cap3D provides captions and Shap-E latent codes for Objaverse objects. The selected records include barns, sheds, tractors, greenhouses, windmills, and water tanks. The example manifests record object IDs, captions, source links, and creator/license metadata. The fine-tuning procedure freezes the pretrained Shap-E text300M model except for the projection that connects text embeddings to the generator [6-8].

Each update draws a noisy version of a target object's latent representation and minimizes mean squared error against its clean latent. Validation uses fixed noise seeds and timesteps for held-out objects, so the starting and ending checkpoint of each round face the same inputs. Scores on different validation sets cannot be compared directly.

### Verified SageMaker results
- **Round 1.** Seven training objects and three held out. After 64 updates, validation error changed from 0.26886 to 0.26765.
- **Round 2.** Thirty-two training objects and seven held out, including the original three. After 192 additional updates, validation error changed from 0.17785 to 0.17686.
- **Round 3.** Same 32 training and seven held-out objects. After 384 additional updates (640 total), held-out error fell from 0.17686 to 0.17549 (0.77%). The experimental checkpoint was not deployed.

### Interpreting the experiment
The held-out metric measures latent denoising. It does not directly score visual realism, dimensions, prompt fidelity, or placement feasibility. For one fixed shed prompt and seed, both checkpoints produced a similar pitched roof and box-shaped walls. Neither sample shows a clear visual advantage. The project has no independent human preference study. Checkpoint selection for a live endpoint should include blinded comparisons across prompts and seeds.

---
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

---
## Operations, evidence, and next validation

### Access and availability
The backend checks parcel ownership before access. Passwords are hashed with a salted password derivation function. Session cookies use HttpOnly and strict SameSite settings, with Secure configurable for HTTPS deployment. Only the signed-in owner initiates a terrain request. AWS receives the selected parcel geometry and USGS receives the bounding rectangle. Authentication, backups, retention, and HTTPS need deployment-specific operating procedures.

The prototype uses an embedded monitoring thread and a local SQLite database. One application worker avoids duplicate polling. External feeds may be stale or unavailable. Consumers should display reading dates and distinguish unavailable data from normal conditions. Real-time GPU endpoints continue to incur hosting charges while deployed.

### Evidence needed for adoption
A field pilot should record how often an alert prompts a useful visit, how often the manager dismisses it, how long each review takes, and what was confirmed on site. Testing on leases, sites, and dates excluded from development would measure transfer to new customers. An independent parcel survey or reference raster is needed to assess absolute geospatial accuracy. For proposed structures, dimensional and planning constraints need their own checks.

### Sources and reproducibility
1. [Idaho Department of Lands grazing program plan](https://www.idl.idaho.gov/wp-content/uploads/sites/2/leasing/grazing/rate/grazing-program-business-plan-final-051915.pdf)
2. [WeatherBench 2 data guide](https://weatherbench2.readthedocs.io/en/latest/data-guide.html)
3. [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api)
4. [NASA GIBS imagery API](https://nasa-gibs.github.io/gibs-api-docs/)
5. [USGS 3DEP products and services](https://www.usgs.gov/3d-elevation-program/about-3dep-products-services)
6. [Shap-E model and source](https://github.com/openai/shap-e)
7. [Cap3D dataset and captions](https://huggingface.co/datasets/tiange/Cap3D)
8. [Objaverse annotations and licenses](https://objaverse.allenai.org/docs/objaverse-1.0/)

The repository includes the FastAPI and browser source, terrain deployment template, evaluation JSON, mock exhibits, and object manifests. Run the workflow tests with python -m unittest discover -s tests. Historical performance figures above come from the checked-in evaluation files.
