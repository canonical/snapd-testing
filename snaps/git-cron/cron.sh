#!/bin/bash

INIT_DIR="$(pwd)"
STEP_TIME=600
RECOVER_TIME=60
FINAL_TIME=0
INITIAL_TIME=0
CLEAN_ITERS=12

record_new_value(){
    message=$1
    new_value=$2
    git commit --allow-empty -m "$message ($new_value)"
    git push -f
}

check(){
    branch=$1
    jobs_dir="$BASE_DIR/$jobs_dir/$branch"
    if [ ! -d "$jobs_dir" ]; then
        echo "Jobs directory $jobs_dir does not exist, skipping..."
        return
    fi
    echo "Checking trigger"
    . "$jobs_dir"/trigger

    if [ -z "$pattern_extractor" -o -z "$message" ]; then
        echo "Required fields pattern_extractor and message not found in ../trigger"
        exit 1
    fi

    new_value=$(eval "$pattern_extractor" | tr -d '\r')
    if [ -z "$new_value" ] || [ "$new_value" = "null" ]; then
        echo "pattern extractor $pattern_extractor could not extract anything"
        exit 1
    fi

    if [ "$manual" = "true" ]; then
        echo "Manual branches are skipped"
        return
    fi

    # Checkout the branch to get last value to compare it there is a change
    git checkout "$branch"
    log_entry=$(git log HEAD~0 --pretty=%B | grep "^$message" | head -n1)
    if [ -z "$log_entry" ]; then
        # no previous recorded value found
        record_new_value "$message" "$new_value"
    else
        old_value=$( echo "$log_entry" | sed -e "s|$message (\(.*\))|\1|")
        # check if new value needs to be recorded
        if [ "$new_value" != "$old_value" ]; then
            record_new_value "$message" "$new_value"
        fi
    fi
    git checkout master
}

if [ -n "$HTTPS_PROXY" ] && ! git config -l | grep -q https.proxy; then
    echo "Setting https proxy for git"
    git config --global https.proxy "$HTTPS_PROXY"
fi

if [ -n "$HTTP_PROXY" ] && ! git config -l | grep -q http.proxy; then
    echo "Setting http proxy for git"
    git config --global http.proxy "$HTTP_PROXY"
fi

iter=0
while true; do
    echo "Moving to init dir"
    cd "$INIT_DIR"

    iter=$((iter+1))
    echo "Iteration $iter started"

    # run checker on each branch
    if [ ! -f "$SNAP_COMMON/git-credentials" ]; then
        echo "No credentials file found, please run 'snap set git-cron username=<username> password=<password>' with valid credentials of a github user with push priveleges for the snapcore/spread-cron repository."
        sleep "$RECOVER_TIME"
        continue
    fi
    if [ ! -f "$SNAP_COMMON/git-project" ]; then
        echo "No project file found, please run 'snap set git-cron project-url=<project_url> project-name=<project_name> jobs-dir=<jobs_dir>' with valid project information"
        sleep "$RECOVER_TIME"
        continue
    fi

    project_url=$(grep project-url "$SNAP_COMMON/git-project" | cut -d "=" -f2)
    project_name=$(grep project-name "$SNAP_COMMON/git-project" | cut -d "=" -f2)
    jobs_dir=$(grep jobs-dir "$SNAP_COMMON/git-project" | cut -d "=" -f2)

    if [ -z "$project_url" ] || [ -z "$project_name" ] || [ -z "$jobs_dir" ]; then
        echo "Values for project-url, project-name and jobs-dir cannot be empty"
        sleep "$RECOVER_TIME"
        continue
    fi

    BASE_DIR="$SNAP_COMMON/$project_name"
    if [ ! -d "$BASE_DIR" ]; then
        echo "Cloning $project_url to $BASE_DIR" 
        git clone "$project_url" "$BASE_DIR"
    fi
    if [ ! -d "$BASE_DIR" ]; then
        sleep "$RECOVER_TIME"
        continue
    fi
    echo "Moving to base dir"
    cd "$BASE_DIR"

    INITIAL_TIME=$SECONDS
    git remote prune origin
    git fetch origin

    # Clean all the local branches but master
    for remote in $(git branch -r); do
        if [ "$remote" != "origin/HEAD" ] && [ "$remote" != "origin/master" ] && [ "$remote" != "->" ]; then
            branch="${remote#origin/}"
            git branch -D "$branch"
        fi
    done

    # Fetch all the remote branches
    git pull --all

    # Check for each branch if there ara changes
    for remote in $(git branch -r); do
        if [ "$remote" != "origin/HEAD" ] && [ "$remote" != "origin/master" ] && [ "$remote" != "->" ]; then
            branch="${remote#origin/}"
            ( check "$branch" )
            git checkout master
        fi
    done
    FINAL_TIME=$SECONDS

    # Clean local repository after $CLEAN_ITERS iterations
    # This is done to avoid races that could happen when there are remote changes
    if [ "$iter" -ge "$CLEAN_ITERS" ]; then
        echo "Cleaning local repository: $BASE_DIR"
        iter=0
        rm -rf "$BASE_DIR"
    fi

    # Wait for next iteration
    echo "Waiting for next iteration $((STEP_TIME - FINAL_TIME + INITIAL_TIME)) seconds"
    sleep $((STEP_TIME - FINAL_TIME + INITIAL_TIME))
done
