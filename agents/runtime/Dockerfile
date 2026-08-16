FROM python:3.12-alpine
WORKDIR /runtime
COPY runtime.py /runtime/runtime.py
RUN addgroup -S runtime && adduser -S -G runtime runtime
USER runtime
EXPOSE 8000
ENTRYPOINT ["python", "/runtime/runtime.py"]
