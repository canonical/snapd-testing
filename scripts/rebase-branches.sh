#!/bin/bash

BRANCH_PREFIX=${1:-""}

git for-each-ref 'refs/heads/*' | \
while read rev type ref; do
    branch=$(expr "$ref" : 'refs/heads/\(.*\)' )
    revs=$(git rev-list $rev..master)
    echo "checking branch: $branch"
    if [[ $branch != "$BRANCH_PREFIX"* ]]; then
        echo "branch: $branch doesn't match the specified prefix $BRANCH_PREFIX"
        continue
    fi
    if [ -n "$revs" ]; then
        echo $branch needs update
        git checkout $branch
        git rebase master
        git push -f
    else
        echo "no rebase needed"
    fi
done

git checkout master

