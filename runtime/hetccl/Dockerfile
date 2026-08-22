FROM python:3.12-slim
WORKDIR /source
COPY runtime/hetccl /source/runtime/hetccl
RUN python -m pip install --no-cache-dir /source/runtime/hetccl && rm -rf /source
USER 65532:65532
EXPOSE 29500
ENTRYPOINT ["hetccl"]
CMD ["serve", "--host", "0.0.0.0", "--port", "29500"]
