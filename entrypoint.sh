#!/bin/sh -l

if [[ "x$INPUT_PASSWORD" == "x" ]] ; then
    echo "debug file={entrypoint.sh}:: No 'INPUT_PASSWORD' parameter; assuming this is a fork for which no email notification is configured. Exiting gracefully."
    exit
fi

pip3 install github3.py unidiff
python3 /entrypoint.py
