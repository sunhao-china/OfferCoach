"""OfferCoach 服务器部署工具（paramiko）。

凭据一律从环境变量读取，绝不写入仓库文件：
  OC_SSH_PASS           服务器 root 密码
  OC_MYSQL_PASS         服务器 MySQL root 密码（建 offercoach 库与题库导入用）
  OC_DEEPSEEK_KEY       DEEPSEEK_API_KEY（deploy 写入服务器 /data/offercoach.env）
  OC_LANGFUSE_SECRET    Langfuse 密钥（deploy 写入服务器 /data/offercoach.env）
  OC_LANGFUSE_PUBLIC    Langfuse 公钥（deploy 写入服务器 /data/offercoach.env）
用法：
  python scripts/deploy/deploy.py recon <host>
  python scripts/deploy/deploy.py exec <host> "<远程命令>"
  python scripts/deploy/deploy.py deploy <host>
  python scripts/deploy/deploy.py embed <host>  # bge-m3 服务（独立 venv，127.0.0.1:8090）
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REMOTE_DIR = "/data/offercoach"
ENV_FILE = "/data/offercoach.env"
SERVICE = "/etc/systemd/system/offercoach.service"
MYSQL_DB_URL = "mysql+pymysql://root:{pwd}@127.0.0.1:3306/offercoach?charset=utf8mb4"

# 上传清单：代码 + 配置 + 题库数据（vendor 源数据仅重取需要，不上传；会话库不传，服务器全新开始）
UPLOAD = ["app", "scripts", "pyproject.toml", "data/offercoach_dev.db", "data/question_bank"]
EXCLUDE_DIRS = {"__pycache__", ".venv", "tests", "specs", "vendor"}

SERVICE_UNIT = (
    "[Unit]\nDescription=OfferCoach FastAPI\nAfter=network.target\n\n"
    "[Service]\n"
    f"WorkingDirectory={REMOTE_DIR}\nEnvironmentFile={ENV_FILE}\n"
    f"ExecStart={REMOTE_DIR}/.venv/bin/python scripts/run_server.py\n"
    "Restart=always\nRestartSec=3\n\n[Install]\nWantedBy=multi-user.target\n"
)

EMBED_VENV = "/data/offercoach/embed-venv"
EMBED_SERVICE = "/etc/systemd/system/offercoach-embed.service"
EMBED_UNIT = (
    "[Unit]\nDescription=OfferCoach bge-m3 Embedding\nAfter=network.target\n\n"
    "[Service]\n"
    f"WorkingDirectory={REMOTE_DIR}\n"
    "Environment=HF_HUB_OFFLINE=1\nEnvironment=HF_ENDPOINT=https://hf-mirror.com\n"
    f"ExecStart={EMBED_VENV}/bin/python scripts/embed_server.py\n"
    "Restart=always\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target\n"
)


def ssh(host: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username="root", password=os.environ["OC_SSH_PASS"], timeout=15)
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 900) -> tuple[int, str, str]:
    """执行命令，返回 (exit_code, stdout, stderr)。

    stdout/stderr 并发排空：静默长任务（pip/torch/模型下载的进度只在 stderr）
    不再触发通道读超时；超时按整体墙钟计。
    """
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    channel = stdout.channel
    chunks_out: list[bytes] = []
    chunks_err: list[bytes] = []
    deadline = time.time() + timeout
    while not channel.exit_status_ready() and time.time() < deadline:
        while channel.recv_ready():
            chunks_out.append(channel.recv(65536))
        while channel.recv_stderr_ready():
            chunks_err.append(channel.recv_stderr(65536))
        time.sleep(0.2)
    # 进程刚退出时缓冲可能还有数据，收尾排空
    while channel.recv_ready():
        chunks_out.append(channel.recv(65536))
    while channel.recv_stderr_ready():
        chunks_err.append(channel.recv_stderr(65536))
    if not channel.exit_status_ready():
        channel.close()
        raise TimeoutError(f"命令执行超过 {timeout}s：{command[:80]}…")
    code = channel.recv_exit_status()
    out = b"".join(chunks_out).decode(errors="replace")
    err = b"".join(chunks_err).decode(errors="replace")
    if out.strip():
        print(out.strip())
    if err.strip():
        print(err.strip(), file=sys.stderr)
    return code, out, err


def cmd_recon(host: str) -> None:
    client = ssh(host)
    print(f"=== {host} 侦察 ===")
    run(
        client,
        "cat /etc/os-release | head -2; uname -m; nproc; free -m | head -2; "
        "df -h / | tail -1; systemctl --version 2>/dev/null | head -1 || echo 'no systemd'; "
        "python3 --version 2>&1; python3 -m pip --version 2>&1 | head -1; "
        "which python3.11 python3.12 2>/dev/null; "
        "which mysql mysqld redis-server docker qdrant 2>/dev/null; "
        "ls /data 2>/dev/null || echo '/data 不存在'; "
        "ss -tlnp 2>/dev/null | head -15; "
        "getent hosts pypi.org >/dev/null && echo 'pypi DNS OK'; "
        "curl -sI --max-time 8 https://pypi.org/simple/ 2>&1 | head -1",
    )
    client.close()


def _tar_payload() -> bytes:
    """把 UPLOAD 清单打成 tar.gz 字节流（排除缓存与 vendor 源目录）。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for item in UPLOAD:
            path = PROJECT_ROOT / item
            tar.add(
                path,
                arcname=item,
                filter=lambda info: (
                    None if any(part in EXCLUDE_DIRS for part in Path(info.name).parts) else info
                ),
            )
    return buffer.getvalue()


