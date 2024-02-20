#!/bin/bash

show_help() {
    echo "usage:    reserve-tf <DEVICE> <CHANNEL> <VERSION> [URL] [LAUNCHPAD-ID]"
    echo "examples: reserve-tf.sh pi3 beta 18"
    echo "          reserve-tf.sh pi4 beta 20 'https://storage.googleapis.com/snapd-spread-tests/images/pi4-20-stable-snapd_beta/pi.img.xz' 'sergio-j-cazzolato'"
    echo "          reserve-tf.sh caracalla"
}

if [ $# -eq 0 ] || [ "$1" = '-h' ] || [ "$1" = '--help' ]; then
    show_help
    exit 0
fi

DEVICE=$1
CHANNEL=${2:-beta}
VERSION=${3:-18}
URL=${4:-}
LP_ID=${5:-sergio-j-cazzolato}
TEST_DATA=""

TF_JOB=job.yaml
TF_CLIENT=/snap/bin/testflinger-cli
SUPPORTED_DEVICES='pi2 pi3 pi4 dragonboard caracalla caracalla-media caracalla-transport stlouis murcia-3200 havana-dome'
SUPPORTED_CHANNELS='edge beta candidate stable'
SUPPORTED_VERSIONS='16 18 20 22'


# Define the queue to use
DEVICE_QUEUE=
if [ "$DEVICE" = pi2 ]; then
	DEVICE_QUEUE=cert-rpi2
elif [ "$DEVICE" = pi3 ]; then
	DEVICE_QUEUE=rpi3b
elif [ "$DEVICE" = pi4 ]; then
	DEVICE_QUEUE=rpi4b4g
elif [ "$DEVICE" = caracalla ]; then
	DEVICE_QUEUE=caracalla-media
elif [[ "$DEVICE" =~ caracalla* ]]; then
	DEVICE_QUEUE=$DEVICE
elif [ "$DEVICE" = stlouis ]; then
	DEVICE_QUEUE=stlouis
elif [ "$DEVICE" = dragonboard ]; then
	DEVICE_QUEUE=dragonboard
else
	echo "Device $DEVICE not in supported list, usign it as a device queue"
	DEVICE_QUEUE=$DEVICE
fi

if ! [[ "$SUPPORTED_CHANNELS" =~ "$CHANNEL" ]]; then
	echo "Channel $CHANNEL not supported. Supported channels are: [$SUPPORTED_CHANNELS]"
	exit 1
fi

if ! [[ "$SUPPORTED_VERSIONS" =~ "$VERSION" ]]; then
	echo "Version $VERSION not supported. Supported verions are: [$SUPPORTED_VERSIONS]"
	exit 1
fi

if [ -z "$URL" ]; then
	if [[ "$DEVICE_QUEUE" =~ caracalla* ]]; then
		URL=http://10.102.196.9/plano/caracalla-current.img.xz
	elif [ "$DEVICE_QUEUE" = stlouis ]; then
		URL=http://10.102.196.9/plano/stlouis-current.img.xz
	elif [[ "$DEVICE_QUEUE" =~ murcia-* ]]; then
		DISTRO=core20-latest-stable
	elif [[ "$DEVICE_QUEUE" =~ havana-dome* ]]; then
		DISTRO=
		URL=
	else
		# Define the url to get
		if [ "$VERSION" != 16 ] && [[ "$DEVICE" =~ pi* ]]; then
			IMAGE="pi.img.xz"
		else	
			IMAGE="${DEVICE}.img.xz"
		fi
		if [ "$CHANNEL" == stable ]; then
			URL=https://storage.googleapis.com/snapd-spread-tests/images/$DEVICE-$VERSION-$CHANNEL/$IMAGE
		else
			URL=https://storage.googleapis.com/snapd-spread-tests/images/$DEVICE-$VERSION-stable-snapd_$CHANNEL/$IMAGE
		fi
	fi
fi

# Install testflinger client in case it is not installed
if ! snap list testflinger-cli; then
    sudo snap install testflinger-cli    
fi

PROVISION_DATA="provision_data:"
if [ -n "$DISTRO" ]; then
	PROVISION="distro: $DISTRO"
elif [ -n "$URL" ]; then
  PROVISION="url: $URL"
else
	PROVISION_DATA=""
	PROVISION=""
fi

cat > "$TF_JOB" <<EOF
job_queue: $DEVICE_QUEUE
global_timeout: 36000
$PROVISION_DATA
  $PROVISION
reserve_data:
  ssh_keys:
    - lp:$LP_ID
EOF

# Just add test data for caracalla and stlouis 
if [[ "$DEVICE_QUEUE" =~ caracalla-* ]] || [ "$DEVICE_QUEUE" = stlouis ]; then
	cat >> "$TF_JOB" <<EOF
test_data:
  test_username: admin
  test_password: admin
  test_cmds: |
    echo ready
EOF
fi

echo "Submitting job to testflinger"
JOB_ID=$("$TF_CLIENT" submit -q "$TF_JOB")
echo "JOB_ID: ${JOB_ID}"
echo ""

echo "Print job: $TF_JOB "
cat "$TF_JOB" | tee "${JOB_ID}.job"
echo ""

echo "Waiting for job output"
sleep 10
echo ""

echo "Showing job data for $JOB_ID"
"$TF_CLIENT" poll "${JOB_ID}" | tee "${JOB_ID}.log"
