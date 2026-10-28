FROM mirror.gcr.io/library/python:3.12-slim
WORKDIR /app
RUN useradd -m -u 1000 agent
COPY inputs/ /app/inputs/
COPY precedents/ /app/precedents/
COPY bin/precedents /usr/local/bin/precedents
RUN chmod 0755 /usr/local/bin/precedents && chmod -R a-w /app/inputs /app/precedents \
    && mkdir -p /app/output && chown agent:agent /app/output && chmod 0755 /app/output
