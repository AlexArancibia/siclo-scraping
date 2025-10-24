import html
import re
import time
from urllib.parse import urljoin

import openai
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Page
from pydantic.v1.schema import field_class_to_schema
from selectolax.parser import HTMLParser

from sitemap_utils import get_filtered_sitemap_urls
from src.llm import categorize_urls_with_llm, extract_structured_data

pages_to_scrape = {
    # "bioritmo": "https://www.bioritmo.com.pe/",
    # "limayoga": "https://limayoga.com/",
    "nasceyoga": "https://www.nasceyoga.com/",
    "zendayoga": "https://www.zendayoga.com/",
    "matmax": "https://matmax.world/",
    "anjali": "https://anjali.pe/",
    "purepilatesperu": "https://www.purepilatesperu.com/",
    "balancestudio": "https://balancestudio.pe/",
    "curvaestudio": "https://curvaestudio.com/", # no robots.txt
    "fitstudioperu": "https://fitstudioperu.com/",
    "funcionalstudio": "https://www.funcionalstudio.pe/",
    "pilatesesencia": "https://pilatesesencia.com/",
    "twopilatesstudio": "https://twopilatesstudio.wixsite.com/twopilatesstudio", # no sitemap in robots.txt
    "iliveko": "https://iliveko.com/", # no sitemap in robots.txt
    "raise": "https://raise.pe/",
    "shadow": "https://shadow.pe/", #  no sitemap in robots.txt
    "elevatestudio": "https://elevatestudio.my.canva.site/", # no robots.txt
    "boost-studio": "https://www.boost-studio.com/"
}


def should_skip_frame(frame):
    skip_domains = ["stripe.com", "facebook.com", "google.com", "analytics"]
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
        print(f"     🔎 Scroll {i+1}/{max_scrolls}: found {iframes_count} iframes")

        if iframes_count == last_count:
            stable_counter += 1
        else:
            stable_counter = 0
            last_count = iframes_count

        if stable_counter >= stable_checks and iframes_count > 0:
            print(f"     ✅ Iframes stabilized at {iframes_count} after {i+1} scrolls")
            break

        page.mouse.wheel(0, scroll_step)
        page.wait_for_timeout(1000)
    return last_count

def prune_html_for_llm(html_content: str, keywords: list[str] = None) -> str:
    """
    Prunes HTML content to only the most relevant sections for an LLM.
    This significantly reduces token count and improves accuracy.
    """
    tree = HTMLParser(html_content)

    # Step 1: Isolate the main content container
    main_container = None

    # Try finding the <main> tag first
    if tree.body:
        main_container = tree.body.css_first('main')

    # If no <main>, try finding a div/section with role="main"
    if not main_container and tree.body:
        main_container = tree.body.css_first('[role="main"]')

    # Fallback: if keywords are provided, find the best container holding them
    if not main_container and keywords and tree.body:
        best_candidate = None
        max_score = 0
        for node in tree.body.css('div, section'):
            score = sum(node.text(deep=True).lower().count(kw) for kw in keywords)
            if score > max_score:
                max_score = score
                best_candidate = node
        if max_score > 0:
            main_container = best_candidate

    # If we found a specific container, use it. Otherwise, use the whole body.
    root_node = main_container if main_container else tree.body

    if not root_node:
        return ""  # Return empty string if no body or content found

    # Step 2: Remove noise tags from the chosen container
    noise_tags = ['script', 'style', 'svg', 'nav', 'footer', 'header']
    for tag in noise_tags:
        for node in root_node.css(tag):
            node.decompose()  # decompose() removes the node and its children
    # 2. Obtener HTML limpio.
    html_sin_ruido = root_node.html

    # 4. Reemplazar múltiples saltos de línea por uno solo.
    html_sin_lineas = re.sub(r'\n+', '\n', html_sin_ruido)

    # 5. Reemplazar múltiples espacios (incluyendo tabs y line breaks) por uno solo.
    html_comprimido = re.sub(r'\s+', ' ', html_sin_lineas)

    return html_comprimido.strip()



