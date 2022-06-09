#!/bin/bash
set -x

echo "Creating vm"

echo "installing nested dependencies"
sudo apt install -y qemu qemu-utils genisoimage sshpass qemu-kvm cloud-image-utils ovmf kpartx git unzip gdisk

echo "installing snapd"
sudo apt update
sudo apt install -y snapd

echo "Installing snaps needed"
sudo snap install core

ARCHITECTURE=$1
IMAGE_URL=$2
USER_ASSERTION_URL=$3
BUILD_SNAPD=$4


execute_remote(){
    sshpass -p ubuntu ssh -p $PORT -q -o ConnectTimeout=10 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no user1@localhost "$*"
}

wait_for_ssh(){
    retry=200
    while ! execute_remote true; do
        retry=$(( retry - 1 ))
        if [ $retry -le 0 ]; then
            echo "Timed out waiting for ssh. Aborting!"
            return 1
        fi
        sleep 1
    done
    return 0
}

wait_for_no_ssh(){
    retry=150
    while execute_remote true; do
        retry=$(( retry - 1 ))
        if [ $retry -le 0 ]; then
            echo "Timed out waiting for no ssh. Aborting!"
            return 1
        fi
        sleep 1
    done
    return 0
}

prepare_ssh(){
    execute_remote "sudo adduser --uid 12345 --extrausers --quiet --disabled-password --gecos '' test"
    execute_remote "echo test:ubuntu | sudo chpasswd"
    execute_remote "echo 'test ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-test"

    execute_remote "sudo adduser --extrausers --quiet --disabled-password --gecos '' external"
    execute_remote "echo external:ubuntu | sudo chpasswd"
    execute_remote "echo 'external ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/create-user-external"
}

create_cloud_init_config(){
    cat <<EOF > "$WORK_DIR/user-data"
#cloud-config
  ssh_pwauth: True
  users:
   - name: user1
     sudo: ALL=(ALL) NOPASSWD:ALL
     shell: /bin/bash
  chpasswd:
   list: |
    user1:ubuntu
   expire: False
EOF

    cat <<EOF > "$WORK_DIR/meta-data"
instance_id: cloud-images
EOF

    loops=$(kpartx -avs "$WORK_DIR/ubuntu-core.img"  | cut -d' ' -f 3)
    part=$(echo "$loops" | tail -1)
    tmp=$(mktemp -d)
    mount "/dev/mapper/$part" "$tmp"

    mkdir -p "$tmp/system-data/var/lib/cloud/seed/nocloud-net/"
    cp "$WORK_DIR/user-data" "$tmp/system-data/var/lib/cloud/seed/nocloud-net/"
    cp "$WORK_DIR/meta-data" "$tmp/system-data/var/lib/cloud/seed/nocloud-net/"

    umount "$tmp"
    kpartx -d "$WORK_DIR/ubuntu-core.img"
}
 
create_cloud_init_config_uc20(){
    cat << 'EOF' > "$WORK_DIR/data.cfg"
#cloud-config
datasource_list: [NoCloud]
users:
  - name: user1
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: false
    # passwd is just "ubuntu"
    passwd: "$6$rounds=4096$PCrfo.ggdf4ubP$REjyaoY2tUWH2vjFJjvLs3rDxVTszGR9P7mhH9sHb2MsELfc53uV/v15jDDOJU/9WInfjjTKJPlD5URhX5Mix0"
EOF

    loop=$(kpartx -avs "$WORK_DIR/ubuntu-core.img" | sed -n 2p | awk '{print $3}')
    tmp=$(mktemp -d)

    mount "/dev/mapper/$loop" "$tmp"
    mkdir -p "$tmp/data/etc/cloud/cloud.cfg.d/"
    cp -f "$WORK_DIR/data.cfg" "$tmp/data/etc/cloud/cloud.cfg.d/"
    sync
    umount "$tmp"
    kpartx -d "$WORK_DIR/ubuntu-core.img"
}

systemd_create_and_start_unit() {
    printf "[Unit]\nDescription=For testing purposes\n[Service]\nType=simple\nExecStart=%s\n" "$2" > "/run/systemd/system/$1.service"
    if [ -n "${3:-}" ]; then
        echo "Environment=$3" >> "/run/systemd/system/$1.service"
    fi
    systemctl daemon-reload
    systemctl start "$1"
}

get_qemu_for_nested_vm(){
    case "$ARCHITECTURE" in
    amd64)
        command -v qemu-system-x86_64
        ;;
    i386)
        command -v qemu-system-i386
        ;;
    *)
        echo "unsupported architecture"
        exit 1
        ;;
    esac
}

