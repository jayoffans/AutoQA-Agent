"""Start a disposable CI MySQL container with per-run random, masked credentials."""
import base64
import os
import secrets
import subprocess
import time
from pathlib import Path

import pymysql
from sqlalchemy import URL


def provision():
    password = secrets.token_urlsafe(32)
    if os.getenv("GITHUB_ACTIONS") == "true":
        print("::add-mask::" + password, flush=True)
    name = "autoqa-auth-ci-" + secrets.token_hex(6)
    env = {**os.environ, "MYSQL_ROOT_PASSWORD": password, "MYSQL_ROOT_HOST": "%"}
    process = subprocess.run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::3306",
        "-e", "MYSQL_ROOT_PASSWORD", "-e", "MYSQL_ROOT_HOST", "mysql:8.4"], env=env, capture_output=True)
    if process.returncode:
        raise RuntimeError("CI MySQL container could not start (output withheld)")
    try:
        address = subprocess.check_output(["docker", "port", name, "3306"], text=True).strip()
        port = int(address.rsplit(":", 1)[1])
        deadline = time.monotonic() + 150
        while True:
            try:
                connection = pymysql.connect(host="127.0.0.1", port=port, user="root", password=password,
                                             autocommit=True, connect_timeout=2)
                break
            except pymysql.Error:
                if time.monotonic() >= deadline:
                    raise RuntimeError("CI MySQL did not become ready (credentials withheld)") from None
                time.sleep(1)
        databases = {"AUTOQA_TEST_COMPAT_MYSQL_ADMIN_URL": "test_autoqa",
            "AUTOQA_TEST_MYSQL_URL": "test_autoqa_demo", "AUTOQA_TEST_PRODUCT_MYSQL_URL": "test_autoqa_product",
            "AUTOQA_TEST_AUTH_MYSQL_URL": "test_autoqa_auth"}
        with connection.cursor() as cursor:
            for database in databases.values():
                cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        connection.close()
        master_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        if os.getenv("GITHUB_ACTIONS") == "true":
            print("::add-mask::" + master_key, flush=True)
        values = {"AUTOQA_CI_MYSQL_CONTAINER": name, "AUTOQA_REQUIRE_MYSQL": "1",
                  "AUTOQA_CI_SECRET_KEY": master_key}
        for variable, database in databases.items():
            url = URL.create("mysql+pymysql", username="root", password=password, host="127.0.0.1", port=port,
                database=database, query={"charset": "utf8mb4"}).render_as_string(False)
            if os.getenv("GITHUB_ACTIONS") == "true":
                print("::add-mask::" + url, flush=True)
            values[variable] = url
        env_file = os.getenv("GITHUB_ENV")
        if not env_file:
            raise RuntimeError("This helper requires the GitHub Actions environment file")
        with Path(env_file).open("a", encoding="utf-8") as output:
            output.write("".join(f"{key}={value}\n" for key, value in values.items()))
        print("Disposable MySQL ready; credentials masked")
    except BaseException:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True)
        raise


if __name__ == "__main__":
    provision()
