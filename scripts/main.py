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
import base64
import urllib.parse
import xml.etree.ElementTree as ET
import requests as req_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from datetime import datetime, timedelta
from jinja2 import Template
from trafilatura import extract as traf_extract

# ==================== 配置 ====================

# 搜索关键词（每个关键词对应一次 Google News RSS 查询）
# 聚焦工业废水/工业水处理，减少纯市政新闻
SEARCH_QUERIES = [
    "工业废水处理",
    "工业废水 零排放",
    "工业园区 污水",
    "化工废水 处理",
    "制药废水 处理",
    "废水治理 工程",
    "排放标准 生态环境部",
    "水污染防治",
    "环保 水处理 技术",
    "污泥处理 处置",
    "再生水 工业",
    "工业用水 节水",
]

# 相关性过滤关键词（标题或摘要中包含才算相关）
RELEVANT_KEYWORDS = [
    '水处理', '污水', '环保', '废水', 'MBR', 'MVR', '零排放',
    '排放标准', '水务', '膜技术', '脱硫', '污泥', '再生水',
    '黑臭水体', '工业园区', '海水淡化', '饮用水', '水污染',
    '水环境', '污水处理厂', '给水', '排水',
    '工业废水', '工业污水', '工业用水', '化工废水', '制药废水',
    '电镀废水', '印染废水', '焦化废水', '矿井水',
    '中水回用', '废水回用', '蒸发结晶', '反渗透',
    # 英文关键词（新加坡等国际新闻）
    'water', 'wastewater', 'treatment', 'desalination', 'recycling',
    'environment', 'pollution', 'emission', 'sustainability', 'seawater',
    'NEWater', 'PUB', 'sewerage', 'stormwater', 'reservoir',
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
    # 广告/推广/排行榜类
    '一览表', '排行榜', '龙头企业榜', '评价高', '性能优',
    '选高效', '哪家好', '多少钱', '价格表', '批发',
]

# 台湾省相关关键词（直接排除，不收录台湾新闻，含繁体字）
# 覆盖地名、机构、企业、人物，确保从标题/摘要/正文/URL各维度拦截
TAIWAN_KEYWORDS = [
    # 地名（简体）
    '台湾', '台北', '台南', '高雄', '新北', '桃园', '台中', '台东',
    '新竹', '基隆', '嘉义', '屏东', '花莲', '宜兰', '彰化',
    '云林', '苗栗', '澎湖', '金门', '马祖', '日月潭',
    '六轻',  # 台塑六轻工业区（涉水处理）
    # 机构/企业
    '台啤', '台企', '台商', '台当局', '台水', '台电', '台塑',
    '台积电', '鸿海', '联电', '台达电', '环保署', '水利署',
    '中华民国', '陆委会',
    # 人物
    '蔡英文', '赖清德',
    # 繁体字变体
    '臺灣', '臺北', '臺南', '臺中', '臺東', '臺當局', '臺',
    '臺塑', '臺積電', '臺電', '臺水',
    '新竹市', '新竹縣', '嘉義市', '嘉義縣', '屏東', '花蓮',
    '宜蘭', '彰化', '雲林', '苗栗', '澎湖', '金門', '馬祖',
]

# 台湾相关URL关键词（检查链接是否来自台湾媒体/含taiwan标识）
TAIWAN_URL_KEYWORDS = [
    'taiwan', '/tw/', 'taipei', 'kaohsiung',
    'udn.com',      # 联合报
    'chinatimes',   # 中时
    'ltn.com',      # 自由时报
    'cna.com.tw',   # 中央社
    'ettoday',      # 东森
    'storm.mg',     # 风传媒
    'newtalk',      # 新头壳
    'mohw.gov.tw',  # 台湾卫福部
    'epa.gov.tw',   # 台湾环保署
    'wra.gov.tw',   # 台湾水利署
]

# 新加坡相关关键词（新加坡环保/水务新闻，最多保留1条）
SINGAPORE_KEYWORDS = [
    '新加坡', 'Singapore', 'PUB', 'NEWater', 'Marina Barrage',
    'Tuas', 'Changi', 'Jurong',
]

# 跳过的域名（JS渲染/反爬/内容质量差）
SKIP_DOMAINS = ['msn.com', 'msn.cn', 'investing.com', 'bing.com']

