FROM python:3.12-slim
WORKDIR /source
COPY runtime/mccl /source/runtime/mccl
RUN python -m pip install --no-cache-dir /source/runtime/mccl && rm -rf /source
USER 65532:65532
EXPOSE 29500
ENTRYPOINT ["mccl"]
CMD ["serve", "--host", "0.0.0.0", "--port", "29500"]
