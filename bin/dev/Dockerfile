FROM python:3.13.3-slim
ENV PYTHONUNBUFFERED 1
ENV DEBUG True
COPY requirements.txt /code/requirements.txt
WORKDIR /code
# RUN pip install --no-cache-dir -r requirements.txt # evvel bu idi indi ashagidaki 2 setrdi


RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y gettext && rm -rf /var/lib/apt/lists/*
