FROM ubuntu:24.04
WORKDIR /code

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    python3-pip python3.12-venv ffmpeg libgl1 curl \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt requirements-dev.txt /code/
# Dev deps (pytest) baked in too, not just prod -- this image doubles as
# the test-runner (see CLAUDE.md), and installing them ad hoc into a
# running container stopped working once containers run as a non-root
# user (see `user:` in docker-compose.yml): a non-root user can't write
# into /opt/venv's site-packages, which was owned by root from this build
# step regardless.
RUN pip3 install -r /code/requirements-dev.txt

COPY . /code

RUN groupadd --gid 1001 usergrp && useradd --uid 1001 --gid usergrp -m user

WORKDIR /code
