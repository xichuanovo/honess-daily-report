#!/usr/bin/env python3
"""
泓济环保行业日报 - 自动抓取新闻并发送邮件
运行环境: GitHub Actions (Ubuntu)
功能: 从 Google News RSS 抓取环保/水处理行业新闻 -> 生成报纸风格HTML -> 通过SMTP发送给订阅者
"""

import feedparser
import json
import smtplib
import os
import re
import time
import urllib.parse
import requests as req_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from datetime import datetime, timedelta
from jinja2 import Template
from trafilatura import extract as traf_extract

# ==================== 配置 ====================

# 搜索关键词（每个关键词对应一次 Google News RSS 查询）
SEARCH_QUERIES = [
    "污水处理",
    "环保 水处理",
    "工业废水 零排放",
    "水务 中标 项目",
    "排放标准 生态环境部",
    "水污染防治",
    "再生水 水务",
    "污泥处理",
]

# 相关性过滤关键词（标题或摘要中包含才算相关）
RELEVANT_KEYWORDS = [
    '水处理', '污水', '环保', '废水', 'MBR', 'MVR', '零排放',
    '排放标准', '水务', '膜技术', '脱硫', '污泥', '再生水',
    '黑臭水体', '工业园区', '海水淡化', '饮用水', '水污染',
    '水环境', '污水处理厂', '给水', '排水',
]

# 政策相关关键词（用于判断文章是否属于政策类）
POLICY_KEYWORDS = ['政策', '法规', '标准', '条例', '方案', '规划', '管理办法', '印发', '实施', '修订']
# 技术相关关键词
TECH_KEYWORDS = ['技术', '工艺', '突破', '创新', '研发', '专利', '膜', '曝气', '生化', '催化', '蒸发']

# 水处理/环保行业核心关键词（用于过滤MEE新闻，排除纯政治新闻）
WATER_KEYWORDS = [
    '水处理', '污水', '废水', '水务', '水污染', '水环境', '水生态',
    '污水处理厂', '排水', '给水', '再生水', '海水淡化', '饮用水',
    '黑臭水体', '河长制', '流域', '水体', '水质',
    '排放标准', '污染物', '排放', 'COD', '氨氮', '总磷',
    '污泥', '工业废水', '零排放', '脱硫', '脱硝',
    '环保督察', '污染防治', '生态环境', '碳达峰', '碳中和',
    'VOCs', '环保税', '排放许可',
]

# 排除关键词（纯政治新闻，与水处理无关）
EXCLUDE_KEYWORDS = [
    '省委书记', '常委会', '重要讲话', '主持会议', '讲话精神',
    '传达学习', '指示精神', '党组', '纪委监委', '巡视',
    '干部大会', '主题教育', '党建', '干部培训',
]

# 跳过的域名（JS渲染/反爬/内容质量差）
SKIP_DOMAINS = ['msn.com', 'msn.cn', 'investing.com', 'bing.com']

# 垃圾文本模式（trafilatura可能提取到的跳转/加载页面文本）
JUNK_PATTERNS = [
    '正在访问', '正在跳转', '正在加载', '访问网站', '访问原网',
    '请稍候', '正在为您', '点击这里', '跳转中', '正在进入',
    'Loading', 'Redirecting', 'Please wait', 'Click here',
    '页面不存在', '404', '无法访问', '访问出错',
]

# 选取新闻条数
NEWS_COUNT = 6
# 政策法规条数
POLICY_COUNT = 5
# 最近N天的新闻
RECENT_DAYS = 14


# ==================== 新闻抓取 ====================

def is_google_news_link(url):
    """判断是否为 Google News 重定向链接（国内无法访问）"""
    return url and 'news.google.com' in url


def extract_real_url(link):
    """从 Bing News 跳转链接中提取真实文章 URL"""
    if not link:
        return link
    if 'bing.com/news/apiclick' in link:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        real_url = params.get('url', [None])[0]
        if real_url:
            return real_url
    return link


