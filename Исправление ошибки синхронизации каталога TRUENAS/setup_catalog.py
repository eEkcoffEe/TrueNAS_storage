#!/usr/bin/env python3
"""
Автоматическая настройка каталога приложений TrueNAS.

Запускать от root:
    python3 setup_catalog.py
или:
    python3 setup_catalog.py --cron-hour 3 --no-sysctl
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------- Константы ----------
CATALOG_URL = "https://github.com/truenas/apps"
BOOT_DIR = Path("/mnt/boot-pool/ix-apps-catalog")
CATALOG_DIR = BOOT_DIR / "catalogs"
RUN_DIR = Path("/var/run/middleware/ix-apps")
RUN_LINK = RUN_DIR / "catalogs"
WRAPPER = BOOT_DIR / "truenas-catalog-sync.sh"
SYSCTL_VAR = "net.ipv6.conf.all.disable_ipv6"
SYSCTL_VALUE = "1"
SYSCTL_COMMENT = "Disable IPv6 for TrueNAS update/catalog"
CRON_DESC = "Auto Sync TrueNAS Catalog"


def log(msg: str) -> None:
    print(f"[setup_catalog] {msg}", flush=True)


def run(cmd, check=True, capture=False):
    """Обёртка над subprocess.run с логированием."""
    log(f"$ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    return subprocess.run(
        cmd,
        shell=isinstance(cmd, str),
        check=check,
        text=True,
        capture_output=capture,
    )


def is_root() -> bool:
    return os.geteuid() == 0


def midclt_call(method: str, payload: str | None = None):
    """Вызов midclt с проверкой ошибок."""
    cmd = ["midclt", "call", method]
    if payload is not None:
        cmd.append(payload)
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            f"midclt {method} failed: "
            f"{res.stdout.strip()} {res.stderr.strip()}"
        )
    return res.stdout.strip()


# ---------- Шаги ----------
def step_configure_git():
    log("Настраиваю Git на HTTP/1.1 (обход обрывов с HTTP/2)")
    run(["git", "config", "--system", "http.version", "HTTP/1.1"])


def step_prepare_dirs():
    log("Создаю директорию для каталога")
    CATALOG_DIR.parent.mkdir(parents=True, exist_ok=True)


def step_clone_catalog(force: bool = False):
    if CATALOG_DIR.exists() and any(CATALOG_DIR.iterdir()):
        if not force:
            log(f"Каталог уже существует: {CATALOG_DIR} — пропускаю клон")
            return
        log("Удаляю существующий каталог (--force)")
        shutil.rmtree(CATALOG_DIR)

    log("Клонирую репозиторий TrueNAS apps")
    run([
        "git", "clone", "--depth=1",
        CATALOG_URL, str(CATALOG_DIR),
    ])


def step_create_symlink():
    log("Создаю симлинк в /var/run")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    if RUN_LINK.is_symlink() or RUN_LINK.exists():
        if RUN_LINK.is_symlink():
            RUN_LINK.unlink()
        else:
            shutil.rmtree(RUN_LINK)
    RUN_LINK.symlink_to(CATALOG_DIR)
    log(f"  {RUN_LINK} -> {CATALOG_DIR}")


def step_sysctl_ipv6(enable: bool = True):
    """Добавляет tunable для отключения IPv6 через midclt tunable.create."""
    if not enable:
        log("Sysctl IPv6 — пропущено по флагу --no-sysctl")
        return

    log("Проверяю наличие tunable для отключения IPv6")
    try:
        existing = midclt_call("tunable.query")
        tunables = json.loads(existing) if existing else []
    except Exception as e:
        log(f"  Не удалось получить список tunable: {e}")
        tunables = []

    for t in tunables:
        if t.get("var") == SYSCTL_VAR:
            log(f"  Tunable {SYSCTL_VAR} уже существует (id={t['id']}) — пропускаю")
            return

    log(f"Создаю tunable: {SYSCTL_VAR}={SYSCTL_VALUE}")
    payload = json.dumps({
        "type": "SYSCTL",
        "var": SYSCTL_VAR,
        "value": SYSCTL_VALUE,
        "comment": SYSCTL_COMMENT,
        "enabled": True,
    })
    try:
        midclt_call("tunable.create", payload)
        log("  Tunable создан успешно")
    except Exception as e:
        log(f"  ОШИБКА при создании tunable: {e}")
        log("  Добавьте вручную: System Settings → Advanced → Sysctl")
        log(f"  Variable: {SYSCTL_VAR}, Value: {SYSCTL_VALUE}")


def step_create_wrapper():
    log(f"Создаю shell-обёртку {WRAPPER}")
    BOOT_DIR.mkdir(parents=True, exist_ok=True)
    WRAPPER.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # Авто-синхронизация каталога TrueNAS.
        # Создано setup_catalog.py
        set -e
        # Восстанавливаем симлинк (на случай перезагрузки)
        mkdir -p {RUN_DIR}
        if [ ! -e {RUN_LINK} ]; then
            ln -s {CATALOG_DIR} {RUN_LINK}
        fi
        /usr/bin/midclt call catalog.sync >/dev/null 2>&1
    """))
    WRAPPER.chmod(0o755)
    log(f"  Обёртка создана: {WRAPPER}")


