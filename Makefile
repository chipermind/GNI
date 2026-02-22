# Minimal Makefile for deploy archive and common tasks.
# Run from repo root.

.PHONY: deploy-archive deploy_bundle.tar.gz smoke-desk

deploy-archive: deploy_bundle.tar.gz

smoke-desk:
	DESK24H_ENABLED=0 python -m desk.scheduler --dry-run --type PANORAMA_0900

deploy_bundle.tar.gz:
	bash deploy/scripts/build_archive.sh