# 工业废水/工业水处理关键词（命中优先选择）
INDUSTRIAL_KEYWORDS = [
    '工业废水', '工业污水', '工业用水', '工业水', '工业园区',
    '化工废水', '制药废水', '电镀废水', '印染废水', '造纸废水',
    '焦化废水', '农药废水', '冶炼废水', '矿井水',
    '零排放', '零液排放', '中水回用', '废水回用',
    '废水处理工程', '污水处理工程', '水处理项目',
    '高盐废水', '高浓度废水', '含油废水', '含磷废水',
    '脱硫废水', '浓盐水', '蒸发结晶', 'MVR', 'MBR',
    'COD降解', '氨氮去除', '膜处理', '反渗透',
    '中标', 'EPC', '总承包', '运维',
]

# 纯市政新闻关键词（命中则降低优先级，但不直接排除）
# 只有标题+摘要全部是市政内容、不含任何工业关键词时才排除
MUNICIPAL_KEYWORDS = [
    '市政污水', '城镇污水', '城市污水处理', '农村污水',
    '污水处理厂投运', '污水处理厂通水', '管网改造', '管网更新',
    '黑臭水体', '河长制', '断面水质', '国考断面',
    '提标改造', '准四类', '雨污分流', '排水管网',
    '自来水', '饮用水水源', '供水',
]

# 垃圾文本模式（trafilatura可能提取到的跳转/加载页面文本）
JUNK_PATTERNS = [
    '正在访问', '正在跳转', '正在加载', '访问网站', '访问原网',
    '请稍候', '正在为您', '点击这里', '跳转中', '正在进入',
    'Loading', 'Redirecting', 'Please wait', 'Click here',
    '页面不存在', '404', '无法访问', '访问出错',
    # MEE官网导航/功能文本
    '仅打印内容', '相关阅读推荐', '您可能对以下文章感兴趣',
    '温馨提示', '您访问的链接即将离开', '是否继续',
    '字号：', '打印', '分享到', '收藏', '返回顶部',
    # 广告/推广文本
    '进口KNC', '脑益莱', 'BRARELIV', '细胞养脑', '健忘用脑',
    '限时抢购', '立即下单', '点击购买', '免费领取', '优惠券',
    '扫码下载', '下载APP', '关注公众号', '扫码关注',
]

# 选取新闻条数
NEWS_COUNT = 6
# 政策法规条数
POLICY_COUNT = 5
# 最近N天的新闻
RECENT_DAYS = 21


# ==================== 新闻抓取 ====================

def is_google_news_link(url):
    """判断是否为 Google News 重定向链接（国内无法访问）"""
    return url and 'news.google.com' in url


def resolve_google_news_url(url):
    """从 Google News 重定向 URL 中提取真实文章 URL
    方法1: base64 解码后在 bytes 中搜索 URL（Google News article ID 是 protobuf 编码）
    方法2: HTTP 重定向跟随
    方法3: Playwright 无头浏览器（最可靠，能执行 JS 跳转）"""
    if not url or 'news.google.com' not in url:
        return url
    # 方法1: base64 解码后在原始 bytes 中搜索 URL
    try:
        if '/articles/' in url:
            article_id = url.split('/articles/')[1].split('?')[0]
            padding = 4 - len(article_id) % 4
            if padding < 4:
                article_id += '=' * padding
            decoded = base64.urlsafe_b64decode(article_id)
            # 在 bytes 中搜索 URL（protobuf 数据中 URL 是连续的 UTF-8 字符串）
            match = re.search(rb'https?://[^\x00-\x1f\x7f<>"\s]+', decoded)
            if match:
                real_url = match.group(0).decode('utf-8', errors='ignore').rstrip()
                if 'news.google.com' not in real_url and len(real_url) > 20:
                    return real_url
    except Exception:
        pass
    # 方法2: HTTP 重定向跟随（在 GitHub Actions 美国服务器上可用）
    try:
        resp = req_lib.get(url, timeout=10, allow_redirects=True, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })
        final_url = resp.url
        if final_url and 'news.google.com' not in final_url:
            return final_url
    except Exception:
        pass
    # 方法3: Playwright 无头浏览器（能执行 JavaScript 跳转）
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000, wait_until='domcontentloaded')
            # 等待 JS 跳转完成
            page.wait_for_timeout(3000)
            final_url = page.url
            browser.close()
            if final_url and 'news.google.com' not in final_url:
                return final_url
    except ImportError:
        print("    [playwright] 未安装，跳过")
    except Exception as e:
        print(f"    [playwright] 解析失败: {e}")
    return url


