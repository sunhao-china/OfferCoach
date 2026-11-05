"""服务器侧 bge-m3 模型下载（nohup 独立进程运行，与 SSH 通道解耦）。

权重走 pytorch_model.bin（torch>=2.6 才允许 torch.load 加载，见 CVE-2025-32434；
embed-requirements.txt 已锁 torch 2.7.1）。allow_patterns 排除 safetensors/onnx，
避免重复下载 2.2GB 级大文件；小文件（tokenizer/1_Pooling 等）断点续传补齐。

完成（含首次加载自检）后写 /data/offercoach/models_ready 标记；
deploy.py embed [3/5] 轮询该标记。
"""

from __future__ import annotations

import os
import pathlib

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from huggingface_hub import snapshot_download  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

MODELS_DIR = "/data/offercoach/models"
READY_MARK = "/data/offercoach/models_ready"

# 排除 safetensors/onnx 大文件：权重用已下载的 pytorch_model.bin
snapshot_download(
    "BAAI/bge-m3",
    cache_dir=MODELS_DIR,
    allow_patterns=["*.json", "*.model", "*.txt", "*.md", "*.bin"],
)
# 本地加载自检（不再触网）
SentenceTransformer("BAAI/bge-m3", cache_folder=MODELS_DIR, local_files_only=True)
pathlib.Path(READY_MARK).write_text("ready", encoding="utf-8")
print("MODEL_READY")
