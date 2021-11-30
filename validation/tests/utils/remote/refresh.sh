#!/bin/bash
set -x

echo "Refresh core on device"

if [ "$#" -ne 7 ]; then
    echo "Illegal number of parameters: $#"
    i=1
    for param in $*; do
        echo "param $i: $param"
        i=$(( i + 1 ))
    done
    exit 1
fi

DEVICE_IP=$1
DEVICE_PORT=$2
USER=$3
PASS=$4
CHANNEL=$5
CORE_CHANNEL=$6
SNAPD_CHANNEL=$7

execute_remote(){
    if [ -z "$PASS" ]; then
        ssh -p $DEVICE_PORT -o ConnectTimeout=10 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $USER@$DEVICE_IP "$*"
    else
        sshpass -p $PASS ssh -p $DEVICE_PORT -o ConnectTimeout=10 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no $USER@$DEVICE_IP "$*"
    fi    
}

wait_system_ready(){
    # Wait for seeding to finish.
    output=$(execute_remote "sudo snap refresh 2>&1" || echo "snapd is about to reboot the system")
    if echo "$output" | grep -E "snapd is about to reboot the system"; then
        wait_for_no_ssh 60 5
    fi

    # Wait for autorefresh is done.
    wait_for_ssh 60 5

    # Wait for seeding to finish.
    execute_remote "sudo snap wait system seed.loaded" || true
}

wait_for_ssh(){
    local retries=$1
    local sleep=$2
    while ! execute_remote true; do
        retries=$(( retries - 1 ))
        if [ $retries -le 0 ]; then
            echo "Timed out waiting for ssh. Aborting!"
            break
        fi
        sleep $sleep
    done
}

wait_for_no_ssh(){
    local retries=$1
    local sleep=$2
    while execute_remote true; do
        retries=$(( retries - 1 ))
        if [ $retries -le 0 ]; then
            echo "Timed out waiting for no ssh. Aborting!"
            break
        fi
        sleep $sleep
    done
}

retry_until(){
    local command=$1
    local output=$2
    local retries=$3
    local sleep=$4

    while ! execute_remote "$command" | grep -q -E "$output"; do
        retries=$(( retries - 1 ))
        if [ $retries -le 0 ]; then
            echo "Timed out reached. Aborting!"
            return 1
        fi
        sleep $sleep
    done
}

retry_while(){
    local command=$1
    local output=$2
    local retries=$3
    local sleep=$4

    while execute_remote "$command" | grep -q -E "$output"; do
        retries=$(( retries - 1 ))
        if [ $retries -le 0 ]; then
            echo "Timed out reached. Aborting!"
            return 1
        fi
        sleep $sleep
    done
}

check_refresh(){
    local refresh_channel=$1
    local snap=$2

    wait_for_no_ssh 30 2
    wait_for_ssh 120 2
    retry_until "snap info $snap" "tracking: +(latest/${refresh_channel}|${refresh_channel})" 10 2
}

do_full_refresh(){
    local channel=$1
    local core_channel=$2
    local snapd_channel=$3

    if [ -z "$core_channel" ]; then
        core_channel="$channel"
    fi
    if [ -z "$snapd_channel" ]; then
        snapd_channel="$core_channel"
    fi
    wait_auto_refresh
    do_core_refresh "$core_channel"
    wait_auto_refresh
    do_snapd_refresh "$snapd_channel"
    wait_auto_refresh
    do_kernel_refresh "$channel"

    # Run update and make "|| true" to continue when the connection is closed by remote host or not any snap to update
    execute_remote "sudo snap refresh" || true
}

do_kernel_refresh(){
    local refresh_channel=$1

    local kernel_line=$(execute_remote "snap list | grep 'kernel$'")
    local kernel_name=$(echo $kernel_line | awk '{ print $1 }')

    if [ -z "$kernel_name" ]; then
        echo "No kernel snap to update"
        return
    fi

    output=$(execute_remote "sudo snap refresh --channel ${refresh_channel} $kernel_name 2>&1" || true)
    if echo "$output" | grep -E "(no updates available|cannot refresh \"$kernel_name\"|is not installed)"; then
        echo "snap \"$kernel_name\" has no updates available"
    else
        check_refresh "$refresh_channel" "$kernel_name"
    fi
}

do_snapd_refresh(){
    local refresh_channel=$1

    local snapd_line=$(execute_remote "snap list | grep 'snapd'")
    if [ -z "$snapd_line" ]; then
        echo "No snapd snap to update"
        return
    fi
    local snapd_name=$(echo $snapd_line | awk '{ print $1 }')

    # Run update and make "|| true" to continue when the connection is closed by remote host
    output=$(execute_remote "sudo snap refresh --channel ${refresh_channel} $snapd_name 2>&1" || true)
    if echo "$output" | grep -E "(no updates available|cannot refresh \"$snapd_name\"|is not installed)"; then
        echo "snap \"$snapd_name\" has no updates available"
    else
        check_refresh "$refresh_channel" "$snapd_name"
    fi
}

do_core_refresh(){
    local refresh_channel=$1

    local core_line=$(execute_remote "snap list | grep -E '(core18|core20)'")
    if [ -z "$core_line" ]; then
        core_line=$(execute_remote "snap list | grep 'core'")
    fi
    local core_name=$(echo $core_line | awk '{ print $1 }')

    # Run update and make "|| true" to continue when the connection is closed by remote host
    output=$(execute_remote "sudo snap refresh --channel ${refresh_channel} $core_name 2>&1" || true)
    if echo "$output" | grep -E "(no updates available|cannot refresh \"$core_name\"|is not installed)"; then
        echo "snap \"$core_name\" has no updates available"
    else
        check_refresh "$refresh_channel" "$core_name"
    fi
}

wait_auto_refresh(){
    # Wait in case auto-refresh is finished
    if execute_remote "snap changes" | grep -q -E "Doing.*Auto-refresh snap.*"; then
        echo "Auto-refresh in progress"
        retry_while "snap changes" "Doing.*Auto-refresh.*" 120 30
        wait_for_ssh 120 30
        
        if execute_remote "snap changes" | grep "Error.*Auto-refresh.*"; then
            execute_remote "snap change --last=refresh"
            echo "Auto-refresh failed"
            exit 1
        fi

        retry_until "snap changes" "Done.*Auto-refresh.*" 120 4
        echo "Auto-refresh is completed"
    fi
}

check_install_snap(){
    local snap_name=jq
    local core_line_18=$(execute_remote "snap list | grep 'core18'")
    local core_line_20=$(execute_remote "snap list | grep 'core20'")
    if [ -n "$core_line_18" ]; then
        snap_name=jq-core18
    elif [ -n "$core_line_20" ]; then
        snap_name=jq-core20
    fi

    # Retry until the core is ready to install a snap and remove it
    retry_until "sudo snap install --edge $snap_name" "$snap_name .* installed" 20 10
    execute_remote "sudo snap remove $snap_name"
}

echo "Waiting system is ready"
wait_system_ready

echo "Snaps install before refresh"
execute_remote "snap list"

if [ "$SKIP_REFRESH" != "true" ]; then
    # Refresh core
    do_full_refresh "$CHANNEL" "$CORE_CHANNEL" "$SNAPD_CHANNEL"
    check_install_snap
else
    echo "Skipping refresh..."
fi

echo "Snaps install after refresh"
execute_remote "snap list"