def should_skip_url(url):
    """判断 URL 是否应该跳过（JS渲染/反爬/内容质量差）"""
    if not url:
        return True
    url_lower = url.lower()
    for domain in SKIP_DOMAINS:
        if domain in url_lower:
            return True
    return False


def clean_content(content, title=''):
    """清理提取的正文内容：过滤垃圾文本、验证相关性"""
    if not content:
        return None
    content = content.strip()
    # 太短的内容直接丢弃
    if len(content) < 50:
        return None
    # 检查是否包含垃圾文本
    for pattern in JUNK_PATTERNS:
        if pattern in content:
            print(f"    [clean] 检测到垃圾文本 [{pattern}]，丢弃内容")
            return None
    # 检查内容与标题的相关性（至少共享2个中文词）
    if title:
        title_chars = set(re.findall(r'[\u4e00-\u9fff]', title))
        content_chars = set(re.findall(r'[\u4e00-\u9fff]', content[:200]))
        overlap = title_chars & content_chars
        if len(overlap) < 2:
            print(f"    [clean] 内容与标题不相关（重叠{len(overlap)}字），丢弃")
            return None
    return content


def fetch_page_content(url):
    """抓取指定 URL 页面的正文内容，返回 (url, content)"""
    if should_skip_url(url):
        print(f"    [skip] 跳过域名: {url[:60]}")
        return url, None
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = req_lib.get(url, timeout=15, headers=headers, allow_redirects=True)
        # 传入原始字节让 trafilatura 自动检测编码（解决 GBK/GB2312 乱码问题）
        content = traf_extract(resp.content, include_comments=False, include_tables=False)
        return resp.url, content
    except Exception as e:
        print(f"    [fetch] 失败: {e}")
        return url, None


def fetch_rss(query):
    """从 Bing News RSS 获取新闻，返回带真实链接的文章列表"""
    encoded_query = urllib.parse.quote(query)
    # Bing News RSS — 直接提供原始文章链接，国内可访问
    url = f"https://www.bing.com/news/search?q={encoded_query}&format=rss"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries:
            title = entry.title if hasattr(entry, 'title') else ''
            link = entry.link if hasattr(entry, 'link') else ''

            # 来源
            source = ''
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                source = entry.source.title
            elif hasattr(entry, 'author'):
                source = entry.author

            # 摘要
            summary = ''
            if hasattr(entry, 'summary'):
                summary = re.sub(r'<[^>]+>', '', entry.summary).strip()

            # 日期
            published = entry.published if hasattr(entry, 'published') else ''
            published_parsed = entry.published_parsed if hasattr(entry, 'published_parsed') and entry.published_parsed else None

            articles.append({
                'title': title.strip(),
                'source': source.strip(),
                'link': extract_real_url(link),
                'summary': summary,
                'content': None,
                'published': published,
                'published_parsed': published_parsed,
            })
        return articles
    except Exception as e:
        print(f"  Bing RSS 获取失败 [{query}]: {e}")
        return []


def fetch_mee_news():
    """从生态环境部官网抓取要闻作为新闻来源（过滤相关性，排除纯政治新闻）"""
    urls = [
        'https://www.mee.gov.cn/ywdt/hjywnews/',
        'https://www.mee.gov.cn/ywdt/dfnews/',
    ]
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    articles = []
    pattern = re.compile(
        r'<li[^>]*>.*?<span[^>]*>(\d{4}-\d{2}-\d{2})</span>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</li>',
        re.S
    )

    for url in urls:
        try:
            resp = req_lib.get(url, headers=headers, timeout=15)
            html = resp.content.decode('utf-8', errors='ignore')
            for date_str, href, title_raw in pattern.findall(html):
                title = re.sub(r'<[^>]+>', '', title_raw).strip()
                if not title or not href:
                    continue

                # 排除纯政治新闻
                if any(kw in title for kw in EXCLUDE_KEYWORDS):
                    continue

                # 必须包含水处理/环保核心关键词
                if not any(kw in title for kw in WATER_KEYWORDS):
                    continue

                # 补齐链接
                if href.startswith('http'):
                    link = href
                elif href.startswith('/'):
                    link = 'https://www.mee.gov.cn' + href
                else:
                    link = url + href
                link = link.replace('/./', '/')

                try:
                    pp = datetime.strptime(date_str, '%Y-%m-%d').timetuple()
                except Exception:
                    pp = None

                articles.append({
                    'title': title,
                    'source': '生态环境部',
                    'link': link,
                    'summary': '',
                    'content': None,
                    'published': date_str,
                    'published_parsed': pp,
                })
        except Exception as e:
            print(f"  MEE 新闻抓取失败 [{url}]: {e}")

    return articles


