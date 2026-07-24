# 泓济环保行业日报 - GitHub Actions 部署指南

## 这是什么

一套完全自动化的行业日报系统，每天早上 8:00 自动：
1. 从 Google News 抓取环保/水处理行业最新新闻
2. 筛选去重，生成报纸风格 HTML 日报
3. 通过 SMTP 发送到所有订阅者邮箱

**不需要电脑开机，不需要 WorkBuddy 在线**，GitHub 服务器 24 小时运行。

---

## 部署步骤（约 15 分钟）

### 第一步：创建 GitHub 仓库

1. 打开 https://github.com 注册/登录账号
2. 点击右上角 **+** → **New repository**
3. 仓库名填 `honess-daily-report`
4. 选择 **Private**（私有仓库）
5. 勾选 **Add a README file**
6. 点击 **Create repository**

### 第二步：上传文件

把 `daily-report-system` 目录下的所有文件上传到仓库：

```
honess-daily-report/
├── .github/
│   └── workflows/
│       └── daily-report.yml      ← 定时任务配置
├── scripts/
│   ├── main.py                    ← 主程序
│   └── requirements.txt           ← Python 依赖
├── templates/
│   └── report.html.j2             ← HTML 模板
├── subscribers.json               ← 订阅者邮箱列表
└── README.md                      ← 本文件
```

方法：在 GitHub 网页上点 **Add file → Upload files**，拖入文件即可。
注意 `.github` 是隐藏目录，需要先创建再上传。

### 第三步：配置邮箱 Secrets

在仓库页面：**Settings → Secrets and variables → Actions → New repository secret**

依次添加以下 5 个 Secret：

| Secret 名称 | 值 | 说明 |
|-------------|-----|------|
| `SMTP_HOST` | `smtp.qq.com` | SMTP 服务器地址 |
| `SMTP_PORT` | `465` | SMTP 端口（SSL） |
| `SMTP_USER` | `你的QQ邮箱@qq.com` | 发件邮箱 |
| `SMTP_PASS` | `QQ邮箱授权码` | **不是QQ密码！** 是授权码 |
| `SENDER_NAME` | `泓济环保` | 发件人显示名 |

#### 如何获取 QQ 邮箱授权码：

1. 登录 QQ 邮箱 → **设置** → **账户**
2. 找到 **POP3/IMAP/SMTP** → **开启**
3. 按提示用手机发短信获取授权码（16位字母）
4. 把授权码填入 `SMTP_PASS`

> 也可以用企业邮箱、网易邮箱等，只需对应修改 SMTP_HOST 和 SMTP_PORT。
>
> | 邮箱 | SMTP_HOST | SMTP_PORT |
> |------|-----------|-----------|
> | QQ邮箱 | smtp.qq.com | 465 |
> | 163邮箱 | smtp.163.com | 465 |
> | 企业微信邮箱 | smtp.exmail.qq.com | 465 |
> | Gmail | smtp.gmail.com | 587 |

### 第四步：添加订阅者

编辑仓库中的 `subscribers.json`：

```json
{
  "emails": [
    "chenxingyu@honess.cn",
    "huangy@honess.cn",
    "newperson@honess.cn"
  ]
}
```

直接在 GitHub 网页上点 `subscribers.json` → ✏️ 编辑 → 保存即可。

### 第五步：测试运行

1. 进入仓库 **Actions** 标签页
2. 左侧选择 **泓济环保行业日报**
3. 点击 **Run workflow** → **Run workflow**
4. 等待几分钟，查看运行日志

如果显示绿色 ✅，说明成功！检查邮箱是否收到日报。

---

## 日常管理

### 添加新订阅者

**方法一**：直接编辑 `subscribers.json`（GitHub 网页即可操作）

**方法二**：通过订阅页面自动收集
1. 注册 https://formspree.io 免费账号
2. 创建表单，获取 Form ID
3. 编辑 `subscribe.html`，替换 `YOUR_FORMSPREE_ID`
4. 部署到 CloudStudio 或 GitHub Pages
5. 同事访问页面填写邮箱 → 你收到通知 → 手动添加到 subscribers.json

### 修改发送时间

编辑 `.github/workflows/daily-report.yml`：

```yaml
on:
  schedule:
    - cron: '0 0 * * *'    # UTC 00:00 = 北京 08:00
```

| 北京时间 | cron 值 |
|---------|---------|
| 07:00 | `23 0 * * *`... |

格式：`分 时 * * *`（UTC 时间，北京时间 = UTC + 8）

### 查看运行记录

GitHub 仓库 → **Actions** → 点击每次运行查看日志

### 暂停发送

GitHub 仓库 → **Actions** → 选择 workflow → 右上角 **...** → **Disable workflow**

---

## 技术说明

- **新闻来源**：Google News RSS（覆盖生态环境部、北极星环保网、新浪财经、中国环境报等数十家媒体）
- **运行环境**：GitHub Actions（Ubuntu），免费额度每月 2000 分钟，每天约用 2 分钟
- **邮件发送**：Python smtplib + SSL
- **HTML 模板**：Jinja2，报纸排版风格，响应式设计
- **无需 AI API**：纯 RSS + 关键词过滤，零额外成本

---

## 目录结构

```
daily-report-system/
├── .github/workflows/daily-report.yml   ← GitHub Actions 定时任务
├── scripts/
│   ├── main.py                          ← Python 主程序
│   └── requirements.txt                 ← 依赖：feedparser + Jinja2
├── templates/
│   └── report.html.j2                   ← 报纸风格 HTML 模板
├── subscribers.json                      ← 订阅者邮箱列表
└── README.md                            ← 本文件
```

---

## 常见问题

**Q: 运行失败怎么办？**
A: 进入 Actions → 点击失败的运行 → 查看日志，通常是因为 SMTP 授权码错误或网络问题。

**Q: 新闻数量不够？**
A: Google News RSS 返回结果有时偏少，可编辑 main.py 中的 `SEARCH_QUERIES` 增加更多关键词。

**Q: 想换发件邮箱？**
A: 修改 GitHub Secrets 中的 SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS。

**Q: 如何更换 HTML 模板样式？**
A: 编辑 `templates/report.html.j2`，修改 CSS 样式即可。Jinja2 变量参考模板中的 `{{ }}` 语法。