def extract_real_url(link):
    """从新闻跳转链接中提取真实文章 URL（支持 Bing 和 Google News）"""
    if not link:
        return link
    # Bing News 跳转链接
    if 'bing.com/news/apiclick' in link:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        real_url = params.get('url', [None])[0]
        if real_url:
            return real_url
    # Google News 链接需要跟随重定向（在 fetch_rss 中已处理，这里作为后备）
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


def is_valid_link(url):
    """检查 URL 是否适合在邮件中展示为'查看原文'链接"""
    if not url:
        return False
    # Google News 链接国内无法访问，不展示为"查看原文"
    if is_google_news_link(url):
        return False
    url_lower = url.lower()
    for domain in SKIP_DOMAINS:
        if domain in url_lower:
            return False
    return True


def clean_content(content, title=''):
    """清理提取的正文内容：过滤垃圾文本、验证相关性"""
    if not content:
        return None
    content = content.strip()
    # 太短的内容直接丢弃
    if len(content) < 50:
        return None
    # 检查是否包含垃圾文本（整段丢弃）
    for pattern in JUNK_PATTERNS:
        if pattern in content:
            print(f"    [clean] 检测到垃圾文本 [{pattern}]，丢弃内容")
            return None
    # 验证正文是否包含水处理/环保行业关键词（排除广告/无关内容）
    # 使用 RELEVANT_KEYWORDS（更宽泛），且标题已含行业词时跳过检查
    title_has_kw = title and any(kw in title for kw in RELEVANT_KEYWORDS)
    if not title_has_kw:
        has_industry_kw = any(kw in content for kw in RELEVANT_KEYWORDS)
        if not has_industry_kw:
            print(f"    [clean] 正文不含行业关键词，疑似广告/无关内容，丢弃")
            return None
    # 检查内容与标题的相关性（至少共享2个中文词）
    if title:
        title_chars = set(re.findall(r'[\u4e00-\u9fff]', title))
        # 英文标题（无中文字符）跳过重叠检查，直接保留
        if title_chars:
            content_chars = set(re.findall(r'[\u4e00-\u9fff]', content[:200]))
            overlap = title_chars & content_chars
            if len(overlap) < 2:
                print(f"    [clean] 内容与标题不相关（重叠{len(overlap)}字），丢弃")
                return None
    # 清理正文末尾常见的导航/功能文本块（截断式，不丢弃整段）
    junk_triggers = [
        '相关阅读推荐', '您可能对以下文章感兴趣', '温馨提示',
        '您访问的链接即将离开', '字号：', '分享到：',
        '【打印', '【收藏', '【关闭',
    ]
    for trigger in junk_triggers:
        pos = content.find(trigger)
        if pos > 0:  # 只在正文中间或末尾出现时截断
            truncated = content[:pos].strip()
            if len(truncated) >= 50:  # 截断后内容仍然够长
                content = truncated
                break
    return content


def fetch_page_content(url):
    """抓取指定 URL 页面的正文内容，返回 (url, content)
    注意：返回原始 URL 而非重定向后的 URL，避免站点重定向到首页覆盖文章链接"""
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
        # 返回原始 URL，不用 resp.url（部分站点会重定向到首页）
        return url, content
    except Exception as e:
        print(f"    [fetch] 失败: {e}")
        return url, None


def parse_google_news_source_urls(rss_text):
    """从 Google News RSS XML 中提取 <source url="..."> 属性
    注意: <source url> 包含的是发布者域名（如 https://news.hfut.edu.cn），
    不是文章 URL。仅用于获取来源信息，不用于替换文章链接。"""
    source_map = {}
    try:
        root = ET.fromstring(rss_text)
        for item in root.findall('.//item'):
            link_elem = item.find('link')
            source_elem = item.find('source')
            if link_elem is not None and source_elem is not None:
                link = (link_elem.text or '').strip()
                source_url = (source_elem.get('url') or '').strip()
                if source_url and link:
                    source_map[link] = source_url
    except Exception as e:
        print(f"  [source_url] XML 解析失败: {e}")
    return source_map


