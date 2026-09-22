<template>
  <div class="register-container">
    <div class="register-form">
      <div class="form-header">
        <h2>{{ $t('auth.registerTitle') }}</h2>
        <p>{{ $t('auth.registerSubtitle') }}</p>
      </div>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        @submit.prevent="handleRegister"
      >
        <el-form-item prop="username">
          <el-input
            v-model="form.username"
            :placeholder="$t('auth.username')"
            size="large"
            :prefix-icon="User"
          />
        </el-form-item>

        <el-form-item prop="phone">
          <el-input
            v-model="form.phone"
            placeholder="请输入手机号"
            size="large"
            :prefix-icon="Phone"
            maxlength="11"
          />
        </el-form-item>

        <!-- 图形验证码 -->
        <el-row :gutter="12">
          <el-col :span="14">
            <el-form-item prop="captcha_code">
              <el-input
                v-model="form.captcha_code"
                placeholder="图形验证码"
                size="large"
                maxlength="4"
              />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <img
              :src="captchaImage"
              alt="验证码"
              class="captcha-img"
              @click="refreshCaptcha"
              title="点击刷新验证码"
            />
          </el-col>
        </el-row>

        <!-- 短信验证码 -->
        <el-form-item prop="verify_code">
          <el-input
            v-model="form.verify_code"
            placeholder="短信验证码"
            size="large"
            maxlength="6"
          >
            <template #append>
              <el-button
                :disabled="smsCountdown > 0 || !form.phone || !form.captcha_code"
                :loading="sendingSms"
                @click="sendVerifyCode"
                style="min-width: 110px"
              >
                {{ smsCountdown > 0 ? `${smsCountdown}s后重试` : '发送验证码' }}
              </el-button>
            </template>
          </el-input>
        </el-form-item>

        <el-form-item prop="email">
          <el-input
            v-model="form.email"
            type="email"
            :placeholder="$t('auth.email')"
            size="large"
            :prefix-icon="Message"
          />
        </el-form-item>

        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item prop="first_name">
              <el-input
                v-model="form.first_name"
                :placeholder="$t('auth.firstName')"
                size="large"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item prop="last_name">
              <el-input
                v-model="form.last_name"
                :placeholder="$t('auth.lastName')"
                size="large"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item prop="password">
          <el-input
            v-model="form.password"
            type="password"
            :placeholder="$t('auth.password')"
            size="large"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-form-item prop="password_confirm">
          <el-input
            v-model="form.password_confirm"
            type="password"
            :placeholder="$t('auth.confirmPassword')"
            size="large"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-row :gutter="20">
          <el-col :span="12">
            <el-form-item prop="department">
              <el-input
                v-model="form.department"
                :placeholder="$t('auth.department')"
                size="large"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item prop="position">
              <el-input
                v-model="form.position"
                :placeholder="$t('auth.position')"
                size="large"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-form-item>
          <el-button
            type="primary"
            size="large"
            :loading="loading"
            @click="handleRegister"
            style="width: 100%"
          >
            {{ $t('auth.register') }}
          </el-button>
        </el-form-item>

        <div class="form-footer">
          <router-link to="/login">{{ $t('auth.hasAccount') }}</router-link>
        </div>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { User, Lock, Message, Phone } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import api from '@/utils/api'

const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
const formRef = ref()
const loading = ref(false)
const sendingSms = ref(false)
const smsCountdown = ref(0)
const captchaImage = ref('')
const captchaToken = ref('')
let countdownTimer = null

const form = reactive({
  username: '',
  phone: '',
  captcha_code: '',
  verify_code: '',
  verify_code_token: '',
  email: '',
  first_name: '',
  last_name: '',
  password: '',
  password_confirm: '',
  department: '',
  position: ''
})

const validatePhone = (rule, value, callback) => {
  if (!value) {
    callback(new Error('请输入手机号'))
  } else if (!/^1[3-9]\d{9}$/.test(value)) {
    callback(new Error('手机号格式不正确'))
  } else {
    callback()
  }
}