def _get_item_key(item: dict, category: str) -> tuple | None:
    # ... (código de la respuesta anterior)
    if category == "ubicaciones":
        key_parts = (item.get("distrito"), item.get("direccion_completa"))
        return key_parts if all(key_parts) else None
    elif category == "precios":
        key_parts = (item.get("descripcion_plan"), item.get("valor"), item.get("recurrencia"))
        return key_parts if all(key_parts) else None
    elif category == "horarios":
        key_parts = (item.get("sede"), item.get("nombre_clase"), item.get("dia_semana"), item.get("hora_inicio"))
        return key_parts if all(key_parts) else None
    elif category == "disciplinas":
        key_parts = (item.get("nombre"),)
        return key_parts if all(key_parts) else None
    return None

def _merge_items(existing_item: dict, new_item: dict) -> dict:
    # ... (código de la respuesta anterior)
    score_existing = sum(1 for v in existing_item.values() if v)
    score_new = sum(1 for v in new_item.values() if v)
    if score_new > score_existing:
        return new_item
    elif score_new == score_existing:
        if len(new_item.get("content_para_busqueda", "")) > len(existing_item.get("content_para_busqueda", "")):
            return new_item
    return existing_item


def scrape_single_url(client: openai.OpenAI, page: Page, url: str, url_type: str, gym_name: str):
    """
    Raspa una URL y cualquier iframe relevante que contenga, fusionando los resultados.
    """
    print(f"  -> Scraping URL principal: {url}")

    # Acumulador para todos los datos encontrados en esta URL y sus iframes.
    datos_acumulados = {
        "ubicaciones": {},
        "precios": {},
        "horarios": {},
        "disciplinas": {}
    }

    chunks_data = {}

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=180000)
        scroll_until_iframes(page)

        # 3. Procesar los iframes relevantes
        for frame in page.frames:
            # Heurística para decidir si un iframe es interesante
            if should_skip_frame(frame):
                continue
            print(f"     Found relevant iframe. Scraping: {frame.url}")
            try:
                page.goto(frame.url, wait_until="networkidle", timeout=45000)
                frame_html = page.content()
                pruned_frame_html = prune_html_for_llm(frame_html)

                if pruned_frame_html.strip():
                    print(f"     Extracting from iframe content...")
                    iframe_data = extract_structured_data(client, frame.url, "iframe_content", pruned_frame_html,
                                                          gym_name)
                    # Fusionar datos del iframe
                    if iframe_data:
                        chunks_data[frame.url] = iframe_data
                        for category, items in iframe_data.items():
                            for item in items:
                                item_key = _get_item_key(item, category)
                                if item_key:
                                    if item_key not in datos_acumulados[category]:
                                        datos_acumulados[category][item_key] = item
                                    else:
                                        existing = datos_acumulados[category][item_key]
                                        datos_acumulados[category][item_key] = _merge_items(existing, item)
            except Exception as e:
                print(f"     ❌ Failed to scrape iframe {frame.url}: {e}")

        # Convertir los diccionarios acumulados de nuevo a listas
        return {category: list(items_dict.values()) for category, items_dict in datos_acumulados.items()}

    except Exception as e:
        print(f"     ❌ Failed to scrape main URL {url}: {e}")
        return {"ubicaciones": [], "precios": [], "horarios": [], "disciplinas": []}


def main():
    load_dotenv()
    client = openai.Client()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for gym_name, site_url in pages_to_scrape.items():
            print(f"Scraping {site_url}")
            urls_to_scrape = get_filtered_sitemap_urls(site_url)
            print(urls_to_scrape)
            filtered_urls = categorize_urls_with_llm(urls_to_scrape, client)
            filtered_urls["homepage"] = [site_url]
            print(filtered_urls)
            datos_site = {
                "ubicaciones": {},
                "precios": {},
                "horarios": {},
                "disciplinas": {}
            }
            for page_type, sub_urls in filtered_urls.items():
                if page_type != "homepage":
                    continue
                page = browser.new_page()
                try:
                    for sub_url in sub_urls:
                        extracted_data = scrape_single_url(client, page, sub_url, page_type, gym_name)
                        for category, items in extracted_data.items():
                            for item in items:
                                key = _get_item_key(item, category)
                                if not key:
                                    continue
                                if key not in datos_site[category]:
                                    datos_site[category][key] = item
                                else:
                                    datos_site[category][key] = _merge_items(datos_site[category][key], item)
                except Exception as e:
                    print(e)
                finally:
                    page.close()
            print(datos_site)
        browser.close()
    print("Scraping complete.")


if __name__ == "__main__":
    main()
