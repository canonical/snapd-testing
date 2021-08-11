
# Preparation
How to prepare the device before run the tests?

To prepare the device do:
1. Create the image and run the initial setup in the device
2. exec > ./external/prepare_ssh <device_ip> <connection_port> <registered_user>

# Variables
Which variable should I setup?

Defaults are defined for the different systems but could be updated depending on the execution environment
  ETHERNET_READY: (true/false) -> if ehternet is gonna be tested
  ETHERNET_IFACE: (eth0) -> the network interface id to be used
  ETHERNET_POSITION: (1) -> position on the networks list
  WIFI_READY: (true/false) -> if wifi is gonna be tested
  WIFI_IFACE: wlan0 -> the network interface id to be used
  WIFI_POSITION: 2 -> position on the networks list

# Execution
How to run the tests?

To run the tests do:
1. dragonboard example (with wifi)
  > WIFI_SSID=<ssid> WIFI_PASSWORD=<password> SPREAD_EXTERNAL_ADDRESS=<device_ip> <spread_path> -v external:ubuntu-core-16-arm64:tests/
2. i386 example (without wifi)
  > SPREAD_EXTERNAL_ADDRESS=<device_ip> <spread_path> -v external:ubuntu-core-16-32:tests/
