# Multi-stage build -> distroless runtime (Section 5.3: "multi-stage builds to distroless images").
# One image for every Python stage; the command selects the stage:
#   docker run lap:dev stage window-features
ARG PYTHON_VERSION=3.11

FROM python:${PYTHON_VERSION}-slim-bookworm AS build
ARG WITH_TORCH=false
ARG WITH_UI=false
WORKDIR /src
COPY requirements.txt requirements-dev.txt ./
# one resolver run, so optional extras cannot silently shadow pinned versions in --target
RUN cp requirements.txt req-all.txt \
 && if [ "$WITH_UI" = "true" ]; then printf 'mlflow==2.17.2\nstreamlit==1.56.0\nplotly==6.6.0\n' >> req-all.txt; fi \
 && pip install --no-cache-dir --target /deps -r req-all.txt \
 && if [ "$WITH_TORCH" = "true" ]; then pip install --no-cache-dir --target /deps torch --index-url https://download.pytorch.org/whl/cpu; fi
COPY . /app
RUN rm -rf /app/.venv /app/data /app/tests /app/.git \
 # writable state for the nonroot user; named volumes mounted here inherit this ownership
 && mkdir -p /app/data/checkpoints /models \
 && chown -R 65532:65532 /app/data /models

FROM gcr.io/distroless/python3-debian12:nonroot
ENV PYTHONPATH=/deps:/app PYTHONUNBUFFERED=1 LAP_ENV=prod HOME=/app/data
COPY --from=build /deps /deps
COPY --from=build /app /app
COPY --from=build --chown=65532:65532 /models /models
WORKDIR /app
USER nonroot
ENTRYPOINT ["python3", "/app/lap.py"]
CMD ["--help"]
