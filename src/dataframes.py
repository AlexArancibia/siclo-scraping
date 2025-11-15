from datetime import datetime
from io import BytesIO

import pandas as pd

from src.drive_uploader import upload_file


def init_dataframes():
    """Initialize empty dataframes for all sheets."""
    df_disciplines = pd.DataFrame(columns=["nombre_gym", "nombre", "descripcion"])
    df_schedules = pd.DataFrame(columns=["nombre_gym", "sede", "nombre_clase", "instructor", "fecha", "dia_semana", "hora_inicio", "hora_fin"])
    df_prices = pd.DataFrame(columns=["nombre_gym", "sede", "descripcion_plan", "valor", "moneda", "recurrencia"])
    return df_disciplines, df_schedules, df_prices


def append_scraped_data(df_disciplines, df_schedules, df_prices, gym_name, scraped):
    """
    Append scraped dict data for one gym into the master dataframes.
    scraped = { "horarios": [...], "precios": [...], "disciplinas": [...] }
    """

    # Disciplines
    if "disciplinas" in scraped:
        temp = pd.DataFrame(scraped["disciplinas"])
        temp["nombre_gym"] = gym_name
        df_disciplines = pd.concat([df_disciplines, temp], ignore_index=True)

    # Schedules (horarios)
    if "horarios" in scraped:
        temp = pd.DataFrame(scraped["horarios"])
        temp["nombre_gym"] = gym_name
        df_schedules = pd.concat([df_schedules, temp], ignore_index=True)

    # Prices (precios)
    if "precios" in scraped:
        temp = pd.DataFrame(scraped["precios"])
        temp["nombre_gym"] = gym_name
        df_prices = pd.concat([df_prices, temp], ignore_index=True)

    return df_disciplines, df_schedules, df_prices


def create_excel_in_memory(df_disciplines, df_schedules, df_prices):
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_disciplines.to_excel(writer, sheet_name="Disciplines", index=False)
        df_schedules.to_excel(writer, sheet_name="Schedules", index=False)
        df_prices.to_excel(writer, sheet_name="Prices", index=False)

    output.seek(0)  # rewind file pointer
    return output.getvalue()


def export_and_upload(df_disciplines, df_schedules, df_prices, folder_id):
    # 1. Filename with today's date
    filename = f"gyms-data-{datetime.now().strftime('%Y-%m-%d')}.xlsx"

    # 2. Create Excel in memory
    excel_bytes = create_excel_in_memory(df_disciplines, df_schedules, df_prices)

    # 3. Upload
    mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    response = upload_file(excel_bytes, filename, mimetype, folder_id)

    return response