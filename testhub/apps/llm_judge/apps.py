from django.apps import AppConfig


class LLMJudgeConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.llm_judge'
    verbose_name = '智能评分器'

    def ready(self):
        try:
            from . import signals  # noqa: F401
        except ImportError:
            pass