def fetch_rss(query):
    """从 Google News RSS 获取新闻，返回带真实链接的文章列表
    Google News RSS 在美国服务器（GitHub Actions）可访问，返回标准 RSS XML
    同时对 Google News 重定向链接做 URL 解析获取真实文章地址"""
    encoded_query = urllib.parse.quote(query)
    # Google News RSS — 从 GitHub Actions (美国) 可访问，返回中文新闻
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    }
    feed = None
    for attempt in range(2):  # 最多尝试2次
        try:
            resp = req_lib.get(url, headers=headers, timeout=15)
            # 检查返回内容是否为 RSS/XML
            if not resp.text.startswith('<?xml') and '<rss' not in resp.text and '<feed' not in resp.text:
                if attempt == 0:
                    time.sleep(1)
                    continue
                else:
                    print(f"  Google News RSS 返回非XML内容 [{query}]")
                    return []
            feed = feedparser.parse(resp.text)
            if feed.entries:
                break
            if attempt == 0:
                time.sleep(1)  # 第一次为空时等1秒重试
        except Exception as e:
            if attempt == 0:
                time.sleep(1)
            else:
                print(f"  Google News RSS 获取失败 [{query}]: {e}")
                return []
    if not feed or not feed.entries:
        return []
    try:
        articles = []
        for idx, entry in enumerate(feed.entries[:10]):
            title = entry.title if hasattr(entry, 'title') else ''
            link = entry.link if hasattr(entry, 'link') else ''

            # 来源
            source = ''
            if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
                source = entry.source.title
            elif hasattr(entry, 'author'):
                source = entry.author

            # 摘要 — Google News RSS的summary就是文章前几段文字
            summary = ''
            if hasattr(entry, 'summary'):
                summary = re.sub(r'<[^>]+>', '', entry.summary).strip()

            # 日期
            published = entry.published if hasattr(entry, 'published') else ''
            published_parsed = entry.published_parsed if hasattr(entry, 'published_parsed') and entry.published_parsed else None

            articles.append({
                'title': title.strip(),
                'source': source.strip(),
                'link': link,
                'summary': summary,
                # Google News 链接无法直接提取正文(JS渲染)，用RSS summary作为初始正文
                'content': summary if is_google_news_link(link) and len(summary) > 20 else None,
                'published': published,
                'published_parsed': published_parsed,
            })
        return articles
    except Exception as e:
        print(f"  Google News RSS 解析失败 [{query}]: {e}")
        return []


