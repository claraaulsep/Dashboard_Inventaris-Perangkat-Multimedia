#from migrate_pengaduan import response
#from migrate_pengaduan import complaint
from whatsapp_bot import save_pending_state
from whatsapp_bot import get_pending_state
from whatsapp_bot import delete_pending_state
from pydantic import functional_validators
from pydantic import functional_validators
from pandas.io.formats import style_render
from whatsapp_bot import detect_urgent
import os
import pandas as pd
from flask import Flask, jsonify, render_template, request, session, redirect, url_for
from datetime import datetime
from database import supabase
from whatsapp_bot import parse_complaint_message, send_whatsapp_reply, build_confirmation_message, find_device_candidates 

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY",
    "dev-secret-key"
)


def is_bot_enabled():
    try:
        response = (
            supabase
            .table("bot_settings")
            .select("enabled")
            .eq("id", 1)
            .single()
            .execute()
        )

        if response.data:
            enabled = response.data.get("enabled", False)

            print(
                "DEBUG BOT SETTINGS SUPABASE:",
                enabled,
                flush=True
            )

            return enabled

        return False

    except Exception as e:
        print(
            "Gagal membaca bot_settings dari Supabase:",
            e,
            flush=True
        )

        # Kalau database bermasalah, bot dibuat OFF
        return False

# --- Initialize Notifications File ---

# --- Users Data ---
USERS = {
    'admin': {'password': '123', 'role': 'admin', 'name': 'Admin'},
    'tamu': {'password': '123', 'role': 'tamu', 'name': 'Tamu / Pekerja'}
}

# --- Helpers ---
def load_data():
    try:
        response = (
            supabase
            .table("inventaris")
            .select("*")
            .order("id")
            .execute()
        )

        rows = response.data or []

        df = pd.DataFrame(rows)

        if df.empty:
            return pd.DataFrame(columns=[
                'Aset',
                'Period',
                'Device Model',
                'Category',
                'Serial Number',
                'Status',
                'Location',
                'Documentation'
            ])

        # Samakan nama kolom Supabase dengan format lama aplikasi
        df = df.rename(columns={
            'aset': 'Aset',
            'period': 'Period',
            'device_model': 'Device Model',
            'category': 'Category',
            'serial_number': 'Serial Number',
            'status': 'Status',
            'location': 'Location',
            'documentation': 'Documentation'
        })

        # Buang kolom id Supabase dari dataframe aplikasi
        if 'id' in df.columns:
            df = df.drop(columns=['id'])

        if 'Status' in df.columns:
            df['Status'] = (
                df['Status']
                .fillna('')
                .astype(str)
                .str.strip()
            )

        return df

    except Exception as e:
        print(f"Error loading data dari Supabase: {e}", flush=True)

        return pd.DataFrame(columns=[
            'Aset',
            'Period',
            'Device Model',
            'Category',
            'Serial Number',
            'Status',
            'Location',
            'Documentation'
        ])

def save_data(df):
    try:
        for _, row in df.iterrows():

            serial = str(
                row.get("Serial Number", "")
            ).strip()

            if serial in ["", "-", "nan", "None"]:
                continue

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

            (
                supabase
                .table("inventaris")
                .update(data)
                .eq("serial_number", serial)
                .execute()
            )

        return True

    except Exception as e:
        print(
            f"Error saving data ke Supabase: {e}",
            flush=True
        )
        return False


# --- Authentication Routes ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        username = data.get('username')
        password = data.get('password')
        
        if username in USERS and USERS[username]['password'] == password:
            session['user'] = username
            session['role'] = USERS[username]['role']
            session['name'] = USERS[username]['name']
            if request.is_json:
                return jsonify({"success": True})
            return redirect('/')
        else:
            if request.is_json:
                return jsonify({"success": False, "message": "Username atau password salah!"})
            return render_template('login.html', error="Username atau password salah!")
            
    return render_template('login.html')

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect('/login')