def step_create_cron(hour: int = 3):
    log(f"Создаю Cron-задачу: ежедневно в {hour:02d}:00")

    # Удаляем старую задачу с тем же описанием, если есть
    try:
        jobs_raw = midclt_call(
            "cronjob.query",
            json.dumps([["description", "=", CRON_DESC]]),
        )
        jobs = json.loads(jobs_raw) if jobs_raw else []
    except Exception as e:
        log(f"  Не удалось получить список cron задач: {e}")
        jobs = []

    for job in jobs:
        log(f"  Удаляю старую задачу id={job['id']}")
        try:
            midclt_call("cronjob.delete", str(job["id"]))
        except Exception as e:
            log(f"  Не удалось удалить: {e}")

    cron_payload = json.dumps({
        "enabled": True,
        "description": CRON_DESC,
        "command": str(WRAPPER),
        "user": "root",
        "schedule": {
            "minute": "0",
            "hour": str(hour),
            "dom": "*",
            "month": "*",
            "dow": "*",
        },
    })
    try:
        midclt_call("cronjob.create", cron_payload)
        log("  Cron-задача создана успешно")
    except Exception as e:
        log(f"  ОШИБКА при создании Cron-задачи: {e}")
        log(f"  Создайте вручную: System Settings → Advanced → Cron Jobs")
        log(f"  Command: {WRAPPER}, User: root, Schedule: 0 {hour} * * *")


def step_sync_now():
    log("Запускаю первичную синхронизацию каталога")
    try:
        out = midclt_call("catalog.sync")
        log(f"  Ответ: {out}")
        log("  Проверить статус задания:")
        log(f"    midclt call core.get_jobs '[[\"id\", \"=\", {out}]]'")
    except Exception as e:
        log(f"  ОШИБКА синхронизации: {e}")


# ---------- main ----------
def main():
    parser = argparse.ArgumentParser(
        description="Автонастройка каталога приложений TrueNAS"
    )
    parser.add_argument("--force", action="store_true",
                        help="Переклонировать каталог, даже если он есть")
    parser.add_argument("--no-sysctl", action="store_true",
                        help="Не трогать Sysctl (IPv6)")
    parser.add_argument("--no-cron", action="store_true",
                        help="Не создавать Cron-задачу")
    parser.add_argument("--cron-hour", type=int, default=3,
                        help="Час запуска Cron (0-23), по умолчанию 3")
    parser.add_argument("--skip-clone", action="store_true",
                        help="Не клонировать, использовать существующий каталог")
    args = parser.parse_args()

    if not is_root():
        log("ОШИБКА: скрипт должен запускаться от root")
        sys.exit(1)

    if shutil.which("git") is None:
        log("ОШИБКА: git не найден в PATH")
        sys.exit(1)

    if shutil.which("midclt") is None:
        log("ПРЕДУПРЕЖДЕНИЕ: midclt не найден — часть шагов будет пропущена")

    log("=== Начинаю настройку ===")
    step_configure_git()
    step_prepare_dirs()
    if not args.skip_clone:
        step_clone_catalog(force=args.force)
    else:
        log("Клонирование пропущено (--skip-clone)")
    step_create_symlink()
    step_sysctl_ipv6(enable=not args.no_sysctl)
    step_create_wrapper()
    if not args.no_cron:
        step_create_cron(hour=args.cron_hour)
    else:
        log("Cron-задача пропущена (--no-cron)")
    step_sync_now()
    log("=== Готово ===")
    log("Проверьте Apps в веб-интерфейсе.")


if __name__ == "__main__":
    main()