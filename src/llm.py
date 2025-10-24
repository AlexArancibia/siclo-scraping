import openai
import json
from typing import Dict, List, Dict, Any


def _sanitize_and_generate_content(facts: List[Dict], category: str) -> List[Dict]:
    """
    Una función interna para sanitizar los hechos y generar el campo de contenido si falta.
    Esta es nuestra red de seguridad contra las inconsistencias del LLM.
    """
    sanitized_facts = []
    for fact in facts:
        if not isinstance(fact, dict):
            continue  # Ignorar elementos que no son diccionarios

        # Si 'content_para_busqueda' falta o está vacío, lo generamos
        if not fact.get("content_para_busqueda"):
            print(f"     🛠️ Generando 'content_para_busqueda' faltante para un hecho de '{category}'.")
            summary_parts = []
            if category == "ubicaciones":
                summary_parts.append(f"La sede se encuentra en {fact.get('direccion_completa', 'dirección no especificada')}")
                if fact.get('distrito'):
                    summary_parts.append(f"en el distrito de {fact.get('distrito')}.")
            elif category == "precios":
                summary_parts.append(f"Se ofrece un plan '{fact.get('descripcion_plan', 'no especificado')}'")
                if fact.get('valor') is not None:
                    summary_parts.append(f"por {fact.get('valor')} {fact.get('moneda', '')}.")
            elif category == "horarios":
                 summary_parts.append(f"La clase '{fact.get('nombre_clase', 'no especificada')}' es impartida por {fact.get('instructor', 'instructor no especificado')}")
                 if fact.get('dia_semana'):
                    summary_parts.append(f"el día {fact.get('dia_semana')} de {fact.get('hora_inicio', '')} a {fact.get('hora_fin', '')}.")
            else: # Fallback genérico
                summary_parts.append(f"Dato de tipo '{category}': " + ", ".join([f"{k}: {v}" for k, v in fact.items() if k != 'content_para_busqueda' and v]))

            fact["content_para_busqueda"] = " ".join(summary_parts).strip()

        sanitized_facts.append(fact)
    return sanitized_facts