def _put(client: paramiko.SSHClient, content: str | bytes, remote_path: str) -> None:
    # putfo 自带创建/截断；sftp.open("w") 无 O_CREAT，新文件会 ENOENT
    sftp = client.open_sftp()
    try:
        payload = content if isinstance(content, bytes) else content.encode("utf-8")
        sftp.putfo(io.BytesIO(payload), remote_path)
        sftp.close()
    except Exception:
        sftp.close()
        raise


def _upload_payload(client: paramiko.SSHClient) -> None:
    payload = _tar_payload()
    print(f"上传包 {len(payload) / 1024 / 1024:.1f} MB …")
    _put(client, payload, "/tmp/offercoach.tar.gz")
    run(
        client,
        f"mkdir -p {REMOTE_DIR} && tar -xzf /tmp/offercoach.tar.gz -C {REMOTE_DIR} "
        "&& rm /tmp/offercoach.tar.gz",
    )


def _remote_env_value(client: paramiko.SSHClient, key: str) -> str:
    """读取服务器现有 env 文件的键值（凭据服务器侧继承）。

    直接 exec 不经过 run()——run() 会打印输出，密钥绝不能进本地日志。
    """
    _, stdout, _ = client.exec_command(
        f"grep -oP '^{key}=\\K.*' {ENV_FILE} 2>/dev/null || true", timeout=30
    )
    lines = stdout.read().decode(errors="replace").strip().splitlines()
    return lines[-1] if lines else ""


