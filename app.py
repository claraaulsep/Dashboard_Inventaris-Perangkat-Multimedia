#from migrate_pengaduan import response
#from migrate_pengaduan import complaint
from whatsapp_bot import save_pending_state
from whatsapp_bot import get_pending_state
from whatsapp_bot import delete_pending_state
from pandas.io.formats import style_render
from whatsapp_bot import detect_urgent
import os
import io
import pandas as pd

from flask import (
    Flask,
    jsonify,
    render_template,
    request,
    session,
    redirect,
    url_for,
    send_file
)

from datetime import datetime, timedelta, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer
)
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
    'Manager': {'password': '123', 'role': 'Manager', 'name': 'Manager'}
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
                serial = str(data.get('Serial Number', '')).strip()

                if not serial:
                    return jsonify({
                        "success": False,
                        "message": "Serial Number required"
                    }), 400

                update_data = {
                    "aset": data.get("Aset"),
                    "period": data.get("Period"),
                    "device_model": data.get("Device Model"),
                    "category": data.get("Category"),
                    "status": data.get("Status"),
                    "location": data.get("Location")
                }

                try:
                    response = (
                        supabase
                        .table("inventaris")
                        .update(update_data)
                        .eq("serial_number", serial)
                        .execute()
                    )

                    print("UPDATE DEVICE:", serial, response.data, flush=True)

                    if not response.data:
                        return jsonify({
                            "success": False,
                            "message": "Perangkat tidak ditemukan / tidak terupdate"
                        }), 404

                    return jsonify({
                        "success": True,
                        "message": "Data perangkat berhasil diperbarui"
                    })

                except Exception as e:
                    print("ERROR UPDATE DEVICE:", e, flush=True)

                    return jsonify({
                        "success": False,
                        "message": str(e)
                    }), 500
                

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

            # Pengaduan aktif sekarang menggunakan status Reported
            reported_c = [
                c for c in complaints
                if str(c.get("status", "")).strip().lower()
                == "reported"
            ]

            # Urgent ditampilkan lebih dahulu
            reported_c.sort(
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
                "complaints": reported_c,
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
            data = request.json or {}

            serial = str(data.get("serial", "")).strip()
            title = str(data.get("title", "")).strip()
            urgent = bool(data.get("urgent", False))

            # ==============================
            # VALIDASI INPUT
            # ==============================
            if not serial:
                return jsonify({
                    "success": False,
                    "message": "Perangkat belum dipilih"
                }), 400

            if not title:
                return jsonify({
                    "success": False,
                    "message": "Keluhan wajib diisi"
                }), 400

            # ==============================
            # CARI PERANGKAT DARI INVENTARIS
            # ==============================
            device_response = (
                supabase
                .table("inventaris")
                .select("*")
                .eq("serial_number", serial)
                .limit(1)
                .execute()
            )

            if not device_response.data:
                return jsonify({
                    "success": False,
                    "message": "Perangkat tidak ditemukan"
                }), 404

            device = device_response.data[0]

            # ==============================
            # CEK STATUS PERANGKAT
            # ==============================
            current_status = str(
                device.get("status", "")
            ).strip()

            if current_status.lower() == "repair":
                return jsonify({
                    "success": False,
                    "message": "Perangkat sedang dalam proses penanganan"
                }), 409

            # ==============================
            # CEK TIKET AKTIF
            # ==============================
            active_response = (
                supabase
                .table("pengaduan")
                .select("id,status")
                .eq("serial", serial)
                .eq("status", "Reported")
                .limit(1)
                .execute()
            )

            if active_response.data:
                return jsonify({
                    "success": False,
                    "message": "Perangkat sudah memiliki tiket aktif"
                }), 409

            # ==============================
            # DATA PENGADUAN
            # Semua detail perangkat diambil
            # langsung dari master inventaris
            # ==============================
            new_complaint = {
                "title": title,
                "location": device.get("location", "-"),
                "urgent": urgent,
                "status": "Reported",
                "asset": device.get("aset", "-"),
                "category": device.get("category", "-"),
                "serial": serial,
                "device_name": device.get("device_model", "-"),
                "source": "web"
            }

            # ==============================
            # BUAT TIKET
            # ==============================
            insert_response = (
                supabase
                .table("pengaduan")
                .insert(new_complaint)
                .execute()
            )

            if not insert_response.data:
                return jsonify({
                    "success": False,
                    "message": "Pengaduan gagal disimpan"
                }), 500

            saved_complaint = insert_response.data[0]

            # ==============================
            # UBAH PERANGKAT -> REPAIR
            # ==============================
            try:
                inventory_response = (
                    supabase
                    .table("inventaris")
                    .update({
                        "status": "Repair"
                    })
                    .eq("serial_number", serial)
                    .execute()
                )

                if not inventory_response.data:
                    raise Exception(
                        "Status perangkat gagal diperbarui"
                    )

            except Exception as inventory_error:

                # Rollback tiket jika update inventory gagal
                (
                    supabase
                    .table("pengaduan")
                    .delete()
                    .eq("id", saved_complaint["id"])
                    .execute()
                )

                raise inventory_error

            # ==============================
            # BUAT NOTIFIKASI
            # ==============================
            notif_data = {
                "complaint_id": saved_complaint["id"],
                "text": f"Pengaduan baru: {title}",
                "device": device.get("device_model", ""),
                "serial": serial,
                "location": device.get("location", ""),
                "urgent": urgent,
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
                "message": "Pengaduan berhasil dibuat",
                "complaint": saved_complaint
            })

        except Exception as e:
            print(
                "Gagal membuat pengaduan:",
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
            # LANGSUNG KE SUPABASE
            # ==========================================
            serial = str(
                complaint.get("serial", "")
            ).strip()

            if serial and serial.lower() not in ["-", "none", "nan"]:

                inventory_response = (
                    supabase
                    .table("inventaris")
                    .update({
                        "status": "In Use"
                    })
                    .eq("serial_number", serial)
                    .execute()
                )

                print(
                    "UPDATE INVENTARIS IN USE:",
                    serial,
                    inventory_response.data,
                    flush=True
                )

                if not inventory_response.data:
                    print(
                        f"PERINGATAN: serial {serial} tidak ditemukan "
                        f"di tabel inventaris",
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
# ==========================================
# EXPORT PDF LAPORAN PENGADUAN
# ==========================================
@app.route('/export/pengaduan/pdf')
def export_pengaduan_pdf():

    if 'user' not in session:
        return redirect('/login')

    tanggal_mulai = request.args.get('start')
    tanggal_akhir = request.args.get('end')

    if not tanggal_mulai or not tanggal_akhir:
        return jsonify({
            "error": "Tanggal mulai dan tanggal akhir wajib diisi"
        }), 400

    try:
        start_date = datetime.strptime(
            tanggal_mulai,
            "%Y-%m-%d"
        )

        end_date = datetime.strptime(
            tanggal_akhir,
            "%Y-%m-%d"
        )

        if end_date < start_date:
            return jsonify({
                "error": "Tanggal akhir tidak boleh lebih kecil dari tanggal mulai"
            }), 400

        # ==========================================
        # BATAS PERIODE WIB
        # ==========================================
        start_iso = (
            start_date.strftime("%Y-%m-%d")
            + "T00:00:00+07:00"
        )

        next_day = end_date + timedelta(days=1)

        end_iso = (
            next_day.strftime("%Y-%m-%d")
            + "T00:00:00+07:00"
        )

        # ==========================================
        # AMBIL PENGADUAN DARI SUPABASE
        # ==========================================
        response = (
            supabase
            .table("pengaduan")
            .select("*")
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .order("created_at")
            .execute()
        )

        complaints = response.data or []

        # ==========================================
        # HITUNG RINGKASAN
        # ==========================================
        total_pengaduan = len(complaints)

        total_Reported = len([
            item for item in complaints
            if str(item.get("status", "")).strip().lower()
            in ["reported", "open"]
        ])

        total_resolved = len([
            item for item in complaints
            if str(item.get("status", "")).strip().lower()
            == "resolved"
        ])

        # ==========================================
        # BUAT FILE PDF DI MEMORY
        # ==========================================
        buffer = io.BytesIO()

        document = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            rightMargin=1.2 * cm,
            leftMargin=1.2 * cm,
            topMargin=1.2 * cm,
            bottomMargin=1.2 * cm
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            "ReportTitle",
            parent=styles["Title"],
            alignment=TA_CENTER,
            fontSize=15,
            leading=18,
            spaceAfter=4
        )

        subtitle_style = ParagraphStyle(
            "ReportSubtitle",
            parent=styles["Normal"],
            alignment=TA_CENTER,
            fontSize=9,
            leading=12
        )

        cell_style = ParagraphStyle(
            "CellStyle",
            parent=styles["Normal"],
            fontSize=7,
            leading=9
        )

        header_style = ParagraphStyle(
            "HeaderStyle",
            parent=cell_style,
            textColor=colors.white,
            alignment=TA_CENTER
        )

        elements = []

        # ==========================================
        # JUDUL
        # ==========================================
        elements.append(
            Paragraph(
                "PT PERTAMINA HULU ROKAN",
                title_style
            )
        )

        elements.append(
            Paragraph(
                "ZONA 1 JAMBI",
                subtitle_style
            )
        )

        elements.append(Spacer(1, 0.2 * cm))

        elements.append(
            Paragraph(
                "LAPORAN PENGADUAN PERANGKAT MULTIMEDIA",
                title_style
            )
        )

        # ==========================================
        # FORMAT PERIODE
        # ==========================================
        bulan_indonesia = {
            1: "Januari",
            2: "Februari",
            3: "Maret",
            4: "April",
            5: "Mei",
            6: "Juni",
            7: "Juli",
            8: "Agustus",
            9: "September",
            10: "Oktober",
            11: "November",
            12: "Desember"
        }

        periode_mulai = (
            f"{start_date.day} "
            f"{bulan_indonesia[start_date.month]} "
            f"{start_date.year}"
        )

        periode_akhir = (
            f"{end_date.day} "
            f"{bulan_indonesia[end_date.month]} "
            f"{end_date.year}"
        )

        elements.append(
            Paragraph(
                f"Periode: {periode_mulai} s.d. {periode_akhir}",
                subtitle_style
            )
        )

        elements.append(Spacer(1, 0.4 * cm))

        # ==========================================
        # RINGKASAN
        # ==========================================
        summary_data = [
            [
                Paragraph("<b>Total Pengaduan</b>", cell_style),
                Paragraph("<b>Reported</b>", cell_style),
                Paragraph("<b>Resolved</b>", cell_style)
            ],
            [
                str(total_pengaduan),
                str(total_Reported),
                str(total_resolved)
            ]
        ]

        summary_table = Table(
            summary_data,
            colWidths=[5 * cm, 5 * cm, 5 * cm]
        )

        summary_table.setStyle(TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#E2E8F0")
            ),
            (
                "ALIGN",
                (0, 0),
                (-1, -1),
                "CENTER"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.5,
                colors.HexColor("#94A3B8")
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "MIDDLE"
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                6
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                6
            )
        ]))

        elements.append(summary_table)
        elements.append(Spacer(1, 0.5 * cm))

        # ==========================================
        # HEADER TABEL
        # ==========================================
        table_data = [[
            Paragraph("<b>No</b>", header_style),
            Paragraph("<b>Tanggal</b>", header_style),
            Paragraph("<b>Perangkat</b>", header_style),
            Paragraph("<b>Serial Number</b>", header_style),
            Paragraph("<b>Lokasi</b>", header_style),
            Paragraph("<b>Pengaduan</b>", header_style),
            Paragraph("<b>Status</b>", header_style)
        ]]

        # ==========================================
        # ISI TABEL
        # ==========================================
        for index, complaint in enumerate(
            complaints,
            start=1
        ):
            created_at = complaint.get("created_at")

            tanggal = "-"

            if created_at:
                try:
                    dt = datetime.fromisoformat(
                        str(created_at).replace(
                            "Z",
                            "+00:00"
                        )
                    )

                    if dt.tzinfo is not None:
                        dt = dt.astimezone(
                            timezone(
                                timedelta(hours=7)
                            )
                        )

                    tanggal = dt.strftime(
                        "%d/%m/%Y"
                    )

                except Exception:
                    tanggal = str(created_at)[:10]

            table_data.append([
                Paragraph(str(index), cell_style),

                Paragraph(
                    tanggal,
                    cell_style
                ),

                Paragraph(
                    str(
                        complaint.get(
                            "device_name",
                            "-"
                        ) or "-"
                    ),
                    cell_style
                ),

                Paragraph(
                    str(
                        complaint.get(
                            "serial",
                            "-"
                        ) or "-"
                    ),
                    cell_style
                ),

                Paragraph(
                    str(
                        complaint.get(
                            "location",
                            "-"
                        ) or "-"
                    ),
                    cell_style
                ),

                Paragraph(
                    str(
                        complaint.get(
                            "title",
                            "-"
                        ) or "-"
                    ),
                    cell_style
                ),

                Paragraph(
                    str(
                        complaint.get(
                            "status",
                            "-"
                        ) or "-"
                    ),
                    cell_style
                )
            ])

        # ==========================================
        # JIKA DATA KOSONG
        # ==========================================
        if not complaints:
            table_data.append([
                "",
                "",
                "",
                Paragraph(
                    "Tidak ada pengaduan pada periode ini.",
                    cell_style
                ),
                "",
                "",
                ""
            ])

        complaint_table = Table(
            table_data,
            repeatRows=1,
            colWidths=[
                1 * cm,
                2.2 * cm,
                4.5 * cm,
                3.5 * cm,
                4.2 * cm,
                8 * cm,
                2.2 * cm
            ]
        )

        complaint_table.setStyle(TableStyle([
            (
                "BACKGROUND",
                (0, 0),
                (-1, 0),
                colors.HexColor("#1E3A8A")
            ),
            (
                "TEXTCOLOR",
                (0, 0),
                (-1, 0),
                colors.white
            ),
            (
                "ALIGN",
                (0, 0),
                (0, -1),
                "CENTER"
            ),
            (
                "ALIGN",
                (1, 0),
                (1, -1),
                "CENTER"
            ),
            (
                "ALIGN",
                (-1, 0),
                (-1, -1),
                "CENTER"
            ),
            (
                "VALIGN",
                (0, 0),
                (-1, -1),
                "TOP"
            ),
            (
                "GRID",
                (0, 0),
                (-1, -1),
                0.35,
                colors.HexColor("#CBD5E1")
            ),
            (
                "ROWBACKGROUNDS",
                (0, 1),
                (-1, -1),
                [
                    colors.white,
                    colors.HexColor("#F8FAFC")
                ]
            ),
            (
                "TOPPADDING",
                (0, 0),
                (-1, -1),
                5
            ),
            (
                "BOTTOMPADDING",
                (0, 0),
                (-1, -1),
                5
            )
        ]))

        elements.append(complaint_table)
        elements.append(Spacer(1, 0.4 * cm))

        # ==========================================
        # TANGGAL CETAK
        # ==========================================
        sekarang_wib = datetime.now(
            timezone(
                timedelta(hours=7)
            )
        )

        elements.append(
            Paragraph(
                "Dicetak pada: "
                + sekarang_wib.strftime(
                    "%d/%m/%Y %H:%M WIB"
                ),
                styles["Normal"]
            )
        )

        document.build(elements)

        buffer.seek(0)

        filename = (
            f"laporan_pengaduan_"
            f"{tanggal_mulai}_sd_{tanggal_akhir}.pdf"
        )

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )

    except Exception as e:
        print(
            "Gagal export PDF pengaduan:",
            e,
            flush=True
        )

        return jsonify({
            "error": str(e)
        }), 500
