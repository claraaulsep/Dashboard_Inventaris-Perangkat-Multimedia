import os
import json
import pandas as pd
from flask import Flask, jsonify, render_template, request, session, redirect, url_for

app = Flask(__name__)
app.secret_key = 'pertamina_secret_key_123'

DATA_FILE = 'data.csv'
COMPLAINTS_FILE = 'pengaduan.json'
NOTIF_FILE = 'notifications.json'

# --- Initialize Notifications File ---
def init_notifications():
    if not os.path.exists(NOTIF_FILE):
        with open(NOTIF_FILE, 'w') as f:
            json.dump({'unread': 0, 'items': []}, f)

init_notifications()

# --- Users Data ---
USERS = {
    'admin': {'password': '123', 'role': 'admin', 'name': 'Admin'},
    'tamu': {'password': '123', 'role': 'tamu', 'name': 'Tamu / Pekerja'}
}

# --- Initialize Complaints File ---
def init_complaints():
    if not os.path.exists(COMPLAINTS_FILE):
        dummy_data = [
            {"id": 1, "title": "Layar Proyektor Blur", "location": "Bullian 1 Meeting Room", "urgent": False, "status": "Open", "asset": "Field", "serial": "MMD202608070168"},
            {"id": 2, "title": "Kabel HDMI Hilang", "location": "Cut Mutia Meeting Room", "urgent": False, "status": "Open", "asset": "Zona", "serial": "MMD202608070001"}
        ]
        with open(COMPLAINTS_FILE, 'w') as f:
            json.dump(dummy_data, f)

init_complaints()

# --- Helpers ---
def load_data():
    try:
        if not os.path.exists(DATA_FILE):
            return pd.DataFrame(columns=['Aset', 'Period', 'Device Model', 'Category', 'Serial Number', 'Status', 'Location'])
        df = pd.read_csv(DATA_FILE, sep=';', on_bad_lines='skip', dtype=str)
        df = df.dropna(axis=1, how='all')
        df.columns = df.columns.str.strip()
        if 'Status' in df.columns:
            df['Status'] = df['Status'].str.strip().str.title()
        return df
    except Exception as e:
        print(f"Error loading data: {e}")
        return pd.DataFrame()

def save_data(df):
    # Ensure column order matches original if possible
    try:
        df.to_csv(DATA_FILE, sep=';', index=False)
        return True
    except Exception as e:
        print(f"Error saving data: {e}")
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
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    
    with open(COMPLAINTS_FILE, 'r') as f:
        complaints = json.load(f)
        
    if request.method == 'GET':
        # Filter for open complaints to show on dashboard widget
        open_c = [c for c in complaints if c['status'] == 'Open']
        # Sort by urgency
        open_c.sort(key=lambda x: x.get('urgent', False), reverse=True)
        # Read notification state
        with open(NOTIF_FILE, 'r') as nf:
            notif_data = json.load(nf)
        return jsonify({"complaints": open_c, "all": complaints, "unread_count": notif_data.get('unread', 0), "notif_items": notif_data.get('items', [])})
        
    if request.method == 'POST':
        # Admin / Tamu adds a new complaint
        data = request.json
        new_id = max([c['id'] for c in complaints] + [0]) + 1
        data['id'] = new_id
        data['status'] = 'Open'
        
        # If reporting a broken device, update CSV status to Repair
        if data.get('serial'):
            df = load_data()
            idx = df.index[df['Serial Number'] == data['serial']].tolist()
            if idx:
                df.at[idx[0], 'Status'] = 'Repair'
                save_data(df)

        complaints.append(data)
        with open(COMPLAINTS_FILE, 'w') as f:
            json.dump(complaints, f)

        # Add notification
        with open(NOTIF_FILE, 'r') as nf:
            notif_data = json.load(nf)
        notif_data['unread'] = notif_data.get('unread', 0) + 1
        notif_data['items'].insert(0, {
            'text': f"Pengaduan baru: {data.get('title', '')}",
            'device': data.get('device_name', ''),
            'serial': data.get('serial', ''),
            'location': data.get('location', ''),
            'urgent': data.get('urgent', False),
            'time': 'Baru saja'
        })
        with open(NOTIF_FILE, 'w') as nf:
            json.dump(notif_data, nf)

        return jsonify({"success": True})
        
    if request.method == 'PUT':
        # Admin resolves complaint
        if session['role'] != 'admin':
            return jsonify({"success": False, "message": "Unauthorized"}), 403
            
        data = request.json
        c_id = data.get('id')
        for c in complaints:
            if c['id'] == c_id:
                c['status'] = 'Resolved'
                # Auto update CSV status to In Use
                if c.get('serial'):
                    df = load_data()
                    idx = df.index[df['Serial Number'] == c['serial']].tolist()
                    if idx:
                        df.at[idx[0], 'Status'] = 'In Use'
                        save_data(df)
                break
                
        with open(COMPLAINTS_FILE, 'w') as f:
            json.dump(complaints, f)
        return jsonify({"success": True})

# --- Notifications: Mark as Read ---
@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read():
    if 'user' not in session: return jsonify({"error": "Unauthorized"}), 401
    with open(NOTIF_FILE, 'r') as f:
        notif_data = json.load(f)
    notif_data['unread'] = 0
    with open(NOTIF_FILE, 'w') as f:
        json.dump(notif_data, f)
    return jsonify({"success": True})

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
