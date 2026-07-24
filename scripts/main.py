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
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from datetime import datetime, timedelta
from jinja2 import Template

# ==================== 配置 ====================

# 搜索关键词（每个关键词对应一次 Google News RSS 查询）
SEARCH_QUERIES = [
    "环保政策 水处理",
    "污水处理 技术 创新",
    "工业废水 零排放 MBR MVR",
    "排放标准 生态环境部 法规",
    "水务 中标 项目 市场",
    "环保行业 业绩 并购",
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

# 选取新闻条数
NEWS_COUNT = 6
# 政策法规条数
POLICY_COUNT = 5
# 最近N天的新闻
RECENT_DAYS = 7


# ==================== 新闻抓取 ====================

def resolve_google_news_link(url):
    """解析 Google News 重定向链接，返回真实文章来源 URL"""
    if not url or 'news.google.com' not in url:
        return url
    try:
        from googlenewsdecoder import decoderv2, decoderv1
        # v2 会调用 Google 内部接口，在 GitHub Actions 海外节点通常可用
        decoded = decoderv2(url)
        if decoded and decoded.startswith('http'):
            return decoded
        # v2 失败时尝试本地 v1 解码
        decoded = decoderv1(url)
        if decoded and decoded.startswith('http'):
            return decoded
        return url
    except Exception as e:
        print(f"    链接解析失败: {e}")
        return url


def fetch_rss(query):
    """从 Google News RSS 获取新闻，返回真实来源链接"""
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-CN&gl=CN&ceid=CN:zh"
    try:
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries:
            # Google News 标题格式: "标题 - 来源"
            title = entry.title
            source = ''
            source_url = ''
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                source = entry.source.title
                if hasattr(entry.source, 'href'):
                    source_url = entry.source.href
            elif ' - ' in title:
                parts = title.rsplit(' - ', 1)
                title = parts[0]
                source = parts[1] if len(parts) > 1 else ''

            # 优先解析真实来源 URL，避免邮件/工具中打开 Google News 被墙
            raw_link = entry.link if hasattr(entry, 'link') else ''
            if raw_link and 'news.google.com' in raw_link:
                link = resolve_google_news_link(raw_link)
            elif source_url and source_url.startswith('http'):
                link = source_url
            else:
                link = raw_link

            # 清理摘要
            summary = ''
            if hasattr(entry, 'summary'):
                summary = re.sub(r'<[^>]+>', '', entry.summary)
                summary = summary.strip()

            # 解析日期
            published = entry.published if hasattr(entry, 'published') else ''
            published_parsed = entry.published_parsed if hasattr(entry, 'published_parsed') and entry.published_parsed else None

            articles.append({
                'title': title.strip(),
                'source': source.strip(),
                'link': link,
                'summary': summary,
                'published': published,
                'published_parsed': published_parsed,
            })
        return articles
    except Exception as e:
        print(f"  RSS 获取失败 [{query}]: {e}")
        return []


def fetch_all_news():
    """获取所有关键词的新闻"""
    all_articles = []
    for i, query in enumerate(SEARCH_QUERIES):
        print(f"  [{i+1}/{len(SEARCH_QUERIES)}] 搜索: {query}")
        articles = fetch_rss(query)
        print(f"    获取 {len(articles)} 篇")
        all_articles.extend(articles)
        time.sleep(0.5)  # 礼貌性延迟
    return all_articles


def deduplicate(articles):
    """去重"""
    seen = set()
    unique = []
    for a in articles:
        key = a['title'][:40].strip()
        if key and key not in seen:
            seen.add(key)
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


def filter_recent(articles, days=7):
    """过滤最近N天的新闻"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    recent = []
    for a in articles:
        if a['published_parsed']:
            try:
                dt = datetime(*a['published_parsed'][:6])
                if dt >= cutoff:
                    recent.append(a)
            except:
                recent.append(a)
        else:
            recent.append(a)
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
    """获取政策法规"""
    url = "https://news.google.com/rss/search?q=%E6%8E%92%E6%94%BE%E6%A0%87%E5%87%86+OR+%E6%B1%A1%E6%9F%93%E9%98%B2%E6%B2%BB+OR+%E7%94%9F%E6%80%81%E7%8E%AF%E5%A2%83%E9%83%A8+OR+%E6%B0%B4%E6%B1%A1%E6%9F%93%E9%98%B2%E6%B2%BB&hl=zh-CN&gl=CN&ceid=CN:zh"
    try:
        feed = feedparser.parse(url)
        policies = []
        for entry in feed.entries[:10]:
            title = entry.title
            source = ''
            source_url = ''
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                source = entry.source.title
                if hasattr(entry.source, 'href'):
                    source_url = entry.source.href
            elif ' - ' in title:
                parts = title.rsplit(' - ', 1)
                title = parts[0]
                source = parts[1] if len(parts) > 1 else ''

            # 优先解析真实来源 URL，避免被墙
            raw_link = entry.link if hasattr(entry, 'link') else ''
            if raw_link and 'news.google.com' in raw_link:
                link = resolve_google_news_link(raw_link)
            elif source_url and source_url.startswith('http'):
                link = source_url
            else:
                link = raw_link

            # 判断状态
            text = title.lower()
            if any(kw in text for kw in ['实施', '施行', '生效']):
                status = '已实施'
            else:
                status = '新发布'

            # 日期
            date_display = ''
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                try:
                    dt = datetime(*entry.published_parsed[:6])
                    date_display = dt.strftime('%m-%d')
                except:
                    pass

            policies.append({
                'title': title.strip(),
                'link': link,
                'source': source.strip(),
                'date': date_display,
                'status': status,
            })

        # 去重
        seen = set()
        unique = []
        for p in policies:
            key = p['title'][:30]
            if key not in seen:
                seen.add(key)
                unique.append(p)

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


def format_date_short(published_parsed):
    """格式化日期为 MM-DD"""
    if not published_parsed:
        return ''
    try:
        dt = datetime(*published_parsed[:6])
        return dt.strftime('%m-%d')
    except:
        return ''


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

    two_col_news = news[1:4] if len(news) > 1 else []
    for n in two_col_news:
        n['date_short'] = format_date_short(n.get('published_parsed'))

    briefs_raw = news[4:] if len(news) > 4 else []
    briefs = []
    for n in briefs_raw[:3]:
        briefs.append({
            'title': n['title'],
            'tag': n['tag'],
            'summary': (n.get('summary', '') or '')[:60] + ('...' if len(n.get('summary', '')) > 60 else ''),
            'date': now.strftime('%m-%d'),
        })

    keywords = generate_keywords(news)

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
            'url': n.get('link', ''),
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
            'url': p.get('link', ''),
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

    # 2. 抓取政策法规
    print("\n[2/6] 抓取政策法规...")
    policies = fetch_policies()
    print(f"  获取: {len(policies)} 条")

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