def cmd_deploy(host: str) -> None:
    mysql_pass = os.environ["OC_MYSQL_PASS"]
    db_url = MYSQL_DB_URL.format(pwd=mysql_pass)
    client = ssh(host)
    print(f"=== 部署到 {host} ===")

    print("[1/8] apt 安装 Python 3.11（deadsnakes PPA 已配置）…")
    run(
        client,
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive "
        "apt-get install -y -qq python3.11 python3.11-venv && python3.11 --version",
    )

    print("[2/8] 上传代码与题库数据 …")
    _upload_payload(client)

    print("[3/8] venv + 锁定依赖安装 …")
    run(
        client,
        f"cd {REMOTE_DIR} && python3.11 -m venv .venv && "
        ".venv/bin/pip install -q --upgrade pip && "
        ".venv/bin/pip install -q -r scripts/deploy/requirements.txt",
    )

    print("[4/8] MySQL 建库（只动 offercoach）…")
    create_db = (
        f"cd {REMOTE_DIR} && MYSQL_PWD='{mysql_pass}' "
        ".venv/bin/python scripts/deploy/create_db.py"
    )
    run(client, create_db)

    print("[5/8] 题库导入（seed_v1 + java_guide_v1 + 三期多岗位 v3_*）…")
    for data_dir in (
        "seed_v1",
        "java_guide_v1",
        "v3_python_backend",
        "v3_c_backend",
        "v3_cpp_backend",
        "v3_csharp_backend",
        "v3_ai_agent",
        "v3_project_manager",
    ):
        run(
            client,
            f"cd {REMOTE_DIR} && if [ -d data/question_bank/{data_dir} ]; then "
            f"DATABASE_URL='{db_url}' .venv/bin/python "
            f"scripts/import_questions.py --data-dir data/question_bank/{data_dir}; fi",
        )

    print("[6/8] 环境文件（仓库外 600）+ systemd unit …")
    deepseek_key = os.environ.get("OC_DEEPSEEK_KEY") or _remote_env_value(
        client, "DEEPSEEK_API_KEY"
    )
    langfuse_secret = os.environ.get("OC_LANGFUSE_SECRET") or _remote_env_value(
        client, "LANGFUSE_SECRET_KEY"
    )
    langfuse_public = os.environ.get("OC_LANGFUSE_PUBLIC") or _remote_env_value(
        client, "LANGFUSE_PUBLIC_KEY"
    )
    if not (deepseek_key and langfuse_secret and langfuse_public):
        sys.exit("DEEPSEEK/LANGFUSE 凭据在本地环境与服务器 env 文件均缺失")
    env_content = (
        f"DATABASE_URL={db_url}\n"
        f"DEEPSEEK_API_KEY={deepseek_key}\n"
        f"LANGFUSE_SECRET_KEY={langfuse_secret}\n"
        f"LANGFUSE_PUBLIC_KEY={langfuse_public}\n"
        "LANGFUSE_BASE_URL=https://us.cloud.langfuse.com\n"
        "OFFERCOACH_HOST=0.0.0.0\nOFFERCOACH_PORT=8002\n"
    )
    _put(client, env_content, ENV_FILE)
    _put(client, SERVICE_UNIT, SERVICE)
    run(client, f"chmod 600 {ENV_FILE}")

    print("[7/8] 启动服务 …")
    run(
        client,
        "systemctl daemon-reload && systemctl enable offercoach && "
        "systemctl restart offercoach && sleep 4 && systemctl is-active offercoach",
    )
    run(client, "journalctl -u offercoach -n 20 --no-pager | tail -20")

    print("[8/8] 本机 healthz …")
    run(client, "curl -s -o /dev/null -w 'healthz HTTP %{http_code}\\n' http://127.0.0.1:8002/healthz")
    client.close()


