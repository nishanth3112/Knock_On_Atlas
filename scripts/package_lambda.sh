#!/usr/bin/env bash
# Build the one code zip all four Lambdas run (arm64, Python 3.12): lambda-dist/lambda.zip.
#
# Dependencies come from uv.lock without the dev, ingest (polars) and embed
# (fastembed) groups: the Lambdas search with BM25 alone. Fails if the unzipped
# bundle passes Lambda's 250 MB limit.
#
# Usage: scripts/package_lambda.sh
set -euo pipefail
cd "$(dirname "$0")/.."

out=lambda-dist
rm -rf "$out" && mkdir -p "$out/bundle"
uv export --frozen --no-dev --no-group ingest --no-group embed \
  --no-emit-project --quiet -o "$out/requirements.txt"
uv pip install --quiet --target "$out/bundle" \
  --python-platform aarch64-manylinux_2_28 --python-version 3.12 \
  --only-binary :all: -r "$out/requirements.txt"
cp -R src/knock_on_atlas "$out/bundle/"
find "$out/bundle" -type d \( -name __pycache__ -o -name tests -o -name test \) \
  -prune -exec rm -rf {} +
(cd "$out/bundle" && zip -qr9 ../lambda.zip .)

limit=262144000  # Lambda's unzipped limit, in bytes
unzipped=$(python3 -c 'import os, sys; print(sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(sys.argv[1]) for f in fs))' "$out/bundle")
zipped=$(wc -c <"$out/lambda.zip" | tr -d ' ')
echo "lambda.zip: ${zipped} bytes zipped, ${unzipped} bytes unzipped (limit ${limit})"
if [ "$unzipped" -gt "$limit" ]; then
  echo "bundle is over Lambda's unzipped limit" >&2
  exit 1
fi
