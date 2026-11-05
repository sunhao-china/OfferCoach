"""服务器上创建 offercoach 库（只动 offercoach，绝不触碰 fin 库）。

用法：MYSQL_PWD=<root密码> python scripts/deploy/create_db.py
"""

from __future__ import annotations

import os
import sys

import pymysql

try:
    conn = pymysql.connect(
        host="127.0.0.1", port=3306, user="root", password=os.environ["MYSQL_PWD"]
    )
except KeyError:
    sys.exit("缺少环境变量 MYSQL_PWD")
except pymysql.MySQLError as exc:
    sys.exit(f"MySQL 连接失败：{exc}")
with conn.cursor() as cursor:
    cursor.execute(
        "CREATE DATABASE IF NOT EXISTS offercoach CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
conn.commit()
conn.close()
print("offercoach 库就绪")
