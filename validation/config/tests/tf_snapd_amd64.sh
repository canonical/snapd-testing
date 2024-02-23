#!/bin/bash

export VERSION=${VERSION:-"uc16"}
export ARCH=${ARCH:-"amd64"}

export SPREAD_TESTS=${SPREAD_TESTS:-"testflinger:ubuntu-core-16-64:tests/nested/core/full"}
export SKIP_TESTS=${SKIP_TESTS:-"tests/main/interfaces-many-snap-provided,tests/main/interfaces-many-core-provided,tests/core/failover,tests/main/store-state"}