def get_authorized_whatsapp_user(sender):
    try:
        sender = str(sender).strip()

        response = (
            supabase
            .table("whatsapp_users")
            .select("id,nama,nomor_wa,bagian,is_active")
            .eq("nomor_wa", sender)
            .eq("is_active", True)
            .limit(1)
            .execute()
        )

        if response.data:
            return response.data[0]

        return None

    except Exception as e:
        print(
            f"Error cek whitelist WhatsApp: {e}",
            flush=True
        )
        return None
        
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

    print("[whatsapp webhook] payload Reported:", payload, flush=True)

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


    # ==========================================
    # CEK AKSES NOMOR WHATSAPP
    # ==========================================
    authorized_user = get_authorized_whatsapp_user(sender)

    if not authorized_user:
        print(
            f"AKSES WHATSAPP DITOLAK: {sender}",
            flush=True
        )

        send_whatsapp_reply(
            sender,
            "❌ Akses ditolak.\n\n"
            "Nomor WhatsApp Anda tidak terdaftar sebagai "
            "pengguna layanan pengaduan perangkat multimedia."
        )

        return jsonify({
            "status": "unauthorized"
        }), 200


    print(
        "WHATSAPP USER TERDAFTAR:",
        authorized_user.get("nama"),
        flush=True
    )


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
                # CEK TIKET Reported UNTUK SERIAL YANG SAMA
                # ==========================================
                active_response = (
                    supabase
                    .table("pengaduan")
                    .select("*")
                    .eq("serial", serial)
                    .eq("status", "Reported")
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
                # Pengaduan ditolak hanya jika ada tiket Reported atau status
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
                    "status": "Reported",
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
                    # Rollback tiket agar tidak tercipta tiket Reported dengan
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
                    "complaint_id": new_id,
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
                    f"Status: Reported"
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
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    q = request.args.get('q', '').strip().lower()

    if not q:
        return jsonify([])

    df = load_data()

    if df.empty:
        return jsonify([])

    # Hanya perangkat yang TIDAK sedang Repair
    df = df[
        df['Status']
        .fillna('')
        .astype(str)
        .str.strip()
        .str.lower()
        != 'repair'
    ]

    # Cari berdasarkan Device Model atau Serial Number
    mask = (
        df['Serial Number']
        .fillna('')
        .astype(str)
        .str.lower()
        .str.contains(q, na=False, regex=False)
        |
        df['Device Model']
        .fillna('')
        .astype(str)
        .str.lower()
        .str.contains(q, na=False, regex=False)
    )

    results = (
        df[mask]
        .fillna("-")
        .to_dict('records')
    )

    return jsonify(results)

if __name__ == '__main__':
    print("Starting Flask server at http://127.0.0.1:5000")
    app.run(debug=True, port=5000)
