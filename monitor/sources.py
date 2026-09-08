"""Website adapters: fail closed on unexpected responses; never turn errors into []."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

TZ = timezone(timedelta(hours=8))

@dataclass
class Announcement:
    source: str
    code: str
    title: str
    date: str
    number: str
    source_id: str
    url: str

def canonical_url(url):
    p = urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('Invalid announcement URL')
    # Query parameters can identify distinct documents, so preserve them.
    return urlunsplit(('https', p.netloc.lower(), p.path, p.query, ''))

def title_key(title):
    return re.sub(r'\s+', '', title).strip()

def number_from(row, title):
    value = row.get('BULLETIN_NO') or row.get('bulletinNo') or ''
    match = re.search(r'公告编号[：: ]*([0-9]{4}[-－—][0-9]+)', title)
    return str(value or (match.group(1) if match else '')).replace('－', '-').replace('—', '-')

def session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.sse.com.cn/'})
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET', 'POST'])
    s.mount('https://', HTTPAdapter(max_retries=retry))
    return s

def in_window(date, start, end):
    # Websites often supply dates without times. Include the entire boundary day
    # to avoid discarding announcements near the 48-hour boundary.
    return start.date().isoformat() <= date[:10] <= end.date().isoformat()

class SSE:
    name = 'SSE'

    def __init__(self, http=None):
        self.http = http or session()

    @staticmethod
    def parse(row, code):
        if str(row.get('SECURITY_CODE', code)) != code:
            raise ValueError('SSE returned another security')
        title, date, url = row['TITLE'], str(row['SSEDATE'])[:10], row['URL']
        datetime.strptime(date, '%Y-%m-%d')
        if not title or not url:
            raise ValueError('SSE missing title or URL')
        url = canonical_url(urljoin('https://www.sse.com.cn/', url))
        if not urlsplit(url).hostname.endswith('.sse.com.cn'):
            raise ValueError('SSE returned an unexpected document host')
        return Announcement('SSE', code, title, date, number_from(row, title), str(row.get('BULLETIN_ID') or ''), url)

    def fetch(self, code, start, end):
        found = []
        for page in range(1, 101):
            r = self.http.get(os.getenv('SSE_ENDPOINT', 'https://query.sse.com.cn/security/stock/queryCompanyBulletin.do'), params={
                'isPagination': 'true', 'pageHelp.pageSize': 100, 'pageHelp.pageNo': page,
                'pageHelp.beginPage': page, 'pageHelp.endPage': page, 'pageHelp.cacheSize': 1,
                'productId': code, 'beginDate': start.date().isoformat(), 'endDate': end.date().isoformat(),
                'securityType': '0101,120100,020100,020200,120200'}, timeout=(10, 30))
            r.raise_for_status()
            data = r.json()
            help_ = data.get('pageHelp')
            if not isinstance(help_, dict) or not isinstance(help_.get('data'), list):
                raise ValueError('SSE schema changed: pageHelp.data missing')
            rows = help_['data']
            total = int(help_['total'])
            if total < 0 or (not rows and len(found) < total):
                raise ValueError('SSE pagination incomplete')
            found.extend(self.parse(row, code) for row in rows)
            if page * 100 >= total:
                return [a for a in found if in_window(a.date, start, end)]
        raise ValueError('SSE pagination exceeded safety limit')

class CNInfo:
    name = 'CNINFO'

    def __init__(self, http=None):
        self.http = http or session()
        self.http.headers.update({'Referer': 'https://www.cninfo.com.cn/'})

    def fetch(self, code, start, end):
        org = os.getenv('CNINFO_ORG_ID', '')
        if not org:
            r = self.http.post('https://www.cninfo.com.cn/new/information/topSearch/query',
                               data={'keyWord': code, 'maxNum': 10}, timeout=(10, 30))
            r.raise_for_status()
            results = r.json()
            if not isinstance(results, list):
                raise ValueError('CNINFO search schema changed')
            matches = [x for x in results if str(x.get('code')) == code]
            if not matches:
                raise ValueError('CNINFO security not found')
            org = matches[0]['orgId']
        found = []
        for page in range(1, 101):
            r = self.http.post('https://www.cninfo.com.cn/new/hisAnnouncement/query',
                headers={'Referer': 'https://www.cninfo.com.cn/'}, data={
                    'stock': f'{code},{org}', 'tabName': 'fulltext', 'pageSize': 30,
                    'pageNum': page, 'column': 'sse', 'plate': 'sh', 'category': '',
                    'seDate': f'{start.date().isoformat()}~{end.date().isoformat()}',
                    'searchkey': '', 'secid': '', 'sortName': '', 'sortType': '', 'isHLtitle': 'true'}, timeout=(10, 30))
            r.raise_for_status()
            data = r.json()
            if 'totalAnnouncement' not in data or 'announcements' not in data:
                raise ValueError('CNINFO schema changed')
            total = int(data['totalAnnouncement'])
            rows = data['announcements']
            if rows is None and total == 0:
                rows = []
            if not isinstance(rows, list) or (not rows and total > len(found)):
                raise ValueError('CNINFO pagination incomplete')
            for row in rows:
                if str(row['secCode']) != code:
                    raise ValueError('CNINFO returned another security')
                title = re.sub('<[^>]+>', '', row['announcementTitle'])
                date = datetime.fromtimestamp(int(row['announcementTime']) / 1000, TZ).date().isoformat()
                url = canonical_url(urljoin('https://static.cninfo.com.cn/', row['adjunctUrl']))
                if urlsplit(url).hostname != 'static.cninfo.com.cn' or not title:
                    raise ValueError('CNINFO invalid announcement')
                found.append(Announcement(self.name, code, title, date, number_from(row, title), str(row['announcementId']), url))
            if page * 30 >= total:
                return [a for a in found if in_window(a.date, start, end)]
        raise ValueError('CNINFO pagination exceeded safety limit')