def cmd_embed(host: str) -> None:
    """bge-m3 embedding 服务：独立 venv（torch 隔离）+ 模型下载 + systemd + 冒烟。"""
    client = ssh(host)
    print(f"=== bge-m3 embedding 部署到 {host} ===")

    print("[1/5] 上传脚本 …")
    _upload_payload(client)

    print("[2/5] 独立 venv + 依赖安装（torch 较大，耐心）…")
    run(
        client,
        f"cd {REMOTE_DIR} && python3.11 -m venv {EMBED_VENV} && "
        f"{EMBED_VENV}/bin/pip install -q --upgrade pip && "
        f"{EMBED_VENV}/bin/pip install -q -r scripts/deploy/embed-requirements.txt",
        timeout=3600,
    )

    print("[3/5] 下载 bge-m3 模型（hf-mirror 镜像，约 2.2GB，断点续传）…")
    run(
        client,
        f"mkdir -p {REMOTE_DIR}/models && rm -f {REMOTE_DIR}/models_ready && "
        f"nohup {EMBED_VENV}/bin/python {REMOTE_DIR}/scripts/deploy/download_model.py "
        f"> {REMOTE_DIR}/models_download.log 2>&1 &",
    )
    deadline = time.time() + 3 * 3600
    ready = False
    while time.time() < deadline:
        time.sleep(30)
        _, out, _ = run(client, f"test -f {REMOTE_DIR}/models_ready && echo ready")
        if "ready" in out:
            ready = True
            break
        _, out, _ = run(
            client, "pgrep -f download_model.py > /dev/null && echo downloading || echo dead"
        )
        if "dead" in out:
            break
    if not ready:
        _, _, err = run(client, f"tail -30 {REMOTE_DIR}/models_download.log")
        sys.exit(f"模型下载失败或超时（3 小时），日志尾部：{err}")

    print("[4/5] systemd unit + 启动 …")
    _put(client, EMBED_UNIT, EMBED_SERVICE)
    run(
        client,
        "systemctl daemon-reload && systemctl enable --now offercoach-embed",
    )
    # 模型加载需 1-3 分钟：轮询 is-active，失败即打印日志退出（不许静默假成功）
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(10)
        _, out, _ = run(client, "systemctl is-active offercoach-embed")
        if "active" in out:
            break
        if "failed" in out:
            _, _, err = run(client, "journalctl -u offercoach-embed -n 20 --no-pager")
            sys.exit(f"offercoach-embed 启动失败：{err}")
    else:
        sys.exit("offercoach-embed 10 分钟仍未就绪（activating 超时）")

    print("[5/5] 冒烟：healthz + 两段文本 1024 维 …")
    _, out, err = run(
        client,
        f"{EMBED_VENV}/bin/python -c \"import json,urllib.request; "
        "print(urllib.request.urlopen('http://127.0.0.1:8090/healthz').read().decode()); "
        "req=urllib.request.Request('http://127.0.0.1:8090/embed', "
        "data=json.dumps({'texts':['Java 并发','线程池调优']}).encode(), "
        "headers={'Content-Type':'application/json'}); "
        "resp=json.load(urllib.request.urlopen(req)); "
        "print('dims', [len(v) for v in resp['vectors']])\"",
        timeout=300,
    )
    if '"ok"' not in out or "dims [1024, 1024]" not in out:
        sys.exit(f"embed 冒烟失败：{out or err}")
    print("冒烟通过（healthz ok + 两段文本均为 1024 维）")
    client.close()


def cmd_exec(host: str, command: str) -> None:
    client = ssh(host)
    run(client, command)
    client.close()


def cmd_put(host: str, local_path: str, remote_path: str) -> None:
    """上传单个文件（冒烟脚本等临时用途）。"""
    client = ssh(host)
    _put(client, Path(local_path).read_text(encoding="utf-8"), remote_path)
    print(f"已上传 {local_path} → {remote_path}")
    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["recon", "deploy", "exec", "embed", "put"])
    parser.add_argument("host")
    parser.add_argument("remote_command", nargs="?", default="")
    parser.add_argument("--put-local", default="")
    parser.add_argument("--put-remote", default="")
    args = parser.parse_args()
    if "OC_SSH_PASS" not in os.environ:
        sys.exit("缺少环境变量 OC_SSH_PASS")
    if args.command == "deploy" and "OC_MYSQL_PASS" not in os.environ:
        sys.exit("缺少环境变量 OC_MYSQL_PASS")
    if args.command == "exec":
        cmd_exec(args.host, args.remote_command)
    elif args.command == "recon":
        cmd_recon(args.host)
    elif args.command == "embed":
        cmd_embed(args.host)
    elif args.command == "put":
        if not args.put_local or not args.put_remote:
            sys.exit(
                "用法：python scripts/deploy/deploy.py put <host> "
                "--put-local <本地> --put-remote <远程>"
            )
        cmd_put(args.host, args.put_local, args.put_remote)
    else:
        cmd_deploy(args.host)


if __name__ == "__main__":
    main()