const rules = {
  username: [
    { required: true, message: computed(() => t('auth.usernameRequired')), trigger: 'blur' },
    { min: 3, max: 20, message: computed(() => t('auth.usernameLength')), trigger: 'blur' }
  ],
  phone: [
    { required: true, validator: validatePhone, trigger: 'blur' }
  ],
  captcha_code: [
    { required: true, message: '请输入图形验证码', trigger: 'blur' }
  ],
  verify_code: [
    { required: true, message: '请输入短信验证码', trigger: 'blur' }
  ],
  email: [
    { required: true, message: computed(() => t('auth.emailRequired')), trigger: 'blur' },
    { type: 'email', message: computed(() => t('auth.emailFormat')), trigger: 'blur' }
  ],
  password: [
    { required: true, message: computed(() => t('auth.passwordRequired')), trigger: 'blur' },
    { min: 6, message: computed(() => t('auth.passwordLength')), trigger: 'blur' }
  ],
  password_confirm: [
    { required: true, message: computed(() => t('auth.confirmPasswordRequired')), trigger: 'blur' },
    {
      validator: (rule, value, callback) => {
        if (value !== form.password) {
          callback(new Error(t('auth.passwordMismatch')))
        } else {
          callback()
        }
      },
      trigger: 'blur'
    }
  ]
}

// 获取图形验证码
const refreshCaptcha = async () => {
  try {
    const response = await api.get('/auth/captcha/')
    captchaImage.value = response.data.image
    captchaToken.value = response.data.token
    form.captcha_code = ''
  } catch (error) {
    ElMessage.error('获取验证码失败，请刷新重试')
  }
}

// 发送短信验证码
const sendVerifyCode = async () => {
  if (!form.phone) {
    ElMessage.warning('请先输入手机号')
    return
  }
  if (!form.captcha_code) {
    ElMessage.warning('请先输入图形验证码')
    return
  }

  sendingSms.value = true
  try {
    const response = await api.post('/auth/send-register-code/', {
      phone: form.phone,
      captcha_token: captchaToken.value,
      captcha_code: form.captcha_code,
      mode: 'register'
    })
    ElMessage.success('验证码已发送')
    form.verify_code_token = response.data.verify_code_token
    // 开始 60 秒倒计时
    smsCountdown.value = 60
    countdownTimer = setInterval(() => {
      smsCountdown.value--
      if (smsCountdown.value <= 0) {
        clearInterval(countdownTimer)
        countdownTimer = null
      }
    }, 1000)
  } catch (error) {
    const errMsg = error.response?.data?.error || '验证码发送失败'
    ElMessage.error(errMsg)
    // 刷新图形验证码
    refreshCaptcha()
  } finally {
    sendingSms.value = false
  }
}

const handleRegister = async () => {
  if (!formRef.value) return

  await formRef.value.validate(async (valid) => {
    if (valid) {
      loading.value = true
      try {
        await userStore.register(form)
        ElMessage.success(t('auth.registerSuccess'))
        router.replace('/home')
      } catch (error) {
        ElMessage.error(error.response?.data?.error || t('auth.registerFailed'))
        refreshCaptcha()
      } finally {
        loading.value = false
      }
    }
  })
}

// 页面加载时获取图形验证码
refreshCaptcha()
</script>

<style lang="scss" scoped>
.register-container {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.register-form {
  width: 520px;
  padding: 40px;
  background: white;
  border-radius: 10px;
  box-shadow: 0 15px 35px rgba(0, 0, 0, 0.1);

  .form-header {
    text-align: center;
    margin-bottom: 30px;

    h2 {
      color: #303133;
      font-size: 28px;
      font-weight: 600;
      margin: 0 0 10px 0;
    }

    p {
      color: #909399;
      margin: 0;
    }
  }

  .captcha-img {
    width: 100%;
    height: 40px;
    border-radius: 4px;
    cursor: pointer;
    border: 1px solid #dcdfe6;
  }

  .form-footer {
    text-align: center;
    margin-top: 20px;

    a {
      color: #409eff;
      text-decoration: none;

      &:hover {
        text-decoration: underline;
      }
    }
  }
}
</style>
