# Measured parcel terrain

FastAPI sends the selected parcel polygon to SQS when its owner clicks **Load measured terrain**. Lambda requests floating-point USGS 3DEP samples over the bounding rectangle, clips the mesh to the parcel, and writes the sampled grid, GLB and completion status to a private S3 bucket. The API caches completed results in SQLite so the model remains available on subsequent views. Site images are applied locally.

From the repository root, set AWS_REGION and STAGING_BUCKET to the deployment region and an existing private staging bucket. Package the x86_64 Linux wheels in a temporary directory:

```sh
build_dir=$(mktemp -d)
python3 -m pip install --target "$build_dir/libs" --platform manylinux2014_x86_64 --python-version 3.12 --implementation cp --abi cp312 --only-binary=:all: --no-deps --no-compile -r infra/terrain/requirements-lambda.txt
python3 infra/terrain/package.py "$build_dir/libs" "$build_dir/worker.zip"
cp infra/terrain/template.yaml "$build_dir/template.yaml"
aws cloudformation package --region "$AWS_REGION" --s3-bucket "$STAGING_BUCKET" --template-file "$build_dir/template.yaml" --output-template-file "$build_dir/packaged.yaml"
aws cloudformation deploy --region "$AWS_REGION" --stack-name parcelsite-terrain --template-file "$build_dir/packaged.yaml" --capabilities CAPABILITY_IAM
aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name parcelsite-terrain --query 'Stacks[0].Outputs'
```

Set PARCELSITE_TERRAIN_REGION, PARCELSITE_TERRAIN_QUEUE (Queue output) and PARCELSITE_TERRAIN_BUCKET (Bucket output) before starting FastAPI. The app identity needs sqs:SendMessage on the queue, s3:GetObject for terrain/* and s3:ListBucket on the bucket to distinguish an absent status object. Lambda has its own execution role.

Grid spacing in the viewer describes the exported samples. Source survey resolution varies by location. Terrain follows USGS ground heights at true vertical scale. When coverage is incomplete the worker reports a failure.
