## About this project

This project provides a set of scripts intended to accelerate the snappy validation process over the diferent platforms and devices supported. 

The scripts are ready to run spread tests for the different scenarios on actual devices, test flinger devices, local vms and to create vms with 
ubuntu core inside test flinger machines.

The default configuration of the project is ready to run beta validation process.

## Beta validation

In this section it is explained how to run beta validation by using this project. To complete beta validation all the following tests have to be executed on dragonboard, rpi2, rpi3, core64 and core32.

### Create beta images

By running the following lines, the script will create the images for beta validation for all the supported devices.

    git clone https://github.com/snapcore/snapd-testing.git
    cd images
    sudo ./create.sh beta <core-number> <snaps-by-channel> <extra-snaps> <models>

with
 . core-number = Ubuntu Core system version, currently supported: 16 or 18
 . snaps-by-channel = select specific snap by channel to install, for example core=edge
 . extra-snaps = select local snaps to be installed during the image creation, for example core18_782.snap
 . models: if not defined then images for all the models are created. Otherwise one of these can be selected:
    . dragonboard
    . pc-amd64
    . pc-i386
    . pi3
    . pi2

This example shows how to create a core16 image using stable channel but core from beta with amd64 architecture

    sudo ./create.sh stable 16 "core=beta" "" pc-amd64

### Get stable images used to run the refresh core scenario

Download the core 16 images from: http://cdimage.ubuntu.com/ubuntu-core/16/stable/current/
Download the core 18 images from: http://cdimage.ubuntu.com/ubuntu-core/18/stable/current/
Download the core 20 images from: http://cdimage.ubuntu.com/ubuntu-core/20/stable/current/

It is possible to use older images too to validate the refresh scenario. 

### Setup a device:

    sudo dd if=<PAT_TO_IMG> of=/dev/mmcblk0 bs=4M oflag=sync status=noxfer

### Create a vm:

#### Core 16

    amd64: sudo kvm -snapshot -smp 2 -m 1500 -net nic,model=virtio -net user,hostfwd=tcp::8022-:22 -serial mon:stdio <PATH_TO_VM_IMAGE>
    i386: sudo kvm -snapshot -smp 2 -m 1500 -net nic,model=virtio -net user,hostfwd=tcp::8023-:22 -serial mon:stdio <PATH_TO_VM_IMAGE>

#### Core 18

    amd64: sudo kvm -snapshot -smp 2 -m 2000 -net nic,model=virtio -net user,hostfwd=tcp::8022-:22 -serial mon:stdio <PATH_TO_VM_IMAGE>    
    i386: sudo kvm -snapshot -smp 2 -m 2000 -net nic,model=virtio -net user,hostfwd=tcp::8023-:22 -serial mon:stdio <PATH_TO_VM_IMAGE>

    Note: it could be needed to provide initial entropy by moving the mouse and using the keyboard in the kvm window.

#### Core 20

    amd64 without tpm:
    sudo cp /usr/share/OVMF/OVMF_VARS.ms.fd .
    sudo qemu-system-x86_64 -smp 2 -m 4096 -snapshot \
    -machine ubuntu-q35,accel=kvm -global ICH9-LPC.disable_s3=1 \
    -netdev user,id=mynet0,hostfwd=tcp::8022-:22 -device virtio-net-pci,netdev=mynet0 \
    -drive file=/usr/share/OVMF/OVMF_CODE.secboot.fd,if=pflash,format=raw,unit=0,readonly=on \
    -drive file=./OVMF_VARS.ms.fd,if=pflash,format=raw,unit=1 \
    -drive file=<PATH_TO_VM_IMAGE>,cache=none,format=raw,id=disk1,if=none \
    -device virtio-blk-pci,drive=disk1,bootindex=1

    Note: it is needed to install ovmf package as dependency

    amd64 with tpm:
    sudo cp /usr/share/OVMF/OVMF_VARS.ms.fd .
    sudo rm -f /var/snap/swtpm-mvo/current/tpm2-00.permall
    sudo qemu-system-x86_64 -smp 2 -m 4096 -snapshot \
    -machine ubuntu-q35,accel=kvm -global ICH9-LPC.disable_s3=1 \
    -netdev user,id=mynet0,hostfwd=tcp::$PORT-:22 -device virtio-net-pci,netdev=mynet0 \
    -drive file=/usr/share/OVMF/OVMF_CODE.secboot.fd,if=pflash,format=raw,unit=0,readonly=on \
    -drive file=./OVMF_VARS.ms.fd,if=pflash,format=raw,unit=1 \
    -chardev socket,id=chrtpm,path=/var/snap/swtpm-mvo/current/swtpm-sock \
    -tpmdev emulator,id=tpm0,chardev=chrtpm -device tpm-tis,tpmdev=tpm0 \
    -drive file=<PATH_TO_VM_IMAGE>,cache=none,format=raw,id=disk1,if=none \
    -device virtio-blk-pci,drive=disk1,bootindex=1

    Note: it is needed to install swtpm-mvo snap as dependency

### Access to console through screen:

    sudo screen /dev/ttyUSB0 115200

### Run beta validation

Beta validation is triggered automatically by using the job-executor scirpt. All the scenarios are covered by jobs.

The scenarios to cover in beta validation are the following:

Core snap:
 . All the snaps from stable but core snap from beta
 . Tests executed on ubuntu Core 16
 . Snapd tests using devices: pi2, pi3, dragonboard, i386 and amd64
 . Console conf tests using devices: pi2, pi3, dragonboard, i386 and amd64

Snapd snap:
 . All the snaps from stable but snapd snap from beta
 . Snapd tests using devices for ubuntu Core 18: pi2, pi3, dragonboard, i386 and amd64
 . Snapd tests using devices for ubuntu Core 20: pi3, pi4 and amd64

##### Running individual tests

To run individual tests it is used the tests-executor tool which is located in the validation directory.

For more details about the tool run # ./validation/tests-executor -h

Some examples

    # Run the snapd smoke test on pi4 using uc20 image
    ./validation/tests-executor --image-channel stable --core-channel beta --device pi4 --version 20 --tests tests/smoke

    # Run all the console-conf tests for amd64 on uc16
    ./validation/tests-executor --image-channel stable --core-channel beta --device amd64 --version 16 --project cconf

### SRU validation

##### SRU validation execution on google machines
    SPREAD_MODIFY_CORE_SNAP_FOR_REEXEC=0 SPREAD_TRUST_TEST_KEYS=false SPREAD_SNAP_REEXEC=0 SPREAD_CORE_CHANNEL=stable SPREAD_SRU_VALIDATION=1 spread google-sru:

##### SRU validation setup on external desktop machine
    sudo apt install -y snapd && sudo cp /etc/apt/sources.list sources.list.back && sudo echo "deb http://archive.ubuntu.com/ubuntu/ $(lsb_release -c -s)-proposed restricted main multiverse universe" | sudo tee /etc/apt/sources.list -a && sudo apt update && sudo apt install -y --only-upgrade snapd && sudo mv sources.list.back /etc/apt/sources.list && sudo apt update

