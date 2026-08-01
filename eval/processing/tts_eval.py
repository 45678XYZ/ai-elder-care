"""TTS 模型評估腳本 — SageMaker Processing Job

在 ml.g5.xlarge (A10G 24GB) 上執行：
1. 載入 VoxHakka (YourTTS) → 合成客語音檔
2. 用 VoxHakka 輸出當 OmniVoice 的 ref_audio
3. 載入 OmniVoice → 合成客語音檔
4. 輸出所有音檔 + 合成狀態報告到 /opt/ml/processing/output/
"""

import subprocess
import sys

def install_deps():
    """安裝 DLC 映像中未包含的套件"""
    packages = [
        "coqui-tts",
        "formog2p",
        "soundfile",
        "huggingface_hub",
        "git+https://github.com/FormoSpeech/OmniVoice-hakka.git",
    ]
    for pkg in packages:
        print(f"安裝: {pkg}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pkg])

install_deps()

import json
import os
import re
import time
import traceback

import numpy as np
import soundfile as sf
import torch

INPUT_DIR = "/opt/ml/processing/input"
OUTPUT_DIR = "/opt/ml/processing/output"
os.makedirs(f"{OUTPUT_DIR}/voxhakka", exist_ok=True)
os.makedirs(f"{OUTPUT_DIR}/omnivoice", exist_ok=True)

# ─── 載入語料 ───────────────────────────────────────────────
corpus_path = f"{INPUT_DIR}/tts_test_utterances.jsonl"
utterances = []
with open(corpus_path, "r", encoding="utf-8") as f:
    for line in f:
        utterances.append(json.loads(line.strip()))

df_omni = [u for u in utterances if u["model"] == "omnivoice"]
df_vox = [u for u in utterances if u["model"] == "voxhakka"]
print(f"語料載入完成: OmniVoice={len(df_omni)} 筆, VoxHakka={len(df_vox)} 筆")

# ─── VoxHakka ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase 1: VoxHakka (YourTTS + G2P)")
print("=" * 60)

from huggingface_hub import snapshot_download

VOX_MODEL_ID = "formospeech/yourtts-htia-240704"
print(f"下載模型: {VOX_MODEL_ID}")
vox_model_dir = snapshot_download(VOX_MODEL_ID)
print(f"模型目錄: {vox_model_dir}")

# 修補 coqui-tts 與新版 transformers 的相容性
import transformers.pytorch_utils as _pu
if not hasattr(_pu, "isin_mps_friendly"):
    _pu.isin_mps_friendly = torch.isin

# 下載 Space repo 的 config patch
os.system("git clone --depth 1 https://huggingface.co/spaces/united-link/taiwanese-hakka-tts /tmp/hakka-tts-space 2>/dev/null || true")
sys.path.insert(0, "/tmp/hakka-tts-space")

try:
    import TTS.tts.configs.vits_config as vits_config_module
    from replace.tts import ChangedVitsConfig
    vits_config_module.VitsConfig = ChangedVitsConfig
except Exception as e:
    print(f"⚠️ Config patch 失敗: {e}")

from TTS.utils.synthesizer import Synthesizer
from formog2p.hakka import g2p

config_path = os.path.join(vox_model_dir, "config.json")
model_path = os.path.join(vox_model_dir, "model.pth")
speaker_path = os.path.join(vox_model_dir, "speakers.pth")
lang_path = os.path.join(vox_model_dir, "language_ids.json")
emb_path = os.path.join(vox_model_dir, "speaker_embs.pth")

with open(config_path, "r") as f:
    config_content = f.read()
config_content = config_content.replace("speakers.pth", speaker_path)
config_content = config_content.replace("language_ids.json", lang_path)
config_content = config_content.replace("speaker_embs.pth", emb_path)

temp_config = "/tmp/vox_config.json"
with open(temp_config, "w") as f:
    f.write(config_content)

vox_synth = Synthesizer(
    tts_checkpoint=model_path,
    tts_config_path=temp_config,
    use_cuda=torch.cuda.is_available(),
)
print("✅ VoxHakka 載入完成")

VOX_DIALECT_MAP = {
    "sixian": "hak_sx",
    "hailu": "hak_hl",
    "dapu": "hak_dp",
    "raoping": "hak_rp",
    "zhaoan": "hak_za",
}


def parse_ipa(ipa, delete_chars=r"\+\-\|\_", as_space=""):
    text = []
    ipa_list = re.split(r"(?<!\d)(?=\d)|(?<=\d)(?!\d)", ipa)
    for word in ipa_list:
        if word.isdigit():
            text.append(word)
        else:
            if as_space:
                word = re.sub(r"[{}]".format(as_space), " ", word)
            if delete_chars:
                word = re.sub(r"[{}]".format(delete_chars), "", word)
            word = word.replace("，", " ， ")
            text.extend(word)
    return text


def synthesize_voxhakka(text, output_path, dialect="sixian"):
    g2p_code = VOX_DIALECT_MAP.get(dialect, "hak_sx")
    result = g2p(text, g2p_code, include_eng=True)
    if result.unknown_words:
        raise ValueError(f"G2P 未知詞: {result.unknown_words}")

    parsed = [p.replace(" ", "|") for p in result.pronunciations]
    parsed_ipa = parse_ipa(" ".join(parsed))

    vox_synth.tts_model.length_scale = 1.0
    wav = vox_synth.tts(
        parsed_ipa,
        speaker_name="XF",
        language_name=dialect,
        split_sentences=False,
    )
    sf.write(output_path, np.array(wav), 22050)
    return output_path


# 合成 VoxHakka
vox_results = []
g2p_failures = []

print(f"\n合成 {len(df_vox)} 筆 VoxHakka...")
for i, row in enumerate(df_vox):
    out_path = f"{OUTPUT_DIR}/voxhakka/{row['id']}.wav"
    t0 = time.time()
    try:
        synthesize_voxhakka(row["text"], out_path, row.get("hakka_dialect", "sixian"))
        elapsed = time.time() - t0
        status = "ok"
        print(f"  ✓ [{i+1}/{len(df_vox)}] {row['id']}: {row['text'][:20]}... ({elapsed:.1f}s)")
    except ValueError as e:
        status = f"g2p_error: {e}"
        g2p_failures.append({"id": row["id"], "text": row["text"], "error": str(e)})
        print(f"  ⚠ [{i+1}/{len(df_vox)}] {row['id']}: G2P 失敗 — {e}")
    except Exception as e:
        status = f"error: {e}"
        print(f"  ✗ [{i+1}/{len(df_vox)}] {row['id']}: {e}")
        traceback.print_exc()
    vox_results.append({"id": row["id"], "text": row["text"], "status": status,
                        "dialect": row.get("hakka_dialect", "sixian")})

ok_count = sum(1 for r in vox_results if r["status"] == "ok")
print(f"\n✅ VoxHakka 合成完成: {ok_count}/{len(df_vox)} 成功")

# 合成參考音檔
REF_PATH = f"{OUTPUT_DIR}/ref_hakka.wav"
try:
    synthesize_voxhakka("今晡日天時蓋好。", REF_PATH, dialect="sixian")
    print(f"✅ 參考音檔: {REF_PATH}")
except Exception as e:
    print(f"⚠️ 參考音檔合成失敗: {e}")
    # fallback: 用第一筆成功的 VoxHakka 輸出當 ref
    for r in vox_results:
        if r["status"] == "ok":
            REF_PATH = f"{OUTPUT_DIR}/voxhakka/{r['id']}.wav"
            print(f"  Fallback 使用: {REF_PATH}")
            break

# ─── OmniVoice ───────────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase 2: OmniVoice (Voice Cloning)")
print("=" * 60)

omni_results = []

try:
    from omnivoice import OmniVoice

    omni_model = OmniVoice.from_pretrained(
        "formospeech/omnivoice-hakka-community-1",
        device_map="cuda:0",
        dtype=torch.float16,
    )
    print("✅ OmniVoice 載入完成")

    OMNI_DIALECT_MAP = {
        "sixian": "客語四縣腔",
        "hailu": "客語海陸腔",
        "dapu": "客語大埔腔",
        "raoping": "客語饒平腔",
        "zhaoan": "客語詔安腔",
    }

    REF_TEXT = "今晡日天時蓋好。"

    print(f"\n合成 {len(df_omni)} 筆 OmniVoice...")
    for i, row in enumerate(df_omni):
        out_path = f"{OUTPUT_DIR}/omnivoice/{row['id']}.wav"
        t0 = time.time()
        try:
            instruct = OMNI_DIALECT_MAP.get(row.get("hakka_dialect", "sixian"), "客語四縣腔")
            audio = omni_model.generate(
                text=row["text"],
                ref_audio=REF_PATH,
                ref_text=REF_TEXT,
                instruct=instruct,
            )
            sf.write(out_path, audio[0], 24000)
            elapsed = time.time() - t0
            status = "ok"
            print(f"  ✓ [{i+1}/{len(df_omni)}] {row['id']}: {row['text'][:20]}... ({elapsed:.1f}s)")
        except Exception as e:
            status = f"error: {e}"
            print(f"  ✗ [{i+1}/{len(df_omni)}] {row['id']}: {e}")
            traceback.print_exc()
        omni_results.append({"id": row["id"], "text": row["text"], "status": status,
                             "dialect": row.get("hakka_dialect", "sixian")})

    ok_count = sum(1 for r in omni_results if r["status"] == "ok")
    print(f"\n✅ OmniVoice 合成完成: {ok_count}/{len(df_omni)} 成功")

except Exception as e:
    print(f"✗ OmniVoice 載入失敗: {e}")
    traceback.print_exc()
    for row in df_omni:
        omni_results.append({"id": row["id"], "text": row["text"],
                             "status": f"model_load_error: {e}",
                             "dialect": row.get("hakka_dialect", "sixian")})

# ─── 輸出報告 ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("輸出報告")
print("=" * 60)

report = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "instance_type": os.environ.get("SM_CURRENT_INSTANCE_TYPE", "unknown"),
    "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    "voxhakka": {
        "total": len(df_vox),
        "success": sum(1 for r in vox_results if r["status"] == "ok"),
        "g2p_failures": g2p_failures,
        "results": vox_results,
    },
    "omnivoice": {
        "total": len(df_omni),
        "success": sum(1 for r in omni_results if r["status"] == "ok"),
        "results": omni_results,
    },
}

report_path = f"{OUTPUT_DIR}/tts_eval_report.json"
with open(report_path, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

print(f"\n結果已寫入: {report_path}")
print(f"VoxHakka: {report['voxhakka']['success']}/{report['voxhakka']['total']} 成功")
print(f"OmniVoice: {report['omnivoice']['success']}/{report['omnivoice']['total']} 成功")
print(f"音檔目錄: {OUTPUT_DIR}/voxhakka/, {OUTPUT_DIR}/omnivoice/")

# 列出所有產出音檔
for subdir in ["voxhakka", "omnivoice"]:
    files = [f for f in os.listdir(f"{OUTPUT_DIR}/{subdir}") if f.endswith(".wav")]
    print(f"  {subdir}/: {len(files)} 個 wav 檔")
