"""
whatsapp_bot.py
----------------
Helper untuk fitur pengaduan melalui WhatsApp.

Pembagian tanggung jawab:
- File ini: pencarian/matching perangkat, deteksi urgent, pending state, kirim WA.
- app.py: alur percakapan, konfirmasi Y, pengecekan tiket Open/Repair,
  pembuatan tiket, resolve, dan perubahan status inventaris.
"""

import os
import re
from typing import Optional

import requests
from dotenv import load_dotenv

from database import supabase

load_dotenv()


# ==========================================
# WHATSAPP PENDING STATE - SUPABASE
# ==========================================

def save_pending_state(sender, state, data):
    try:
        payload = {
            "sender": str(sender),
            "state": state,
            "data": data,
        }

        (
            supabase
            .table("whatsapp_pending")
            .upsert(payload, on_conflict="sender")
            .execute()
        )
        return True

    except Exception as e:
        print("Gagal menyimpan pending WhatsApp:", e, flush=True)
        return False


def get_pending_state(sender):
    try:
        response = (
            supabase
            .table("whatsapp_pending")
            .select("*")
            .eq("sender", str(sender))
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]
        return None

    except Exception as e:
        print("Gagal mengambil pending WhatsApp:", e, flush=True)
        return None


def delete_pending_state(sender):
    try:
        (
            supabase
            .table("whatsapp_pending")
            .delete()
            .eq("sender", str(sender))
            .execute()
        )
        return True

    except Exception as e:
        print("Gagal menghapus pending WhatsApp:", e, flush=True)
        return False


# ==========================================
# FONNTE
# ==========================================

FONNTE_TOKEN = os.environ.get("FONNTE_TOKEN", "")
print("DEBUG TOKEN TERBACA:", bool(FONNTE_TOKEN))

FONNTE_SEND_URL = "https://api.fonnte.com/send"


def send_whatsapp_reply(phone: str, message: str) -> bool:
    """Kirim balasan WhatsApp melalui Fonnte."""
    if not FONNTE_TOKEN:
        print(
            "[whatsapp_bot] FONNTE_TOKEN kosong -- balasan TIDAK dikirim "
            "(mode dev). Isi environment variable FONNTE_TOKEN untuk "
            "aktifkan pengiriman.",
            flush=True,
        )
        return False

    try:
        response = requests.post(
            FONNTE_SEND_URL,
            headers={"Authorization": FONNTE_TOKEN},
            data={"target": phone, "message": message},
            timeout=10,
        )

        if not response.ok:
            print(
                f"[whatsapp_bot] Fonnte membalas error "
                f"{response.status_code}: {response.text}",
                flush=True,
            )

        return response.ok

    except requests.RequestException as e:
        print(f"[whatsapp_bot] Gagal menghubungi Fonnte: {e}", flush=True)
        return False


# ==========================================
# KEYWORD
# ==========================================

CATEGORY_KEYWORDS = {
    "Microphone": [
        "mic", "mik", "mikrofon", "microphone"
    ],
    "Display": [
        "layar", "proyektor", "projector", "display", "monitor", "tv"
    ],
    "Audio - Speaker/Amplifier": [
        "speaker", "amplifier", "ampli", "suara", "sound system"
    ],
    "Video Extender": [
        "extender", "hdmi", "kabel video", "vga"
    ],
    "Presentation Device": [
        "clicker", "remote presentasi", "presentation device", "laser pointer"
    ],
    "Audio Processor": [
        "audio processor", "mixer", "digital mixer"
    ],
    "Networking": [
        "jaringan", "wifi", "wi-fi", "network", "internet", "lan"
    ],
    "Camera": [
        "kamera", "camera", "cctv", "webcam"
    ],
    "Accessory - Charging/Battery": [
        "baterai", "battery", "charger", "casan", "power bank"
    ],
}

URGENT_KEYWORDS = [
    "urgent", "penting", "segera", "darurat", "asap",
    "sekarang", "besok", "hari ini", "mendesak",
]

# Kata umum yang tidak cukup spesifik untuk membantu pencarian model.
MODEL_STOPWORDS = {
    "with", "inch", "device", "system", "room", "meeting",
    "unit", "the", "and", "for", "dan", "yang", "untuk",
}


