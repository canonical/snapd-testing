[![Build Status][travis-image]][travis-url]

git-cron triggers spread tasks in response to events.

The main use of git-cron is currently making sure that [snapd](https://github.com/snapcore/snapd) works over time after certain external conditions have changed. We are using spread for snapd development, with each pull request we are able to run a suite of checks that exercise the product in a lot of different ways, so that we can be confident that the new changes won't break the existing features. However, given the intrinsic distributed nature and complexity of the snapd environment, there are additional changes that can make the whole thing fail: what happens if the packaging of a new core snap doesn't play well with the rest of the system? will snapd keep working after a rollout of a store endpoint? will an ubuntu-core image made available before publishing work as expected? When any of these events happens, git-cron triggers a customized execution of the snapd suite so we can be confident that things are still working.

Lately we have begun using git-cron for general automated tasks, like keeping snaps in sync between different stores or pushing augmented versions of repos to different locations. Also, we are experimenting with defining task pipelines by making the trigger condition of a job depend on the results of another one.

# Workflow

git-cron was developed to watch specific external resources (currently only web-based) and trigger customized test executions when changes on those are detected. It follows a pull-based model, in which from a central point, where the git-cron snap is deployed, the various predefined resources are polled and, when changes are detected, snapd's source code is cloned, the required customizations are done and the suite execution is triggered.

## Predefined resources

git-cron relies heavily on git infrastructure for handling its config and state storage, not only on the versioned files themselves but on git-specific features too. That makes it very easy to see what's going on using travis's dashboards. For each watched resource a git branch is defined, and the files for checking the external resource and customizing the test execution are added to that branch. This way we can have separate branches for checking core snap versions on the different channels, store endpoints or SRU bugs being filed, the git-cron agent just need to checkout each of them and apply the same set of actions to the specific files for each resource. [This](https://travis-ci.org/snapcore/git-cron/branches) is how the executions look like in travis for each of the branches.

## Changes detection

The way git-cron detects and stores changes is defined in the branch-specific `trigger` file. It is a plain shell file with two variables, `pattern_extractor` and `message`. `pattern_extractor` contains a shell command that scrapes an external resource and returns a meaningful value about the resource's status. For instance, in the core snap case this variable could contain a curl command that asks the store's search endpoint for the snap details and extracts from the returned JSON the `revision` field.

After retrieving the state value from the external resource git-cron compares it with the previous stored state. The state is stored as a commit in the resource branch history. The commit text is compossed of the `message` entry in the `trigger` file, plus the current state scrapped from the external resource. So, for knowing if there have been actual changes in the resource, git-cron iterates through the commit history in the branch until it finds a commit message that matches "`message` (old_value)", then compares the new value with old_value and, if they are different, a new commit is issued with a message like "`message` (new_value)".

For example, say we are tracking changes of core's snap revision on the beta channel. We could use an `trigger` file like this:

    pattern_extractor="curl -s -H \"X-Ubuntu-Architecture: amd64\" https://search.apps.ubuntu.com/api/v1/package/core/beta | jq -j '.revision'"
    message="New amd64 core snap in beta channel"

When git-cron checks this branch, suppose that from the pattern extractor it gets a revision number of 550. Then it checks the log of commits from this branch, if `git log --pretty=oneline` looks like:

    9c8bb698f2c7982cd31138f9451858f9d834a034 fix channel name [skip ci]
    e2dbbd66ad250df912384f90367505d0f4e4211e New OS snap in beta channel (549)
    53ea12890884e73e2705e2621ada9997fafc9bc3 new run-checks file [skip ci]
    12cdb134b31e31ad4cd004089f5c9c935dea9ff5 New OS snap in beta channel (548)

then git-cron will iterate through the commit list until it finds the first entry which matches `$message` from the trigger file and extract the old version from there, in this case 549. Since the two versions are different, it will add a new empty commit in the current branch with the message `New OS snap in beta channel (550)`.
[This](https://travis-ci.org/snapcore/git-cron/builds) is how the different resource versions look like in the travis build history.

## Execution triggering

git-cron is wired with travis trough the `.travis.yml` file, once a change is detected and a new commit is performed, travis takes over and triggers the subequent sequence of commands: get snapd soource, customize it and launch spread.

## snapd's cloning

After a new change is detected the full snapd repo is cloned from master into the `target` directory. spread will be executed from that directory after the suite customization.

## Suite customization

There are currently two ways of customizing the spread execution, through environment variables (defined in the `custom/env.sh` file of each resource branch) and overwriting files of snapd's source tree (done using the `actions.sh` file and customized versions of snapd's files under the `custom` directory). Both methods have pros and cons, with environment variables we don't need to maintain additional files on git-cron but we need to make snapd'd suite be aware of these env vars, the overwrite method gives us full flexibility but we need to keep the diverged files up to date.

# Packaging and deployment

git-cron is distributed as a snap, see the [snapcraft.yaml file](https://github.com/snapcore/git-cron/blob/master/snapcraft.yaml) for its definition. It embeds curl and a git client. The authentication for github can be set up using a configuration hook once the snap is installed:

    snap set git-cron username=<username> password=<password>
    snap set git-cron project-url=<project_url> project-name=<project_name> jobs-dir=<jobs_dir>

with valid credentials of an user with push privileges for github's `snapd/git-cron` repo.

# Watched resources

Resource | Value Fetched | Branch | trigger File
-------- | ------------- | ------ | -------------
core snap, edge channel, amd64 arch | revision | snapd-core-edge | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-edge/trigger)
core snap, beta channel, amd64 arch | revision | snapd-core-beta | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-beta/trigger)
core snap, candidate channel, amd64 arch | revision | snapd-core-candidate | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-candidate/trigger)
core snap, stable channel, amd64 arch | revision | snapd-core-stable | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-stable/trigger)
core snap, edge channel, i386 arch | revision | snapd-core-i386-edge | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-i386-edge/trigger)
core snap, beta channel, i386 arch | revision | snapd-core-i386-beta | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-i386-beta/trigger)
core snap, candidate channel, i386 arch | revision | snapd-core-i386-candidate | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-i386-candidate/trigger)
core snap, stable channel, i386 arch | revision | snapd-core-i386-stable | [trigger](https://github.com/snapcore/git-cron/blob/snapd-core-i386-stable/trigger)
core snap, edge channel for reexec from 2.21 to edge, amd64 | revision | snapd-reexec-2.21-vs-edge | [trigger](https://github.com/snapcore/git-cron/blob/snapd-reexec-2.21-vs-edge/trigger)
kernel snap, edge channel, amd64 arch | revision | kernel-edge-amd64 | [trigger](https://github.com/snapcore/git-cron/blob/kernel-edge-amd64/trigger)
kernel snap, beta channel, amd64 arch | revision | kernel-edge-amd64 | [trigger](https://github.com/snapcore/git-cron/blob/kernel-beta-amd64/trigger)
kernel snap, candidate channel, amd64 arch | revision | kernel-edge-amd64 | [trigger](https://github.com/snapcore/git-cron/blob/kernel-candidate-amd64/trigger)
production store, CPI endpoint | X-Bzr-Revision-Number | snapd-production-store-cpi | [trigger](https://github.com/snapcore/git-cron/blob/snapd-production-store-cpi/trigger)
production store, SAS endpoint | X-Vcs-Revision | snapd-production-store-sas | [trigger](https://github.com/snapcore/git-cron/blob/snapd-production-store-sas/trigger)
production store, SCA endpoint | X-Bzr-Revision-Number | snapd-production-store-sca | [trigger](https://github.com/snapcore/git-cron/blob/snapd-production-store-sca/trigger)
production store, SSO endpoint | X-Bzr-Revision-Number | snapd-production-store-sso | [trigger](https://github.com/snapcore/git-cron/blob/snapd-production-store-sso/trigger)
staging store, CPI endpoint | X-Bzr-Revision-Number | snapd-staging-store-cpi | [trigger](https://github.com/snapcore/git-cron/blob/snapd-staging-store-cpi/trigger)
staging store, SAS endpoint | X-Vcs-Revision | snapd-staging-store-sas | [trigger](https://github.com/snapcore/git-cron/blob/snapd-staging-store-sas/trigger)
staging store, SCA endpoint | X-Bzr-Revision-Number | snapd-staging-store-sca | [trigger](https://github.com/snapcore/git-cron/blob/snapd-staging-store-sca/trigger)
staging store, SSO endpoint | X-Bzr-Revision-Number | snapd-staging-store-sso | [trigger](https://github.com/snapcore/git-cron/blob/snapd-staging-store-sso/trigger)
refresh core snap from stable to edge, amd64 arch | revision | core-amd64-refresh-to-edge | [trigger](https://github.com/snapcore/git-cron/blob/core-amd64-refresh-to-edge/trigger)
refresh core snap from stable to edge, i386 arch | revision | core-i386-refresh-to-edge | [trigger](https://github.com/snapcore/git-cron/blob/core-i386-refresh-to-edge/trigger)
refresh core snap from stable to candidate, amd64 arch | revision | core-amd64-refresh-to-edge | [trigger](https://github.com/snapcore/git-cron/blob/core-amd64-refresh-to-candidate/trigger)
refresh core snap from stable to candidate, i386 arch | revision | core-i386-refresh-to-edge | [trigger](https://github.com/snapcore/git-cron/blob/core-i386-refresh-to-candidate/trigger)
refresh core snap from stable to beta, amd64 arch | revision | core-amd64-refresh-to-edge | [trigger](https://github.com/snapcore/git-cron/blob/core-amd64-refresh-to-beta/trigger)
refresh core snap from stable to beta, i386 arch | revision | core-i386-refresh-to-edge | [trigger](https://github.com/snapcore/git-cron/blob/core-i386-refresh-to-beta/trigger)
snapd deb package in proposed pocket, trusty | version | snapd-trusty-sru | [trigger](https://github.com/snapcore/git-cron/blob/snapd-trusty-sru/trigger)
snapd deb package in proposed pocket, xenial | version | snapd-xenial-sru | [trigger](https://github.com/snapcore/git-cron/blob/snapd-xenial-sru/trigger)
snapd deb package in proposed pocket, yakkety | version | snapd-yakkety-sru | [trigger](https://github.com/snapcore/git-cron/blob/snapd-yakkety-sru/trigger)
snapd deb package in proposed pocket, zesty | version | snapd-zesty-sru | [trigger](https://github.com/snapcore/git-cron/blob/snapd-zesty-sru/trigger)

# Pipelines

Resource | Value Fetched | Branch | trigger File
-------- | ------------- | ------ | -------------
analyze edge snapd builds | last green build number | snapd-analyze-build | [trigger](https://github.com/snapcore/git-cron/blob/snapd-analyze-build/trigger)
sync edge core snap from production to staging, amd64 | last green build number after new publication on edge | core-amd64-staging-sync | [trigger](https://github.com/snapcore/git-cron/blob/core-amd64-staging-sync/trigger)
sync edge core snap from production to staging, i386 | last green build number after new publication on edge | core-i386-staging-sync | [trigger](https://github.com/snapcore/git-cron/blob/core-i386-staging-sync/trigger)
sync core snap source from gh to lp after merge and green build | last green build number (core snap) | core-gh-lp-sync | [trigger](https://github.com/snapcore/git-cron/blob/core-gh-lp-sync/trigger)
sync snapd-vendor with snapd after merge and green build | last green build number (snapd master) | snapd-vendor-sync | [trigger](https://github.com/snapcore/git-cron/blob/snapd-vendor-sync/trigger)

# Daily builds

Description | Branch
----------- | ------
Nightly suite | snapd-nightly-suite
Execution against amd64 ubuntu-core image unmodified | built-image-amd64-smoketest
Reexec disabled | snapd-reexec-disabled

[travis-image]: https://travis-ci.org/snapcore/git-cron.svg?branch=master
[travis-url]: https://travis-ci.org/snapcore/git-cron?branch=master
