import json
import re
import html
import xml.etree.ElementTree as ET

from datetime import datetime, timezone
from email.utils import format_datetime
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


SOURCE_URL = "https://www.almirall.es/inversores/noticias-inversores"
OUTPUT_FILE = "rss.xml"

# Se descargan las tres primeras páginas de resultados.
PAGES_TO_READ = 3

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        text = data.strip()

        if text:
            self.parts.append(text)

    def get_text(self):
        return " ".join(self.parts)


def clean_html(value):
    if not value:
        return ""

    parser = TextExtractor()
    parser.feed(html.unescape(value))

    return re.sub(r"\s+", " ", parser.get_text()).strip()


def parse_spanish_date(value):
    try:
        parts = value.lower().strip().split()

        day = int(parts[0])
        month = MONTHS[parts[1]]
        year = int(parts[2])

        return datetime(
            year,
            month,
            day,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        )

    except Exception:
        return datetime.now(timezone.utc)


def download_page():
    request = Request(
        SOURCE_URL,
        headers={
            "User-Agent": "Mozilla/5.0 Almirall RSS Generator",
            "Accept-Language": "es-ES,es;q=0.9",
        },
    )

    with urlopen(request, timeout=60) as response:
        return response.read().decode(
            "utf-8",
            errors="replace",
        )


def discover_search_endpoint(page_html):
    pattern = re.compile(
        r'<form\s+name="([^"]+_news)"\s+action="([^"]+)"',
        re.IGNORECASE,
    )

    match = pattern.search(page_html)

    if not match:
        raise RuntimeError(
            "No se ha encontrado el buscador de noticias de Almirall."
        )

    form_name = html.unescape(match.group(1))
    action_url = html.unescape(match.group(2))

    # El nombre termina en “news”.
    # Al eliminarlo obtenemos el prefijo de los parámetros.
    prefix = form_name[:-4]

    return action_url, prefix


def request_results(action_url, prefix, page_number):
    form_data = {
        prefix + "page": str(page_number),
        prefix + "keywords": "",
        prefix + "period": "all",
        prefix + "dateStart": "",
        prefix + "dateEnd": "",
        prefix + "dateRange": "",
    }

    request = Request(
        action_url,
        data=urlencode(form_data).encode("utf-8"),
        headers={
            "User-Agent": "Mozilla/5.0 Almirall RSS Generator",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": SOURCE_URL,
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=60) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def obtain_news():
    page_html = download_page()
    action_url, prefix = discover_search_endpoint(page_html)

    articles = []
    seen_urls = set()

    for page_number in range(1, PAGES_TO_READ + 1):
        data = request_results(
            action_url,
            prefix,
            page_number,
        )

        for result in data.get("results", []):
            title = clean_html(result.get("title", ""))

            article_url = urljoin(
                SOURCE_URL,
                result.get("url", ""),
            )

            if (
                not title
                or not article_url
                or article_url in seen_urls
            ):
                continue

            seen_urls.add(article_url)

            description = clean_html(
                result.get("description", "")
            )

            attachment = result.get("attachment") or {}
            attachment_url = attachment.get("url")

            if attachment_url:
                attachment_url = urljoin(
                    SOURCE_URL,
                    attachment_url,
                )

                description += (
                    f" — Documento relacionado: {attachment_url}"
                )

            articles.append(
                {
                    "title": title,
                    "url": article_url,
                    "description": description,
                    "date": parse_spanish_date(
                        result.get("date", "")
                    ),
                }
            )

    articles.sort(
        key=lambda article: article["date"],
        reverse=True,
    )

    return articles


def create_rss(articles):
    rss = ET.Element(
        "rss",
        {
            "version": "2.0",
            "xmlns:atom": "http://www.w3.org/2005/Atom",
        },
    )

    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = (
        "Almirall — Noticias para inversores"
    )

    ET.SubElement(channel, "link").text = SOURCE_URL

    ET.SubElement(channel, "description").text = (
        "Últimas noticias para inversores publicadas por Almirall."
    )

    ET.SubElement(channel, "language").text = "es-es"

    ET.SubElement(channel, "lastBuildDate").text = (
        format_datetime(datetime.now(timezone.utc))
    )

    for article in articles:
        item = ET.SubElement(channel, "item")

        ET.SubElement(item, "title").text = article["title"]
        ET.SubElement(item, "link").text = article["url"]

        guid = ET.SubElement(
            item,
            "guid",
            {"isPermaLink": "true"},
        )

        guid.text = article["url"]

        ET.SubElement(
            item,
            "description",
        ).text = article["description"]

        ET.SubElement(
            item,
            "pubDate",
        ).text = format_datetime(article["date"])

    tree = ET.ElementTree(rss)
    ET.indent(tree, space="  ")

    tree.write(
        OUTPUT_FILE,
        encoding="utf-8",
        xml_declaration=True,
    )


def main():
    articles = obtain_news()

    if not articles:
        raise RuntimeError(
            "Almirall no ha devuelto ninguna noticia."
        )

    create_rss(articles)

    print(
        f"RSS creada correctamente con {len(articles)} noticias."
    )


if __name__ == "__main__":
    main()
