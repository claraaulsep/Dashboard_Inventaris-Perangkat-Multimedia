import pandas as pd
from database import supabase

CSV_FILE = "data.csv"

df = pd.read_csv(CSV_FILE, sep=";")

print("Jumlah data inventaris:", len(df))

for _, row in df.iterrows():

    serial = str(row.get("Serial Number", "")).strip()

    # Supabase UNIQUE tidak cocok untuk serial placeholder "-"
    if serial in ["", "-", "nan", "None"]:
        serial = None

    data = {
        "aset": row.get("Aset"),
        "period": row.get("Period"),
        "device_model": row.get("Device Model"),
        "category": row.get("Category"),
        "serial_number": serial,
        "status": row.get("Status"),
        "location": row.get("Location"),
        "documentation": row.get("Documentation")
    }

    try:
        if serial:
            (
                supabase
                .table("inventaris")
                .upsert(
                    data,
                    on_conflict="serial_number"
                )
                .execute()
            )
        else:
            (
                supabase
                .table("inventaris")
                .insert(data)
                .execute()
            )

    except Exception as e:
        print(
            "GAGAL:",
            serial,
            row.get("Device Model"),
            e
        )

print("MIGRASI INVENTARIS SELESAI")