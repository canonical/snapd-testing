#!/bin/sh

. "$SCRIPTS_DIR/env/common.sh"

export ARCH=${ARCH:-"amd64"}
export PROJECT=${PROJECT:-"snapd"}
export CHANNEL=${CHANNEL:-"stable"}
export CORE_CHANNEL=${CORE_CHANNEL:-"stable"}
export DISTRO=${DISTRO:-"bionic"}
export DEVICE_QUEUE=${DEVICE_QUEUE:-"nvidia-gfx"}
export SPREAD_TESTS=${SPREAD_TESTS:-"external:ubuntu-16.04-64"}
export SPREAD_PARAMS=${SPREAD_PARAMS:-"-v"}
export TEST_PASS=${TEST_PASS:-"ubuntu"}
export TEST_USER_TYPE=${TEST_USER_TYPE:-"regular"}
export SKIP_TESTS=${SKIP_TESTS:-""}
export SETUP=${SETUP:-"sudo sed 's/PasswordAuthentication no/PasswordAuthentication yes/g' -i /etc/ssh/sshd_config && sudo service ssh restart"}
export POST_SETUP=${POST_SETUP:-""}
export SETUP_2=${SETUP_2:-"sudo add-apt-repository -y ppa:graphics-drivers && sudo apt-get update && sudo apt-get install -y nvidia-367 && echo 'rebooting' && sudo reboot"}
export POST_SETUP_2=${POST_SETUP_2:-"echo 'Waiting ...' && sleep 180"}
export SETUP_3=${SETUP_3:-"echo 'Check nvidia is installed' && lsmod | grep nvidia"}
