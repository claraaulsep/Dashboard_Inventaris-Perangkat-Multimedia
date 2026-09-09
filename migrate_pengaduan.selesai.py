import json
from datetime import datetime

from database import supabase


# ==========================================
# BACA PENGADUAN.JSON
# ==========================================

with open(
    "pengaduan.json",
    "r",
    encoding="utf-8"
) as f:
    complaints = json.load(f)


print(
    f"Jumlah data yang akan dimigrasikan: "
    f"{len(complaints)}"
)


# ==========================================
# NORMALISASI DATA
# ==========================================

rows = []

for complaint in complaints:

    # Ubah Open menjadi Diterima
    old_status = str(
        complaint.get("status", "Open")
    ).strip()

    if old_status.lower() == "open":
        status = "Open"
    else:
        status = old_status

    row = {
        "id": complaint.get("id"),

        "title": complaint.get(
            "title",
            "Pengaduan perangkat"
        ),

        "location": complaint.get(
            "location"
        ),

        "urgent": bool(
            complaint.get("urgent", False)
        ),

        "status": status,

        "asset": complaint.get(
            "asset"
        ),

        "category": complaint.get(
            "category"
        ),

        "serial": complaint.get(
            "serial"
        ),

        "device_name": complaint.get(
            "device_name"
        ),

        "source": complaint.get(
            "source",
            "web"
        ),

        "phone": complaint.get(
            "phone"
        ),

        "original_message": complaint.get(
            "original_message"
        ),

        "created_at": complaint.get(
            "created_at"
        ) or datetime.now().isoformat()
    }

    rows.append(row)


# ==========================================
# KIRIM KE SUPABASE
# ==========================================

try:

    response = (
        supabase
        .table("pengaduan")
        .upsert(
            rows,
            on_conflict="id"
        )
        .execute()
    )

    print("MIGRASI BERHASIL")
    print(
        f"Jumlah data dikirim: {len(rows)}"
    )

except Exception as e:

    print("MIGRASI GAGAL")
    print(e)