def fetch_all_news():
    """获取所有关键词的新闻（Bing News RSS + MEE 官网 双来源）"""
    all_articles = []
    for i, query in enumerate(SEARCH_QUERIES):
        print(f"  [{i+1}/{len(SEARCH_QUERIES)}] 搜索: {query}")
        articles = fetch_rss(query)
        print(f"    获取 {len(articles)} 篇")
        all_articles.extend(articles)
        time.sleep(0.5)  # 礼貌性延迟

    # 始终从生态环境部官网补充新闻（不再仅作为备份）
    print(f"  补充来源: 生态环境部官网要闻...")
    mee_articles = fetch_mee_news()
    print(f"    获取 {len(mee_articles)} 篇（已过滤相关性）")
    all_articles.extend(mee_articles)

    return all_articles


def extract_title_keywords(title):
    """从标题中提取关键词集合（滑动窗口法，用于去重）"""
    chars = re.findall(r'[\u4e00-\u9fff]', title)
    keywords = set()
    # 提取所有2字中文子串
    for i in range(len(chars) - 1):
        keywords.add(''.join(chars[i:i+2]))
    # 提取所有3字中文子串
    for i in range(len(chars) - 2):
        keywords.add(''.join(chars[i:i+3]))
    # 过滤停用词
    stop_words = {'本报', '记者', '报道', '新闻', '今天', '昨日', '据悉',
                  '根据', '会的', '月日', '指出', '要求', '强调'}
    return set(w for w in keywords if w not in stop_words)


def deduplicate(articles):
    """去重：基于标题关键词相似度（滑动窗口）"""
    if not articles:
        return []
    seen = []
    unique = []
    for a in articles:
        title = a['title'].strip()
        if not title:
            continue
        kw = extract_title_keywords(title)
        is_dup = False
        for prev_title, prev_kw in seen:
            overlap = kw & prev_kw
            # 共享3字关键词 = 高度相似
            long_overlap = {k for k in overlap if len(k) >= 3}
            if len(long_overlap) >= 1 or len(overlap) >= 5:
                is_dup = True
                break
        if not is_dup:
            seen.append((title, kw))
            unique.append(a)
    return unique


def filter_relevant(articles):
    """过滤相关新闻"""
    relevant = []
    for a in articles:
        text = a['title'] + ' ' + a['summary']
        if any(kw in text for kw in RELEVANT_KEYWORDS):
            relevant.append(a)
    return relevant


