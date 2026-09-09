FROM python:3.10-slim

ENV PYTHONUNBUFFERED 1
ENV DEBUG False
ENV APP_ROOT /code

WORKDIR ${APP_ROOT}

COPY ./requirements.txt requirements.txt

RUN apt-get update && \
  apt-get install -y \
  libglib2.0-0 \
  libnss3 \
  libfontconfig \
  locales \
  locales-all \
  build-essential \
  python3-all-dev \
  libpq-dev \
  libjpeg-dev \
  binutils \
  libproj-dev \
  gdal-bin \
  libxml2-dev \
  libxslt1-dev \
  zlib1g-dev \
  libffi-dev \
  libssl-dev \
  curl \
  gettext \
  python3-opencv \
  libzbar-dev \
  && pip install --upgrade pip \
  && pip install --no-cache-dir -r requirements.txt \
  && pip install --no-cache-dir uwsgi==2.0.22 \
  && apt-get clean --dry-run

COPY ./mime.types /etc/mime.types
COPY ./uwsgi.ini /conf/uwsgi.ini
COPY . /code

# Start uWSGI
CMD [ "uwsgi", "--ini", "/conf/uwsgi.ini" ]
