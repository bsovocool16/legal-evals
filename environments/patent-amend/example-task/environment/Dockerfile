FROM mirror.gcr.io/library/python:3.12-slim
WORKDIR /app
ADD case.tar.xz /app/case/
RUN chmod -R a-w /app/case && chmod 0777 /app