def fetch_mee_news():
    """从生态环境部官网抓取要闻作为新闻来源（过滤相关性，排除纯政治新闻）"""
    urls = [
        'https://www.mee.gov.cn/ywdt/hjywnews/',
        'https://www.mee.gov.cn/ywdt/dfnews/',
        'https://www.mee.gov.cn/ywdt/hjywnews/index2.shtml',  # 第2页
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


# 新加坡环保/水务新闻搜索关键词
SINGAPORE_SEARCH_QUERIES = [
    "Singapore water treatment",
    "Singapore PUB water",
    "Singapore environmental policy",
    "新加坡 水务 环保",
]


def fetch_singapore_news():
    """搜索新加坡环保/水务相关新闻（Bing News RSS）
    仅返回与水处理/环保相关的新加坡新闻，最多5篇"""
    all_articles = []
    for query in SINGAPORE_SEARCH_QUERIES:
        articles = fetch_rss(query)
        # 过滤：标题或摘要中必须包含新加坡相关关键词
        for a in articles:
            text = a['title'] + ' ' + a['summary']
            if any(kw.lower() in text.lower() for kw in SINGAPORE_KEYWORDS):
                # 同时检查是否与环保/水处理相关
                env_keywords = ['water', 'waterwater', 'treatment', 'environment',
                                'seawater', 'desalination', 'recycling', 'reuse',
                                'pollution', 'emission', 'sustainability',
                                '水', '环保', '污水', '废水', '水务', '再生水']
                if any(kw.lower() in text.lower() for kw in env_keywords):
                    a['tag'] = '市场'  # 新加坡新闻标记为市场类
                    a['_singapore'] = True  # 标记为新加坡来源，跳过相关性过滤
                    all_articles.append(a)
        time.sleep(0.3)
        if len(all_articles) >= 5:
            break

    return all_articles[:5]


def fetch_honess_news():
    """从泓济环保官网抓取最新新闻/公告
    每次发送前自动检查官网，如有新动态则加入日报"""
    url = 'https://www.honess.cn/honess-news/'
    try:
        resp = req_lib.get(url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp.encoding = 'utf-8'
        html = resp.text
    except Exception as e:
        print(f"  泓济官网抓取失败: {e}")
        return []

    articles = []
    # 提取所有指向 /blog/ 的链接及其位置
    link_pattern = re.compile(
        r'<a[^>]*href="(https://(?:www\.)?honess\.cn/blog/[^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL
    )
    # 提取所有日期 (YYYY-MM-DD) 及其位置
    date_pattern = re.compile(r'(20\d{2}-\d{2}-\d{2})')

    links = [(m.start(), m.group(1), re.sub(r'<[^>]+>', '', m.group(2)).strip())
             for m in link_pattern.finditer(html)]
    dates = [(m.start(), m.group(1)) for m in date_pattern.finditer(html)]

    for link_pos, link_url, title in links:
        # 跳过非文章链接（导航、侧边栏等）
        if not title or len(title) < 5:
            continue
        # 跳过"更多新闻"等导航链接
        if '更多' in title or 'more' in title.lower():
            continue

        # 找距离链接最近的、在链接之前的日期
        closest_date = ''
        min_dist = float('inf')
        for date_pos, date_str in dates:
            dist = link_pos - date_pos
            if 0 < dist < min_dist and dist < 800:
                min_dist = dist
                closest_date = date_str

        pp = None
        if closest_date:
            try:
                pp = datetime.strptime(closest_date, '%Y-%m-%d').timetuple()
            except Exception:
                pass

        articles.append({
            'title': title,
            'source': '泓济环保',
            'link': link_url,
            'summary': '',
            'content': None,
            'published': closest_date,
            'published_parsed': pp,
            '_honess': True,
        })

    return articles


def fetch_all_news():
    """获取所有关键词的新闻（Bing News RSS + MEE 官网 + 新加坡 + 泓济官网）"""
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

    # 新加坡环保/水务新闻（独立搜索，最多保留1条在日报中）
    print(f"  补充来源: 新加坡环保水务新闻...")
    sg_articles = fetch_singapore_news()
    print(f"    获取 {len(sg_articles)} 篇")
    all_articles.extend(sg_articles)

    # 泓济环保官网新闻/公告（每次发送前自动检查）
    print(f"  补充来源: 泓济环保官网...")
    honess_articles = fetch_honess_news()
    if honess_articles:
        print(f"    获取 {len(honess_articles)} 篇")
        for a in honess_articles:
            print(f"      - [{a.get('published', '无日期')}] {a['title'][:40]}")
    else:
        print(f"    无新动态")
    all_articles.extend(honess_articles)

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
    """过滤相关新闻（排除台湾省新闻）"""
    relevant = []
    taiwan_excluded = 0
    municipal_excluded = 0
    for a in articles:
        # 新加坡来源已预先过滤，直接通过
        if a.get('_singapore'):
            relevant.append(a)
            continue
        # 泓济官网新闻直接通过（公司动态不需要关键词匹配）
        if a.get('_honess'):
            relevant.append(a)
            continue
        text = a['title'] + ' ' + a['summary']
        link_lower = (a.get('link') or '').lower()
        # 排除台湾省新闻（标题+摘要+URL三重检查）
        if any(kw in text for kw in TAIWAN_KEYWORDS) or any(kw in link_lower for kw in TAIWAN_URL_KEYWORDS):
            taiwan_excluded += 1
            continue
        # 排除广告/排行榜类内容
        if any(kw in text for kw in EXCLUDE_KEYWORDS):
            continue
        if any(kw in text for kw in RELEVANT_KEYWORDS):
            # 排除纯市政新闻：标题+摘要不含工业关键词但含市政关键词
            has_industrial = any(kw in text for kw in INDUSTRIAL_KEYWORDS)
            has_municipal = any(kw in text for kw in MUNICIPAL_KEYWORDS)
            if has_municipal and not has_industrial:
                municipal_excluded += 1
                continue
            relevant.append(a)
    if taiwan_excluded:
        print(f"  排除台湾省新闻: {taiwan_excluded}条")
    if municipal_excluded:
        print(f"  排除纯市政新闻: {municipal_excluded}条")
    return relevant


def filter_recent(articles, days=14):
    """过滤最近N天的新闻（无日期的保留，新加坡来源不受日期限制，泓济官网用90天窗口）"""
    cutoff = datetime.utcnow() - timedelta(days=days)
    honess_cutoff = datetime.utcnow() - timedelta(days=90)
    recent = []
    for a in articles:
        # 新加坡来源跳过日期过滤
        if a.get('_singapore'):
            recent.append(a)
            continue
        # 泓济官网用90天窗口（公司官网更新不频繁）
        if a.get('_honess'):
            if a['published_parsed']:
                try:
                    dt = datetime(*a['published_parsed'][:6])
                    if dt >= honess_cutoff:
                        recent.append(a)
                except Exception:
                    recent.append(a)
            else:
                recent.append(a)
            continue
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


def filter_valid_links(articles):
    """过滤掉链接无效的文章（MSN等跳过域名，且无正文）
    Google News 链接保留（URL已在fetch_rss中解析，解析失败的在正文提取阶段处理）"""
    valid = []
    removed = 0
    for a in articles:
        link = a.get('link', '')
        content = a.get('content', '')
        if is_valid_link(link) or content:
            # 有效链接 或 已有正文的文章 保留
            valid.append(a)
        elif is_google_news_link(link):
            # Google News 链接暂且保留（URL解析可能部分失败，后续正文提取会处理）
            valid.append(a)
        else:
            removed += 1
    if removed:
        print(f"  过滤无效链接: 移除 {removed} 篇（MSN/Bing等）")
    return valid


def tag_article(article):
    """为文章打标签: 公司动态/政策/技术/市场"""
    if article.get('_honess'):
        return '公司动态'
    text = article['title'] + ' ' + article['summary']
    if any(kw in text for kw in POLICY_KEYWORDS):
        return '政策'
    elif any(kw in text for kw in TECH_KEYWORDS):
        return '技术'
    else:
        return '市场'


def select_news(articles, count=6):
    """选择最重要的N条新闻，保证分类多样性
    - 泓济官网新闻优先入选（公司动态）
    - 工业废水相关新闻优先入选
    - 新加坡新闻最多保留1条
    """
    # 排序：新到旧
    articles.sort(key=lambda x: x['published_parsed'] or (0,), reverse=True)

    # 打标签
    for a in articles:
        a['tag'] = tag_article(a)

    # 分区：泓济官网优先 → 工业新闻 → 非工业大陆新闻 → 新加坡限1条
    honess = []
    industrial = []
    other_mainland = []
    singapore = []
    for a in articles:
        if a.get('_honess'):
            honess.append(a)
        elif any(kw in (a['title'] + ' ' + a['summary']) for kw in SINGAPORE_KEYWORDS):
            singapore.append(a)
        elif any(kw in (a['title'] + ' ' + a['summary']) for kw in INDUSTRIAL_KEYWORDS):
            industrial.append(a)
        else:
            other_mainland.append(a)

    # 新加坡最多保留1条
    singapore = singapore[:1]
    if singapore:
        print(f"  新加坡新闻: 保留1条（共{len([a for a in articles if any(kw in (a['title']+' '+a['summary']) for kw in SINGAPORE_KEYWORDS)])}条）")

    if honess:
        print(f"  泓济官网新闻: {len(honess)}条（优先入选）")
    if industrial:
        print(f"  工业相关新闻: {len(industrial)}条（优先入选）")

    # 合并：泓济官网 → 工业新闻 → 非工业大陆 → 新加坡(1条)
    prioritized = honess + industrial + other_mainland + singapore

    # 按分类限制数量
    selected = []
    tag_counts = {'公司动态': 0, '政策': 0, '技术': 0, '市场': 0}
    max_per_tag = 3

    for a in prioritized:
        if len(selected) >= count:
            break
        tag = a['tag']
        # 公司动态不限量（泓济官网新闻有多少选多少）
        if tag == '公司动态':
            selected.append(a)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        elif tag_counts.get(tag, 0) < max_per_tag:
            selected.append(a)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1

    # 如果不够，从未选中的补充
    if len(selected) < count:
        for a in prioritized:
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
        featured['has_valid_link'] = is_valid_link(featured.get('link', ''))
        # 展示内容：优先用抓取的正文，回退到 RSS 摘要，最后回退到生成的摘要
        featured['display_text'] = generate_snippet(
            featured.get('title', ''),
            featured.get('content'),
            featured.get('summary')
        )[:500]

    two_col_news = news[1:4] if len(news) > 1 else []
    for n in two_col_news:
        n['date_short'] = format_date_short(n.get('published_parsed'))
        n['has_valid_link'] = is_valid_link(n.get('link', ''))
        n['display_text'] = generate_snippet(
            n.get('title', ''),
            n.get('content'),
            n.get('summary')
        )[:300]

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
        p['has_valid_link'] = is_valid_link(p.get('link', ''))
        p['display_text'] = (p.get('content') or '')[:150]

    # 统计
    tag_counts = {'公司动态': 0, '政策': 0, '技术': 0, '市场': 0}
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
        company_count=tag_counts.get('公司动态', 0),
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
            'content': (n.get('content') or '')[:500],
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
            'url': p.get('link', '') if is_valid_link(p.get('link', '')) else '',
            'content': (p.get('content') or '')[:500],
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

def generate_text_version(news, policies):
    """生成纯文本版本的日报（用于反垃圾邮件）"""
    now = datetime.utcnow() + timedelta(hours=8)
    lines = []
    lines.append(f"泓济环保·行业日报 {now.strftime('%Y年%m月%d日')}")
    lines.append("=" * 40)
    lines.append("")

    # 新闻
    lines.append(f"【行业资讯】共{len(news)}条")
    lines.append("-" * 40)
    for i, n in enumerate(news):
        lines.append(f"\n{i+1}. [{n.get('tag', '市场')}] {n.get('title', '')}")
        content = n.get('content') or n.get('summary') or ''
        if content:
            lines.append(content[:300])
        link = n.get('link', '')
        if link and is_valid_link(link):
            lines.append(f"原文链接: {link}")
        lines.append("")

    # 法规
    lines.append(f"【政策法规】共{len(policies)}条")
    lines.append("-" * 40)
    for i, p in enumerate(policies):
        lines.append(f"\n{i+1}. [{p.get('status', '新发布')}] {p.get('title', '')}")
        content = p.get('content') or ''
        if content:
            lines.append(content[:200])
        link = p.get('link', '')
        if link and is_valid_link(link):
            lines.append(f"原文链接: {link}")
        lines.append("")

    lines.append("-" * 40)
    lines.append("本日报由泓济环保行业日报系统自动生成")
    lines.append("Powered by GitHub Actions")

    return '\n'.join(lines)


def send_emails(html, text_body, subscribers):
    """通过 SMTP 发送邮件（HTML + 纯文本双版本，降低垃圾邮件风险）"""
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
            # 纯文本版本必须在前，HTML在后（MIME标准要求）
            msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
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

    articles_relevant = filter_relevant(articles)
    print(f"  相关: {len(articles_relevant)} 篇")

    articles = filter_recent(articles_relevant, RECENT_DAYS)
    print(f"  最近{RECENT_DAYS}天: {len(articles)} 篇")

    articles = filter_valid_links(articles)
    print(f"  有效链接: {len(articles)} 篇")

    news = select_news(articles, NEWS_COUNT)
    print(f"  选中: {len(news)} 条")

    # 兜底：如果新闻不足4条，放宽日期限制到30天再选一次（复用已抓取数据）
    if len(news) < 4 and len(articles_relevant) > len(articles):
        print(f"  新闻不足4条，放宽到30天...")
        articles_wide = filter_recent(articles_relevant, 30)
        articles_wide = filter_valid_links(articles_wide)
        news = select_news(articles_wide, NEWS_COUNT)
        print(f"  重新选中: {len(news)} 条")

    # 1.5 对选中新闻补充抓取原文（仅对尚未提取正文的条目）
    print("\n[1.5/6] 抓取新闻原文...")
    for i, n in enumerate(news):
        if n.get('content') and len(n.get('content', '')) > 100:
            # 已有充足正文（>100字），跳过
            print(f"  [{i+1}/{len(news)}] 已有正文: {n['title'][:30]}...")
            continue
        link = n.get('link', '')
        if is_google_news_link(link):
            # Google News 链接：用 Playwright 解析真实 URL，然后抓取正文
            print(f"  [{i+1}/{len(news)}] 解析Google News: {n['title'][:30]}...")
            real_url = resolve_google_news_url(link)
            if real_url and not is_google_news_link(real_url):
                n['link'] = real_url
                _, content = fetch_page_content(real_url)
                if content:
                    cleaned = clean_content(content, n.get('title', ''))
                    if cleaned:
                        n['content'] = cleaned
                        print(f"    -> 已抓取正文: {real_url[:60]}")
                    else:
                        print(f"    -> 正文过滤后为空，保留RSS summary")
                else:
                    print(f"    -> 正文抓取失败，保留RSS summary")
            else:
                print(f"    -> URL解析失败，保留RSS summary")
            time.sleep(0.5)
            continue
        if link:
            print(f"  [{i+1}/{len(news)}] 抓取: {n['title'][:30]}...")
            real_url, content = fetch_page_content(link)
            n['link'] = real_url
            n['content'] = clean_content(content, n.get('title', ''))
        else:
            print(f"  [{i+1}/{len(news)}] 无有效链接: {n['title'][:30]}...")
        time.sleep(0.5)

    # 1.6 二次过滤：正文内容检查台湾省新闻，如有则替换
    print("\n[1.6/6] 正文二次检查台湾省新闻...")
    taiwan_found = []
    safe_news = []
    for n in news:
        full_text = (n.get('content') or '') + ' ' + (n.get('link') or '').lower()
        if any(kw in full_text for kw in TAIWAN_KEYWORDS) or any(kw in full_text for kw in TAIWAN_URL_KEYWORDS):
            taiwan_found.append(n)
        else:
            safe_news.append(n)

    if taiwan_found:
        print(f"  发现台湾内容（正文/URL）: {len(taiwan_found)} 条，正在替换...")
        for n in taiwan_found:
            print(f"    移除: {n['title'][:40]}...")
        # 从候选池补充非台湾新闻
        selected_titles = {n['title'] for n in safe_news} | {n['title'] for n in taiwan_found}
        need = NEWS_COUNT - len(safe_news)
        cutoff_30 = datetime.utcnow() - timedelta(days=30)
        for a in articles_relevant:
            if need <= 0:
                break
            if a['title'] in selected_titles:
                continue
            # 检查标题+摘要+URL是否台湾
            text = a['title'] + ' ' + a['summary']
            link_lower = (a.get('link') or '').lower()
            if any(kw in text for kw in TAIWAN_KEYWORDS) or any(kw in link_lower for kw in TAIWAN_URL_KEYWORDS):
                continue
            # 检查链接有效性和日期（用30天窗口）
            if not is_valid_link(a.get('link', '')) and not a.get('content'):
                continue
            if a['published_parsed']:
                try:
                    dt = datetime(*a['published_parsed'][:6])
                    if dt < cutoff_30:
                        continue
                except:
                    pass
            # 提取正文并再次检查
            if not a.get('content') and a.get('link') and not is_google_news_link(a['link']):
                real_url, content = fetch_page_content(a['link'])
                a['link'] = real_url
                a['content'] = clean_content(content, a.get('title', ''))
                time.sleep(0.5)
            if any(kw in (a.get('content') or '') for kw in TAIWAN_KEYWORDS):
                continue
            a['tag'] = tag_article(a)
            safe_news.append(a)
            selected_titles.add(a['title'])
            need -= 1
            print(f"    补充: {a['title'][:40]}...")
        news = safe_news
        print(f"  替换后: {len(news)} 条")
    else:
        print("  无台湾内容，跳过")

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

    # 3. 生成 HTML 和纯文本
    print("\n[3/6] 生成日报HTML...")
    html = generate_html(news, policies)
    print(f"  HTML 长度: {len(html)} 字符")

    # 生成纯文本版本（降低垃圾邮件风险）
    text_body = generate_text_version(news, policies)
    print(f"  纯文本长度: {len(text_body)} 字符")

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
    results = send_emails(html, text_body, subscribers)

    # 汇总
    success = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - success
    print(f"\n{'=' * 50}")
    print(f"完成! 新闻 {len(news)} 条 | 法规 {len(policies)} 条")
    print(f"邮件: OK {success} | FAIL {failed}")
    print(f"{'=' * 50}")


if __name__ == '__main__':
    main()
