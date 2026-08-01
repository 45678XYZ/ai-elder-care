# FormoSpeech Whisper-v3 客語 ASR 推論容器（SageMaker real-time endpoint）。
#
# 六腔共用同一個映像，差別只在 terraform 注入的 FORMO_PROMPT_ID；因此 prompt 不進
# 映像，換腔調不必重建。部署機型見 docs/asr/model-catalog.md。
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/opt/app \
    HF_HUB_OFFLINE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3.10 \
        python3.10-dev \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /opt/app
COPY app/ /opt/app/app/
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

# SageMaker 以 8080 打 /ping 與 /invocations。
EXPOSE 8080