# ==========================================
# NORMALISASI TEKS
# ==========================================

def normalize_text(value) -> str:
    text = str(value or "").lower().strip()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_phrase(text: str, phrase: str) -> bool:
    """Cek kata/frasa sebagai token, bukan substring sembarang."""
    text_norm = normalize_text(text)
    phrase_norm = normalize_text(phrase)

    if not text_norm or not phrase_norm:
        return False

    return f" {phrase_norm} " in f" {text_norm} "


def classify_category(text: str) -> str:
    """Deteksi satu kategori perangkat dari keyword pesan."""
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(contains_phrase(text, keyword) for keyword in keywords):
            return category
    return "Lainnya"


def detect_urgent(text: str) -> bool:
    return any(contains_phrase(text, keyword) for keyword in URGENT_KEYWORDS)


def match_location(text: str, known_locations: list) -> Optional[str]:
    """Cari lokasi paling spesifik yang disebut di pesan."""
    matches = []

    for location in known_locations:
        location_norm = normalize_text(location)
        location_key = normalize_text(
            str(location).lower().replace("meeting room", "")
        )

        if (
            (location_norm and contains_phrase(text, location_norm))
            or (location_key and contains_phrase(text, location_key))
        ):
            matches.append(str(location))

    if not matches:
        return None

    matches.sort(key=lambda item: len(normalize_text(item)), reverse=True)
    return matches[0]


def match_device(device_list: list, location: Optional[str], category: str):
    """Compatibility helper untuk parse_complaint_message()."""
    if not location or not category or category == "Lainnya":
        return None

    candidates = [
        device
        for device in device_list
        if normalize_text(device.get("Location")) == normalize_text(location)
        and normalize_text(device.get("Category")) == normalize_text(category)
    ]

    if len(candidates) == 1:
        return candidates[0]
    return None


# ==========================================
# PENCARIAN PERANGKAT
# ==========================================

def _model_matches(text: str, device_model: str) -> bool:
    """
    Device Model dihitung sebagai satu fitur.
    Model dianggap cocok jika nama lengkap disebut, atau minimal 2 kata
    bermakna dari model disebut oleh pengguna.
    """
    model_norm = normalize_text(device_model)

    if not model_norm or model_norm in {"-", "none", "nan"}:
        return False

    if contains_phrase(text, model_norm):
        return True

    model_words = [
        word
        for word in model_norm.split()
        if len(word) >= 4 and word not in MODEL_STOPWORDS
    ]

    matched_words = [
        word
        for word in model_words
        if contains_phrase(text, word)
    ]

    return len(set(matched_words)) >= 2