def extract_structured_data(
        client: openai.OpenAI,
        page_url: str,
        url_type: str,
        html_content: str,
        gym_name: str
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Uses an OpenAI model to parse HTML and extract a list of structured "fact documents".
    """
    prompt_template = """
``` text
Eres un agente de extracción de datos de clase mundial para la industria del fitness, especializado en convertir contenido web en registros estructurados para una base de datos PostgreSQL que utiliza pgvector.

**Tu Objetivo:**
Analizar el contenido HTML de la página de un gimnasio y extraer rigurosamente toda la información sobre **ubicaciones, precios, horarios y disciplinas**.

**Tus Instrucciones Clave:**
1.  **Idioma de Salida:** Todo el texto extraído DEBE estar en **español**.
2.  **Estructura de Salida:** Debes devolver un único objeto JSON con claves de nivel superior que se mapean a tablas: `"ubicaciones"`, `"precios"`, `"horarios"`, `"disciplinas"`.
3.  **Requisito Híbrido (¡MUY IMPORTANTE!):** Para CADA objeto individual que extraigas, DEBES generar dos cosas:
    a) Un campo `"content_para_busqueda"`: Una única oración concisa y en lenguaje natural que resuma la información del objeto. Este campo es esencial para la búsqueda por vectores.
    b) Los campos de datos estructurados (`snake_case`) que se mapearán a las columnas de la base de datos.
4.  **Búsqueda Oportunista:** La `url_type` es una pista, pero DEBES escanear todo el HTML en busca de CUALQUIER tipo de dato relevante en cada página.
5.  **Caso Vacío:** Si no encuentras información para una categoría, devuelve una lista vacía `[]` para esa clave.

**Definición de Esquemas (Schemas):**
*   **Para `"ubicaciones"`:** `{{"content_para_busqueda": str, "direccion_completa": str, "distrito": str}}`
*   **Para `"precios"`:** `{{"content_para_busqueda": str, "descripcion_plan": str, "valor": float, "moneda": str, "recurrencia": str}}`
*   **Para `"horarios"`:** `{{"content_para_busqueda": str, "sede": str, "nombre_clase": str, "instructor": str, "dia_semana": str, "hora_inicio": str, "hora_fin": str}}`
*   **Para `"disciplinas"`:** `{{"content_para_busqueda": str, "nombre": str, "descripcion_corta": str}}`

---
**Ejemplo 1: Contenido Mixto en una URL de 'ubicaciones'**

**page_url:** "https://gym.com/sedes/miraflores"
**url_type:** "locations"
**html_content:** '''
  <h2>Nuestra Sede en Miraflores</h2>
  <p>Encuéntranos en Av. Larco 123, Miraflores, Lima.</p>
  <h3>¡Oferta de Apertura!</h3>
  <p>Plan Anual Exclusivo: S/ 1500</p>
'''

**Tu Salida:**
```json
{{
  "ubicaciones": [
    {{
      "content_para_busqueda": "La sede de Miraflores se encuentra en Av. Larco 123, Miraflores, Lima.",
      "direccion_completa": "Av. Larco 123, Miraflores, Lima",
      "distrito": "Miraflores"
    }}
  ],
  "precios": [
    {{
      "content_para_busqueda": "Se ofrece un Plan Anual Exclusivo por S/ 1500 en esta sede.",
      "descripcion_plan": "Plan Anual Exclusivo",
      "valor": 1500.0,
      "moneda": "PEN",
      "recurrencia": "anual"
    }}
  ],
  "horarios": [],
  "disciplinas": []
}}
```
---
**Ejemplo 2: Sin datos relevantes**

**page_url:** "https://gym.com/blog/noticias"
**url_type:** "general"
**html_content:** '''
  <h1>Nuestro Blog</h1>
  <p>Lee las últimas noticias del mundo fitness.</p>
'''

**Tu Salida:**
```

json {{ "ubicaciones": [], "precios": [], "horarios": [], "disciplinas": [] }}``` 
---
**Fin de los Ejemplos. Ahora, completa la tarea real.**

**Tarea:** Analiza las siguientes entradas y genera el objeto JSON estructurado.

**page_url:** "{page_url}"
**url_type:** "{url_type}"
**html_content:** '''
{html_content}
'''

**Tu Salida:**
```

"""
    # Using .format() requires escaping the JSON braces with {{ and }}
    # But for the placeholder {html_content}, we use single braces.
    # The prompt is already formatted this way.

    full_prompt = prompt_template.format(
        gym_name=gym_name,
        page_url=page_url,
        url_type=url_type,
        html_content=html_content
    )

    try:
        print(f"     Calling OpenAI to extract data from {page_url}...")
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": full_prompt}],
            temperature=0.0,
            # IMPORTANT: Use JSON mode to guarantee valid JSON output
            response_format={"type": "json_object"}
        )
        response_content = completion.choices[0].message.content

        # The entire response is a JSON object, but the actual data is inside a list.
        # Sometimes the model might wrap the list in a key, e.g., {"data": [...]}.
        # We need to robustly extract the list.
        if not response_content:
            return {}

        parsed_json = json.loads(response_content)

        sanitized_output = {}
        for category in ["ubicaciones", "precios", "horarios", "disciplinas"]:
            if category in parsed_json and isinstance(parsed_json[category], list):
                # Pasa la lista de hechos a través de nuestra red de seguridad
                sanitized_facts = _sanitize_and_generate_content(parsed_json[category], category)
                sanitized_output[category] = sanitized_facts
            else:
                # Asegurarse de que la clave siempre exista, incluso si está vacía
                sanitized_output[category] = []

        print("     ✅ Sanitization complete.")
        return sanitized_output
        # --- FIN DE LA NUEVA LÓGICA ---

    except Exception as e:
        print(f"     ❌ An error occurred calling OpenAI: {e}")
        return {"ubicaciones": [], "precios": [], "horarios": [], "disciplinas": []}

    except Exception as e:
        print(f"     ❌ An error occurred calling OpenAI: {e}")
        return []


def categorize_urls_with_llm(urls: List[str], client: openai.OpenAI) -> Dict[str, List[str]]:
    """
    Uses an OpenAI LLM to categorize URLs based on their likely content.

    Args:
        urls: A list of URLs to categorize.
        client: An initialized OpenAI client instance.

    Returns:
        A dictionary categorizing the URLs.
    """

    # This is the prompt template from above
    prompt_template = """
You are an expert data architect and SEO analyst specializing in the fitness industry. Your task is to analyze a list of URLs from a gym's website sitemap and categorize them based on their likely content.

You will be given a JSON list of URLs. Your goal is to determine which URLs are most likely to contain information about:
1.  **locations**: Physical gym locations, addresses, maps, contact pages.
2.  **pricing**: Membership plans, prices, fees, sign-up offers.
3.  **schedules**: Class timetables, calendars, schedules for different locations.
4.  **disciplines**: Information about specific types of activities like Yoga, Pilates, Cycling, etc.

You MUST return a JSON object with four keys: "locations", "pricing", "schedules", and "disciplines". Each key should contain a list of the URLs that belong to that category. A URL can appear in multiple categories if it's relevant to more than one.

Analyze the URL path carefully. Prioritize Spanish keywords such as 'sedes', 'precios', 'horarios', but also consider English and Portuguese equivalents.

---
**Example 1: Standard URLs**
**Input URLs:**
["https://example.com/es/nuestros-gimnasios", "https://example.com/es/tarifas-2024", "https://example.com/blog/post-1"]

**Your Output:**
{{
  "locations": ["https://example.com/es/nuestros-gimnasios"],
  "pricing": ["https://example.com/es/tarifas-2024"],
  "schedules": [],
  "disciplines": []
}}
---
**Example 2: Complex and Overlapping URLs**
**Input URLs:**
["https://example.com/clases-y-horarios", "https://example.com/sedes/miraflores", "https://example.com/disciplinas/yoga-y-pilates", "https://example.com/es/contacto"]

**Your Output:**
{{
  "locations": ["https://example.com/sedes/miraflores", "https://example.com/es/contacto"],
  "pricing": [],
  "schedules": ["https://example.com/clases-y-horarios"],
  "disciplines": ["https://example.com/clases-y-horarios", "https://example.com/disciplinas/yoga-y-pilates"]
}}
---
**End of Examples. Now, complete the real task.**

**Task: Categorize the following URLs.**

**Input URLs:**
{urls_json}

**Your Output:**
"""

    # Format the list of URLs as a JSON string for the prompt
    urls_as_json_string = json.dumps(urls)

    # Inject the URLs into the prompt
    full_prompt = prompt_template.format(urls_json=urls_as_json_string)

    try:
        print("🤖 Calling OpenAI to categorize URLs...")
        completion = client.chat.completions.create(
            model="gpt-4o-mini",  # Use a fast, affordable model
            messages=[
                {"role": "user", "content": full_prompt}
            ],
            temperature=0.0,  # Set to 0 for deterministic, factual tasks
            response_format={"type": "json_object"}  # Enable JSON mode
        )

        response_content = completion.choices[0].message.content
        print("✅ OpenAI response received.")

        # Parse the JSON string from the response
        categorized_urls = json.loads(response_content)
        return categorized_urls

    except Exception as e:
        print(f"❌ An error occurred while calling OpenAI: {e}")
        return {"locations": [], "pricing": [], "schedules": [], "disciplines": []}