ensure_vmimage_size() {
    # set a minimum size of 8gb
    local image_file="$1"
    local minimum_size=$((8*1024*1024*1024))
    local actual_size=$(stat -c %s "$image_file")
    if [ $actual_size -lt $minimum_size ]; then
        echo "Image size is too small, resizing to 8gb"
        qemu-img resize "$image_file" 8G

        # TODO: workaround in place to ensure that the GPT header is correct
        # (see PR https://github.com/snapcore/snapd/pull/11394), maybe remove this
        # once this has been merged?
        printf "w\nY\nY\nq\n" | gdisk "$WORK_DIR/ubuntu-core.img"
    fi
}

export PORT=8022
export WORK_DIR=/tmp/work-dir
export QEMU=$(get_qemu_for_nested_vm)

# create ubuntu-core image
mkdir -p "$WORK_DIR"

if [[ "$IMAGE_URL" == *.img.xz ]]; then
    if [ -f "$IMAGE_URL" ]; then
        cp "$IMAGE_URL" "$WORK_DIR/ubuntu-core.img.xz"
    else
        wget -q -O "$WORK_DIR/ubuntu-core.img.xz" "$IMAGE_URL"
    fi
    unxz "$WORK_DIR/ubuntu-core.img.xz"
elif [[ "$IMAGE_URL" == *.img ]]; then
    if [ -f "$IMAGE_URL" ]; then
        cp "$IMAGE_URL" "$WORK_DIR/ubuntu-core.img"
    else
        wget -q -O "$WORK_DIR/ubuntu-core.img" "$IMAGE_URL"
    fi
else
    echo "Image extension not supported, exiting..."
    exit 1
fi

# to support the cdimages we need to ensure they are 
# expanded, as they come in 4gb flavors.
ensure_vmimage_size "$WORK_DIR/ubuntu-core.img"

CURR_SYSTEM="$(lsb_release -cs)"
if [ "$CURR_SYSTEM" = focal ] || [ "$CURR_SYSTEM" = jammy ]; then
    snap install test-snapd-swtpm --beta
    create_cloud_init_config_uc20
    rm -f /var/snap/test-snapd-swtpm/current/tpm2-00.permall

    if [ "$CURR_SYSTEM" = jammy ]; then
        wget https://storage.googleapis.com/snapd-spread-tests/dependencies/OVMF_CODE.secboot.fd
        mv OVMF_CODE.secboot.fd /usr/share/OVMF/OVMF_CODE.secboot.fd
        wget https://storage.googleapis.com/snapd-spread-tests/dependencies/OVMF_VARS.ms.fd
        mv OVMF_VARS.ms.fd "$WORK_DIR"/OVMF_VARS.ms.fd
    else
        cp /usr/share/OVMF/OVMF_VARS.ms.fd "$WORK_DIR"/OVMF_VARS.ms.fd
    fi

    systemd_create_and_start_unit nested-vm "${QEMU} -m 4096 -nographic -snapshot \
        -machine ubuntu-q35,accel=kvm -global ICH9-LPC.disable_s3=1 \
        -netdev user,id=mynet0,hostfwd=tcp::$PORT-:22 -device virtio-net-pci,netdev=mynet0 \
        -drive file=/usr/share/OVMF/OVMF_CODE.secboot.fd,if=pflash,format=raw,unit=0,readonly=on \
        -drive file=$WORK_DIR/OVMF_VARS.ms.fd,if=pflash,format=raw,unit=1 \
        -chardev socket,id=chrtpm,path=/var/snap/test-snapd-swtpm/current/swtpm-sock \
        -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
        -drive file=$WORK_DIR/ubuntu-core.img,cache=none,format=raw,id=disk1,if=none \
        -device virtio-blk-pci,drive=disk1,bootindex=1"         
else
    create_cloud_init_config
    systemd_create_and_start_unit nested-vm "${QEMU} -m 2048 -nographic -snapshot \
        -net nic,model=virtio -net user,hostfwd=tcp::$PORT-:22 \
        -serial mon:stdio -machine accel=kvm \
        $WORK_DIR/ubuntu-core.img"
fi

if wait_for_ssh; then
    prepare_ssh
else
    echo "ssh not established, exiting..."
    journalctl -u nested-vm -n 150
    exit 1
fi

echo "Wait for first boot to be done"
while ! execute_remote "snap changes" | grep -q -E "Done.*Initialize system state"; do
    sleep 1
done

echo "VM Ready"
