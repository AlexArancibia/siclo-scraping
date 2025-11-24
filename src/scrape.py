import logging
import os
import re

import openai
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page
from bs4 import BeautifulSoup

from src.dataframes import init_dataframes, append_scraped_data, export_and_upload
from src.sitemap_utils import get_filtered_sitemap_urls, get_all_links_from_homepage
from src.db_utils import bulk_insert, get_connection, init_db
from src.llm import categorize_urls_with_llm, extract_structured_data, merge_gym_data_with_llm

pages_to_scrape = {
    "bioritmo": "https://www.bioritmo.com.pe/",
    # "limayoga": "https://limayoga.com/",
    # "nasceyoga": "https://www.nasceyoga.com/",
    # "zendayoga": "https://www.zendayoga.com/",
    # "matmax": "https://matmax.world/",
    # "anjali": "https://anjali.pe/",
    # "purepilatesperu": "https://www.purepilatesperu.com/",
    # "balancestudio": "https://balancestudio.pe/",
    # "curvaestudio": "https://curvaestudio.com/", # no robots.txt
    # "fitstudioperu": "https://fitstudioperu.com/",
    # "funcionalstudio": "https://www.funcionalstudio.pe/",  # can't parse sitemaps xml
    # "pilatesesencia": "https://pilatesesencia.com/",
    # "twopilatesstudio": "https://twopilatesstudio.wixsite.com/twopilatesstudio/", # no sitemap in robots.txt
    # "iliveko": "https://iliveko.com/locales/pe/", # no sitemap in robots.txt
    # "raise": "https://raise.pe/",
    # "shadow": "https://shadow.pe/", #  no sitemap in robots.txt
    # "elevatestudio": "https://elevatestudio.my.canva.site/", # no robots.txt
    # "boost-studio": "https://www.boost-studio.com/"
}


def should_skip_frame(frame):
    skip_domains = ["stripe.com", "facebook.com", "google.com", "analytics", "wixapps"]
    return any(domain in frame.url for domain in skip_domains)


def scroll_until_iframes(page: Page, max_scrolls: int = 30, scroll_step: int = 1000, stable_checks: int = 3):
    """
    Hace scroll progresivo hasta que los iframes dejan de aumentar.
    Retorna el número final de iframes encontrados.
    """
    last_count = 0
    stable_counter = 0

    for i in range(max_scrolls):
        iframes_count = len(page.query_selector_all("iframe"))
        logging.info(f"🔎 Scroll {i+1}/{max_scrolls}: found {iframes_count} iframes")

        if iframes_count == last_count:
            stable_counter += 1
        else:
            stable_counter = 0
            last_count = iframes_count

        if stable_counter >= stable_checks and iframes_count > 0:
            logging.info(f"✅ Iframes stabilized at {iframes_count} after {i+1} scrolls")
            break

        page.mouse.wheel(0, scroll_step)
        page.wait_for_timeout(1000)
    return last_count


def flatten_nested_divs_regex(html: str) -> str:
    """
    Colapsa wrappers <div><div>...</div></div> hasta dejar solo <div>...</div>.
    Funciona de forma iterativa y es segura para fragments.
    """
    if not html:
        return html

    prev = None
    out = html

    # Paso 1: normalizar un poco espacios entre etiquetas
    out = re.sub(r'>\s+<', '><', out)

    # Iteramos hasta convergencia
    while prev != out:
        prev = out
        # 1) Colapsar aperturas: <div ...><div ...> -> <div>
        out = re.sub(r'<div\b[^>]*>\s*<div\b[^>]*>', '<div>', out, flags=re.IGNORECASE)

        # 2) Colapsar cierres: </div></div> -> </div>
        out = re.sub(r'</div>\s*</div>', '</div>', out, flags=re.IGNORECASE)

    # Opcional: limpiar repetidos de espacios y newlines
    out = re.sub(r'\s+', ' ', out).strip()

    return out


