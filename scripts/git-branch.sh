#!/bin/bash	

VERSION=$1
GITHUB_URL=${2:-https://github.com/snapcore/snapd.git}

# Clean version when it is like 16-2.38
VERSION="${VERSION//16-/}"
# Clean version when it is like 2.38~pre1
VERSION=$(echo "$VERSION" | cut -f1 -d "~")

if git ls-remote --tags "$GITHUB_URL" "$VERSION" | grep -q "$VERSION"; then
    echo "$VERSION"
elif git ls-remote --heads "$GITHUB_URL" "release/$VERSION" | grep -q "$VERSION"; then
    echo "release/$VERSION"
elif echo "$VERSION" | grep -q '+git'; then
	echo "$VERSION" | sed 's/\+git.*//'
else
    echo "master"
fi
