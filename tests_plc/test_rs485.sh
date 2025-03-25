#!/bin/bash
stty 115200 -F /dev/ttySC2 raw -echo # will be used to send data
stty 115200 -F /dev/ttySC3 raw -echo # will be used to receive data

cat /dev/ttySC2 > /tmp/rs485.txt &
sleep 1
echo "OK" > /dev/ttySC3
RESULT=$(cat /tmp/rs485.txt)
sudo killall cat > /dev/null

if [ -n "${RESULT}" ] ; then
    echo rs485 true "${RESULT}"
else
    echo rs485 false "cannot read from /dev/ttySC1"
fi