def filter_recent(articles, days=14):
    """过滤最近N天的新闻（无日期的保留）"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = []
    for a in articles:
        if a['published_parsed']:
            try:
                dt = datetime(*a['published_parsed'][:6])
                if dt >= cutoff:
                    recent.append(a)
            except:
                recent.append(a)  # 日期解析失败也保留
        else:
            recent.append(a)  # 无日期信息也保留
    return recent


def tag_article(article):
    """为文章打标签: 政策/技术/市场"""
    text = article['title'] + ' ' + article['summary']
    if any(kw in text for kw in POLICY_KEYWORDS):
        return '政策'
    elif any(kw in text for kw in TECH_KEYWORDS):
        return '技术'
    else:
        return '市场'


def select_news(articles, count=6):
    """选择最重要的N条新闻，保证分类多样性"""
    # 排序：新到旧
    articles.sort(key=lambda x: x['published_parsed'] or (0,), reverse=True)

    # 打标签
    for a in articles:
        a['tag'] = tag_article(a)

    # 按分类限制数量
    selected = []
    tag_counts = {'政策': 0, '技术': 0, '市场': 0}
    max_per_tag = 3

    for a in articles:
        if len(selected) >= count:
            break
        tag = a['tag']
        if tag_counts.get(tag, 0) < max_per_tag:
            selected.append(a)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # 如果不够，补齐
    if len(selected) < count:
        for a in articles:
            if a not in selected:
                selected.append(a)
                if len(selected) >= count:
                    break

    return selected


def fetch_policies():
    """获取政策法规 — 直接从生态环境部官网抓取（系列去重）"""
    url = 'https://www.mee.gov.cn/zcwj/zcjd/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        resp = req_lib.get(url, headers=headers, timeout=15)
        html = resp.content.decode('utf-8', errors='ignore')

        # 匹配列表项：日期 + 链接 + 标题
        pattern = re.compile(
            r'<li[^>]*>.*?<span[^>]*>(\d{4}-\d{2}-\d{2})</span>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?</li>',
            re.S
        )
        matches = pattern.findall(html)

        # 先收集所有政策，按系列分组
        all_policies = []
        for date_str, href, title_raw in matches[:15]:
            title = re.sub(r'<[^>]+>', '', title_raw).strip()
            if not title or not href:
                continue

            # 补齐链接
            if href.startswith('http'):
                link = href
            elif href.startswith('/'):
                link = 'https://www.mee.gov.cn' + href
            else:
                base = 'https://www.mee.gov.cn/zcwj/zcjd/'
                link = base + href
            link = link.replace('/./', '/')

            # 判断状态
            text = title.lower()
            if any(kw in text for kw in ['实施', '施行', '生效']):
                status = '已实施'
            else:
                status = '新发布'

            # 提取系列前缀（如"守护群众身边水 | xxx"中的"守护群众身边水"）
            series = ''
            if '|' in title:
                series = title.split('|')[0].strip()

            all_policies.append({
                'title': title,
                'link': link,
                'source': '生态环境部',
                'date': date_str[5:],  # 转为 MM-DD
                'status': status,
                'content': None,
                'series': series,
            })

        # 系列去重：同一系列前缀最多保留1条（选最新的）
        seen_series = set()
        unique = []
        for p in all_policies:
            series = p.get('series', '')
            if series:
                if series in seen_series:
                    continue
                seen_series.add(series)
            unique.append(p)
            if len(unique) >= POLICY_COUNT:
                break

        return unique[:POLICY_COUNT]
    except Exception as e:
        print(f"  政策抓取失败: {e}")
        return []


# ==================== HTML 生成 ====================

def generate_keywords(news):
    """从新闻标题中提取关键词"""
    text = ' '.join([n['title'] for n in news])
    candidates = [
        '废水零排放', '排放标准', '膜技术', '水务', '工业园区',
        '脱硫废水', '污水处理', 'MBR', '再生水', '污泥',
        '海水淡化', '黑臭水体', '饮用水', '水污染', '零排放',
    ]
    keywords = [kw for kw in candidates if kw in text]
    if not keywords:
        keywords = ['水处理', '环保', '污水', '政策']
    return keywords[:6]


def deduplicate_cross_section(news, policies):
    """跨栏目去重：如果新闻和法规涉及相同主题，从法规中移除重复项"""
    news_keywords = set()
    for n in news:
        news_keywords |= extract_title_keywords(n.get('title', ''))

    filtered = []
    for p in policies:
        pol_kw = extract_title_keywords(p.get('title', ''))
        overlap = news_keywords & pol_kw
        long_overlap = {k for k in overlap if len(k) >= 3}
        # 共享3字关键词 = 主题重复
        if len(long_overlap) >= 1 or len(overlap) >= 5:
            print(f"  [跨栏目去重] 移除法规（与新闻重复）: {p['title'][:40]}...")
            continue
        filtered.append(p)

    return filtered


def format_date_short(published_parsed):
    """格式化日期为 MM-DD"""
    if not published_parsed:
        return ''
    try:
        dt = datetime(*published_parsed[:6])
        return dt.strftime('%m-%d')
    except:
        return ''


def generate_snippet(title, content=None, summary=None):
    """为没有正文的文章生成简短展示文本"""
    if content:
        return content
    if summary and not any(p in summary for p in JUNK_PATTERNS):
        return summary
    # 从标题生成简短描述
    return f'本期关注：{title[:60]}。详情请点击查看原文。'


def generate_html(news, policies):
    """生成报纸风格 HTML 日报"""
    template_path = os.path.join(os.path.dirname(__file__), '..', 'templates', 'report.html.j2')
    with open(template_path, 'r', encoding='utf-8') as f:
        template = Template(f.read())

    now = datetime.utcnow() + timedelta(hours=8)  # 转北京时间
    weekday_map = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

    # 期号：从2026年1月1日开始计数
    start = datetime(2026, 1, 1)
    issue_num = str((now - start).days + 45).zfill(3)

    # 分配新闻
    featured = news[0] if news else None
    if featured:
        featured['date_short'] = format_date_short(featured.get('published_parsed'))
        featured['has_valid_link'] = featured.get('link', '') and not is_google_news_link(featured.get('link', ''))
        # 展示内容：优先用抓取的正文，回退到 RSS 摘要，最后回退到生成的摘要
        featured['display_text'] = generate_snippet(
            featured.get('title', ''),
            featured.get('content'),
            featured.get('summary')
        )[:500]

    two_col_news = news[1:4] if len(news) > 1 else []
    for n in two_col_news:
        n['date_short'] = format_date_short(n.get('published_parsed'))
        n['has_valid_link'] = n.get('link', '') and not is_google_news_link(n.get('link', ''))
        n['display_text'] = generate_snippet(
            n.get('title', ''),
            n.get('content'),
            n.get('summary')
        )[:200]

    briefs_raw = news[4:] if len(news) > 4 else []
    briefs = []
    for n in briefs_raw[:3]:
        snippet = generate_snippet(
            n.get('title', ''),
            n.get('content'),
            n.get('summary')
        )
        briefs.append({
            'title': n['title'],
            'tag': n['tag'],
            'summary': snippet[:80] + ('...' if len(snippet) > 80 else ''),
            'date': now.strftime('%m-%d'),
        })

    keywords = generate_keywords(news)

    # 为政策标记是否有有效链接 + 准备展示内容
    for p in policies:
        p['has_valid_link'] = p.get('link', '') and not is_google_news_link(p.get('link', ''))
        p['display_text'] = (p.get('content') or '')[:150]

    # 统计
    tag_counts = {'政策': 0, '技术': 0, '市场': 0}
    for n in news:
        tag_counts[n.get('tag', '市场')] = tag_counts.get(n.get('tag', '市场'), 0) + 1

    html = template.render(
        date=now.strftime('%Y年%m月%d日'),
        weekday=weekday_map[now.weekday()],
        issue_num=issue_num,
        featured=featured,
        two_col_news=two_col_news,
        briefs=briefs,
        policies=policies,
        keywords=keywords,
        news_count=len(news),
        policy_count=len(policies),
        tech_count=tag_counts.get('技术', 0),
        market_count=tag_counts.get('市场', 0),
        policy_tag_count=tag_counts.get('政策', 0),
    )

    return html


def save_news_json(news, policies):
    """生成与招聘工具页同格式的新闻数据 JSON"""
    now = datetime.utcnow() + timedelta(hours=8)

    news_items = []
    for n in news:
        pp = n.get('published_parsed')
        time_str = ''
        if pp:
            try:
                time_str = datetime(*pp[:6]).strftime('%m-%d')
            except Exception:
                pass
        if not time_str:
            time_str = now.strftime('%m-%d')
        news_items.append({
            'title': n.get('title', ''),
            'source': n.get('source', ''),
            'time': time_str,
            'tag': n.get('tag', '市场'),
            'url': n.get('link', '') if not is_google_news_link(n.get('link', '')) else '',
            'content': (n.get('content') or '')[:300],
        })

    law_items = []
    for p in policies:
        # 把 MM-DD 补成年份（取最近7天内且不超过当天）
        raw_date = p.get('date', '')
        date_str = ''
        if raw_date and len(raw_date) >= 5:
            try:
                md = datetime.strptime(raw_date, '%m-%d')
                year = now.year
                candidate = md.replace(year=year)
                if candidate > now:
                    candidate = candidate.replace(year=year - 1)
                date_str = candidate.strftime('%Y-%m-%d')
            except Exception:
                date_str = now.strftime('%Y-%m-%d')
        if not date_str:
            date_str = now.strftime('%Y-%m-%d')

        law_items.append({
            'title': p.get('title', ''),
            'date': date_str,
            'status': p.get('status', '新发布'),
            'url': p.get('link', '') if not is_google_news_link(p.get('link', '')) else '',
            'content': (p.get('content') or '')[:300],
        })

    data = {
        'updated': now.strftime('%Y-%m-%d %H:%M'),
        'hash': now.strftime('%Y%m%d') + '-v1',
        'news': news_items,
        'laws': law_items,
    }

    with open('news-data.json', 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("  已保存: news-data.json")


# ==================== 邮件发送 ====================

def send_emails(html, subscribers):
    """通过 SMTP 发送邮件"""
    smtp_host = os.environ.get('SMTP_HOST', 'smtp.qq.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '465'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_pass = os.environ.get('SMTP_PASS', '')
    sender_name = os.environ.get('SMTP_FROM_NAME', os.environ.get('SENDER_NAME', '泓济环保'))

    now = datetime.utcnow() + timedelta(hours=8)
    subject = f'泓济环保\u00b7行业日报 {now.strftime("%Y年%m月%d日")}'

    results = []
    for email in subscribers:
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = formataddr((sender_name, smtp_user))
            msg['To'] = email
            msg['Date'] = formatdate(localtime=True)
            msg['Message-ID'] = make_msgid(idstring='honess-daily', domain='qq.com')
            msg['Reply-To'] = smtp_user
            msg['List-Unsubscribe'] = f'<mailto:{smtp_user}?subject=unsubscribe>'
            msg['List-Unsubscribe-Post'] = 'List-Unsubscribe=One-Click'
            msg['X-Mailer'] = 'HonessDailyReport/1.0'
            msg['Auto-Submitted'] = 'auto-generated'
            msg.attach(MIMEText(html, 'html', 'utf-8'))

            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
                server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_user, email, msg.as_string())

            results.append((email, True, ''))
            print(f"  OK {email}")
        except Exception as e:
            results.append((email, False, str(e)))
            print(f"  FAIL {email}: {e}")

    return results


# ==================== 主流程 ====================

def main():
    now = datetime.utcnow() + timedelta(hours=8)
    print("=" * 50)
    print(f"泓济环保行业日报")
    print(f"执行时间: {now.strftime('%Y-%m-%d %H:%M')} (北京时间)")
    print("=" * 50)

    # 1. 抓取新闻
    print("\n[1/6] 抓取新闻...")
    articles = fetch_all_news()
    print(f"  总计: {len(articles)} 篇")

    articles = deduplicate(articles)
    print(f"  去重后: {len(articles)} 篇")

    articles = filter_relevant(articles)
    print(f"  相关: {len(articles)} 篇")

    articles = filter_recent(articles, RECENT_DAYS)
    print(f"  最近{RECENT_DAYS}天: {len(articles)} 篇")

    news = select_news(articles, NEWS_COUNT)
    print(f"  选中: {len(news)} 条")

    # 1.5 对选中新闻补充抓取原文（仅对尚未提取正文的条目）
    print("\n[1.5/6] 抓取新闻原文...")
    for i, n in enumerate(news):
        if n.get('content'):
            print(f"  [{i+1}/{len(news)}] 已有正文: {n['title'][:30]}...")
            continue
        link = n.get('link', '')
        if link and not is_google_news_link(link):
            print(f"  [{i+1}/{len(news)}] 抓取: {n['title'][:30]}...")
            real_url, content = fetch_page_content(link)
            n['link'] = real_url
            n['content'] = clean_content(content, n.get('title', ''))
        else:
            print(f"  [{i+1}/{len(news)}] 无有效链接: {n['title'][:30]}...")
        time.sleep(0.5)

    # 2. 抓取政策法规
    print("\n[2/6] 抓取政策法规...")
    policies = fetch_policies()
    print(f"  获取: {len(policies)} 条")

    # 2.5 对政策法规补充抓取原文
    print("\n[2.5/6] 抓取政策原文...")
    for i, p in enumerate(policies):
        if p.get('content'):
            print(f"  [{i+1}/{len(policies)}] 已有正文: {p['title'][:30]}...")
            continue
        link = p.get('link', '')
        if link and not is_google_news_link(link):
            print(f"  [{i+1}/{len(policies)}] 抓取: {p['title'][:30]}...")
            real_url, content = fetch_page_content(link)
            p['link'] = real_url
            p['content'] = clean_content(content, p.get('title', ''))
        else:
            print(f"  [{i+1}/{len(policies)}] 无有效链接: {p['title'][:30]}...")
        time.sleep(0.5)

    # 2.6 跨栏目去重：移除与新闻重复的法规
    print("\n[2.6/6] 跨栏目去重...")
    before_count = len(policies)
    policies = deduplicate_cross_section(news, policies)
    print(f"  法规: {before_count} -> {len(policies)} 条")

    # 3. 生成 HTML
    print("\n[3/6] 生成日报HTML...")
    html = generate_html(news, policies)
    print(f"  HTML 长度: {len(html)} 字符")

    # 保存 HTML 副本（用于 GitHub Actions artifact）
    with open('daily-report.html', 'w', encoding='utf-8') as f:
        f.write(html)
    print("  已保存: daily-report.html")

    # 4. 保存新闻数据 JSON（供招聘工具页同步使用）
    save_news_json(news, policies)

    # 5. 读取订阅者
    print("\n[5/6] 读取订阅者...")
    subs_path = os.path.join(os.path.dirname(__file__), '..', 'subscribers.json')
    with open(subs_path, 'r', encoding='utf-8') as f:
        subscribers = json.load(f)['emails']

    # 如果指定了立即发送目标（如新订阅者），则只发给这些邮箱
    immediate_env = os.environ.get('IMMEDIATE_RECIPIENTS', '').strip()
    if immediate_env:
        subscribers = [e.strip() for e in immediate_env.split(',') if e.strip()]
        print(f"  立即发送模式: {len(subscribers)} 人 -> {subscribers}")
    else:
        print(f"  订阅者: {len(subscribers)} 人")

    # 6. 发送邮件
    print("\n[6/6] 发送邮件...")
    results = send_emails(html, subscribers)

    # 汇总
    success = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - success
    print(f"\n{'=' * 50}")
    print(f"完成! 新闻 {len(news)} 条 | 法规 {len(policies)} 条")
    print(f"邮件: OK {success} | FAIL {failed}")
    print(f"{'=' * 50}")


if __name__ == '__main__':
    main()