def prune_html_for_llm(html_content: str, keywords: list[str] = None) -> str:
    """
    Cleans HTML for LLM input using BeautifulSoup:
    - Keeps only the main container (based on <main>, role="main", or keyword relevance)
    - Removes scripts, styles, SVGs, etc.
    - Removes all attributes from tags
    - Collapses redundant <div><div>...</div></div>
    - Removes \n, \t, and redundant spaces
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # 1. Locate main content
    main_container = soup.find("main") or soup.find(attrs={"role": "main"})
    if not main_container and keywords:
        candidates = soup.find_all(["div", "section"])
        best_candidate, max_score = None, 0
        for node in candidates:
            text = node.get_text(" ", strip=True).lower()
            score = sum(text.count(kw.lower()) for kw in keywords)
            if score > max_score:
                best_candidate, max_score = node, score
        if best_candidate:
            main_container = best_candidate
    root = main_container if main_container else soup.body or soup

    # 2. Remove unwanted tags
    for tag in ["script", "style", "svg", "nav", "footer", "header", "noscript"]:
        for el in root.find_all(tag):
            el.decompose()

    # 3. Remove all attributes from remaining tags
    for tag in root.find_all(True):
        tag.attrs = {}

    # 4. Collapse redundant nested divs like <div><div>...</div></div>
    def collapse_redundant_divs(soup_node):
        changed = True
        while changed:
            changed = False
            for div in soup_node.find_all("div"):
                children = [child for child in div.children if child.name or str(child).strip()]
                if len(children) == 1 and children[0].name == "div":
                    div.replace_with(children[0])
                    changed = True
                    break

    collapse_redundant_divs(root)

    # 5. Get cleaned HTML as string
    html_clean = str(root)

    # 6. Remove tabs, newlines, and normalize whitespace
    html_clean = re.sub(r"[\n\r\t]+", " ", html_clean)
    html_clean = re.sub(r"\s{2,}", " ", html_clean).strip()

    return html_clean


def scrape_single_url(client: openai.OpenAI, page: Page, url: dict[str, str], url_type: str, gym_name: str) -> dict[str, dict[str, list]]:
    """
    Raspa una URL y cualquier iframe relevante que contenga
    """
    url_str = url["loc"]
    lastmod = url["lastmod"]
    freq = url["changefreq"]
    logging.info(f" -> Scraping URL principal: {url_str}")

    chunks_data = {}

    try:
        page.goto(url_str, wait_until="domcontentloaded", timeout=180000)
        scroll_until_iframes(page)

        # 3. Procesar los iframes relevantes
        for frame in page.frames:
            # Heurística para decidir si un iframe es interesante
            if frame.url == 'about:blank': continue
            if should_skip_frame(frame):
                continue
            logging.info(f"Found relevant iframe. Scraping: {frame.url}")
            try:
                try:
                    page.goto(frame.url, wait_until="networkidle", timeout=45000)
                except Exception:
                    page.goto(frame.url, wait_until="domcontentloaded", timeout=45000)
                frame_html = page.content()
                pruned_frame_html = prune_html_for_llm(frame_html)

                if pruned_frame_html.strip():
                    logging.info(f"Extracting from iframe content...")
                    iframe_data = extract_structured_data(client, frame.url, "iframe_content", pruned_frame_html,
                                                          gym_name, lastmod, freq)
                    # Fusionar datos del iframe
                    if iframe_data:
                        chunks_data[frame.url] = iframe_data
            except Exception as e:
                logging.error(f"❌ Failed to scrape iframe {frame.url}: {e}")

        # Convertir los diccionarios acumulados de nuevo a listas
        return chunks_data

    except Exception as e:
        logging.error(f"❌ Failed to scrape main URL {url}: {e}")
        return {}


def main():
    if not os.getenv("OPENAI_API_KEY"):
        load_dotenv("../.env")  # local dev
    # with get_connection() as conn:
    #     init_db(conn)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    folder_id = os.getenv("FOLDER_ID")
    custom_urls_env = os.getenv("SCRAPE_URLS")
    # if custom_urls_env:
    #     try:
    #         # Ejemplo: "bioritmo:https://bioritmo.com/,zendayoga:https://zendayoga.com/"
    #         pairs = [pair.strip() for pair in custom_urls_env.split(",") if pair.strip()]
    #         pages_to_scrape_used = {}
    #         for pair in pairs:
    #             if ":" not in pair:
    #                 logging.warning(f"⚠️ Invalid entry (missing colon): {pair}")
    #                 continue
    #             name, url = pair.split(":", 1)
    #             pages_to_scrape_used[name.strip()] = url.strip()
    #         logging.info(f"⚙️ Using URLs from environment: {pages_to_scrape}")
    #     except Exception as e:
    #         logging.warning(f"⚠️ Failed to parse SCRAPE_URLS: {e}")
    #         pages_to_scrape_used = pages_to_scrape
    # else:
    pages_to_scrape_used = pages_to_scrape
    client = openai.Client()
    df_disciplines, df_places, df_schedules, df_prices = init_dataframes()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for gym_name, site_url in pages_to_scrape_used.items():
            logging.info(f"Scraping {site_url}")
            urls_to_scrape = get_filtered_sitemap_urls(site_url)
            if not urls_to_scrape:
                urls_to_scrape = get_all_links_from_homepage(site_url, browser)
            schedules = []
            logging.info(f"URLs obtained: {urls_to_scrape}")
            filtered_urls = categorize_urls_with_llm(urls_to_scrape, client)
            filtered_urls["homepage"] = [{"loc": site_url, "lastmod": None, "changefreq": None, "priority": None}]
            logging.info(f"Categorized URLs: {filtered_urls}")
            chunked_data = {}
            for page_type, sub_urls in filtered_urls.items():
                page = browser.new_page()
                try:
                    for sub_url in sub_urls:
                        extracted_data = scrape_single_url(client, page, sub_url, page_type, gym_name)
                        for url, chunk_data in extracted_data.items():
                            if chunk_data.get("horarios"):
                                schedules.extend(chunk_data.pop("horarios"))  # separar datos de horarios para no hacer merge de estos
                        chunked_data = chunked_data | extracted_data
                except Exception as e:
                    logging.error(e)
                finally:
                    page.close()
            merged_gym_data = merge_gym_data_with_llm(gym_name, chunked_data, client)
            merged_gym_data["horarios"] = schedules  # recuperar data de horarios
            logging.info(f"Merged data: {merged_gym_data}")
            df_disciplines, df_places, df_schedules, df_prices = append_scraped_data(
                df_disciplines, df_places, df_schedules, df_prices, gym_name, merged_gym_data
            )
            # conn = get_connection()
            # bulk_insert(conn, gym_name, merged_gym_data)
        browser.close()
        logging.info("Uploading data to Drive...")
        res = export_and_upload(df_disciplines, df_places, df_schedules, df_prices, folder_id)
        logging.info("Uploaded: ", res)
    logging.info("Scraping complete.")


if __name__ == "__main__":
    main()
