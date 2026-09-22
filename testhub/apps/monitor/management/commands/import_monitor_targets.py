"""
批量导入监控目标。

用法：
    # 仅预览，不写入（推荐先跑一次确认）
    python manage.py import_monitor_targets docs/monitor-targets-import.json --dry-run

    # 真实写入（密码在 check_config 中以明文提供，入库时由模型 save() 自动 Fernet 加密）
    python manage.py import_monitor_targets docs/monitor-targets-import.json

    # 指定创建人（可选）
    python manage.py import_monitor_targets docs/monitor-targets-import.json --created-by admin

JSON 格式：顶层为数组，每个元素字段：
    name              目标名称（唯一，重复则更新）
    type              探测类型：LOGIN / HTTP / ONLINE / DOCKER / SL651（默认 LOGIN）
    url               探测 URL
    method            请求方法（LOGIN/HTTP 默认 POST，其余 GET）
    interval_seconds  轮询间隔秒（默认 60）
    alert_threshold   告警阈值-连续失败次数（默认 3）
    enabled           是否启用（默认 true）
    check_config      探测配置 JSON（含 password 等敏感字段，入库自动加密）

幂等性：以 name 为唯一键，重复执行只会更新、不会新建重复记录。
"""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.monitor.models import MonitorTarget

VALID_TYPES = {"LOGIN", "HTTP", "ONLINE", "DOCKER", "SL651"}


class Command(BaseCommand):
    help = "批量导入监控目标（JSON 数组）。密码入库时自动 Fernet 加密。"

    def add_arguments(self, parser):
        parser.add_argument("json_path", help="JSON 文件路径（顶层为数组）")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="仅预览将被创建/更新的记录，不写入数据库",
        )
        parser.add_argument(
            "--created-by", type=str, default=None,
            help="指定创建人用户名（可选，不填则创建人为空）",
        )

    def handle(self, *args, **options):
        path = options["json_path"]
        dry = options["dry_run"]
        created_by_username = options.get("created_by")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise CommandError(f"读取 JSON 失败: {e}")

        if not isinstance(data, list):
            raise CommandError("JSON 顶层必须是数组（目标列表）")

        user = None
        if created_by_username:
            U = get_user_model()
            user = U.objects.filter(username=created_by_username).first()
            if not user:
                raise CommandError(f"找不到用户: {created_by_username}")

        created = updated = skipped = 0
        errors = []

        for i, t in enumerate(data, 1):
            name = (t.get("name") or "").strip()
            if not name:
                errors.append(f"第{i}条缺少 name，已跳过")
                skipped += 1
                continue

            ttype = (t.get("type") or "LOGIN").strip().upper()
            if ttype not in VALID_TYPES:
                errors.append(f"[{name}] 非法类型 '{ttype}'，已跳过")
                skipped += 1
                continue

            defaults = {
                "type": ttype,
                "url": t.get("url", "") or "",
                "method": t.get("method", "POST" if ttype in ("LOGIN", "HTTP") else "GET"),
                "interval_seconds": int(t.get("interval_seconds", 60) or 60),
                "alert_threshold": int(t.get("alert_threshold", 3) or 3),
                "enabled": bool(t.get("enabled", True)),
                "check_config": t.get("check_config", {}) or {},
            }
            if user:
                defaults["created_by"] = user

            exists = MonitorTarget.objects.filter(name=name).exists()

            if dry:
                tag = "[更新]" if exists else "[新建]"
                self.stdout.write(
                    f"{tag} {name} | {ttype} | {defaults['url']} | "
                    f"间隔{defaults['interval_seconds']}s | 阈值{defaults['alert_threshold']}"
                )
                if exists:
                    updated += 1
                else:
                    created += 1
                continue

            try:
                with transaction.atomic():
                    obj, was_created = MonitorTarget.objects.update_or_create(
                        name=name, defaults=defaults
                    )
                if was_created:
                    created += 1
                    self.stdout.write(self.style.SUCCESS(f"[新建] {name}"))
                else:
                    updated += 1
                    self.stdout.write(self.style.WARNING(f"[更新] {name}"))
            except Exception as e:
                errors.append(f"[{name}] 保存失败: {e}")
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"\n完成: 新建 {created}, 更新 {updated}, 跳过 {skipped}"
                + ("（DRY-RUN，未写入）" if dry else "")
            )
        )
        for e in errors:
            self.stdout.write(self.style.ERROR(e))
