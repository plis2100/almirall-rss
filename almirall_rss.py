import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

SOURCE_URLS = [
    'https://www.almirall.es/inversores/noticias-inversores',
    'https://www.almirall.es/prensa-medios/sala-prensa',
    'https://www.almirall.es/inversores',
]
OUTPUT_FILE = Path('rss.xml')
MAX_ITEMS = 80
MONTHS = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4,
          'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8,
          'septiembre': 9, 'setiembre': 9, 'octubre': 10,
          'noviembre': 11, 'diciembre': 12}
HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
           'Accept-Language': 'es-ES,es;q=0.9'}
DATE_RE = re.compile(r'(?:publicado\s+el\s+)?(\d{1,2})\s+de?\s*(' + '|'.join(MONTHS) + r')\s+de?\s*(20\d{2})', re.I)


def tidy(s):
    return ' '.join((s or '').split())


def date_from(text):
    m = DATE_RE.search(tidy(text))
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)), 12, tzinfo=timezone.utc)
    except ValueError:
        return None


def normalize_url(url, base):
    absolute = urljoin(base, url or '')
    p = urlsplit(absolute)
    if p.hostname not in ('www.almirall.es', 'almirall.es'):
        return ''
    return urlunsplit((p.scheme, p.netloc.lower(), p.path.rstrip('/'), '', ''))


def previous_items():
    result = {}
    if not OUTPUT_FILE.exists():
        return result
    try:
        root = ET.parse(OUTPUT_FILE).getroot()
        for item in root.findall('./channel/item'):
            link = tidy(item.findtext('link'))
            title = tidy(item.findtext('title'))
            if not link or not title:
                continue
            try:
                dt = parsedate_to_datetime(item.findtext('pubDate', ''))
            except (TypeError, ValueError):
                dt = None
            result[link] = {'url': link, 'title': title, 'date': dt,
                            'description': tidy(item.findtext('description'))}
    except (OSError, ET.ParseError):
        print('Aviso: RSS anterior ilegible; no se utilizará como respaldo.')
    return result


def fetch(session, url):
    error = None
    for attempt in range(3):
        try:
            r = session.get(url, timeout=35)
            r.raise_for_status()
            if len(r.content) < 500:
                raise ValueError('Respuesta HTML demasiado corta')
            return r.text
        except (requests.RequestException, ValueError) as exc:
            error = exc
            print(f'Intento {attempt + 1} fallido en {url}: {exc}')
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    print(f'No se pudo consultar {url}: {error}')
    return None


def extract(html, base, investors_only):
    soup = BeautifulSoup(html, 'html.parser')
    found = {}
    for heading in soup.find_all(['h2', 'h3', 'h4']):
        title = tidy(heading.get_text(' ', strip=True))
        if len(title) < 22:
            continue
        link_tag = heading.find('a', href=True) or heading.find_parent('a', href=True)
        if link_tag is None:
            parent = heading.parent
            link_tag = parent.find('a', href=True) if parent else None
        if link_tag is None:
            continue
        url = normalize_url(link_tag.get('href'), base)
        if not url or '/prensa-medios/sala-prensa/' not in url and '/inversores/noticias-inversores/' not in url:
            continue
        context = None
        published = None
        for ancestor in [heading] + list(heading.parents)[:6]:
            text = tidy(ancestor.get_text(' ', strip=True))
            if len(text) > 2200:
                break
            published = date_from(text)
            if published:
                context = text
                break
        if not published:
            continue
        # In the general press room, include only entries labelled investor relations.
        if investors_only and 'relación con inversores' not in (context or '').lower():
            continue
        if url not in found:
            found[url] = {'url': url, 'title': title, 'date': published,
                          'description': 'Noticia oficial de Almirall para inversores.'}
    return found


def write_rss(items):
    ET.register_namespace('atom', 'http://www.w3.org/2005/Atom')
    root = ET.Element('rss', {'version': '2.0'})
    channel = ET.SubElement(root, 'channel')
    for tag, value in [('title', 'Almirall — Noticias para inversores'),
                       ('link', SOURCE_URLS[0]),
                       ('description', 'Noticias oficiales de Almirall para inversores.'),
                       ('language', 'es'),
                       ('lastBuildDate', format_datetime(datetime.now(timezone.utc)))]:
        ET.SubElement(channel, tag).text = value
    for entry in items:
        item = ET.SubElement(channel, 'item')
        for tag, value in [('title', entry['title']), ('link', entry['url']),
                           ('description', entry['description'])]:
            ET.SubElement(item, tag).text = value
        ET.SubElement(item, 'guid', {'isPermaLink': 'true'}).text = entry['url']
        if entry['date']:
            ET.SubElement(item, 'pubDate').text = format_datetime(entry['date'])
    ET.indent(root, space='  ')
    temp = OUTPUT_FILE.with_suffix('.xml.tmp')
    ET.ElementTree(root).write(temp, encoding='utf-8', xml_declaration=True)
    ET.parse(temp)
    temp.replace(OUTPUT_FILE)


def main():
    old = previous_items()
    session = requests.Session()
    session.headers.update(HEADERS)
    fresh = {}
    for index, url in enumerate(SOURCE_URLS):
        page = fetch(session, url)
        if not page:
            continue
        results = extract(page, url, investors_only=(index == 1))
        print(f'{url}: {len(results)} noticias identificadas')
        fresh.update(results)
    if not fresh:
        if old:
            print('AVISO: No se detectaron noticias nuevas; se conserva rss.xml sin cambios.')
            return
        raise RuntimeError('No se encontraron noticias y no existe RSS anterior. Revisar estructura HTML.')
    for url, entry in fresh.items():
        if url in old and old[url]['date'] and not entry['date']:
            entry['date'] = old[url]['date']
    old.update(fresh)
    entries = sorted(old.values(), key=lambda x: x['date'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:MAX_ITEMS]
    write_rss(entries)
    print(f'RSS generado: {len(entries)} entradas ({len(fresh)} detectadas en web).')


if __name__ == '__main__':
    main()
