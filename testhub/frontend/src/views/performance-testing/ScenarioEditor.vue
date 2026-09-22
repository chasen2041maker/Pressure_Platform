<template>
  <div class="perf-scenario-editor" v-loading="loading">
    <el-alert v-if="hydrationError" class="environment-alert" type="error" :closable="false" show-icon :title="hydrationError">
      <el-button :loading="loading" @click="load">{{ t('performanceTesting.common.retry') }}</el-button>
    </el-alert>
    <el-alert v-if="startUnconfirmed" class="environment-alert" type="warning" :closable="false" show-icon
      title="启动结果尚未确认，请先查看执行历史或当前运行；不要重复启动。">
      <el-button type="primary" @click="viewExecutionHistory">查看执行历史 / 当前运行</el-button>
    </el-alert>
    <el-alert v-if="isNew" class="environment-alert" type="info" :closable="false" show-icon
      title="新场景默认 1 个并发、1 轮、最多 30 秒，目标 P95 2000ms、错误率 0%；均可修改，目标值不代表已验证通过。" />
    <el-alert v-if="defaultsError" class="environment-alert" type="error" :closable="false" show-icon :title="defaultsError">
      <el-button :loading="defaultsLoading" @click="loadProjectDefaults(form.project)">{{ t('performanceTesting.common.retry') }}</el-button>
    </el-alert>
    <!-- ---------- 顶栏 ---------- -->
    <div class="editor-header" :inert="editorBlocked || catalogUpdating || importVisible || diffVisible">
      <div class="header-left">
        <el-button link :icon="ArrowLeft" @click="goBack">{{ t('performanceTesting.common.back') }}</el-button>
        <el-divider direction="vertical" />
        <el-input
          v-model="form.name"
          class="name-input"
          :placeholder="t('performanceTesting.scenario.name')"
          maxlength="200"
          @input="markDirty"
        />
        <el-tag v-if="dirty" type="warning" size="small" effect="plain">
          {{ t('performanceTesting.editor.unsaved') }}
        </el-tag>
      </div>
      <div class="header-right">
        <div class="editor-context">
          <el-select
            v-if="isNew"
            v-model="form.project"
            class="hd-select"
            :loading="projectsLoading"
            :disabled="projectsLoading || projectsLoadError || !projectsLoaded"
            :placeholder="t('performanceTesting.common.selectProject')"
          >
            <el-option v-for="p in projects" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <el-select v-model="form.engine" class="hd-select-sm">
            <el-option
              v-for="opt in engineOptions"
              :key="opt.value"
              :value="opt.value"
              :disabled="!opt.available"
              :label="opt.label"
            >
              <span>{{ opt.label }}</span>
              <span v-if="!opt.available" class="engine-na">{{ t('performanceTesting.engine.unavailable') }}</span>
            </el-option>
          </el-select>
        </div>
        <div class="editor-actions">
          <el-button :icon="Tools" :loading="debugging" :disabled="!canDebug" @click="handleDebug">
            {{ form.engine === 'K6' ? t('performanceTesting.editor.debugK6') : t('performanceTesting.scenario.debug') }}
          </el-button>
          <el-button type="primary" :icon="Check" :loading="saving" :disabled="editorBlocked || !scenarioReady || environmentBlocked || projectSelectionBlocked || accountPoolBlocked" @click="handleSave()">
            {{ t('performanceTesting.common.save') }}
          </el-button>
          <el-button type="danger" :icon="Odometer" :disabled="!canExecute" @click="handleSaveAndExecute">
            {{ t('performanceTesting.editor.saveAndExecute') }}
          </el-button>
        </div>
      </div>
    </div>

    <el-alert v-if="projectsLoadError" class="environment-alert" type="error" :closable="false" show-icon
      :title="t('performanceTesting.environment.projectOptionsLoadFailed')">
      <el-button link type="primary" :loading="projectsLoading" @click="loadProjects">{{ t('performanceTesting.common.retry') }}</el-button>
    </el-alert>
    <el-alert v-else-if="isNew && projectsLoaded && !projects.length" class="environment-alert" type="info" :closable="false" show-icon
      :title="t('performanceTesting.environment.noAuthorizedProjects')">
      <el-button link type="primary" @click="router.push('/performance-testing/projects')">{{ t('performanceTesting.project.create') }}</el-button>
      <el-button link type="primary" :loading="projectsLoading" @click="loadProjects">{{ t('performanceTesting.common.refresh') }}</el-button>
    </el-alert>

    <div class="editor-body" :inert="editorBlocked || catalogUpdating || importVisible || diffVisible">
      <!-- ---------- 左侧步骤列表 ---------- -->
      <div class="step-panel">
        <div class="panel-head">
          <span class="panel-title">{{ t('performanceTesting.editor.steps') }}</span>
          <span class="panel-count">{{ steps.length }}</span>
        </div>
        <div class="panel-tip">{{ t('performanceTesting.editor.dragTip') }}</div>

        <el-scrollbar class="step-scroll">
          <div v-if="!steps.length" class="step-empty">
            {{ t('performanceTesting.editor.noSteps') }}
          </div>
          <draggable
            v-else
            v-model="steps"
            item-key="_uid"
            handle=".drag-handle"
            :animation="160"
            @change="markDirty"
          >
            <template #item="{ element, index }">
              <div
                class="step-row"
                :class="{ active: index === activeIndex, disabled: !element.enabled }"
                @click="selectStep(index)"
              >
                <el-icon class="drag-handle"><Rank /></el-icon>
                <span class="step-idx">{{ index + 1 }}</span>
                <div class="step-main">
                  <div class="step-name">
                    <el-tag v-if="element.is_setup" size="small" type="warning" effect="plain">
                      {{ t('performanceTesting.editor.setupSteps') }}
                    </el-tag>
                    {{ element.name || t('performanceTesting.common.unnamed') }}
                  </div>
                  <div class="step-url">
                    <span class="method" :class="'m-' + (element.method || 'GET').toLowerCase()">
                      {{ element.protocol === 'WEBSOCKET' ? 'WS' : element.protocol === 'SSE' ? 'SSE' : element.method || 'GET' }}
                    </span>
                    <span class="url-text">{{ element.url || '-' }}</span>
                  </div>
                </div>
                <el-dropdown trigger="click" @command="(c) => onStepCommand(c, index)">
                  <el-icon class="step-more" @click.stop><MoreFilled /></el-icon>
                  <template #dropdown>
                    <el-dropdown-menu>
                      <el-dropdown-item command="toggle">
                        {{ element.enabled ? t('performanceTesting.common.no') : t('performanceTesting.common.yes') }}
                        · {{ t('performanceTesting.editor.stepEnabled') }}
                      </el-dropdown-item>
                      <el-dropdown-item command="copy">{{ t('performanceTesting.common.copy') }}</el-dropdown-item>
                      <el-dropdown-item command="delete" divided>
                        {{ t('performanceTesting.common.delete') }}
                      </el-dropdown-item>
                    </el-dropdown-menu>
                  </template>
                </el-dropdown>
              </div>
            </template>
          </draggable>
        </el-scrollbar>

        <div class="panel-foot">
          <el-button size="small" :icon="Plus" @click="addStep">
            {{ t('performanceTesting.editor.addStep') }}
          </el-button>
          <el-button size="small" :icon="Download" @click="openImport">
            {{ t('performanceTesting.editor.importFromApi') }}
          </el-button>
        </div>
      </div>

      <!-- ---------- 右侧主区 ---------- -->
      <div class="main-panel">
        <div v-if="isK6" class="capability-summary" aria-live="polite">
          <div class="capability-heading">
            <span>{{ capabilitySummary }}</span>
            <el-button link type="primary" :loading="engineStatusLoading" @click="loadEngineStatus">
              {{ t('performanceTesting.capabilities.refresh') }}
            </el-button>
          </div>
          <el-alert v-if="proxyBlocked" type="warning" :closable="false" show-icon
            :title="t('performanceTesting.capabilities.proxyBlocked')" />
          <el-collapse v-if="k6Capabilities" class="capability-details">
            <el-collapse-item :title="t('performanceTesting.capabilities.details')" name="capabilities">
              <div class="capability-version">
                <span>k6 {{ k6Capabilities.runtime.binary_version || t('performanceTesting.capabilities.unknown') }}</span>
                <span>{{ t('performanceTesting.capabilities.adapter') }} {{ k6Capabilities.runtime.adapter_version }}</span>
                <span>{{ k6Capabilities.runtime.mode || t('performanceTesting.capabilities.unknown') }}</span>
                <span v-if="k6Capabilities.runtime.source_commit">{{ k6Capabilities.runtime.source_commit }}</span>
                <span v-if="k6Capabilities.runtime.image_id">{{ k6Capabilities.runtime.image_id }}</span>
              </div>
              <p>{{ t('performanceTesting.capabilities.scriptNotice') }}</p>
              <ul class="capability-list">
                <li v-for="item in k6Capabilities.items" :key="item.id">
                  <div class="capability-row">
                    <span>{{ t(`performanceTesting.capabilities.names.${item.id}`) }}</span>
                    <el-tag size="small" :type="item.enabled ? 'success' : 'info'" effect="plain">
                      {{ t(`performanceTesting.capabilities.states.${item.form_status}`) }}
                    </el-tag>
                  </div>
                  <div v-if="!item.enabled" class="capability-reason">{{ capabilityReason(item.reason_code) }}</div>
                </li>
              </ul>
            </el-collapse-item>
          </el-collapse>
        </div>
        <el-alert v-if="!isNew && !loading && !scenarioReady" type="error" :closable="false"
          :title="t('performanceTesting.capabilities.scenarioLoadFailed')" />
        <el-alert v-if="environmentState.legacyBlocked" class="environment-alert" type="warning" :closable="false" show-icon
          :title="t('performanceTesting.environment.legacyBlocked')">
          <el-button :disabled="loading || saving" @click="clearEnvironmentReferences">{{ t('performanceTesting.environment.clearReferences') }}</el-button>
        </el-alert>
        <el-alert v-else-if="isK6 && (environmentLoadError || environmentState.invalid)" class="environment-alert" type="error" :closable="false" show-icon
          :title="t(environmentLoadError ? 'performanceTesting.environment.optionsLoadFailed' : 'performanceTesting.environment.invalidSelection')">
          <el-button link type="primary" :loading="environmentLoading" @click="loadEnvironmentOptions(form.project)">{{ t('performanceTesting.common.retry') }}</el-button>
          <el-button v-if="environmentState.invalid" link type="primary" @click="clearEnvironmentReferences">{{ t('performanceTesting.environment.clearReferences') }}</el-button>
        </el-alert>
        <el-alert v-if="accountPoolNotice" class="environment-alert" :type="accountPoolBlocked ? 'error' : 'warning'" :closable="false" show-icon :title="accountPoolNotice">
          <el-button link type="primary" @click="activeTab = 'env'">{{ t('performanceTesting.accountPool.selection') }}</el-button>
        </el-alert>
        <section v-if="isK6 && hasCatalogSteps" class="scenario-readiness" aria-live="polite">
          <div class="readiness-actions">
            <strong>{{ t('performanceTesting.catalog.scenarioPreparation') }}</strong>
            <el-tag :type="readinessStale ? 'warning' : readinessBlocked ? 'danger' : 'success'">{{ t(`performanceTesting.catalog.${readinessStale ? 'stale' : readinessBlocked ? 'needsPreparation' : 'ready'}`) }}</el-tag>
            <el-button size="small" :loading="readinessLoading" :disabled="saving || debugging || executing" @click="checkReadiness">{{ t('performanceTesting.catalog.checkPreparation') }}</el-button>
            <el-button size="small" :disabled="saving || debugging || executing" @click="openCatalogDiff">{{ t('performanceTesting.catalog.sourceChanges') }}</el-button>
          </div>
          <p class="form-tip">{{ t('performanceTesting.catalog.enabledOnly') }}</p>
          <el-alert v-if="readinessError" :title="readinessError" type="error" :closable="false" />
          <ul v-if="!readinessStale" class="readiness-gaps"><li v-for="item in scenarioReadiness.filter(item => !item.ready)" :key="item.step_id">
            <el-button link type="primary" @click="selectStep(steps.findIndex(step => step.id === item.step_id))">{{ steps.find(step => step.id === item.step_id)?.name || item.step_id }}</el-button>
            <span v-for="gap in item.gaps" :key="gap.code + gap.field"> · {{ gap.field }}: {{ gap.message }}</span>
          </li></ul>
        </section>
        <el-tabs v-model="activeTab" class="main-tabs">
          <!-- 请求编排 -->
          <el-tab-pane :label="t('performanceTesting.editor.tabRequest')" name="request">
            <div v-if="!currentStep" class="tab-empty">
              {{ t('performanceTesting.editor.noSteps') }}
            </div>
            <StepEditor
              v-else
              :key="currentStep._uid"
              v-model="steps[activeIndex]"
              :upload-files="uploadFiles"
              :is-k6="isK6"
              :access-variable="websocketAccessVariable"
              :readiness="scenarioReadiness.find(item => item.step_id === currentStep.id) || null"
              :readiness-stale="readinessStale"
              @update:modelValue="markDirty"
              @upload-file="handleUploadFile"
            />
          </el-tab-pane>

          <!-- 压力策略 -->
          <el-tab-pane :label="t('performanceTesting.editor.tabLoad')" name="load">
            <!-- Locust/JMeter 引擎只实现了固定并发模型，非固定并发会被预检拦截，
                 提前在这里提示，避免用户配了半天策略最后才发现不生效（脚本模式不受限） -->
            <el-alert
              v-if="engineModelMismatch"
              type="warning"
              show-icon
              :closable="false"
              class="engine-model-alert"
              :title="t('performanceTesting.editor.engineModelMismatch', { engine: t('performanceTesting.engine.' + form.engine) })"
            />
            <LoadProfileEditor v-model="form.load_config" :engine="form.engine" :limits="limits" @update:modelValue="markDirty" />
            <p v-if="isK6" class="capability-reason">{{ t('performanceTesting.capabilities.loadNotice') }}</p>
          </el-tab-pane>

          <!-- 变量与环境 -->
          <el-tab-pane :label="t('performanceTesting.editor.tabEnv')" name="env">
            <div class="pane-body">
              <AccountPoolSelector v-if="isK6 || form.account_pool_version || form.account_pool_group"
                v-model="form.account_pool_version" v-model:group="form.account_pool_group"
                :project="form.project" :engine="form.engine" :concurrency="Number(form.load_config.concurrency || 1)"
                :variable-names="accountVariableNames" :has-legacy-csv="form.variables.some(variable => variable.type === 'CSV')"
                :disabled="loading || saving || debugging || executing" @state="accountPoolState = $event"
                @update:modelValue="markDirty" @update:group="markDirty" />
              <AuthProfileEditor v-if="isK6" :model-value="form.runtime_config.auth_profile || {}"
                :saved-revision="authSavedRevision"
                :identity="form.runtime_config.account_identity_variable || ''" :sources="authenticationSources"
                :source-loading="!!accountPoolState?.loading" :source-error="!!accountPoolState?.error"
                :disabled="loading || saving || debugging || executing"
                @update:model-value="value => { form.runtime_config.auth_profile = value; markDirty() }"
                @update:identity="value => { form.runtime_config.account_identity_variable = value; markDirty() }"
                @validity="authFormValid = $event" @edit="markDirty" />
              <ReminderRecoveryEditor v-if="isK6 || form.runtime_config.resource_recovery"
                :model-value="form.runtime_config.resource_recovery || {}" :steps="steps" :engine="form.engine"
                :concurrency="Number(form.load_config.concurrency || 1)" :disabled="loading || saving || debugging || executing"
                @update:model-value="value => { form.runtime_config.resource_recovery = value; markDirty() }" />
              <template v-if="isK6 || form.environment || form.global_environment">
                <div class="block-title">{{ t('performanceTesting.environment.selection') }}</div>
                <el-form label-width="140px" class="env-form">
                  <el-form-item :label="t('performanceTesting.environment.GLOBAL')">
                    <el-select v-model="form.global_environment" clearable filterable :disabled="!isK6 || environmentLoading || !!environmentLoadError" :loading="environmentLoading" :placeholder="t('performanceTesting.environment.noSelection')" @change="markDirty">
                      <el-option v-for="env in globalEnvironments" :key="env.id" :value="env.id" :label="environmentLabel(env)" />
                    </el-select>
                  </el-form-item>
                  <el-form-item :label="t('performanceTesting.environment.PROJECT')">
                    <el-select v-model="form.environment" clearable filterable :disabled="!isK6 || !form.project || environmentLoading || !!environmentLoadError" :loading="environmentLoading" :placeholder="t('performanceTesting.environment.noSelection')" @change="markDirty">
                      <el-option v-for="env in projectEnvironments" :key="env.id" :value="env.id" :label="environmentLabel(env)" />
                    </el-select>
                    <div v-if="!environmentLoading && !environmentLoadError && !projectEnvironments.length" class="form-tip">{{ t('performanceTesting.environment.noProjectEnvironments') }}</div>
                  </el-form-item>
                  <el-form-item>
                    <el-button link type="primary" @click="router.push({ path: '/performance-testing/environments', query: { project: form.project } })">{{ t('performanceTesting.environment.manage') }}</el-button>
                    <el-button link type="primary" :loading="environmentLoading" @click="loadEnvironmentOptions(form.project)">{{ t('performanceTesting.common.refresh') }}</el-button>
                    <div class="form-tip">{{ t('performanceTesting.environment.precedenceTip') }}</div>
                  </el-form-item>
                </el-form>
                <el-alert v-if="environmentPending" class="environment-alert" type="info" :closable="false" :title="t('performanceTesting.environment.pendingResolution')" />
                <el-descriptions v-else-if="savedEnvironment" class="environment-preview" :column="1" size="small" border>
                  <el-descriptions-item :label="t('performanceTesting.environment.resolvedBase')">{{ savedEnvironment.env_config?.base_url || t('performanceTesting.environment.absoluteUrls') }}</el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.editor.verifySsl')">{{ effectiveTlsVerified(savedEnvironment.env_config) ? t('performanceTesting.environment.tlsOn') : t('performanceTesting.environment.tlsOff') }}</el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.environment.sources')">
                    <span v-if="!savedEnvironment.sources?.length">{{ t('performanceTesting.environment.inlineOnly') }}</span>
                    <el-tag v-for="source in savedEnvironment.sources" :key="source.id" class="source-tag" size="small" effect="plain">{{ source.name }} · v{{ source.version }}</el-tag>
                  </el-descriptions-item>
                </el-descriptions>
                <p class="form-tip">{{ t('performanceTesting.environment.savedPreviewTip') }}</p>
              </template>
              <div class="block-title">{{ t('performanceTesting.editor.envTitle') }}</div>
              <el-form label-width="140px" class="env-form">
                <el-form-item :label="t('performanceTesting.editor.baseUrl')">
                  <el-input
                    v-model="form.env_config.base_url"
                    placeholder="https://api.example.com"
                    @input="markDirty"
                  />
                  <div class="form-tip">{{ t('performanceTesting.editor.baseUrlTip') }}</div>
                </el-form-item>
                <el-form-item :label="t('performanceTesting.editor.globalHeaders')">
                  <KeyValueEditor v-model="globalHeaders" @update:modelValue="onGlobalHeaders" />
                </el-form-item>
                <el-row :gutter="16">
                  <el-col :span="12">
                    <el-form-item :label="t('performanceTesting.editor.verifySsl')">
                      <el-select v-if="isK6" v-model="tlsMode">
                        <el-option :label="t('performanceTesting.environment.tlsInherit')" value="inherit" />
                        <el-option :label="t('performanceTesting.environment.tlsOn')" value="on" />
                        <el-option :label="t('performanceTesting.environment.tlsOff')" value="off" />
                      </el-select>
                      <el-switch v-else v-model="legacyTls" />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item :label="t('performanceTesting.editor.keepAlive')">
                      <el-switch v-model="form.runtime_config.keep_alive" @change="markDirty" />
                    </el-form-item>
                  </el-col>
                </el-row>
                <el-row :gutter="16">
                  <el-col :span="12">
                    <el-form-item :label="t('performanceTesting.editor.timeout')">
                      <el-input-number
                        v-model="form.runtime_config.timeout"
                        :min="1"
                        :max="300"
                        controls-position="right"
                        @change="markDirty"
                      />
                    </el-form-item>
                  </el-col>
                  <el-col :span="12">
                    <el-form-item :label="t('performanceTesting.editor.sampleInterval')">
                      <el-input-number
                        v-model="form.runtime_config.sample_interval"
                        :min="1"
                        :max="60"
                        controls-position="right"
                        @change="markDirty"
                      />
                    </el-form-item>
                  </el-col>
                </el-row>
                <el-form-item :label="t('performanceTesting.editor.proxy')">
                  <el-input
                    v-model="form.runtime_config.proxy"
                    placeholder="http://127.0.0.1:8888"
                    :disabled="isK6 && !capabilityEnabled('proxy')"
                    @input="markDirty"
                  />
                  <div v-if="isK6" class="capability-reason">
                    {{ capabilityReason(k6Capabilities?.items.find(item => item.id === 'proxy')?.reason_code) }}
                  </div>
                  <el-button v-if="proxyBlocked" :disabled="loading || !scenarioReady || saving"
                    @click="clearProxy">
                    {{ t('performanceTesting.capabilities.clearProxy') }}
                  </el-button>
                </el-form-item>
              </el-form>

              <div class="block-title">
                {{ t('performanceTesting.editor.variables') }}
                <span class="block-tip">{{ t('performanceTesting.editor.variableTip') }}</span>
              </div>
              <el-table :data="form.variables" size="small" border class="var-table">
                <el-table-column :label="t('performanceTesting.editor.variableName')" width="180">
                  <template #default="{ row }">
                    <el-input v-model="row.name" size="small" @input="markDirty" />
                  </template>
                </el-table-column>
                <el-table-column :label="t('performanceTesting.editor.variableType')" width="150">
                  <template #default="{ row }">
                    <el-select v-model="row.type" size="small" @change="markDirty">
                      <el-option v-for="vt in variableTypes" :key="vt" :label="vt" :value="vt"
                        :disabled="isK6 && !['CONSTANT', 'CSV'].includes(vt)" />
                    </el-select>
                  </template>
                </el-table-column>
                <el-table-column :label="t('performanceTesting.editor.variableValue')">
                  <template #default="{ row }">
                    <!-- 固定值 -->
                    <el-input
                      v-if="row.type === 'CONSTANT'"
                      v-model="row.value"
                      size="small"
                      :type="row.secret ? 'password' : 'text'"
                      show-password
                      @input="markDirty"
                    />
                    <!-- 随机整数 -->
                    <div v-else-if="row.type === 'RANDOM_INT'" class="inline-fields">
                      <el-input-number v-model="row.min" size="small" controls-position="right" @change="markDirty" />
                      <span class="sep">~</span>
                      <el-input-number v-model="row.max" size="small" controls-position="right" @change="markDirty" />
                    </div>
                    <!-- 随机字符串 -->
                    <div v-else-if="row.type === 'RANDOM_STRING'" class="inline-fields">
                      <el-input-number
                        v-model="row.length"
                        :min="1"
                        :max="256"
                        size="small"
                        controls-position="right"
                        @change="markDirty"
                      />
                      <el-select v-model="row.charset" size="small" style="width: 120px" @change="markDirty">
                        <el-option label="alnum" value="alnum" />
                        <el-option label="alpha" value="alpha" />
                        <el-option label="digit" value="digit" />
                      </el-select>
                    </div>
                    <!-- 枚举轮询 -->
                    <div v-else-if="row.type === 'ENUM'" class="inline-fields">
                      <el-input v-if="row.values === '******'" model-value="******" type="password" readonly />
                      <el-select
                        v-else
                        v-model="row.values"
                        multiple
                        filterable
                        allow-create
                        default-first-option
                        size="small"
                        style="flex: 1"
                        :placeholder="t('performanceTesting.editor.variableValue')"
                        @change="markDirty"
                      />
                      <el-select v-model="row.strategy" size="small" style="width: 130px" @change="markDirty">
                        <el-option label="ROUND_ROBIN" value="ROUND_ROBIN" />
                        <el-option label="RANDOM" value="RANDOM" />
                      </el-select>
                    </div>
                    <!-- 时间戳 -->
                    <el-select
                      v-else-if="row.type === 'TIMESTAMP'"
                      v-model="row.format"
                      size="small"
                      @change="markDirty"
                    >
                      <el-option label="ms" value="ms" />
                      <el-option label="s" value="s" />
                    </el-select>
                    <!-- CSV -->
                    <div v-else-if="row.type === 'CSV'" class="inline-fields">
                      <el-select
                        v-model="row.data_file_id"
                        size="small"
                        style="flex: 1"
                        :placeholder="t('performanceTesting.editor.varCsv')"
                        @change="markDirty"
                      >
                        <el-option v-for="f in dataFiles" :key="f.id" :label="f.name" :value="f.id" />
                      </el-select>
                      <el-select
                        v-model="row.column"
                        size="small"
                        style="width: 140px"
                        clearable
                        @change="markDirty"
                      >
                        <el-option v-for="c in columnsOf(row.data_file_id)" :key="c" :label="c" :value="c" />
                      </el-select>
                    </div>
                    <span v-else class="auto-hint">{{ row.type }}</span>
                  </template>
                </el-table-column>
                <el-table-column width="70" align="center">
                  <template #header>
                    <el-tooltip :content="t('performanceTesting.editor.variableValue')" placement="top">
                      <span>🔒</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">
                    <el-switch v-model="row.secret" size="small" @change="markDirty" />
                  </template>
                </el-table-column>
                <el-table-column width="70" align="center" :label="t('performanceTesting.common.actions')">
                  <template #default="{ $index }">
                    <el-button link type="danger" :icon="Delete" @click="removeVariable($index)" />
                  </template>
                </el-table-column>
              </el-table>
              <el-button size="small" :icon="Plus" class="add-row-btn" @click="addVariable">
                {{ t('performanceTesting.editor.addRow') }}
              </el-button>
            </div>
          </el-tab-pane>

          <!-- SLA 阈值 -->
          <el-tab-pane :label="t('performanceTesting.editor.tabSla')" name="sla">
            <div class="pane-body">
              <el-form label-width="200px" class="sla-form">
                <el-form-item :label="t('performanceTesting.sla.enable')">
                  <el-switch v-model="form.sla_config.enabled" @change="markDirty" />
                </el-form-item>
                <template v-if="form.sla_config.enabled">
                  <SlaRuleEditor v-if="isK6" v-model="form.sla_config" :steps="steps" @change="markDirty" />
                  <template v-else>
                  <el-form-item :label="t('performanceTesting.sla.p95ResponseTime')">
                    <el-input-number
                      v-model="form.sla_config.thresholds.p95_response_time"
                      :min="0"
                      :step="100"
                      controls-position="right"
                      @change="markDirty"
                    />
                  </el-form-item>
                  <el-form-item :label="t('performanceTesting.sla.avgResponseTime')">
                    <el-input-number
                      v-model="form.sla_config.thresholds.avg_response_time"
                      :min="0"
                      :step="100"
                      controls-position="right"
                      @change="markDirty"
                    />
                  </el-form-item>
                  <el-form-item :label="t('performanceTesting.sla.errorRate')">
                    <el-input-number
                      v-model="form.sla_config.thresholds.error_rate"
                      :min="0"
                      :max="100"
                      :precision="2"
                      :step="0.1"
                      controls-position="right"
                      @change="markDirty"
                    />
                  </el-form-item>
                  <el-form-item :label="t('performanceTesting.sla.minTps')">
                    <el-input-number
                      v-model="form.sla_config.thresholds.min_tps"
                      :min="0"
                      :step="10"
                      controls-position="right"
                      @change="markDirty"
                    />
                  </el-form-item>
                  </template>
                  <el-form-item :label="t('performanceTesting.sla.abortOnBreach')">
                    <el-switch v-model="form.sla_config.abort_on_breach" @change="markDirty" />
                    <div class="form-tip">{{ t('performanceTesting.sla.abortTip') }}</div>
                  </el-form-item>
                  <el-form-item
                    v-if="form.sla_config.abort_on_breach"
                    :label="t('performanceTesting.sla.breachWindow')"
                  >
                    <el-input-number
                      v-model="form.sla_config.breach_window"
                      :min="1"
                      :max="600"
                      controls-position="right"
                      @change="markDirty"
                    />
                    <div class="form-tip">{{ t('performanceTesting.sla.breachWindowTip') }}</div>
                  </el-form-item>
                </template>
              </el-form>
            </div>
          </el-tab-pane>

          <!-- 验收目标 -->
          <el-tab-pane label="验收目标" name="targets">
            <div class="pane-body">
              <el-form label-width="200px" class="sla-form">
                <div class="form-tip" style="margin-bottom: 16px;">
                  验收目标用于判定压测结果是否"通过"。执行完成后自动评估，结果在报告中显示。
                  留空表示不评估该指标。
                </div>
                <el-form-item label="P95 响应时间上限 (ms)">
                  <el-input-number
                    v-model="form.perf_targets.max_p95_rt"
                    :min="0"
                    :step="100"
                    placeholder="如 2000"
                    controls-position="right"
                    @change="markDirty"
                  />
                  <span class="form-tip" style="margin-left: 8px;">任一步骤 P95 超过即未通过</span>
                </el-form-item>
                <el-form-item label="平均响应时间上限 (ms)">
                  <el-input-number
                    v-model="form.perf_targets.max_avg_rt"
                    :min="0"
                    :step="100"
                    placeholder="如 1000"
                    controls-position="right"
                    @change="markDirty"
                  />
                  <span class="form-tip" style="margin-left: 8px;">任一步骤平均响应时间超过即未通过</span>
                </el-form-item>
                <el-form-item label="TPS 下限">
                  <el-input-number
                    v-model="form.perf_targets.min_tps"
                    :min="0"
                    :step="10"
                    placeholder="如 100"
                    controls-position="right"
                    @change="markDirty"
                  />
                  <span class="form-tip" style="margin-left: 8px;">整体 TPS 低于即未通过</span>
                </el-form-item>
                <el-form-item label="错误率上限 (%)">
                  <el-input-number
                    v-model="form.perf_targets.max_error_rate"
                    :min="0"
                    :max="100"
                    :precision="2"
                    :step="0.1"
                    placeholder="如 1.0"
                    controls-position="right"
                    @change="markDirty"
                  />
                  <span class="form-tip" style="margin-left: 8px;">任一步骤错误率超过即未通过</span>
                </el-form-item>
              </el-form>
            </div>
          </el-tab-pane>

          <!-- JMeter 脚本（仅 JMeter 引擎可见） -->
          <el-tab-pane v-if="isJmeter" :label="t('performanceTesting.script.tab')" name="script">
            <div class="pane-body">
              <div class="block-title">
                {{ t('performanceTesting.script.title') }}
                <span class="block-tip">{{ t('performanceTesting.script.tip') }}</span>
              </div>

              <el-radio-group v-model="scriptMode" class="script-mode" @change="onScriptModeChange">
                <el-radio-button value="scenario">{{ t('performanceTesting.script.modeScenario') }}</el-radio-button>
                <el-radio-button value="script">{{ t('performanceTesting.script.modeScript') }}</el-radio-button>
              </el-radio-group>
              <div class="form-tip">
                {{ scriptMode === 'script'
                  ? t('performanceTesting.script.modeScriptTip')
                  : t('performanceTesting.script.modeScenarioTip') }}
              </div>

              <template v-if="scriptMode === 'script'">
                <div class="script-toolbar">
                  <el-select
                    v-model="selectedScriptId"
                    class="script-select"
                    filterable
                    clearable
                    :placeholder="t('performanceTesting.script.selectPlaceholder')"
                    @change="onScriptSelected"
                  >
                    <el-option v-for="f in scriptFiles" :key="f.id" :label="f.name" :value="f.id">
                      <span>{{ f.name }}</span>
                      <span class="script-opt-meta">
                        {{ (f.meta && f.meta.sampler_count) || 0 }} {{ t('performanceTesting.script.samplers') }}
                      </span>
                    </el-option>
                  </el-select>
                  <el-upload
                    :show-file-list="false"
                    accept=".jmx"
                    :before-upload="handleJmxUpload"
                    :disabled="!form.project"
                  >
                    <el-button :icon="Upload" :loading="uploadingScript" :disabled="!form.project">
                      {{ t('performanceTesting.script.upload') }}
                    </el-button>
                  </el-upload>
                  <el-button
                    :icon="Delete"
                    :disabled="!selectedScriptId"
                    @click="handleDeleteScript"
                  >
                    {{ t('performanceTesting.script.remove') }}
                  </el-button>
                  <el-button :icon="Refresh" @click="loadScriptFiles(form.project)">
                    {{ t('performanceTesting.common.refresh') }}
                  </el-button>
                </div>
                <div v-if="!form.project" class="form-tip warn">
                  {{ t('performanceTesting.scenario.projectRequired') }}
                </div>

                <el-alert
                  v-if="selectedScript"
                  type="info"
                  :closable="false"
                  show-icon
                  class="script-alert"
                  :title="t('performanceTesting.script.overrideNotice')"
                />

                <el-descriptions
                  v-if="selectedScriptMeta"
                  :column="2"
                  border
                  size="small"
                  class="script-desc"
                >
                  <el-descriptions-item :label="t('performanceTesting.script.planName')">
                    {{ selectedScriptMeta.test_plan_name || '-' }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.script.jmeterVersion')">
                    {{ selectedScriptMeta.jmeter_version || '-' }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.script.totalThreads')">
                    {{ selectedScriptMeta.total_threads === null
                      ? t('performanceTesting.script.dynamic')
                      : selectedScriptMeta.total_threads }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.script.maxDuration')">
                    {{ selectedScriptMeta.max_duration === null
                      ? t('performanceTesting.script.dynamic')
                      : formatDuration(selectedScriptMeta.max_duration || 0) }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.script.samplerCount')">
                    {{ selectedScriptMeta.sampler_count || 0 }}
                  </el-descriptions-item>
                  <el-descriptions-item :label="t('performanceTesting.script.hosts')">
                    {{ (selectedScriptMeta.hosts || []).join('、') || t('performanceTesting.script.dynamic') }}
                  </el-descriptions-item>
                </el-descriptions>

                <el-table
                  v-if="selectedScriptMeta && (selectedScriptMeta.thread_groups || []).length"
                  :data="selectedScriptMeta.thread_groups"
                  size="small"
                  border
                  class="script-table"
                >
                  <el-table-column prop="name" :label="t('performanceTesting.script.tgName')" min-width="160" />
                  <el-table-column prop="type" :label="t('performanceTesting.script.tgType')" width="180" />
                  <el-table-column :label="t('performanceTesting.script.tgThreads')" width="100">
                    <template #default="{ row }">{{ row.num_threads === null ? '—' : row.num_threads }}</template>
                  </el-table-column>
                  <el-table-column :label="t('performanceTesting.script.tgRamp')" width="100">
                    <template #default="{ row }">{{ row.ramp_time === null ? '—' : row.ramp_time }}</template>
                  </el-table-column>
                  <el-table-column :label="t('performanceTesting.script.tgDuration')" width="110">
                    <template #default="{ row }">{{ row.duration === null ? '—' : row.duration }}</template>
                  </el-table-column>
                  <el-table-column :label="t('performanceTesting.script.tgEnabled')" width="90">
                    <template #default="{ row }">
                      <el-tag :type="row.enabled ? 'success' : 'info'" size="small" effect="plain">
                        {{ row.enabled ? t('performanceTesting.script.on') : t('performanceTesting.script.off') }}
                      </el-tag>
                    </template>
                  </el-table-column>
                </el-table>

                <div
                  v-if="selectedScriptMeta && selectedScriptMeta.has_dynamic_props"
                  class="form-tip warn"
                >
                  {{ t('performanceTesting.script.dynamicWarn') }}
                </div>
                <el-empty
                  v-if="!selectedScriptId"
                  :description="t('performanceTesting.script.empty')"
                />
              </template>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </div>

    <!-- ---------- 从接口导入 ---------- -->
    <ImportFromApiDrawer v-model="importVisible" :scenario-id="scenarioId" :project-id="form.project" @imported="onImported" @manage="router.push('/performance-testing/projects')" />
    <el-drawer v-model="diffVisible" :title="t('performanceTesting.catalog.sourceChanges')" size="min(700px, 96vw)" :close-on-click-modal="!catalogUpdating" :close-on-press-escape="!catalogUpdating" :show-close="!catalogUpdating">
      <p>{{ t('performanceTesting.catalog.syncHint') }}</p>
      <el-alert v-if="diffError" type="error" :closable="false" :title="diffError" />
      <el-button :loading="diffLoading" :disabled="catalogUpdating" @click="loadCatalogDiff">{{ t('performanceTesting.common.refresh') }}</el-button>
      <el-empty v-if="!diffLoading && !diffError && !catalogDiff.some(item => item.changed)" :description="t('performanceTesting.catalog.noChanges')" />
      <div v-for="item in catalogDiff.filter(item => item.changed)" :key="item.step_id" class="catalog-diff-item">
        <el-checkbox :model-value="selectedDiffIds.includes(item.step_id)" :disabled="item.removed || catalogUpdating" @change="checked => toggleSourceStep(item.step_id, checked)">{{ item.source_key }} · v{{ item.from_version }} → v{{ item.to_version }}</el-checkbox>
        <el-alert v-if="item.removed" type="warning" :closable="false" :title="t('performanceTesting.catalog.removedSource')" />
        <div v-for="field in item.fields" :key="field.field" class="diff-field">
          <strong>{{ field.field }}</strong><el-tag v-if="field.customized" size="small" type="warning">{{ t('performanceTesting.catalog.customized') }}</el-tag>
          <div class="diff-values"><div><small>{{ t('performanceTesting.catalog.before') }}</small><pre>{{ JSON.stringify(field.before, null, 2) }}</pre></div><div><small>{{ t('performanceTesting.catalog.after') }}</small><pre>{{ JSON.stringify(field.after, null, 2) }}</pre></div></div>
        </div>
      </div>
      <template #footer><el-button type="primary" :disabled="!selectedDiffIds.length || diffLoading || !!diffError" :loading="catalogUpdating" @click="confirmCatalogUpdate">{{ t('performanceTesting.catalog.confirmSync', { count: selectedDiffIds.length }) }}</el-button></template>
    </el-drawer>

    <!-- ---------- 调试结果 ---------- -->
    <el-drawer v-model="debugVisible" :title="t('performanceTesting.editor.debugTitle')" size="60%">
      <div v-if="debugging" class="debug-loading">{{ t('performanceTesting.editor.debugRunning') }}</div>
      <template v-else-if="debugResult">
        <el-alert
          :type="debugResult.passed ? 'success' : 'error'"
          :closable="false"
          show-icon
          :title="debugResult.passed
            ? t('performanceTesting.editor.debugPassed')
            : t('performanceTesting.editor.debugFailed', { count: debugResult.failed_count })"
          :description="debugResult.engine === 'JMETER'
            ? (debugResult.jmx_valid
                ? t('performanceTesting.editor.jmxValid')
                : t('performanceTesting.editor.jmxInvalid'))
            : `${t('performanceTesting.editor.debugElapsed')}: ${debugResult.elapsed_ms} ms`"
        />
        <el-collapse class="debug-list">
          <el-collapse-item v-for="(s, i) in debugResult.steps" :key="i" :name="i">
            <template #title>
              <el-icon :class="(s.success ?? s.ok) ? 'ok' : 'bad'">
                <component :is="(s.success ?? s.ok) ? CircleCheck : CircleClose" />
              </el-icon>
              <span class="dbg-name">{{ i + 1 }}. {{ s.name }}</span>
              <el-tag size="small" effect="plain">{{ s.method || (debugResult.engine === 'JMETER' ? 'JMX' : '') }}</el-tag>
              <template v-if="s.status_code !== undefined">
                <span class="dbg-code" :class="{ bad: s.status_code >= 400 || !s.status_code }">
                  {{ s.status_code }}
                </span>
              </template>
              <template v-else-if="debugResult.engine === 'JMETER'">
                <span class="dbg-code">校验</span>
              </template>
              <span v-if="s.elapsed_ms !== undefined" class="dbg-time">{{ s.elapsed_ms }} ms</span>
            </template>
            <div class="dbg-body">
              <div class="dbg-line"><b>URL</b><span>{{ s.url }}</span></div>
              <div v-if="s.error" class="dbg-line err"><b>Error</b><span>{{ s.error }}</span></div>
              <div v-if="s.assertion_message" class="dbg-line err">
                <b>{{ t('performanceTesting.editor.debugAssertion') }}</b><span>{{ s.assertion_message }}</span>
              </div>
              <div v-if="s.extracted && Object.keys(s.extracted).length" class="dbg-line">
                <b>{{ t('performanceTesting.editor.debugExtracted') }}</b>
                <span>{{ JSON.stringify(s.extracted) }}</span>
              </div>
              <div class="dbg-sub">{{ t('performanceTesting.editor.debugRequest') }}</div>
              <pre class="dbg-pre">{{ prettify(s.request_body) || '-' }}</pre>
              <div class="dbg-sub">{{ t('performanceTesting.editor.debugResponse') }}</div>
              <pre class="dbg-pre">{{ prettify(s.response_body) || '-' }}</pre>
            </div>
          </el-collapse-item>
        </el-collapse>
      </template>
      <el-empty v-else :description="t('performanceTesting.editor.debugEmpty')" />
    </el-drawer>

    <!-- ---------- 执行确认 ---------- -->
    <el-dialog v-model="execVisible" :title="t('performanceTesting.execute.title')" width="560px">
      <div v-loading="preflighting">
        <el-alert
          v-if="preflight"
          :type="preflight.passed ? 'success' : 'error'"
          :closable="false"
          show-icon
          :title="preflight.passed
            ? t('performanceTesting.execute.preflightPassed')
            : t('performanceTesting.execute.preflightFailed')"
        />
        <ul v-if="preflight && preflight.errors.length" class="pf-list err">
          <li v-for="(e, i) in preflight.errors" :key="'e' + i">{{ e }}</li>
        </ul>
        <ul v-if="preflight && preflight.warnings.length" class="pf-list warn">
          <li v-for="(w, i) in preflight.warnings" :key="'w' + i">{{ w }}</li>
        </ul>
        <el-descriptions v-if="preflight" :column="2" border size="small" class="pf-desc">
          <el-descriptions-item :label="t('performanceTesting.execute.estimatedPeak')">
            {{ preflight.estimated.peak_concurrency }}
          </el-descriptions-item>
          <el-descriptions-item :label="preflight.estimated.estimate_kind === 'fixed_iterations' ? '最长运行时限' : t('performanceTesting.execute.estimatedDuration')">
            {{ formatDuration(preflight.estimated.planned_duration) }}
          </el-descriptions-item>
          <el-descriptions-item :label="preflight.estimated.estimate_kind === 'fixed_iterations' ? '计划业务请求数' : t('performanceTesting.execute.estimatedRequests')">
            {{ (preflight.estimated.estimated_requests || 0).toLocaleString() }}
            <div v-if="preflight.estimated.estimate_kind === 'fixed_iterations'" class="text-secondary">
              含前置请求合计 {{ preflight.estimated.estimated_http_requests }} 次
            </div>
          </el-descriptions-item>
          <el-descriptions-item :label="t('performanceTesting.editor.steps')">
            {{ preflight.estimated.step_count }}
          </el-descriptions-item>
        </el-descriptions>
        <p v-if="preflight?.estimated?.request_estimate_note" class="pf-confirm">
          {{ preflight.estimated.request_estimate_note }}
        </p>
        <div class="pf-confirm">{{ t('performanceTesting.execute.confirmTip') }}</div>
      </div>
      <template #footer>
        <el-button @click="execVisible = false">{{ t('performanceTesting.common.cancel') }}</el-button>
        <el-button
          type="danger"
          :disabled="!preflight || !preflight.passed || !canExecute"
          :loading="executing"
          @click="doExecute"
        >
          {{ t('performanceTesting.execute.start') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import SlaRuleEditor from './components/SlaRuleEditor.vue'
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { getScenarioActionState } from './scenarioActionState.mjs'
import { newScenarioForm, manualStepDefaults, mergeProjectDefaults, clearProjectBindings } from './scenarioDefaults.mjs'
import { tlsModeOf, withTlsMode, selectionState, latestRequestGate, effectiveTlsVerified } from './environmentForm.mjs'
import { useRoute, useRouter, onBeforeRouteLeave } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  ArrowLeft, Check, Odometer, Tools, Plus, Delete, Download, Upload, Refresh,
  Rank, MoreFilled, CircleCheck, CircleClose
} from '@element-plus/icons-vue'
import draggable from 'vuedraggable'

import KeyValueEditor from '@/views/api-testing/components/KeyValueEditor.vue'
import StepEditor from './components/StepEditor.vue'
import { websocketDraftErrors, stepForSave } from './websocketStepForm.mjs'
import { sseDraftErrors } from './sseStepForm.mjs'
import LoadProfileEditor from './components/LoadProfileEditor.vue'
import ImportFromApiDrawer from './components/ImportFromApiDrawer.vue'
import AccountPoolSelector from './components/AccountPoolSelector.vue'
import AuthProfileEditor from './components/AuthProfileEditor.vue'
import ReminderRecoveryEditor from './components/ReminderRecoveryEditor.vue'
import { recoveryFormErrors } from './reminderRecovery.mjs'
import { authSources } from './authProfileForm.mjs'
import { formatDuration, apiError } from './shared'
import {
  getEngineStatus, getPerfProjects, getPerfScenario, createPerfScenario,
  updatePerfScenario, savePerfScenarioSteps, preflightPerfScenario,
  executePerfScenario, debugPerfScenario, getPerfDataFiles,
  uploadPerfJmxScript, uploadPerfUploadFile, deletePerfDataFile,
  getPerfEnvironments, getPerfEnvironmentPermissions, getPerfScenarioReadiness,
  getPerfScenarioCatalogDiff, updatePerfScenarioCatalog, getPerfScenarioDefaults
} from '@/api/performance-testing'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()

const VARIABLE_TYPES = ['CONSTANT', 'RANDOM_INT', 'RANDOM_STRING', 'ENUM', 'UUID', 'TIMESTAMP', 'CSV']
const variableTypes = VARIABLE_TYPES

const scenarioId = ref(Number(route.params.id) || 0)
const isNew = computed(() => !scenarioId.value)

const loading = ref(true)
const hydrationError = ref('')
const dependencyErrors = reactive({})
const startUnconfirmed = ref(false)
const editorBlocked = computed(() => loading.value || !scenarioReady.value || !!hydrationError.value
  || Boolean(form.account_pool_version && (!accountPoolState.value || accountPoolState.value.project !== form.project || accountPoolState.value.loading)))
let modelHydrated = false
let loadGeneration = 0
const scenarioReady = ref(false)
const engineStatusLoading = ref(true)
const engineStatusError = ref(false)
const saving = ref(false)
const debugging = ref(false)
const executing = ref(false)
const preflighting = ref(false)
const dirty = ref(false)

const activeTab = ref('request')
const activeIndex = ref(-1)
const steps = ref([])
const projects = ref([])
const projectsLoading = ref(true)
const projectsLoaded = ref(false)
const projectsLoadError = ref(false)
const projectRequests = latestRequestGate()
const defaultsRequests = latestRequestGate()
const defaultsLoading = ref(false), defaultsError = ref('')
let defaultsBaseline = null, defaultsLoadedProject = null
const dataFiles = ref([])
// 步骤 multipart 上传文件（file_type=UPLOAD），下发给 StepEditor 做行内文件选择
const uploadFiles = ref([])
const engines = ref([])
const limits = ref({
  max_concurrency: 1000, max_target_rps: 100000, max_duration: 3600, max_concurrent_executions: 5
})

const importVisible = ref(false)
const scenarioReadiness = ref([])
const readinessLoading = ref(false)
const readinessError = ref('')
const readinessSavedSignature = ref('')
const readinessRequests = latestRequestGate()
const diffRequests = latestRequestGate()
const diffVisible = ref(false), diffLoading = ref(false), diffError = ref('')
const catalogDiff = ref([]), selectedDiffIds = ref([]), diffVersion = ref(null), diffSignature = ref('')
const catalogUpdating = ref(false)
const debugVisible = ref(false)
const debugResult = ref(null)
const execVisible = ref(false)
const preflight = ref(null)

const globalHeaders = ref([])
const environmentOptions = ref([])
const environmentLoading = ref(true)
const environmentLoadError = ref(false)
const environmentLoaded = ref(false)
const environmentRequests = latestRequestGate()
const savedEnvironment = ref(null)
const savedEnvironmentSignature = ref('')
const tlsTouched = ref(false)
const accountPoolState = ref(null)
const authFormValid = ref(true)
const authSavedRevision = ref(0)
const authenticationSources = computed(() => authSources(accountPoolState.value || {}, form.variables))

const form = reactive(newScenarioForm())
const websocketAccessVariable = computed(() => form.runtime_config.auth_profile?.access_token_variable || 'access_token')
const websocketErrors = computed(() => websocketDraftErrors(steps.value, websocketAccessVariable.value))
const sseErrors = computed(() => sseDraftErrors(steps.value))
const recoveryErrors = computed(() => recoveryFormErrors(form.runtime_config.resource_recovery, steps.value, form.engine, Number(form.load_config.concurrency || 1)))

const currentStep = computed(() => steps.value[activeIndex.value] || null)
const isK6 = computed(() => form.engine === 'K6')
const hasCatalogSteps = computed(() => steps.value.some(step => step.source_request || Object.keys(step.source_metadata || {}).length))
const currentContent = () => JSON.stringify({ form: buildPayload(), steps: stepsPayload() })
const readinessStale = computed(() => dirty.value || !readinessSavedSignature.value || currentContent() !== readinessSavedSignature.value)
const readinessBlocked = computed(() => isK6.value && hasCatalogSteps.value && !dirty.value &&
  (readinessLoading.value || !!readinessError.value || readinessStale.value || scenarioReadiness.value.some(item => !item.ready)))
const projectSelectionBlocked = computed(() => isNew.value && (projectsLoading.value || projectsLoadError.value
  || defaultsLoading.value || !!defaultsError.value
  || !projectsLoaded.value || !projects.value.some(project => project.id === form.project)))
const tlsMode = computed({
  get: () => tlsModeOf(form.env_config),
  set: mode => { form.env_config = withTlsMode(form.env_config, mode); tlsTouched.value = true; markDirty() }
})
const legacyTls = computed({
  get: () => Boolean(form.env_config.verify_ssl),
  set: value => { form.env_config.verify_ssl = value; tlsTouched.value = true; markDirty() }
})
const globalEnvironments = computed(() => environmentOptions.value.filter(env => env.scope === 'GLOBAL' && env.project === null))
const projectEnvironments = computed(() => environmentOptions.value.filter(env => env.scope === 'PROJECT' && env.project === form.project))
const environmentState = computed(() => selectionState(form, environmentOptions.value, environmentLoaded.value))
const environmentBlocked = computed(() => environmentState.value.legacyBlocked || (isK6.value
  && (environmentLoading.value || environmentLoadError.value || environmentState.value.invalid)))
const accountVariableNames = computed(() => [...form.variables, ...environmentOptions.value
  .filter(env => env.id === form.environment || env.id === form.global_environment)
  .flatMap(env => env.variables || [])].map(variable => variable.name))
const accountPoolBlocked = computed(() => {
  const state = accountPoolState.value
  if (!form.account_pool_version && !form.account_pool_group) return Boolean(state?.project === form.project && state.pending)
  return !isK6.value || !form.account_pool_version || !state || state.project !== form.project
    || state.versionId !== form.account_pool_version || state.group !== form.account_pool_group
    || state.loading || state.error || state.invalid || accountPoolConflict.value
})
const accountPoolConflict = computed(() => accountPoolState.value?.mappedNames?.some(name => accountVariableNames.value.includes(name)))
const accountPoolDebugBlocked = computed(() => accountPoolBlocked.value || Boolean(form.account_pool_version && !(accountPoolState.value?.capacity >= 1)))
const accountPoolExecuteBlocked = computed(() => accountPoolBlocked.value || Boolean(form.account_pool_version && !(accountPoolState.value?.capacity >= Number(form.load_config.concurrency || 1))))
const accountPoolNotice = computed(() => {
  if (!isK6.value && (form.account_pool_version || form.account_pool_group)) return t('performanceTesting.accountPool.legacyBlocked')
  if (accountPoolBlocked.value) return t(`performanceTesting.accountPool.${accountPoolState.value?.loading ? 'loading' : accountPoolConflict.value ? 'conflict' : 'invalidSelection'}`)
  if (accountPoolExecuteBlocked.value) return t('performanceTesting.accountPool.capacityExceeded', { vus: form.load_config.concurrency, count: accountPoolState.value?.capacity || 0 })
  return ''
})
const environmentSignature = () => JSON.stringify({ project: form.project, engine: form.engine,
  environment: form.environment || null, global_environment: form.global_environment || null,
  env_config: form.env_config, variables: form.variables })
const environmentPending = computed(() => !savedEnvironment.value || environmentSignature() !== savedEnvironmentSignature.value)
const selectedEngine = computed(() => engines.value.find(e => (e.name || e.value || e.key) === form.engine))
const k6Capabilities = computed(() => isK6.value ? selectedEngine.value?.capabilities : null)
const actionsReady = computed(() => !editorBlocked.value && !startUnconfirmed.value && scenarioReady.value && !engineStatusLoading.value
  && !saving.value && !debugging.value && !executing.value && !preflighting.value)
const actionState = computed(() => getScenarioActionState({
  engine: form.engine, proxy: form.runtime_config.proxy, capabilities: k6Capabilities.value,
  actionsReady: actionsReady.value, engineStatusLoading: engineStatusLoading.value
}))
const proxyBlocked = computed(() => actionState.value.proxyBlocked)
const canDebug = computed(() => !recoveryErrors.value.length && !websocketErrors.value.length && !sseErrors.value.length && (!isK6.value || authFormValid.value) && actionState.value.canDebug && !environmentBlocked.value && !projectSelectionBlocked.value && !accountPoolDebugBlocked.value && !readinessBlocked.value && !catalogUpdating.value)
const canExecute = computed(() => !recoveryErrors.value.length && !websocketErrors.value.length && !sseErrors.value.length && (!isK6.value || authFormValid.value) && actionState.value.canExecute && !environmentBlocked.value && !projectSelectionBlocked.value && !accountPoolExecuteBlocked.value && !readinessBlocked.value && !catalogUpdating.value)
const capabilitySummary = computed(() => {
  if (engineStatusLoading.value) return t('performanceTesting.capabilities.loading')
  if (engineStatusError.value || !k6Capabilities.value) return t('performanceTesting.capabilities.loadFailed')
  return k6Capabilities.value.verification.state === 'matched'
    ? t('performanceTesting.capabilities.summary') : capabilityReason(k6Capabilities.value.reason_code)
})

function capabilityEnabled(id) {
  return !engineStatusLoading.value && k6Capabilities.value?.items.some(item => item.id === id && item.enabled) === true
}

function capabilityReason(code) {
  return t(`performanceTesting.capabilities.reasons.${code || 'version_unverified'}`)
}

function clearProxy() {
  if (!proxyBlocked.value || loading.value || !scenarioReady.value || saving.value) return
  form.runtime_config.proxy = ''
  markDirty()
}

// ------------------------------------------------------------------ //
// JMeter 脚本模式：与后端 script_ref = { mode, data_file_id } 一一对应
// ------------------------------------------------------------------ //
const MAX_JMX_SIZE = 10 * 1024 * 1024

const scriptMode = ref('scenario')
const scriptFiles = ref([])
const selectedScriptId = ref(null)
const uploadingScript = ref(false)

const isJmeter = computed(() => form.engine === 'JMETER')
const isScriptMode = computed(() => isJmeter.value && scriptMode.value === 'script')
const selectedScript = computed(
  () => scriptFiles.value.find(f => f.id === selectedScriptId.value) || null)
const selectedScriptMeta = computed(() => selectedScript.value?.meta || null)

// Locust/JMeter 引擎仅实现固定并发压力模型（与后端 preflight 拦截规则一致）；
// JMeter 脚本模式的压力参数来自 .jmx 本身，不受此限制。
const engineModelMismatch = computed(() =>
  ['LOCUST', 'JMETER', 'K6'].includes(form.engine)
  && (form.load_config?.model || 'CONCURRENCY') !== 'CONCURRENCY'
  && !isScriptMode.value)

const engineOptions = computed(() => {
  const list = engines.value.length ? engines.value : [{ value: 'BUILTIN', available: true }]
  return list.map(e => ({
    value: e.value || e.key || e.name,
    available: e.available !== false,
    label: t(`performanceTesting.engine.${e.value || e.key || e.name}`)
  }))
})

let uidSeq = 1
function withUid(step) {
  return { ...step, _uid: `s${uidSeq++}` }
}

function markDirty() {
  dirty.value = true
}

// ------------------------------------------------------------------ //
// 加载
// ------------------------------------------------------------------ //
async function loadEngineStatus() {
  engineStatusLoading.value = true
  engineStatusError.value = false
  try {
    const { data } = await getEngineStatus()
    engines.value = (data.engines || []).map(e =>
      typeof e === 'string' ? { value: e, available: true } : e)
    if (data.limits) limits.value = data.limits
  } catch (e) {
    engineStatusError.value = true
    engines.value = [{ value: 'BUILTIN', available: true }]
  } finally {
    engineStatusLoading.value = false
  }
}

async function loadProjects() {
  const request = projectRequests.begin()
  projectsLoading.value = true
  projectsLoaded.value = false
  projectsLoadError.value = false
  if (isNew.value) scenarioReady.value = false
  try {
    const [res, access] = await Promise.all([getPerfProjects({ page_size: 0 }), getPerfEnvironmentPermissions()])
    if (!projectRequests.isCurrent(request)) return
    projects.value = (res.data.results || res.data || []).filter(project => access.data.project_ids.includes(project.id))
    projectsLoaded.value = true
    if (isNew.value) {
      if (!projects.value.some(project => project.id === form.project)) {
        form.project = projects.value.find(project => project.id === Number(route.query.project))?.id || projects.value[0]?.id || null
      }
      scenarioReady.value = true
    }
  } catch {
    if (projectRequests.isCurrent(request)) projectsLoadError.value = true
  } finally {
    if (projectRequests.isCurrent(request)) projectsLoading.value = false
  }
}

function environmentLabel(env) {
  return `${env.name} · v${env.version}${env.is_active ? ' · ' + t('performanceTesting.environment.preferred') : ''}`
}

function clearEnvironmentReferences() {
  if (loading.value || saving.value) return
  form.environment = null
  form.global_environment = null
  markDirty()
}

async function loadEnvironmentOptions(projectId) {
  const request = environmentRequests.begin()
  environmentOptions.value = []
  environmentLoading.value = true
  environmentLoadError.value = false
  environmentLoaded.value = false
  try {
    const responses = await Promise.all([
      getPerfEnvironments({ scope: 'GLOBAL', page_size: 0 }),
      ...(projectId ? [getPerfEnvironments({ scope: 'PROJECT', project: projectId, page_size: 0 })] : [])
    ])
    if (!environmentRequests.isCurrent(request) || projectId !== form.project) return
    environmentOptions.value = responses.flatMap(({ data }) => data.results || data || [])
    environmentLoaded.value = true
  } catch {
    if (environmentRequests.isCurrent(request)) environmentLoadError.value = true
  } finally {
    if (environmentRequests.isCurrent(request)) environmentLoading.value = false
  }
}

const fileRequests = { CSV: 0, JMX: 0, UPLOAD: 0 }

async function loadFileOptions(projectId, fileType, target, errorKey) {
  if (projectId !== form.project) return
  const request = ++fileRequests[fileType]
  const generation = loadGeneration, id = scenarioId.value, routeId = route.params.id
  const current = () => request === fileRequests[fileType] && generation === loadGeneration
    && id === scenarioId.value && routeId === route.params.id && projectId === form.project
  delete dependencyErrors[errorKey]
  if (!projectId) { target.value = []; return }
  try {
    const { data } = await getPerfDataFiles({ project: projectId, file_type: fileType, page_size: 200 })
    if (!current()) return
    target.value = data.results || data || []
    // Only the current successful JMX listing can prove that a saved selection disappeared.
    if (fileType === 'JMX' && selectedScriptId.value && !target.value.some(file => file.id === selectedScriptId.value)) {
      selectedScriptId.value = null
    }
  } catch {
    if (current()) dependencyErrors[errorKey] = true
  }
}

function loadDataFiles(projectId) {
  return loadFileOptions(projectId, 'CSV', dataFiles, 'loadDataFiles')
}
function loadScriptFiles(projectId) {
  return loadFileOptions(projectId, 'JMX', scriptFiles, 'loadScriptFiles')
}
function loadUploadFiles(projectId) {
  return loadFileOptions(projectId, 'UPLOAD', uploadFiles, 'loadUploadFiles')
}

// StepEditor 行内选中新文件后回调：上传成功即把新文件 id 回填到对应行，
// 用户无需再手动从下拉里选一次
async function handleUploadFile({ file, row }) {
  if (!form.project) {
    ElMessage.warning(t('performanceTesting.scenario.projectRequired'))
    return
  }
  try {
    const { data } = await uploadPerfUploadFile({ project: form.project, file, name: file.name })
    await loadUploadFiles(form.project)
    if (row) {
      row.file_id = data.id
      row.filename = data.name || file.name
      row.content_type = file.type || ''
    }
    markDirty()
    ElMessage.success(t('performanceTesting.script.uploadSuccess'))
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.editor.fileUpload')))
  }
}

function applyScenario(data) {
  form.project = data.project
  form.name = data.name
  form.description = data.description || ''
  form.engine = data.engine || 'BUILTIN'
  form.environment = data.environment || null
  form.global_environment = data.global_environment || null
  form.account_pool_version = data.account_pool_version || null
  form.account_pool_group = data.account_pool_group || ''
  form.enabled = data.enabled !== false
  form.load_config = data.load_config && Object.keys(data.load_config).length
    ? { ...data.load_config }
    : { model: 'CONCURRENCY', concurrency: 50, duration: 300, ramp_up: 30 }
  form.sla_config = {
    enabled: false, thresholds: {}, step_thresholds: [], abort_delay: 0, abort_on_breach: false, breach_window: 10,
    ...(data.sla_config || {})
  }
  form.sla_config.step_thresholds = JSON.parse(JSON.stringify(form.sla_config.step_thresholds || []))
  form.sla_config.abort_delay ??= 0
  form.sla_config.thresholds = { ...(form.sla_config.thresholds || {}) }
  form.perf_targets = {
    max_p95_rt: null, max_avg_rt: null, min_tps: null, max_error_rate: null,
    ...(data.perf_targets || {})
  }
  form.variables = (data.variables || []).map(v => ({ ...v }))
  form.env_config = { base_url: '', headers: {}, ...(form.engine === 'K6' ? {} : { verify_ssl: false }), ...(data.env_config || {}) }
  savedEnvironment.value = data.resolved_environment || null
  savedEnvironmentSignature.value = environmentSignature()
  form.runtime_config = {
    timeout: 30, sample_interval: 1, keep_alive: true, proxy: '', ...(data.runtime_config || {})
  }
  authSavedRevision.value++
  // 脚本选择持久化在 runtime_config.script_ref，避免刷新后丢失
  const savedRef = form.runtime_config.script_ref || {}
  scriptMode.value = savedRef.mode === 'script' ? 'script' : 'scenario'
  selectedScriptId.value = savedRef.data_file_id || null
  globalHeaders.value = Object.entries(form.env_config.headers || {})
    .map(([key, value]) => ({ enabled: true, key, value, description: '' }))
  steps.value = (data.steps || []).map(withUid)
  activeIndex.value = steps.value.length ? 0 : -1
}

// 首屏灌数据期间置位，避免 watch 里的联动再触发一次重复请求
let hydrating = false

async function loadProjectDefaults(project) {
  if (!isNew.value || !project || form.engine !== 'K6' || defaultsLoadedProject === project) return
  const ticket = defaultsRequests.begin()
  if (defaultsBaseline?.project !== project) defaultsBaseline = JSON.parse(JSON.stringify(form))
  const baseline = defaultsBaseline
  const current = () => defaultsRequests.isCurrent(ticket) && isNew.value && form.project === project && form.engine === 'K6'
  defaultsLoading.value = true
  defaultsError.value = ''
  try {
    const { data } = await getPerfScenarioDefaults(project)
    if (!current()) return
    const binding = {}
    for (const key of ['environment', 'global_environment', 'account_pool_version', 'account_pool_group']) binding[key] = data.defaults[key]
    binding.runtime_config = {}
    for (const key of ['auth_profile', 'account_identity_variable']) {
      if (Object.hasOwn(data.defaults.runtime_config, key)) binding.runtime_config[key] = data.defaults.runtime_config[key]
    }
    Object.assign(form, mergeProjectDefaults(form, { ...data, defaults: binding }, { project, baseline }))
    defaultsLoadedProject = project
    authSavedRevision.value++
  } catch (error) {
    if (current()) defaultsError.value = apiError(error, '项目默认配置加载失败，请重试或在项目接口库修正公共配置。')
  } finally {
    if (current()) defaultsLoading.value = false
  }
}

async function load() {
  const generation = ++loadGeneration
  const id = scenarioId.value
  const current = () => generation === loadGeneration && id === scenarioId.value
  loading.value = true
  hydrationError.value = ''
  hydrating = true
  try {
    await Promise.all([loadEngineStatus(), loadProjects()])
    if (!current()) return
    if (!modelHydrated) {
      if (!isNew.value) {
        const { data } = await getPerfScenario(id)
        if (!current()) return
        applyScenario(data)
        dirty.value = false
      } else {
        dirty.value = true
      }
      modelHydrated = true
    }
    scenarioReady.value = !isNew.value || (projectsLoaded.value && !projectsLoadError.value)
    await Promise.all([loadDataFiles(form.project), loadScriptFiles(form.project), loadUploadFiles(form.project), loadEnvironmentOptions(form.project)])
    if (!current()) return
    if (isNew.value) await loadProjectDefaults(form.project)
    if (!current()) return
    if (engineStatusError.value || environmentLoadError.value || Object.keys(dependencyErrors).length) {
      throw new Error('场景依赖加载失败，已保留配置，请重试。')
    }
    await loadReadiness()
  } catch (error) {
    if (current()) hydrationError.value = apiError(error, '场景加载失败，请重试。')
  } finally {
    if (current()) { loading.value = false; hydrating = false }
  }
}

function resetNewScenario() {
  defaultsRequests.begin()
  defaultsBaseline = null; defaultsLoadedProject = null
  defaultsLoading.value = false; defaultsError.value = ''
  applyScenario({ ...newScenarioForm(), project: Number(route.query.project) || null, steps: [] })
  globalHeaders.value = []
  savedEnvironment.value = null
  savedEnvironmentSignature.value = ''
  tlsTouched.value = false
  accountPoolState.value = null
  authFormValid.value = true
  dataFiles.value = []; scriptFiles.value = []; uploadFiles.value = []
  for (const key of Object.keys(dependencyErrors)) delete dependencyErrors[key]
  environmentRequests.begin()
  environmentOptions.value = []; environmentLoaded.value = false; environmentLoadError.value = false
  readinessRequests.begin(); diffRequests.begin()
  scenarioReadiness.value = []; readinessSavedSignature.value = ''; readinessError.value = ''; readinessLoading.value = false
  catalogDiff.value = []; selectedDiffIds.value = []; diffVersion.value = null; diffSignature.value = ''; diffError.value = ''
  diffLoading.value = false; diffVisible.value = false; importVisible.value = false
  activeTab.value = 'request'
  debugResult.value = null; debugVisible.value = false; preflight.value = null; execVisible.value = false
  dirty.value = true
}

watch(() => route.params.id, id => {
  const next = id && id !== 'new' ? Number(id) : null
  if (next === scenarioId.value) return
  catalogUpdating.value = false
  defaultsRequests.begin()
  defaultsLoading.value = false; defaultsError.value = ''
  hydrating = true
  scenarioId.value = next
  if (!next) resetNewScenario()
  modelHydrated = false
  scenarioReady.value = false
  startUnconfirmed.value = false
  load()
})

watch(() => form.project, (pid, previous) => {
  if (hydrating) return
  if (pid !== previous) {
    defaultsRequests.begin()
    defaultsBaseline = null; defaultsLoadedProject = null
    defaultsLoading.value = false; defaultsError.value = ''
    Object.assign(form, clearProjectBindings(form))
    accountPoolState.value = null
    authSavedRevision.value++
  }
  loadEnvironmentOptions(pid)
  loadProjectDefaults(pid)
  markDirty()
  selectedScriptId.value = null
  loadDataFiles(pid)
  loadScriptFiles(pid)
  loadUploadFiles(pid)
})

watch(() => form.engine, (val) => {
  if (hydrating) return
  defaultsRequests.begin()
  defaultsLoading.value = false; defaultsError.value = ''
  if (val === 'K6') {
    if (isNew.value && !tlsTouched.value) form.env_config = withTlsMode(form.env_config, 'inherit')
    form.load_config = { ...form.load_config, model: 'CONCURRENCY', ramp_up: 0, max_requests: 0 }
    loadProjectDefaults(form.project)
  }
  if (val !== 'JMETER') {
    // 非 JMeter 引擎不支持脚本模式，回落到步骤编排，顺手把 Tab 切回来
    scriptMode.value = 'scenario'
    if (activeTab.value === 'script') activeTab.value = 'request'
  } else if (!scriptFiles.value.length) {
    loadScriptFiles(form.project)
  }
  markDirty()
})

// ------------------------------------------------------------------ //
// 步骤操作
// ------------------------------------------------------------------ //
function selectStep(index) {
  if (editorBlocked.value) return
  activeIndex.value = index
  activeTab.value = 'request'
}

function addStep() {
  if (editorBlocked.value) return
  steps.value.push(withUid({
    ...manualStepDefaults(), name: `Step ${steps.value.length + 1}`
  }))
  activeIndex.value = steps.value.length - 1
  activeTab.value = 'request'
  markDirty()
}

function onStepCommand(command, index) {
  if (editorBlocked.value) return
  const step = steps.value[index]
  if (command === 'toggle') {
    step.enabled = !step.enabled
    markDirty()
  } else if (command === 'copy') {
    const copy = JSON.parse(JSON.stringify(step))
    delete copy.id
    delete copy.source_metadata
    delete copy.readiness
    steps.value.splice(index + 1, 0, withUid({ ...copy, name: `${step.name} copy` }))
    markDirty()
  } else if (command === 'delete') {
    steps.value.splice(index, 1)
    if (activeIndex.value >= steps.value.length) activeIndex.value = steps.value.length - 1
    markDirty()
  }
}

function onGlobalHeaders(rows) {
  const obj = {}
  ;(rows || []).forEach(r => {
    if (r && r.enabled !== false && r.key) obj[r.key] = r.value
  })
  form.env_config.headers = obj
  markDirty()
}

// ------------------------------------------------------------------ //
// 变量
// ------------------------------------------------------------------ //
function addVariable() {
  form.variables.push({ name: '', type: 'CONSTANT', value: '', secret: false })
  markDirty()
}

function removeVariable(index) {
  form.variables.splice(index, 1)
  markDirty()
}

function columnsOf(fileId) {
  const file = dataFiles.value.find(f => f.id === fileId)
  return file?.columns || []
}

// ------------------------------------------------------------------ //
// JMeter 脚本
// ------------------------------------------------------------------ //
function onScriptModeChange() {
  if (isScriptMode.value) loadScriptFiles(form.project)
  markDirty()
}

function onScriptSelected() {
  markDirty()
}

// before-upload 返回 false，用自己的接口上传，避免 el-upload 走默认 action
async function handleJmxUpload(file) {
  if (!form.project) {
    ElMessage.warning(t('performanceTesting.scenario.projectRequired'))
    return false
  }
  if (!/\.jmx$/i.test(file.name || '')) {
    ElMessage.warning(t('performanceTesting.script.invalidExt'))
    return false
  }
  if (file.size > MAX_JMX_SIZE) {
    ElMessage.warning(t('performanceTesting.script.tooLarge'))
    return false
  }
  uploadingScript.value = true
  try {
    const { data } = await uploadPerfJmxScript({ project: form.project, file, name: file.name })
    await loadScriptFiles(form.project)
    if (data && data.id) selectedScriptId.value = data.id
    markDirty()
    ElMessage.success(t('performanceTesting.script.uploadSuccess'))
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.script.upload')))
  } finally {
    uploadingScript.value = false
  }
  return false
}

async function handleDeleteScript() {
  const target = selectedScript.value
  if (!target) return
  try {
    await ElMessageBox.confirm(
      t('performanceTesting.script.deleteConfirm', { name: target.name }),
      t('performanceTesting.common.confirm'),
      { type: 'warning' }
    )
  } catch (e) {
    return
  }
  try {
    await deletePerfDataFile(target.id)
    selectedScriptId.value = null
    await loadScriptFiles(form.project)
    markDirty()
    ElMessage.success(t('performanceTesting.common.deleteSuccess'))
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.delete')))
  }
}

// 只上报 data_file_id：jmx 真实路径由服务端从 PerfDataFile 反查并校验，
// 前端传路径等于开一个任意文件读取口子
function buildScriptRef() {
  if (isScriptMode.value && selectedScriptId.value) {
    return { mode: 'script', data_file_id: selectedScriptId.value }
  }
  return { mode: 'scenario' }
}

function ensureScriptSelected() {
  if (isScriptMode.value && !selectedScriptId.value) {
    ElMessage.warning(t('performanceTesting.script.needSelect'))
    activeTab.value = 'script'
    return false
  }
  return true
}

// ------------------------------------------------------------------ //
// 保存 / 调试 / 执行
// ------------------------------------------------------------------ //
function buildPayload() {
  return {
    project: form.project,
    name: (form.name || '').trim(),
    description: form.description,
    engine: form.engine,
    environment: form.environment || null,
    global_environment: form.global_environment || null,
    account_pool_version: form.account_pool_version || null,
    account_pool_group: form.account_pool_group || '',
    enabled: form.enabled,
    load_config: form.load_config,
    sla_config: form.sla_config,
    variables: form.variables,
    env_config: isK6.value ? withTlsMode(form.env_config, tlsMode.value)
      : { ...form.env_config, verify_ssl: legacyTls.value },
    // 脚本选择随场景一起持久化，定时压测等触发路径才能复用同一份配置
    runtime_config: { ...form.runtime_config, script_ref: buildScriptRef() }
  }
}

function stepsPayload() {
  return steps.value.map((step, index) => stepForSave(step, index, websocketAccessVariable.value))
}

async function handleSave(silent = false) {
  if (recoveryErrors.value.length) { activeTab.value = 'env'; ElMessage.warning(t('performanceTesting.recovery.invalid')); return false }
  if (sseErrors.value.length) {
    activeTab.value = 'request'
    selectStep(sseErrors.value[0].index)
    ElMessage.warning(t('performanceTesting.sse.invalid'))
    return false
  }
  if (websocketErrors.value.length) {
    activeTab.value = 'request'
    selectStep(websocketErrors.value[0].index)
    ElMessage.warning(t('performanceTesting.websocket.invalid'))
    return false
  }
  if (isK6.value && !authFormValid.value) { activeTab.value = 'env'; ElMessage.warning(t('performanceTesting.auth.invalid')); return false }
  if (editorBlocked.value || !scenarioReady.value || environmentBlocked.value || projectSelectionBlocked.value || accountPoolBlocked.value || saving.value || catalogUpdating.value || importVisible.value || diffVisible.value) return false
  if (!(form.name || '').trim()) {
    ElMessage.warning(t('performanceTesting.scenario.nameRequired'))
    return false
  }
  if (!form.project) {
    ElMessage.warning(t('performanceTesting.scenario.projectRequired'))
    return false
  }
  saving.value = true
  try {
    const submittedPayload = JSON.parse(JSON.stringify(buildPayload()))
    const submittedSteps = JSON.parse(JSON.stringify(stepsPayload()))
    const submittedDrafts = JSON.stringify(steps.value.map(step => [step._uid, step._websocketDraft, step._sseDraft, step._protocolBackup]))
    const submittedUids = steps.value.map(step => step._uid)
    const submittedSignature = environmentSignature()
    let saved
    if (isNew.value) {
      const { data: created } = await createPerfScenario(submittedPayload)
      saved = created
      scenarioId.value = created.id
      router.replace(`/performance-testing/scenarios/${created.id}`)
    } else {
      const { data } = await updatePerfScenario(scenarioId.value, submittedPayload)
      saved = data
    }
    savedEnvironment.value = saved.resolved_environment || null
    const unchanged = JSON.stringify(buildPayload()) === JSON.stringify(submittedPayload)
    if (environmentSignature() === submittedSignature) {
      form.env_config = { base_url: '', headers: {}, ...(saved.env_config || {}) }
      form.variables = saved.variables || []
      globalHeaders.value = Object.entries(form.env_config.headers || {})
        .map(([key, value]) => ({ enabled: true, key, value, description: '' }))
      savedEnvironmentSignature.value = environmentSignature()
    } else {
      savedEnvironmentSignature.value = ''
    }
    if (JSON.stringify(buildPayload().runtime_config) === JSON.stringify(submittedPayload.runtime_config)) {
      form.runtime_config = { ...form.runtime_config, ...(saved.runtime_config || {}) }
    }
    authSavedRevision.value++
    const afterResponse = JSON.stringify(buildPayload())
    const { data: savedSteps } = await savePerfScenarioSteps(scenarioId.value, submittedSteps)
    const editedDuringSave = JSON.stringify(stepsPayload()) !== JSON.stringify(submittedSteps)
      || submittedDrafts !== JSON.stringify(steps.value.map(step => [step._uid, step._websocketDraft, step._sseDraft, step._protocolBackup]))
    const returnedSteps = savedSteps.steps || []
    if (returnedSteps.length !== submittedSteps.length) throw new Error(t('performanceTesting.catalog.saveIdentityFailed'))
    steps.value = steps.value.map(step => {
      const submittedIndex = submittedUids.indexOf(step._uid)
      if (submittedIndex < 0) return step
      const returned = returnedSteps[submittedIndex]
      if (!returned?.id) throw new Error(t('performanceTesting.catalog.saveIdentityFailed'))
      return editedDuringSave ? { ...step, id: returned.id, source_request: returned.source_request,
        source_metadata: returned.source_metadata, readiness: returned.readiness }
        : { ...returned, _uid: step._uid }
    })
    dirty.value = !unchanged || JSON.stringify(buildPayload()) !== afterResponse || editedDuringSave
    if (dirty.value) {
      ElMessage.info(t('performanceTesting.environment.changedDuringSave'))
      return false
    }
    await loadReadiness()
    if (dirty.value) return false
    if (!silent) ElMessage.success(t('performanceTesting.common.saveSuccess'))
    return true
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.common.save')))
    return false
  } finally {
    saving.value = false
  }
}

async function handleDebug() {
  if (!canDebug.value) return
  if (dirty.value || isNew.value) {
    const ok = await handleSave(true)
    if (!ok || !canDebug.value) return
  }
  if (isK6.value && !(await loadReadiness())) return
  debugVisible.value = form.engine !== 'K6'
  debugging.value = true
  debugResult.value = null
  try {
    const { data } = await debugPerfScenario(scenarioId.value, {})
    if (data.execution?.id) {
      debugVisible.value = false
      ElMessage.success(t('performanceTesting.editor.debugStarted'))
      router.push(`/performance-testing/executions/${data.execution.id}/monitor`)
      return
    }
    debugResult.value = data
  } catch (e) {
    handleStartFailure(e)
    const errors = e?.response?.data?.preflight?.errors
    ElMessage.error(Array.isArray(errors) && errors.length
      ? errors.join('；')
      : apiError(e, t('performanceTesting.scenario.debug')))
    debugVisible.value = false
  } finally {
    debugging.value = false
  }
}

async function handleSaveAndExecute() {
  if (!canExecute.value) return
  if (!ensureScriptSelected()) return
  const ok = await handleSave(true)
  if (!ok || !canExecute.value) return
  if (isK6.value && !(await loadReadiness())) return
  execVisible.value = true
  preflighting.value = true
  preflight.value = null
  try {
    const { data } = await preflightPerfScenario(scenarioId.value,
                                                 { script_ref: buildScriptRef() })
    preflight.value = data
  } catch (e) {
    ElMessage.error(apiError(e, t('performanceTesting.execute.preflightTitle')))
    execVisible.value = false
  } finally {
    preflighting.value = false
  }
}

function handleStartFailure(error) {
  const data = error?.response?.data
  if (data?.code === 'k6_start_unconfirmed' && data.retryable === false) {
    startUnconfirmed.value = true
    execVisible.value = false
    debugVisible.value = false
  }
}
function viewExecutionHistory() {
  router.push({ path: '/performance-testing/executions', query: { project: form.project, scenario: scenarioId.value } })
}

async function doExecute() {
  if (!canExecute.value) return
  if (!ensureScriptSelected()) return
  if (dirty.value || (isK6.value && !(await loadReadiness()))) return
  executing.value = true
  try {
    const { data } = await executePerfScenario(scenarioId.value,
                                               { script_ref: buildScriptRef() })
    ElMessage.success(t('performanceTesting.execute.started'))
    execVisible.value = false
    router.push(`/performance-testing/executions/${data.execution.id}/monitor`)
  } catch (e) {
    handleStartFailure(e)
    ElMessage.error(apiError(e, t('performanceTesting.execute.start')))
  } finally {
    executing.value = false
  }
}

async function openImport() {
  if (saving.value || loading.value || catalogUpdating.value) return
  if (isNew.value || dirty.value) {
    if (!(await handleSave(true))) return
  }
  importVisible.value = true
}

async function onImported(data) {
  steps.value.push(...(data.steps || []).map(withUid))
  activeIndex.value = steps.value.length ? steps.value.length - 1 : -1
  if (data.bindings_applied) {
    const id = scenarioId.value, generation = loadGeneration, signature = currentContent()
    const current = () => id === scenarioId.value && generation === loadGeneration
    catalogUpdating.value = true
    try {
      const { data: refreshed } = await getPerfScenario(id)
      if (!current()) return
      if (dirty.value || currentContent() !== signature) throw new Error(t('performanceTesting.apiPool.bindingReload'))
      hydrating = true
      applyScenario(refreshed)
      dirty.value = false
      activeIndex.value = steps.value.length ? steps.value.length - 1 : -1
      await loadEnvironmentOptions(form.project)
    } catch {
      if (current()) { hydrationError.value = t('performanceTesting.apiPool.bindingReload'); modelHydrated = false }
      return
    } finally { if (current()) { catalogUpdating.value = false; hydrating = false } }
  }
  await loadReadiness()
}

function catalogError(error) {
  const status = error?.response?.status
  if ([403, 404, 409].includes(status)) return t(`performanceTesting.catalog.error${status}`)
  return apiError(error, t('performanceTesting.catalog.errorNetwork'))
}

async function scenarioPages(fetchPage, current) {
  const results = []
  let version, count
  for (let page = 1; page <= 100; page++) {
    const { data } = await fetchPage({ page, page_size: 200 })
    if (!current()) return null
    if (page === 1) { version = data.version; count = data.count }
    if (version?.version !== data.version?.version || count !== data.count) throw { response: { status: 409 } }
    results.push(...data.results)
    if (!data.next) return { results, version }
    if (!data.results.length) break
  }
  throw new Error(t('performanceTesting.catalog.errorNetwork'))
}

async function loadReadiness() {
  if (!isK6.value || !hasCatalogSteps.value) return true
  if (!scenarioId.value || dirty.value) return false
  const ticket = readinessRequests.begin(), id = scenarioId.value, signature = currentContent()
  const current = () => readinessRequests.isCurrent(ticket) && id === scenarioId.value && signature === currentContent() && !dirty.value
  readinessLoading.value = true; readinessError.value = ''
  try {
    const data = await scenarioPages(params => getPerfScenarioReadiness(id, params), current)
    if (!data) return false
    scenarioReadiness.value = data.results; readinessSavedSignature.value = signature
    return !data.results.some(item => !item.ready)
  } catch (error) {
    if (current()) { readinessError.value = catalogError(error); readinessSavedSignature.value = '' }
    return false
  } finally { if (readinessRequests.isCurrent(ticket)) readinessLoading.value = false }
}

async function checkReadiness() {
  if (dirty.value || isNew.value) { if (!(await handleSave(true))) return }
  await loadReadiness()
}

async function openCatalogDiff() {
  if (dirty.value || isNew.value) { if (!(await handleSave(true))) return }
  diffVisible.value = true
  await loadCatalogDiff()
}

async function loadCatalogDiff() {
  const ticket = diffRequests.begin(), id = scenarioId.value
  diffLoading.value = true; diffError.value = ''; catalogDiff.value = []; selectedDiffIds.value = []
  diffSignature.value = currentContent()
  try {
    const data = await scenarioPages(params => getPerfScenarioCatalogDiff(id, params), () => diffRequests.isCurrent(ticket) && id === scenarioId.value)
    if (!data) return
    catalogDiff.value = data.results; diffVersion.value = data.version?.version
  } catch (error) { if (diffRequests.isCurrent(ticket)) diffError.value = catalogError(error) }
  finally { if (diffRequests.isCurrent(ticket)) diffLoading.value = false }
}

function toggleSourceStep(id, checked) {
  selectedDiffIds.value = checked ? [...new Set([...selectedDiffIds.value, id])] : selectedDiffIds.value.filter(value => value !== id)
}

async function confirmCatalogUpdate() {
  if (!selectedDiffIds.value.length || catalogUpdating.value || diffLoading.value) return
  if (dirty.value || diffSignature.value !== currentContent()) { diffError.value = t('performanceTesting.catalog.saveBeforeSync'); return }
  const ids = [...selectedDiffIds.value], id = scenarioId.value
  const submitted = new Map(steps.value.map(step => [step.id, JSON.stringify(step)]))
  catalogUpdating.value = true; diffError.value = ''
  try {
    await updatePerfScenarioCatalog(id, { step_ids: ids, expected_version: diffVersion.value })
    const { data } = await getPerfScenario(id)
    if (id !== scenarioId.value) return
    const remote = new Map(data.steps.map(step => [step.id, step]))
    let changed = dirty.value || diffSignature.value !== currentContent()
    steps.value = steps.value.map(step => {
      if (!ids.includes(step.id) || !remote.has(step.id)) return step
      const saved = remote.get(step.id)
      if (submitted.get(step.id) !== JSON.stringify(step)) { changed = true; return { ...step, source_metadata: saved.source_metadata, readiness: saved.readiness } }
      return { ...saved, _uid: step._uid }
    })
    dirty.value = changed; diffVisible.value = false; readinessSavedSignature.value = ''
    await loadReadiness()
    ElMessage.success(t('performanceTesting.catalog.synced'))
  } catch (error) { diffError.value = catalogError(error); selectedDiffIds.value = [] }
  finally { catalogUpdating.value = false }
}

function prettify(text) {
  if (!text) return ''
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch (e) {
    return String(text).slice(0, 20000)
  }
}

function goBack() {
  router.push('/performance-testing/scenarios')
}

// 未保存拦截：压测场景配置很重，误退一次要重配十分钟
onBeforeRouteLeave(async () => {
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      t('performanceTesting.editor.unsaved'),
      t('performanceTesting.common.confirm'),
      { type: 'warning' }
    )
    return true
  } catch (e) {
    return false
  }
})

function beforeUnloadGuard(event) {
  if (!dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => {
  load()
  window.addEventListener('beforeunload', beforeUnloadGuard)
})
onBeforeUnmount(() => { loadGeneration++; defaultsRequests.begin(); readinessRequests.begin(); diffRequests.begin(); environmentRequests.begin(); projectRequests.begin(); window.removeEventListener('beforeunload', beforeUnloadGuard) })
</script>

<style lang="scss" scoped>
.scenario-readiness { flex-shrink: 0; padding: 10px 14px; background: var(--el-fill-color-light); border-bottom: 1px solid var(--el-border-color-lighter); }
.readiness-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.readiness-actions strong { margin-right: auto; }
.readiness-actions :deep(.el-button + .el-button) { margin-left: 0; }
.scenario-readiness .form-tip { margin: 6px 0 0; color: var(--el-text-color-secondary); }
.readiness-gaps { margin: 6px 0 0; padding-left: 20px; font-size: 12px; overflow-wrap: anywhere; }
.readiness-gaps:empty { margin: 0; }
.catalog-diff-item { padding: 12px 0; border-bottom: 1px solid var(--el-border-color); }
.catalog-diff-item :deep(.el-checkbox) { height: auto; }
.catalog-diff-item :deep(.el-checkbox__label) { white-space: normal; overflow-wrap: anywhere; }
.diff-values { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.diff-values pre { max-height: 220px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; background: var(--el-fill-color-light); padding: 8px; }
.diff-field { margin: 12px 0; }
.environment-alert { margin: 10px 0; }
.environment-preview { margin-bottom: 12px; overflow-wrap: anywhere; }
.source-tag { margin: 3px 8px 3px 0; }
.perf-scenario-editor {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
  background: #f5f7fa;
}

.editor-header {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;

  .header-left {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: 1;
    min-width: 0;
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .editor-context, .editor-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
  .editor-actions :deep(.el-button + .el-button) { margin-left: 0; }

  .name-input {
    max-width: 280px;
    :deep(.el-input__wrapper) {
      box-shadow: none;
      background: #f5f7fa;
    }
  }

  .hd-select { width: 180px; }
  .hd-select-sm { width: 130px; }
  .engine-na { float: right; color: #c0c4cc; font-size: 12px; }
}

.editor-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
  padding: 12px;
  overflow: hidden;
}

.step-panel {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 6px;
  overflow: hidden;

  .panel-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 12px 14px 4px;
    .panel-title { font-weight: 600; color: #303133; }
    .panel-count {
      background: #f0f2f5;
      color: #909399;
      font-size: 12px;
      border-radius: 9px;
      padding: 0 8px;
    }
  }

  .panel-tip { padding: 0 14px 8px; font-size: 12px; color: #c0c4cc; }
  .step-scroll { flex: 1; }
  .step-empty { padding: 32px 14px; text-align: center; color: #c0c4cc; font-size: 13px; }

  .panel-foot {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 10px 14px;
    border-top: 1px solid #f0f2f5;
    :deep(.el-button + .el-button) { margin-left: 0; }
  }
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  margin: 0 8px 6px;
  border: 1px solid transparent;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;

  &:hover { background: #f5f7fa; }
  &.active { background: #ecf5ff; border-color: #1890ff; }
  &.disabled { opacity: 0.5; }

  .drag-handle { cursor: move; color: #c0c4cc; }
  .step-idx { width: 18px; font-size: 12px; color: #909399; }
  .step-main { flex: 1; min-width: 0; }
  .step-name {
    font-size: 13px;
    color: #303133;
    display: flex;
    align-items: center;
    gap: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .step-url {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: #909399;
    margin-top: 2px;
    .method { font-weight: 600; }
    .m-get { color: #1890ff; }
    .m-post { color: #52c41a; }
    .m-put { color: #faad14; }
    .m-delete { color: #f5222d; }
    .url-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  }
  .step-more { color: #c0c4cc; &:hover { color: #1890ff; } }
}

.main-panel {
  flex: 1;
  min-width: 0;
  min-height: 0;
  background: #fff;
  border-radius: 6px;
  overflow: auto;
  display: flex;
  flex-direction: column;

  .main-tabs {
    flex: 1 0 auto;
    min-width: 0;
    display: flex;
    flex-direction: column;
    :deep(> .el-tabs__header) { margin: 0; padding: 0 14px; }
    :deep(> .el-tabs__content) { flex: 1; min-width: 0; overflow: visible; padding: 14px; }
  }
}

.tab-empty { padding: 60px 0; text-align: center; color: #c0c4cc; }
.pane-body { max-width: 900px; }
.block-title {
  font-weight: 600;
  color: #303133;
  margin: 4px 0 14px;
  padding-left: 8px;
  border-left: 3px solid #1890ff;
  .block-tip { margin-left: 8px; font-weight: 400; font-size: 12px; color: #c0c4cc; }
}
.form-tip {
  font-size: 12px;
  color: #c0c4cc;
  line-height: 1.5;
  &.warn { color: #faad14; }
}
.env-form { margin-bottom: 24px; }

/* 引擎与压力模型不兼容告警：与压力曲线编辑器保持间距 */
.engine-model-alert { margin-bottom: 12px; }

.capability-summary { flex-shrink: 0; padding: 8px 14px 0; color: var(--el-text-color-regular); font-size: 13px; }
.capability-heading, .capability-row { display: flex; gap: 8px; justify-content: space-between; align-items: center; flex-wrap: wrap; }
.capability-heading > span { flex: 1; min-width: 160px; }
.capability-details { border-top: 0; }
.capability-details :deep(.el-collapse-item__header) { min-height: 32px; height: auto; line-height: 1.5; padding: 6px 0; box-sizing: border-box; }
.capability-details :deep(.el-collapse-item__content) { max-height: 35vh; overflow: auto; }
.capability-version { display: flex; flex-wrap: wrap; gap: 6px 16px; overflow-wrap: anywhere; }
.capability-list { list-style: none; padding: 0; margin: 0; }
.capability-list li { padding: 7px 0; border-bottom: 1px solid var(--el-border-color-lighter); }
.capability-reason { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.6; }

/* JMeter 脚本模式 */
.script-mode { margin-bottom: 6px; }
.script-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 16px 0 8px;
}
.script-select { width: 340px; }
.script-opt-meta { float: right; color: #c0c4cc; font-size: 12px; }
.script-alert { margin: 8px 0 12px; }
.script-desc { margin-bottom: 14px; }
.script-table { margin-bottom: 10px; }
.var-table { margin-bottom: 8px; }
.add-row-btn { width: 100%; border-style: dashed; }
.inline-fields { display: flex; align-items: center; gap: 6px; .sep { color: #c0c4cc; } }
.auto-hint { color: #c0c4cc; font-size: 12px; }

.debug-loading { padding: 40px; text-align: center; color: #909399; }
.debug-list {
  margin-top: 12px;
  .ok { color: #52c41a; margin-right: 6px; }
  .bad { color: #f5222d; margin-right: 6px; }
  .dbg-name { flex: 1; margin-right: 8px; }
  .dbg-code { margin: 0 10px; font-weight: 600; color: #52c41a; &.bad { color: #f5222d; } }
  .dbg-time { color: #909399; font-size: 12px; }
}
.dbg-body { font-size: 13px; }
.dbg-line {
  display: flex;
  gap: 10px;
  padding: 3px 0;
  b { width: 80px; flex-shrink: 0; color: #909399; font-weight: 500; }
  span { word-break: break-all; }
  &.err span { color: #f5222d; }
}
.dbg-sub { margin: 10px 0 4px; font-weight: 600; color: #606266; }
.dbg-pre {
  margin: 0;
  padding: 8px 10px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.pf-list {
  margin: 10px 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.8;
  &.err { color: #f5222d; }
  &.warn { color: #faad14; }
}
.pf-desc { margin-top: 12px; }
.pf-confirm { margin-top: 12px; font-size: 12px; color: #909399; }
@media (max-width: 1400px) {
  .editor-header { flex-wrap: wrap; }
  .editor-header .header-left { flex: 1 1 280px; }
  .editor-header .header-right { display: contents; }
  .editor-header .editor-actions { flex-basis: 100%; justify-content: flex-end; }
  .editor-header .name-input { flex: 1; width: auto; min-width: 160px; }
  .step-panel { width: 260px; }
}
@media (max-width: 1100px) {
  .step-panel { width: 230px; }
}
@media (max-width: 800px) {
  .perf-scenario-editor { height: auto; min-height: calc(100vh - 110px); }
  .editor-body { flex-direction: column; overflow: visible; }
  .step-panel { width: 100%; max-height: 240px; }
  .main-panel { min-height: 500px; }
  .env-form :deep(.el-col) { width: 100%; max-width: 100%; flex: 0 0 100%; }
  .env-form :deep(.el-form-item__label) { float: none; display: block; width: auto !important; text-align: left; }
  .env-form :deep(.el-form-item__content) { margin-left: 0 !important; }
  .env-form :deep(.el-select) { width: 100%; min-width: 0; }
}
</style>

