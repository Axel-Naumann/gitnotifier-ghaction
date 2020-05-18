#!/bin/sh -l

if test "x$INPUT_GITHUBTOKEN" = "x" ; then
    echo "No 'INPUT_GITHUBTOKEN' parameter; assuming this is a fork for which no email notification is configured. Exiting gracefully."
    echo "Consider disabling Actions for ROOT at Github / ${GITHUB_REPOSITORY} / Settings / Actions / Disable Actions for this repository."
    exit
fi

pip3 install github3.py unidiff
python3 /entrypoint.py