@app.route('/api/user')
def get_user():
    if 'user' not in session:
        return jsonify({"authenticated": False}), 401
    return jsonify({
        "authenticated": True,
        "username": session['user'],
        "role": session['role'],
        "name": session['name']
    })

# --- Application Routes ---
@app.route('/')
def index():
    if 'user' not in session:
        return redirect('/login')
    return render_template('index.html', role=session['role'], name=session['name'])

@app.route('/api/data')
def get_data():
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    df = load_data()
    if df.empty: return jsonify({"error": "No data found"}), 404

    filter_options = {
        'kategori': sorted(df['Category'].dropna().unique().tolist()) if 'Category' in df.columns else [],
        'lokasi': sorted(df['Location'].dropna().unique().tolist()) if 'Location' in df.columns else [],
        'status': sorted(df['Status'].dropna().unique().tolist()) if 'Status' in df.columns else [],
        'aset': sorted(df['Aset'].dropna().unique().tolist()) if 'Aset' in df.columns else [],
        'periode': sorted(df['Period'].dropna().unique().tolist()) if 'Period' in df.columns else []
    }

    kategori_filter = request.args.get('kategori')
    lokasi_filter = request.args.get('lokasi')
    status_filter = request.args.get('status')
    aset_filter = request.args.get('aset')
    periode_filter = request.args.get('periode')

    if kategori_filter and 'Category' in df.columns: df = df[df['Category'] == kategori_filter]
    if lokasi_filter and 'Location' in df.columns: df = df[df['Location'] == lokasi_filter]
    if status_filter and 'Status' in df.columns: df = df[df['Status'] == status_filter]
    if aset_filter and 'Aset' in df.columns: df = df[df['Aset'] == aset_filter]
    if periode_filter and 'Period' in df.columns: df = df[df['Period'] == periode_filter]

    total_perangkat = len(df)
    total_kategori = df['Category'].nunique() if 'Category' in df.columns else 0
    total_lokasi = df['Location'].nunique() if 'Location' in df.columns else 0
    in_use = len(df[df['Status'] == 'In Use']) if 'Status' in df.columns else 0
    repair = len(df[df['Status'] == 'Repair']) if 'Status' in df.columns else 0
        
    kpis = {'total_perangkat': total_perangkat, 'total_kategori': total_kategori, 'total_lokasi': total_lokasi, 'in_use': in_use, 'repair': repair}
    
    charts = {}
    if 'Category' in df.columns: charts['kategori'] = df['Category'].value_counts().to_dict()
    if 'Location' in df.columns: charts['lokasi'] = df['Location'].value_counts().to_dict() 
    if 'Aset' in df.columns: charts['aset'] = df['Aset'].value_counts().to_dict()
    if 'Status' in df.columns: charts['status'] = df['Status'].value_counts().to_dict()
    if 'Period' in df.columns: charts['periode'] = df['Period'].value_counts().to_dict()
        
    broken_devices = []
    if 'Status' in df.columns:
        broken_df = df[df['Status'] == 'Repair']
        cols = ['Aset', 'Device Model', 'Period', 'Category', 'Serial Number', 'Location']
        avail_cols = [c for c in cols if c in broken_df.columns]
        broken_df = broken_df[avail_cols].fillna("-")
        if 'Device Model' in broken_df.columns:
            broken_df = broken_df.rename(columns={'Device Model': 'Nama Device'})
        broken_devices = broken_df.to_dict('records')

    # Add full device list for the Data Perangkat view
    all_devices = df.fillna("-").to_dict('records')

    # Full unfiltered device list for dropdowns (e.g. complaint form)
    df_full = load_data()
    device_list = df_full[['Device Model', 'Serial Number', 'Location', 'Aset', 'Category', 'Status', 'Period']].fillna('-').to_dict('records') if not df_full.empty else []

    return jsonify({
        'kpis': kpis,
        'charts': charts,
        'table': broken_devices,
        'all_devices': all_devices,
        'filter_options': filter_options,
        'device_list': device_list
    })

