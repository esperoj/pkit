FROM mcr.microsoft.com/devcontainers/python:3.15

RUN apt-get update \
    && apt-get install -y --no-install-recommends make parallel \
    && rm -rf /var/lib/apt/lists/*
