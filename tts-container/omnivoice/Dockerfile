# OmniVoice 客語 TTS 推論容器（SageMaker real-time endpoint）。
#
# 部署在 ml.g4dn.xlarge（T4）。基底選 CUDA 12.1：上游沒有釘死 torch 版本，
# 由 OmniVoice-hakka 的相依決定，而近期 torch wheel 以 cu121 為主。
FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

# 上游的預設分支是 master（不是 main），且沒有發任何版本 tag。要重現同一份推論行為
# 就以 commit SHA 覆寫這個值：build_and_push.sh --ref <sha>。
ARG OMNIVOICE_REF=master

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PYTHONPATH=/opt/app \
    HF_HUB_OFFLINE=1

# ffmpeg 供 MP3 編碼；libsndfile 是 soundfile 的執行期依賴。
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        git \
        libsndfile1 \
        python3.10 \
        python3.10-dev \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel

# 客語版本只發在 FormoSpeech 的 fork，PyPI 上的 omnivoice 不是同一個東西。
RUN python3 -m pip install --no-cache-dir \
    "git+https://github.com/FormoSpeech/OmniVoice-hakka.git@${OMNIVOICE_REF}"

COPY requirements-serving.txt /tmp/requirements-serving.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements-serving.txt

WORKDIR /opt/app
COPY app/ /opt/app/app/
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

# SageMaker 以 8080 打 /ping 與 /invocations。
EXPOSE 8080
