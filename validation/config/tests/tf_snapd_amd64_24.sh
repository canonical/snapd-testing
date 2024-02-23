#!/bin/bash

export VERSION=${VERSION:-"uc24"}
export ARCH=${ARCH:-"amd64"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-24-64:tests/nested/core/full"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/core/uc20-recovery,tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/persistent-journal-namespace,tests/main/store-state"}