# --- CRUD Devices ---
@app.route('/api/devices', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_devices():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    # GET is allowed for all authenticated users (needed for complaint dropdown)
    if request.method == 'GET':
        df = load_data()
        device_list = df[['Device Model', 'Serial Number', 'Location', 'Aset', 'Category', 'Status', 'Period']].fillna('-').to_dict('records') if not df.empty else []
        return jsonify(device_list)

    # Write operations only for admin
    if session['role'] != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403
        
    df = load_data()
    data = request.json
    
    if request.method == 'POST':
        # Create
        new_row = pd.DataFrame([data])
        df = pd.concat([df, new_row], ignore_index=True)
        save_data(df)
        return jsonify({"success": True})
        
    elif request.method == 'PUT':
        # Update based on Serial Number (assuming it's unique)
        serial = data.get('Serial Number')
        if not serial: return jsonify({"success": False, "message": "Serial Number required"}), 400
        idx = df.index[df['Serial Number'] == serial].tolist()
        if not idx: return jsonify({"success": False, "message": "Device not found"}), 404
        for k, v in data.items():
            if k in df.columns:
                df.at[idx[0], k] = v
        save_data(df)
        return jsonify({"success": True})
        
    elif request.method == 'DELETE':
        # Delete based on Serial Number
        serial = data.get('Serial Number')
        df = df[df['Serial Number'] != serial]
        save_data(df)
        return jsonify({"success": True})

# --- Manage Complaints ---
@app.route('/api/complaints', methods=['GET', 'POST', 'PUT'])
def manage_complaints():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # ==========================================
    # GET PENGADUAN DARI SUPABASE
    # ==========================================
    if request.method == 'GET':
        try:
            response = (
                supabase
                .table("pengaduan")
                .select("*")
                .order("id", desc=True)
                .execute()
            )

            complaints = response.data or []

            # Pengaduan aktif sekarang menggunakan status open
            open_c = [
                c for c in complaints
                if str(c.get("status", "")).strip().lower()
                == "open"
            ]

            # Urgent ditampilkan lebih dahulu
            open_c.sort(
                key=lambda x: x.get("urgent", False),
                reverse=True
            )

            # Notifikasi masih dari JSON untuk sementara
            notif_response = (
                supabase
                .table("notifikasi")
                .select("*")
                .order("id", desc=True)
                .execute()
            )

            notif_items = notif_response.data or []

            unread_count = len([
                n for n in notif_items
                if not n.get("is_read", False)
            ])
            

            return jsonify({
                "complaints": open_c,
                "all": complaints,
                "unread_count": unread_count,
                "notif_items": notif_items
            })

        except Exception as e:
            print(
                "Gagal membaca pengaduan dari Supabase:",
                e,
                flush=True
            )

            return jsonify({
                "error": "Gagal membaca data pengaduan"
            }), 500

    # ==========================================
    # AMBIL PENGADUAN DARI SUPABASE
    # ==========================================
    response = (
        supabase
        .table("pengaduan")
        .select("*")
        .execute()
    )

    complaints = response.data or []
    
    if request.method == 'POST':
        try:
            # ==========================================
            # AMBIL DATA DARI FORM WEB
            # ==========================================
            data = request.json or {}

            serial = str(
                data.get("serial", "")
            ).strip()

            # ==========================================
            # SIAPKAN DATA PENGADUAN
            # ==========================================
            new_complaint = {
                "title": data.get(
                    "title",
                    "Pengaduan perangkat"
                ),

                "location": data.get(
                    "location"
                ),

                "urgent": bool(
                    data.get("urgent", False)
                ),

                "status": "Open",

                "asset": data.get(
                    "asset"
                ),

                "category": data.get(
                    "category"
                ),

                "serial": serial if serial else None,

                "device_name": data.get(
                    "device_name"
                ),

                "source": "web"
            }

            # ==========================================
            # SIMPAN KE SUPABASE
            # ==========================================
            response = (
                supabase
                .table("pengaduan")
                .insert(new_complaint)
                .execute()
            )

            if not response.data:
                return jsonify({
                    "success": False,
                    "message": "Pengaduan gagal disimpan"
                }), 500

            saved_complaint = response.data[0]

            # ==========================================
            # UPDATE INVENTARIS MENJADI REPAIR
            # MASIH MENGGUNAKAN CSV SEMENTARA
            # ==========================================
            if serial:
                df = load_data()

                idx = df.index[
                    df["Serial Number"]
                    .astype(str)
                    .str.strip()
                    == serial
                ].tolist()

                if idx:
                    df.at[
                        idx[0],
                        "Status"
                    ] = "Repair"

                    save_data(df)

            # ==========================================
            # NOTIFIKASI MASIH JSON SEMENTARA
            # ==========================================
            # ==========================================
            # SIMPAN NOTIFIKASI KE SUPABASE
            # ==========================================
            notif_data = {
                "text": (
                    f"Pengaduan baru: "
                    f"{saved_complaint.get('title', '')}"
                ),
                "device": saved_complaint.get(
                    "device_name",
                    ""
                ),
                "serial": saved_complaint.get(
                    "serial",
                    ""
                ),
                "location": saved_complaint.get(
                    "location",
                    ""
                ),
                "urgent": bool(
                    saved_complaint.get(
                        "urgent",
                        False
                    )
                ),
                "is_read": False    
            }

            (
        
                supabase
                .table("notifikasi")
                .insert(notif_data)
                .execute()
            )   
            
            return jsonify({
                "success": True,
                "complaint": saved_complaint
            })

        except Exception as e:
            print(
                "Gagal menambah pengaduan ke Supabase:",
                e,
                flush=True
            )

            return jsonify({
                "success": False,
                "message": str(e)
            }), 500
    if request.method == 'PUT':
        try:
            if session['role'] != 'admin':
                return jsonify({
                    "success": False,
                    "message": "Unauthorized"
                }), 403

            data = request.json or {}
            c_id = data.get("id")

            if not c_id:
                return jsonify({
                    "success": False,
                    "message": "ID pengaduan tidak ditemukan"
                }), 400

            # ==========================================
            # AMBIL DATA PENGADUAN DARI SUPABASE
            # ==========================================
            response = (
                supabase
                .table("pengaduan")
                .select("*")
                .eq("id", c_id)
                .limit(1)
                .execute()
            )

            if not response.data:
                return jsonify({
                    "success": False,
                    "message": "Pengaduan tidak ditemukan"
                }), 404

            complaint = response.data[0]
            print("===== DEBUG RESOLVE =====", flush=True)
            print("COMPLAINT ID:", c_id, flush=True)
            print("COMPLAINT DATA:", complaint, flush=True)
            print(
                "SERIAL COMPLAINT:",
                complaint.get("serial"),
                flush=True
            )
            print("=========================", flush=True)
            # ==========================================
            # UPDATE STATUS PENGADUAN -> RESOLVED
            # ==========================================
            update_response = (
                supabase
                .table("pengaduan")
                .update({
                    "status": "Resolved"
                })
                .eq("id", c_id)
                .execute()
            )

            # ==========================================
            # UPDATE INVENTARIS -> IN USE
            # MASIH CSV SEMENTARA
            # ==========================================
            serial = str(
                complaint.get("serial", "")
            ).strip()
        

            if serial:
                df = load_data()

                idx = df.index[
                    df["Serial Number"]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    == serial.lower()
                ].tolist()

                if idx:
                    df.at[idx[0], "Status"] = "In Use"
                    save_data(df)

                    print(
                         f"Inventory {serial} berhasil diubah ke In Use",
                    flush=True
                    )
                else:
                    print(
                         f"Serial {serial} tidak ditemukan di inventory",
                        flush=True
                    )
            return jsonify({
                "success": True,
                "complaint": (
                    update_response.data[0]
                    if update_response.data
                    else None
                )
            })

        except Exception as e:
            print(
                "Gagal menyelesaikan pengaduan di Supabase:",
                e,
                flush=True
            )

            return jsonify({
                "success": False,
                "message": str(e)
            }), 500

# --- WhatsApp Bot: Lapor Kendala via WhatsApp ---
@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    """
    Endpoint yang dipanggil Fonnte setiap ada pesan masuk.
    """
    # ==========================================
    # CEK STATUS BOT
    # ==========================================
    print("=== WEBHOOK MASUK ===", flush=True)

    if not is_bot_enabled():
        print("BOT NONAKTIF - STOP DI SINI", flush=True)

        return jsonify({
            "status": "bot_disabled"
        }), 200

    # kode webhook kamu yang lama lanjut di bawah
    print("\n========== WEBHOOK MASUK ==========", flush=True)
    print("Content-Type:", request.content_type, flush=True)
    print("FORM:", request.form.to_dict(), flush=True)
    print("JSON:", request.get_json(silent=True), flush=True)
    print("RAW:", request.get_data(as_text=True), flush=True)

    payload = (
        request.form.to_dict()
        if request.form
        else (request.get_json(silent=True) or {})
    )

    print("[whatsapp webhook] payload open:", payload, flush=True)

    sender = (
        payload.get('sender')
        or payload.get('phone')
        or payload.get('from')
    )

    message = (
        payload.get('message')
        or payload.get('text')
        or ''
    )

    print("SENDER:", sender, flush=True)
    print("MESSAGE:", message, flush=True)

    if not sender or not str(message).strip():
        return jsonify({'status': 'ignored'}), 200
    pending_row = get_pending_state(sender)

    print("ISI PENDING SUPABASE:", pending_row, flush=True)

    if pending_row:

        pending = pending_row.get("data", {})
        state = pending_row.get("state", "waiting_confirmation")
   

        
        # ==========================================
        # TAHAP 3: USER SEDANG MEMILIH PERANGKAT
        # ==========================================
        if state == "waiting_selection":

            user_answer = str(message).strip()

            # Harus berupa angka

        
            if not user_answer.isdigit():
                 # Batalkan proses pemilihan perangkat
                delete_pending_state(sender)
                send_whatsapp_reply(
                    sender,
                    "❌ Tidak ada perangkat yang dipilih.\n\n"
                    "Pemilihan dibatalkan.\n"
                    "Silakan kirim laporan baru dengan nama perangkat, "
                    "serial number, aset, atau lokasi yang lebih sesuai."
                )

                return jsonify({
                    "status": "selection_cancelled"
                }), 200

            selected_index = int(user_answer) - 1
            devices = pending["devices"]

            # Angka di luar pilihan
            if selected_index < 0 or selected_index >= len(devices):
                send_whatsapp_reply(
                    sender,
                    f"⚠️ Nomor perangkat tidak tersedia.\n\n"
                    f"Silakan pilih angka 1 sampai {len(devices)}."
                )

                return jsonify({
                    "status": "invalid_selection"
                }), 200

            # Ambil perangkat yang dipilih
            selected_device = devices[selected_index]

            # Ubah state menjadi menunggu Y
            # Ubah state menjadi menunggu konfirmasi Y
            save_pending_state(
                sender,
                "waiting_confirmation",
                {
                "device": selected_device,
                "original_message": pending["original_message"],
                "urgent": pending.get("urgent", False)
                }
            )

            reply = (
                "🔎 Perangkat yang dipilih:\n\n"
                f"Perangkat: {selected_device.get('Device Model', '-')}\n"
                f"Kategori: {selected_device.get('Category', '-')}\n"
                f"Lokasi: {selected_device.get('Location', '-')}\n"
                f"Serial Number: {selected_device.get('Serial Number', '-')}\n\n"
                "Apakah ini perangkat yang dimaksud?\n"
                "Ketik Y untuk konfirmasi."
            )

            send_whatsapp_reply(sender, reply)

            return jsonify({
                "status": "waiting_confirmation"
            }), 200
        # Kalau user mengetik Y / y
        
        # ==========================================
        # TAHAP 2: KONFIRMASI Y
        # ==========================================
        if state == "waiting_confirmation":

            user_answer = str(message).strip().lower()

            if user_answer == "y":
                device = pending["device"]
                original_message = pending["original_message"]
                urgent = pending.get("urgent", False)

                df = load_data()

                # ==========================================
                # CEK STATUS PERANGKAT DAN TIKET AKTIF
                # ==========================================
                serial = str(
                    device.get("Serial Number", "")
                ).strip()

                active_complaint = None
                inventory_status = ""

                serial_valid = (
                    bool(serial)
                    and serial.lower() not in ["-", "none", "nan"]
                )

                # Serial perangkat hasil pencarian harus valid.
                if not serial_valid:
                    delete_pending_state(sender)
                    send_whatsapp_reply(
                        sender,
                        "❌ Serial Number perangkat tidak valid.\n\n"
                        "Silakan kirim laporan kembali dengan "
                        "informasi perangkat yang lebih lengkap."
                    )
                    return jsonify({
                        "status": "invalid_serial"
                    }), 200

                # ==========================================
                # CEK TIKET OPEN UNTUK SERIAL YANG SAMA
                # ==========================================
                active_response = (
                    supabase
                    .table("pengaduan")
                    .select("*")
                    .eq("serial", serial)
                    .eq("status", "Open")
                    .order("id", desc=True)
                    .limit(1)
                    .execute()
                )

                if active_response.data:
                    active_complaint = active_response.data[0]

                # ==========================================
                # CEK STATUS INVENTORY
                # ==========================================
                matching_device = df[
                    df["Serial Number"]
                    .astype(str)
                    .str.strip()
                    .str.lower()
                    .eq(serial.lower())
                ]

                if not matching_device.empty:
                    inventory_status = str(
                        matching_device.iloc[0]["Status"]
                    ).strip()

                device_in_repair = (
                    inventory_status.lower() == "repair"
                )

                print(
                    "DEBUG TICKET CHECK:",
                    {
                        "serial": serial,
                        "inventory_status": inventory_status,
                        "active_complaint": (
                            active_complaint.get("id")
                            if active_complaint
                            else None
                        )
                    },
                    flush=True
                )

                # ==========================================
                # TOLAK JIKA MASIH DALAM PENANGANAN
                # ==========================================
                # Tiket lama yang sudah Resolved tidak menghalangi tiket baru.
                # Pengaduan ditolak hanya jika ada tiket Open atau status
                # inventaris perangkat saat ini adalah Repair.
                if active_complaint or device_in_repair:
                    delete_pending_state(sender)

                    if active_complaint:
                        ticket_info = (
                            f"Tiket Aktif: #{active_complaint.get('id')}\n"
                            f"Status Pengaduan: "
                            f"{active_complaint.get('status')}\n"
                        )
                    else:
                        ticket_info = ""

                    reply = (
                        "⚠️ Perangkat ini sedang dalam proses penanganan.\n\n"
                        f"Perangkat: {device.get('Device Model', '-')}\n"
                        f"Lokasi: {device.get('Location', '-')}\n"
                        f"Serial Number: {serial}\n"
                        f"{ticket_info}"
                        f"Status Perangkat: {inventory_status or '-'}\n\n"
                        "Pengaduan baru tidak dibuat karena perangkat "
                        "yang sama masih dalam proses penanganan."
                    )

                    send_whatsapp_reply(sender, reply)

                    return jsonify({
                        "status": "duplicate_complaint"
                    }), 200

                # ==========================================
                # BUAT DATA PENGADUAN WHATSAPP
                # ==========================================
                device_name = device.get(
                    "Device Model",
                    "-"
                )

                device_location = device.get(
                    "Location",
                    "-"
                )

                new_complaint = {
                    "title": (
                        f"Gangguan {device_name} - "
                        f"{device_location}"
                    ),
                    "location": device_location,
                    "urgent": urgent,
                    "status": "Open",
                    "asset": device.get("Aset", "-"),
                    "category": device.get("Category", "-"),
                    "serial": serial,
                    "device_name": device_name,
                    "source": "whatsapp",
                    "phone": sender,
                    "original_message": original_message
                }

                # ==========================================
                # SIMPAN PENGADUAN KE SUPABASE DULU
                # ==========================================
                # Inventory baru diubah ke Repair setelah insert tiket berhasil.
                insert_response = (
                    supabase
                    .table("pengaduan")
                    .insert(new_complaint)
                    .execute()
                )

                if not insert_response.data:
                    delete_pending_state(sender)
                    send_whatsapp_reply(
                        sender,
                        "❌ Pengaduan gagal disimpan. "
                        "Silakan coba kembali."
                    )
                    return jsonify({
                        "status": "insert_failed"
                    }), 500

                # ==========================================
                # SET INVENTORY -> REPAIR
                # ==========================================
                try:
                    (
                        supabase
                        .table("inventaris")
                        .update({"status": "Repair"})
                        .eq("serial_number", serial)
                        .execute()
                    )
                except Exception as inventory_error:
                    # Rollback tiket agar tidak tercipta tiket Open dengan
                    # inventory yang gagal berubah menjadi Repair.
                    created_id = insert_response.data[0].get("id")
                    if created_id:
                        (
                            supabase
                            .table("pengaduan")
                            .delete()
                            .eq("id", created_id)
                            .execute()
                        )

                    delete_pending_state(sender)
                    print(
                        "Gagal mengubah inventory menjadi Repair:",
                        inventory_error,
                        flush=True
                    )
                    send_whatsapp_reply(
                        sender,
                        "❌ Pengaduan gagal dibuat karena status perangkat "
                        "tidak dapat diperbarui. Silakan coba kembali."
                    )
                    return jsonify({
                        "status": "inventory_update_failed"
                    }), 500

                saved_complaint = insert_response.data[0]

                new_id = saved_complaint["id"]

                # Gunakan data hasil Supabase
                new_complaint = saved_complaint

                # Tambahkan notifikasi
                # ==========================================
                # SIMPAN NOTIFIKASI WHATSAPP KE SUPABASE
                # ==========================================
                notif_data = {
                    "text": (
                        f"Pengaduan baru via WhatsApp: "
                        f"{new_complaint['title']}"
                    ),
                    "device": new_complaint.get(
                        "device_name",
                        ""
                    ),
                    "serial": new_complaint.get(
                        "serial",
                        ""
                    ),
                    "location": new_complaint.get(
                        "location",
                        ""
                    ),
                    "urgent": bool(
                        new_complaint.get(
                            "urgent",
                            False
                        )
                    ),
                    "is_read": False
                }

                ( 
                    supabase
                    .table("notifikasi")
                    .insert(notif_data)
                    .execute()
                )
                
                # Hapus pending karena tiket sudah selesai dibuat
                delete_pending_state(sender)

                reply_text = (
                    f"✅ Pengaduan berhasil dibuat.\n\n"
                    f"Nomor Tiket: #{new_id}\n"
                    f"Perangkat: {new_complaint['device_name']}\n"
                    f"Lokasi: {new_complaint['location']}\n"
                    f"Serial Number: {new_complaint['serial']}\n"
                    f"Status: Open"
                )

                send_whatsapp_reply(
                    sender,
                    reply_text
                )

                return jsonify({
                    'status': 'ticket_created',
                    'ticket_id': new_id
                }), 200

            else:
                # Jawaban selain Y = batal
                delete_pending_state(sender)

                reply = (
                    "❌ Perangkat tidak dikonfirmasi.\n\n"
                    "Konfirmasi dibatalkan. "
                    "Silakan kirim laporan perangkat kembali "
                    "jika ingin membuat pengaduan baru."
                )

                send_whatsapp_reply(
                    sender,
                    reply
                )

                return jsonify({
                    "status": "confirmation_cancelled"
                }), 200

    # KODE KAMU DARI SINI TETAP
    df = load_data()

    known_locations = sorted(
        df['Location'].dropna().unique().tolist()
    ) if 'Location' in df.columns else []

    device_list = (
        df[['Device Model', 'Serial Number', 'Location',
            'Aset', 'Category', 'Status', 'Period']]
        .fillna('-')
        .to_dict('records')
        if not df.empty else []
    )

    parsed = parse_complaint_message(
        message,
        device_list,
        known_locations
    )

    # dst...
    # Ambil data perangkat & daftar lokasi terkini dari CSV, untuk dicocokkan ke pesan
    df = load_data()
    known_locations = sorted(df['Location'].dropna().unique().tolist()) if 'Location' in df.columns else []
    device_list = (
        df[['Device Model', 'Serial Number', 'Location', 'Aset', 'Category', 'Status', 'Period']]
        .fillna('-').to_dict('records')
        if not df.empty else []
    )
    
    result = find_device_candidates(
        message,
        device_list,
        known_locations
    )
    candidates = result["devices"]
    
    print(
        "[whatsapp_bot] kandidat perangkat:",
        candidates,
        flush=True
    )

    # --- kalau tidak ada kandidat ---
    if len(candidates) == 0:
        delete_pending_state(sender)

        reply = (
            "❌ Perangkat yang dimaksud tidak ditemukan "
            "pada data inventaris.\n\n"
            "Silakan cek kembali nama perangkat dan lokasi. "
            "Pastikan tidak ada kesalahan penulisan, "
            "kemudian kirim laporan kembali."
        )

        send_whatsapp_reply(sender, reply)

        return jsonify({
        "status": "device_not_found"
        }), 200

    if len(candidates) == 1:
        device = candidates[0]
        save_pending_state(
            sender,
            "waiting_confirmation",
            {
                "device": device,
                "original_message": message,
                "urgent": detect_urgent(message)
            }
        )

        reply = (
            "🔎 Ditemukan perangkat yang sesuai.\n\n"
            f"Perangkat: {device.get('Device Model', '-')}\n"
            f"Kategori: {device.get('Category', '-')}\n"
            f"Lokasi: {device.get('Location', '-')}\n"
            f"Serial Number: {device.get('Serial Number', '-')}\n\n"
            "Apakah ini perangkat yang dimaksud?\n"
            "Ketik Y untuk konfirmasi."
        )

        send_whatsapp_reply(sender, reply)

        return jsonify({
            "status": "waiting_confirmation"
        }), 200


    if len(candidates) > 1:
        # Simpan semua kandidat sementara
        save_pending_state(
            sender,
            "waiting_selection",
            {
                "devices": candidates,
                "original_message": message,
                "urgent": detect_urgent(message)
            }
        )

        lines = [
            "🔎 Ditemukan beberapa perangkat yang sesuai.\n"
        ]

        for i, device in enumerate(candidates, start=1):
            lines.append(
                f"{i}. {device.get('Device Model', '-')}\n"
                f"   Lokasi: {device.get('Location', '-')}\n"
                f"   SN: {device.get('Serial Number', '-')}"
            )

        lines.append(
            "\nBalas dengan nomor perangkat yang dimaksud.\n"
            "Contoh: 2"
        )

        reply = "\n\n".join(lines)

        send_whatsapp_reply(sender, reply)

        return jsonify({
            "status": "waiting_selection"
        }), 200
@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        (
            supabase
            .table("notifikasi")
            .update({"is_read": True})
            .eq("is_read", False)
            .execute()
        )

        return jsonify({"success": True})

    except Exception as e:
        print(
            f"Error mark notifications read: {e}",
            flush=True
        )

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

# --- Search ---
@app.route('/api/search')
def search_devices():
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    q = request.args.get('q', '').lower()
    if not q: return jsonify([])
    
    df = load_data()
    # Search in SN or Device Model
    mask = df['Serial Number'].str.lower().str.contains(q, na=False) | df['Device Model'].str.lower().str.contains(q, na=False)
    results = df[mask].fillna("-").to_dict('records')
    return jsonify(results)

if __name__ == '__main__':
    print("Starting Flask server at http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
