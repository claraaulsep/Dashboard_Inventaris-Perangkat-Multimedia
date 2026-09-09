from database import supabase, SUPABASE_URL

print("URL YANG DIPAKAI PYTHON:")
print(SUPABASE_URL)

response = (
    supabase
    .table("bot_settings")
    .select("*")
    .execute()
)

print("DATA:")
print(response.data)