#!/bin/bash

git for-each-ref 'refs/heads/*' | \
while read rev type ref; do
    branch=$(expr "$ref" : 'refs/heads/\(.*\)' )
    revs=$(git rev-list $rev..master)
    echo "checking branch: $branch"
    if [ -n "$revs" ]; then
        echo $branch needs update
        git diff --summary --shortstat -M -C -C $rev master
        git checkout $branch
        git rebase master
        git push -f
    else
        echo "no rebase needed"
    fi
done

git checkout master