def find_device_candidates(text: str, device_list: list, known_locations: list) -> dict:
    """
    Aturan final pencarian perangkat:

    1. Serial Number lengkap/exact memiliki prioritas mutlak.
       Jika ditemukan, langsung kembalikan satu perangkat itu saja.

    2. Jika tidak ada SN exact, pencarian memakai empat fitur:
       - Category
       - Device Model
       - Aset
       - Location

    3. Hanya perangkat dengan minimal 2 fitur cocok yang boleh menjadi kandidat.
       Satu fitur saja dianggap belum cukup spesifik.

    4. Semakin banyak fitur cocok, semakin tinggi prioritas.
       Hanya kandidat dengan score tertinggi yang dikembalikan.
       - 1 kandidat terbaik -> app.py langsung meminta konfirmasi Y.
       - >1 kandidat terbaik -> app.py menampilkan pilihan nomor.
       - 0 kandidat -> app.py meminta pengguna mengetik ulang.

    Catatan: pengecekan status Repair/tiket Open TIDAK dilakukan di sini.
    Itu tanggung jawab app.py setelah pengguna mengonfirmasi perangkat.
    """

    text_norm = normalize_text(text)

    # ==========================================
    # 1. SERIAL NUMBER EXACT -> PRIORITAS MUTLAK
    # ==========================================
    for device in device_list:
        serial_raw = str(device.get("Serial Number", "")).strip()
        serial_norm = normalize_text(serial_raw)

        if (
            serial_norm
            and serial_norm not in {"-", "none", "nan"}
            and contains_phrase(text_norm, serial_norm)
        ):
            device_copy = device.copy()
            device_copy["_match_score"] = 100
            device_copy["_matched_fields"] = ["Serial Number"]

            return {
                "devices": [device_copy],
                "match_type": "serial_exact",
                "best_score": 100,
            }

    # ==========================================
    # 2. TANPA SN -> SCORING FITUR
    # ==========================================
    detected_category = classify_category(text)
    detected_location = match_location(text, known_locations)

    matched_devices = []

    for device in device_list:
        score = 0
        matched_fields = []

        category = str(device.get("Category", "")).strip()
        device_model = str(device.get("Device Model", "")).strip()
        asset = str(device.get("Aset", "")).strip()
        location = str(device.get("Location", "")).strip()

        # CATEGORY = 1 fitur
        if (
            detected_category != "Lainnya"
            and normalize_text(category) == normalize_text(detected_category)
        ):
            score += 1
            matched_fields.append("Category")

        # DEVICE MODEL = 1 fitur
        if _model_matches(text, device_model):
            score += 1
            matched_fields.append("Device Model")

        # ASET = 1 fitur
        asset_norm = normalize_text(asset)
        if (
            asset_norm
            and asset_norm not in {"-", "none", "nan"}
            and contains_phrase(text, asset_norm)
        ):
            score += 1
            matched_fields.append("Aset")

        # LOCATION = 1 fitur
        if (
            detected_location
            and normalize_text(location) == normalize_text(detected_location)
        ):
            score += 1
            matched_fields.append("Location")

        # Minimal 2 fitur harus cocok.
        if score >= 2:
            device_copy = device.copy()
            device_copy["_match_score"] = score
            device_copy["_matched_fields"] = matched_fields
            matched_devices.append(device_copy)

    # Tidak cukup informasi / tidak ditemukan.
    if not matched_devices:
        return {
            "devices": [],
            "match_type": "not_found",
            "best_score": 0,
        }

    # ==========================================
    # 3. AMBIL HANYA SCORE TERBAIK
    # ==========================================
    matched_devices.sort(
        key=lambda device: device.get("_match_score", 0),
        reverse=True,
    )

    best_score = matched_devices[0].get("_match_score", 0)

    best_devices = [
        device
        for device in matched_devices
        if device.get("_match_score", 0) == best_score
    ]

    return {
        "devices": best_devices,
        "match_type": "feature_match",
        "best_score": best_score,
    }


# ==========================================
# PARSER COMPATIBILITY
# ==========================================

def parse_complaint_message(text: str, device_list: list, known_locations: list) -> dict:
    """
    Dipertahankan agar import lama di app.py tetap kompatibel.
    Matching utama perangkat WhatsApp memakai find_device_candidates().
    """
    category = classify_category(text)
    location = match_location(text, known_locations)
    urgent = detect_urgent(text)
    device = match_device(device_list, location, category)

    return {
        "title": text.strip()[:150] or "Pengaduan via WhatsApp",
        "location": location or "Belum diketahui (mohon dilengkapi PIC)",
        "urgent": urgent,
        "asset": device.get("Aset") if device else "-",
        "category": category,
        "serial": device.get("Serial Number") if device else None,
        "device_name": device.get("Device Model") if device else None,
        "source": "whatsapp",
    }


def build_confirmation_message(ticket_id: int, parsed: dict) -> str:
    """Dipertahankan untuk kompatibilitas dengan import app.py."""
    urgent_tag = " (🔴 URGENT)" if parsed.get("urgent") else ""

    lines = [
        f"✅ Tiket #{ticket_id} berhasil dibuat{urgent_tag}",
        f"Kategori terdeteksi: {parsed.get('category', '-')}",
        f"Lokasi: {parsed.get('location', '-')}",
    ]

    if parsed.get("device_name"):
        lines.append(
            f"Perangkat: {parsed.get('device_name')} "
            f"({parsed.get('serial', '-')})"
        )

    lines.append("\nTim kami akan segera memproses laporan Anda. Terima kasih 🙏")
    return "\n".join(lines)
