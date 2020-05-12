# Container image that runs your code
FROM python

# Copies your code file from your action repository to the filesystem path `/` of the container
COPY entrypoint.sh entrypoint.py template.html /

# Code file to execute when the docker container starts up (`entrypoint.sh`)
ENTRYPOINT ["/entrypoint.sh"]
