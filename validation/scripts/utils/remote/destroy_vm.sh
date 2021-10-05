#!/bin/bash
set -x

echo "Destroying vm"

systemd_stop_and_destroy_unit() {
    if systemctl is-active "$1"; then
        systemctl stop "$1"
    fi
    rm -f "/run/systemd/system/$1.service"
    systemctl daemon-reload
}

systemd_stop_and_destroy_unit nested-vm
rm -rf /tmp/work-dir
