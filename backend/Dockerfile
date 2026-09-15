FROM ubuntu:24.04
WORKDIR /code

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    python3-pip python3.12-venv ffmpeg libgl1 curl \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY requirements.txt /code/requirements.txt
RUN pip3 install -r /code/requirements.txt

COPY . /code

RUN groupadd --gid 1001 usergrp && useradd --uid 1001 --gid usergrp -m user

WORKDIR /code
