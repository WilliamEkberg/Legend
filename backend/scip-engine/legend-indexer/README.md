# Legend Indexer (SCIP Engine)

Rust CLI tool that detects programming languages in a codebase and runs the appropriate [SCIP](https://github.com/sourcegraph/scip) indexers to produce raw `.scip` protobuf files.

Supports: TypeScript, JavaScript, Python, C#, Java, Kotlin, Scala, Go, and PHP.

## Quick Start (Pull from Registry)

The fastest way to get started is pulling the pre-built image from GitHub Container Registry:

```bash
# Pull pre-built image (seconds, not minutes)
./docker-pull.sh

# Or manually:
docker pull ghcr.io/wuv-ogmem/scip-engine:latest
docker tag ghcr.io/wuv-ogmem/scip-engine:latest scip-engine
```

## Usage

```bash
docker run --rm \
  -v "/path/to/codebase:/workspace" \
  -v "$(pwd)/output:/output" \
  scip-engine /workspace --output /output
```

## Building Locally

If you need to build locally (e.g., for development or if registry is unavailable):

```bash
# Build both base and final image (~15-20 min first time)
./docker-build.sh

# Or step by step:
./build-base.sh      # Build base image with language runtimes
docker build -t scip-engine .  # Build final image
```

## Pushing to Registry

For maintainers who need to push new images:

```bash
# Login to GitHub Container Registry
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin

# Build and push multi-arch images (amd64 + arm64)
./docker-build.sh --push

# Or push just base image (after runtime updates):
./build-base.sh --push
```

CI automatically builds and pushes on commits to main.

## Project Structure

```
src/
├── main.rs         # CLI entry point
├── lib.rs          # Library root
├── config.rs       # Configuration
├── detect.rs       # Language detection
└── orchestrate.rs  # SCIP indexer execution
```

## Testing

```bash
docker build -f Dockerfile.test -t scip-engine-test .
docker run --rm scip-engine-test
```

## Known Limitations

- **Python indexing on very large codebases (10K+ files):** The Python SCIP indexer (`scip-python`) runs on Node.js and may OOM on the default 6GB heap limit. The pipeline continues with available data. Set `SCIP_MEMORY_LIMIT=12288` to increase to 12GB:
  ```bash
  SCIP_MEMORY_LIMIT=12288 ./legend.sh /path/to/large-python-repo
  ```

See the [root README](../README.md) for full documentation, CLI reference, and script usage.
