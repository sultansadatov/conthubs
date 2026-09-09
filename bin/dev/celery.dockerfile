FROM python:3.10-slim
ENV PYTHONUNBUFFERED 1

#ENV C_FORCE_ROOT true

#ENV APP_USER myapp
ENV APP_ROOT /code
ENV DEBUG True

RUN mkdir /code;

RUN apt-get update && \
    apt-get install -y \
        unzip \
        libglib2.0-0 \
        libnss3 \
        libfontconfig \
        wget \
        gnupg \
        gcc

WORKDIR ${APP_ROOT}

RUN mkdir /config
ADD requirements.txt /config/
RUN pip install --no-cache-dir -r /config/requirements.txt

#USER ${APP_USER}
ADD . ${APP_ROOT}
