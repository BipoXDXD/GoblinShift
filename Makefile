.PHONY: image image-release up
IMAGE_TAG ?= dev

# Desenvolvimento nativo; não herda DOCKER_DEFAULT_PLATFORM de outra publicação.
image:
	./scripts/docker-build.sh local -f docker/app.Dockerfile -t ghcr.io/bipoxdxd/goblinshift:$(IMAGE_TAG) .
	./scripts/docker-build.sh local -f docker/executor.Dockerfile -t ghcr.io/bipoxdxd/goblinshift-executor:$(IMAGE_TAG) .
	./scripts/docker-build.sh local -f docker/web.Dockerfile -t ghcr.io/bipoxdxd/goblinshift-web:$(IMAGE_TAG) .

image-release:
	mkdir -p .docker-artifacts
	./scripts/docker-build.sh oci .docker-artifacts/goblinshift-app.tar -f docker/app.Dockerfile -t ghcr.io/bipoxdxd/goblinshift:$(IMAGE_TAG) .
	./scripts/docker-build.sh oci .docker-artifacts/goblinshift-executor.tar -f docker/executor.Dockerfile -t ghcr.io/bipoxdxd/goblinshift-executor:$(IMAGE_TAG) .
	./scripts/docker-build.sh oci .docker-artifacts/goblinshift-web.tar -f docker/web.Dockerfile -t ghcr.io/bipoxdxd/goblinshift-web:$(IMAGE_TAG) .

up:
	./scripts/docker-build.sh compose up --build
