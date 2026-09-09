import json
from database import supabase

NOTIF_FILE = "notifications.json"

with open(NOTIF_FILE, "r", encoding="utf-8") as f:
    notif_data = json.load(f)

# Format lama biasanya:
# {
#     "unread": ...,
#     "items": [...]
# }

if isinstance(notif_data, dict):
    items = notif_data.get("items", [])
else:
    items = notif_data

print("Jumlah notifikasi yang akan dimigrasikan:", len(items))

for item in items:
    data = {
        "text": item.get("text"),
        "device": item.get("device"),
        "serial": item.get("serial"),
        "location": item.get("location"),
        "urgent": bool(item.get("urgent", False)),
        "is_read": bool(item.get("is_read", False))
    }

    try:
        supabase.table("notifikasi").insert(data).execute()

    except Exception as e:
        print("GAGAL:", item, e)

print("MIGRASI NOTIFIKASI SELESAI")