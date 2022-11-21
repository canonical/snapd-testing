#!/bin/bash	

git_version=$1
github_url=${2:-https://github.com/snapcore/snapd.git}

# Clean version when it is like 16-2.38
git_version="${git_version//16-/}"
# Clean version when it is like 2.38~pre1
git_version=$(echo "$git_version" | cut -f1 -d "~")

if [ -n "$HTTPS_PROXY" ]; then
    git config --global http.sslVerify false
    git config --global --unset http.proxy
    git config --global https.proxy "$HTTPS_PROXY"
fi

if git ls-remote --tags "$github_url" "$git_version" | grep -q "$git_version"; then
    echo "$git_version"
elif git ls-remote --heads "$github_url" "release/$git_version" | grep -q "$git_version"; then
    echo "release/$git_version"
elif echo "$git_version" | grep -q '+git'; then
    echo "$git_version" | sed 's/\+git.*//'
else
    echo "master"
fi
