# UI自动化元素 `order` 字段迁移说明

## 背景

在 UI 自动化测试模块的元素管理页面，新增元素调用接口 `/api/ui-automation/elements/` 时，可能出现如下 500 报错：

```text
django.db.utils.OperationalError: (1364, "Field 'order' doesn't have a default value")
```

本问题的根因是 `apps/ui_automation/models.py` 中的 `Element` 模型一度缺少 `order` 字段，而数据库表 `ui_elements` 中已经存在该字段，导致 Django ORM 在插入数据时没有为 `order` 赋值，从而触发数据库层报错。

## 结论

- `Element.order` 是有效且必要的字段。
- 迁移文件 `apps/ui_automation/migrations/0013_alter_element_options_element_order.py` 已定义该字段。
- 代码、迁移文件、数据库表结构三者必须保持一致，否则容易出现字段相关错误。

## 影响范围

不同环境下可能出现的问题如下：

### 场景1：数据库有 `order` 字段，但模型缺少该字段

会出现类似以下错误：

```text
Field 'order' doesn't have a default value
```

这正是本次问题的表现。

### 场景2：模型已有 `order` 字段，但数据库没有该字段

通常不会出现上面的报错，但会出现另一类字段错误，例如：

```text
Unknown column 'ui_elements.order' in 'field list'
```

或在查询/新增时出现与 `order` 列不存在相关的异常。

### 场景3：模型、迁移、数据库都一致

不会因为 `order` 字段产生这类错误。

## 本次修复内容

已完成以下修复：

- 在 `apps/ui_automation/models.py` 中恢复 `Element.order` 字段：

```python
order = models.IntegerField(default=0, verbose_name='排序')
```

- 将 `Element` 的默认排序调整为：

```python
ordering = ['page', 'order', 'name']
```

- 将元素列表查询排序调整为：

```python
order_by('page', 'order', 'name')
```

## 部署要求

请确保以下内容全部同步到目标环境：

- 最新的 `apps/ui_automation/models.py`
- 迁移文件 `apps/ui_automation/migrations/0013_alter_element_options_element_order.py`

部署后执行：

```bash
python manage.py migrate
```

## 自查方法

### 1. 检查迁移是否已存在

确认仓库中存在以下文件：

```text
apps/ui_automation/migrations/0013_alter_element_options_element_order.py
```

### 2. 检查迁移是否已执行

执行：

```bash
python manage.py showmigrations ui_automation
```

确认 `0013_alter_element_options_element_order` 前面带有 `[X]`。

### 3. 检查数据库是否已有字段

可在数据库中检查表结构，确认 `ui_elements` 表中存在 `order` 字段。

## 推荐发布步骤

推荐按以下顺序操作：

1. 拉取最新代码
2. 确认迁移文件已纳入版本控制
3. 执行 `python manage.py migrate`
4. 执行 `python manage.py check`
5. 重启服务
6. 在 UI 自动化元素管理页面新增一个元素进行验证

## 建议

为避免后续再次出现模型与迁移不一致的问题，建议在 CI 或发布前检查中加入：

```bash
python manage.py makemigrations --check --dry-run
```

如果该命令返回非零结果，说明当前模型变更尚未形成正式迁移文件，不建议直接发布。
