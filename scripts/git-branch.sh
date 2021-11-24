#!/bin/bash	

VERSION=$1
GITHUB_URL=${2:-https://github.com/snapcore/snapd.git}

# Prepare the version format
VERSION="${VERSION//16-/}"
VERSION=$(echo "$VERSION" | cut -f1 -d "~")

if [ -n "$HTTPS_PROXY" ]; then
    git config --global --unset http.proxy
    git config --global https.proxy "$HTTPS_PROXY"
fi

if git ls-remote --tags "$GITHUB_URL" "$VERSION" | grep -q "$VERSION"; then
    echo "$VERSION"
elif git ls-remote --heads "$GITHUB_URL" "release/$VERSION" | grep -q "$VERSION"; then
    echo "release/$VERSION"
elif echo "$VERSION" | grep -q '+git'; then
	echo "$VERSION" | sed 's/\+git.*//'
else
    echo "master"
fi
