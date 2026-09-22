"""Creation-only defaults; existing scenarios keep their own immutable bindings."""
from copy import deepcopy

from . import environments, prepared_requests


def base_defaults():
    return dict(engine='K6', environment=None, global_environment=None,
        account_pool_version=None, account_pool_group='',
        load_config=dict(model='CONCURRENCY', concurrency=1, iterations_per_vu=1,
                         duration=30, ramp_up=0, max_requests=0),
        runtime_config=dict(timeout=30, sample_interval=1, keep_alive=True, proxy=''),
        perf_targets=dict(max_p95_rt=2000, max_avg_rt=None, min_tps=None, max_error_rate=0),
        sla_config=dict(enabled=True, thresholds={'p95_response_time': 2000, 'error_rate': 0}, step_thresholds=[], abort_delay=0,
                        abort_on_breach=False, breach_window=10),
        variables=[], env_config=dict(base_url='', headers={}))


def for_project(project, user):
    environments.require_project_access(project.pk, user)
    result = base_defaults()
    revision, config = prepared_requests._config(project)
    source = 'none'
    if revision:
        context, _, _ = prepared_requests._context(project, config, user)
        for key in ('environment', 'global_environment', 'account_pool_version', 'account_pool_group'):
            result[key] = context[key]
        result['runtime_config'].update(context['runtime_config'])
        source = 'project-pool'
    else:
        preferred = list(environments.accessible_environments(user).filter(
            project=project, scope='PROJECT', is_active=True).values_list('pk', flat=True)[:2])
        if len(preferred) == 1:
            result['environment'] = preferred[0]
            source = 'preferred-environment'
    return dict(project=project.pk, config_revision=revision, source=source, defaults=result)


def creation_data(data, project, user):
    result = deepcopy(data)
    if result.get('engine', 'K6') != 'K6':
        return result
    defaults = for_project(project, user)['defaults']
    # 显式空值也归用户所有；部分手工绑定不能继承原账号池的认证变量。
    binding = ('environment', 'global_environment', 'account_pool_version', 'account_pool_group')
    if any(key in result and result[key] != defaults[key] for key in binding):
        for key in binding:
            defaults.pop(key, None)
        defaults['runtime_config'].pop('auth_profile', None)
        defaults['runtime_config'].pop('account_identity_variable', None)
    for key, value in defaults.items():
        result.setdefault(key, deepcopy(value))
    return result
