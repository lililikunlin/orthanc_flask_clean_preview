import os
from functools import wraps
from io import BytesIO
import sqlite3 #資料庫套件

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, send_file, session
from flask_cors import CORS

from fido2.server import Fido2Server
from fido2.webauthn import PublicKeyCredentialRpEntity, AttestedCredentialData
from fido2.utils import websafe_encode, websafe_decode

import time
import hmac
import hashlib
import base64

try:
    from PIL import Image
except Exception:  # Pillow is installed through requirements.txt, this keeps the app safer.
    Image = None


load_dotenv()

app = Flask(__name__)

# 1. 透過環境變數精準判斷是否為開發/除錯模式（預設為 True，方便本地直接跑）
IS_DEVELOPMENT = os.getenv("FLASK_DEBUG", "true").lower() in ["true", "1"]

app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY")
if not app.config["SECRET_KEY"] and not IS_DEVELOPMENT:
    raise ValueError("錯誤：正式環境必須在 .env 中設定 FLASK_SECRET_KEY！")
elif not app.config["SECRET_KEY"]:
    app.config["SECRET_KEY"] = "local-dev-fallback-key"

app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = not IS_DEVELOPMENT  # 正式環境才強制 HTTPS

CORS(app, supports_credentials=True)

ORTHANC_URL = os.getenv("ORTHANC_URL", "http://127.0.0.1:8042").rstrip("/")
ORTHANC_USERNAME = os.getenv("ORTHANC_USERNAME", "")
ORTHANC_PASSWORD = os.getenv("ORTHANC_PASSWORD", "")
ORTHANC_VERIFY_SSL = os.getenv("ORTHANC_VERIFY_SSL", "true").lower() == "true"

# --- FIDO2 的初始化與狀態 ---
states = {}  # 存 FIDO2 挑戰狀態 (臨時考卷，放記憶體即可)

# 從環境變數讀取網域，如果沒設定，就預設使用 'localhost' (本機用)
RP_ID = os.getenv("FIDO_RP_ID", "localhost")
rp = PublicKeyCredentialRpEntity(id=RP_ID, name="遠距醫療安全網關")
server = Fido2Server(rp)

def to_websafe(data):
    if isinstance(data, str): return data
    if isinstance(data, bytes): return websafe_encode(data)
    return data

# --- 【新增】SQLite 資料庫設定 ---
DB_FILE = "medical.db"

def init_db():
    """初始化資料庫表，如果不存在就建立"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 表格 1：credentials (負責 Authentication 身分驗證)
    c.execute('''CREATE TABLE IF NOT EXISTS credentials (username TEXT PRIMARY KEY, credential_data TEXT NOT NULL)''')
    
    # 表格 2：users (負責 Authorization 角色權限)
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, role TEXT NOT NULL)''')
    
    # 表格 3：system_logs (系統稽核日誌)
    c.execute('''
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT (datetime('now', 'localtime')),
            username TEXT,
            action TEXT,
            details TEXT,
            ip_address TEXT
        )
    ''')
    
    # 自動植入預設的三個角色
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO users (username, role) VALUES (?, ?)", [
            ("patient1", "patient"), ("doctor1", "doctor"), ("admin1", "admin")
        ])
        
    conn.commit()
    conn.close()

# 啟動時立刻初始化資料庫
init_db()

# 共用的寫入日誌函數
def write_log(action, details):
    """將系統操作寫入 SQLite 日誌資料庫"""
    user = session.get("user", {})
    username = user.get("username", "System") # 如果沒登入，預設為 System
    ip_address = request.remote_addr or "unknown"
    
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute('''
            INSERT INTO system_logs (username, action, details, ip_address) 
            VALUES (?, ?, ?, ?)
        ''', (username, action, details, ip_address))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"寫入日誌失敗: {e}")

ROLE_PERMISSIONS = {
    "patient": ["view", "modify", "delete"],
    "doctor": ["view", "upload"],
    "admin": ["view", "manage_users"], # 管理使用者的最高權限
}

ROLE_LABELS = {
    "patient": "patient",
    "doctor": "doctor",
    "admin": "admin",
}


# -------------------------
# Helper functions
# -------------------------
def orthanc_request(method: str, path: str, **kwargs):
    """Send a request to Orthanc."""
    url = f"{ORTHANC_URL}{path}"
    timeout = kwargs.pop("timeout", 30)

    headers = kwargs.pop("headers", {}) or {}
    if method.upper() == "GET":
        headers.setdefault("Accept", "image/png,image/jpeg,application/json,*/*")

    response = requests.request(
        method=method,
        url=url,
        auth=(ORTHANC_USERNAME, ORTHANC_PASSWORD),
        timeout=timeout,
        headers=headers,
        verify=ORTHANC_VERIFY_SSL,  # <--- 改用環境變數控制
        **kwargs,
    )
    response.raise_for_status()
    return response


def get_current_user():
    return session.get("user")


def get_permissions(role: str):
    return ROLE_PERMISSIONS.get(role, [])


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "user" not in session:
            return jsonify({"ok": False, "message": "尚未登入"}), 401
        return func(*args, **kwargs)

    return wrapper


def permission_required(permission: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if not user:
                return jsonify({"ok": False, "message": "尚未登入"}), 401

            role = user.get("role", "")
            if permission not in get_permissions(role):
                return jsonify({"ok": False, "message": f"{role} 沒有 {permission} 權限"}), 403

            return func(*args, **kwargs)

        return wrapper

    return decorator


def dicom_tags(resource: dict, key: str):
    return resource.get(key, {}) or {}


def convert_to_png_bytes(content: bytes):
    """
    Convert Orthanc image response to PNG.
    This helps when Orthanc returns non-browser-friendly image formats.
    """
    if Image is None:
        return None

    try:
        image = Image.open(BytesIO(content))
        image.load()

        if image.mode not in ("RGB", "RGBA"):
            image = image.convert("RGB")

        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except Exception:
        return None


def make_image_response(content: bytes, source_path: str):
    """
    Return image response.
    Prefer converting to PNG to avoid browser preview failures.
    """
    png_content = convert_to_png_bytes(content)
    if png_content:
        response = Response(png_content, mimetype="image/png")
    else:
        response = Response(content, mimetype="image/png")

    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["X-Preview-Source"] = source_path
    return response


# -------------------------
# Page
# -------------------------
@app.route("/")
def index():
    return render_template("index.html")


# -------------------------
# Auth APIs
# -------------------------
@app.route("/api/health", methods=["GET"])
def health_check():
    """檢查 Flask 後端與 Orthanc 伺服器健康狀態 (保留組員 A 原本功能)"""
    status = {"backend": "ok", "orthanc": "unknown"}
    orthanc_error = None

    try:
        resp = orthanc_request("GET", "/system")
        if resp.status_code == 200:
            status["orthanc"] = "ok"
    except Exception as exc:
        status["orthanc"] = "error"
        orthanc_error = str(exc)

    return jsonify({"ok": True, "status": status, "orthanc_error": orthanc_error})

@app.route("/api/users/registered", methods=["GET"])
def get_registered_users():
    """取得『已經綁定生物特徵』的帳號清單，供前端自動聯想與智慧按鈕判斷使用"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT username FROM credentials")
        rows = c.fetchall()
        conn.close()
        
        # 轉換成單純的字串陣列，例如 ["doctor1", "admin1"]
        users = [row[0] for row in rows]
        return jsonify({"ok": True, "users": users})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500


# -------------------------------------------------------------
# FIDO2 階段一：人臉綁定註冊 (Registration)
# -------------------------------------------------------------

@app.route('/api/register/begin', methods=['POST'])
def register_begin():
    try:
        # 1. 接收前端傳來的帳號
        data = request.json
        username = data.get('username')
        
        if not username:
            return jsonify({"ok": False, "status": "error", "message": "請輸入操作帳號"}), 400

        # 【全新修改】去 SQLite 的 users 表檢查這個帳號是否合法
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT 1 FROM users WHERE username=?", (username,))
        user_exists = c.fetchone()
        conn.close()

        if not user_exists:
            return jsonify({"ok": False, "status": "error", "message": "無效的帳號：系統中查無此人員"}), 400

        # 2. 動態建立 user 資訊
        # 將字串編碼為 bytes 作為 FIDO2 的內部 ID
        user_id_bytes = username.encode('utf-8')
        user = {'id': user_id_bytes, 'name': username, 'displayName': f"{username} (FIDO2)"}
        
        registration_data, state = server.register_begin(user)
        
        # 3. 用動態 username 作為 Key 來存狀態
        states[username] = state 
        
        output = registration_data['publicKey']
        output['challenge'] = to_websafe(output['challenge'])
        output['user']['id'] = to_websafe(output['user']['id'])
        
        return jsonify(output)
    except Exception as e:
        return jsonify({"ok": False, "status": "error", "message": str(e)}), 500


@app.route('/api/register/complete', methods=['POST'])
def register_complete():
    credential_data = request.json
    username = credential_data.get('username')
    state = states.get(username)
    
    if not state:
        return jsonify({"ok": False, "status": "error", "message": "狀態已過期，請重試"}), 400

    try:
        # 校驗 FIDO2 註冊資料
        auth_data = server.register_complete(state, credential_data)
        cred_bytes = auth_data.credential_data
        cred_b64 = websafe_encode(cred_bytes)
        
        # 寫入 SQLite 資料庫
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("REPLACE INTO credentials (username, credential_data) VALUES (?, ?)", (username, cred_b64))
        
        # 自動登入
        c.execute("SELECT role FROM users WHERE username=?", (username,))
        user_row = c.fetchone()
        conn.commit()
        conn.close()

        del states[username] 
        
        if not user_row:
            return jsonify({"ok": False, "status": "error", "message": "資料庫查無此使用者的權限設定"}), 500
            
        # 核發 Session 通行證
        real_role = user_row[0] 
        session["user"] = {"username": username, "role": real_role}
        
        print(f"✅ FIDO2 註冊成功並自動登入：{username} (權限: {real_role})")
        write_log("REGISTER", f"FIDO2 註冊並自動登入成功 (指派角色: {real_role})")
        
        # 【修改回傳格式】比照 authenticate_complete，把 user 資料傳給前端
        return jsonify({
            "ok": True,
            "status": "success",
            "message": "FIDO2 註冊並自動登入成功",
            "user": session["user"],
            "permissions": get_permissions(real_role),
            "role_label": ROLE_LABELS.get(real_role, real_role)
        })
        
    except Exception as e:
        print(f"❌ 註冊失敗: {e}")
        return jsonify({"ok": False, "status": "error", "message": str(e)}), 400


# -------------------------------------------------------------
# FIDO2 階段二：身分驗證登入 (Authentication)
# -------------------------------------------------------------

@app.route('/api/authenticate/begin', methods=['POST'])
def authenticate_begin():
    data = request.json
    username = data.get('username')
    
    # 【修改】從 SQLite 讀取公鑰
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT credential_data FROM credentials WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if not row:
        return jsonify({"ok": False, "status": "error", "message": f"帳號 [{username}] 尚未綁定人臉，請先註冊"}), 404

    # 將字串還原為 FIDO2 套件看得懂的格式
    cred_bytes = websafe_decode(row[0])
    # 注意：fido2 authenticate_begin 參數要求的是 list
    credentials = [AttestedCredentialData(cred_bytes)] 

    # 發起驗證挑戰
    auth_data, state = server.authenticate_begin(credentials)
    states[username] = state 
    
    output = auth_data['publicKey']
    output['challenge'] = to_websafe(output['challenge'])
    if 'allowCredentials' in output:
        for cred in output['allowCredentials']:
            cred['id'] = to_websafe(cred['id'])
            
    return jsonify(output)


@app.route('/api/authenticate/complete', methods=['POST'])
def authenticate_complete():
    credential_data = request.json
    username = credential_data.get('username')
    state = states.get(username)
    
    if not state:
        return jsonify({"ok": False, "status": "error", "message": "驗證會話已過期"}), 400
    
    # 【修改】從 SQLite 讀取公鑰
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT credential_data FROM credentials WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()

    if not row:
         return jsonify({"ok": False, "status": "error", "message": "找不到公鑰資料"}), 404
         
    cred_bytes = websafe_decode(row[0])
    credentials = [AttestedCredentialData(cred_bytes)]
    
    try:
        # 校驗人臉簽章
        server.authenticate_complete(state, credentials, credential_data)
        del states[username] 
        
        #【全新架構】從 SQLite 資料庫的 users 表中查詢該人員的角色
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT role FROM users WHERE username=?", (username,))
        user_row = c.fetchone()
        conn.close()

        if not user_row:
            return jsonify({"ok": False, "status": "error", "message": "資料庫查無此使用者的權限設定"}), 500
            
        real_role = user_row[0] # 從資料庫拿到的真實角色 (doctor, admin, patient)
        session["user"] = {"username": username, "role": real_role}
        
        print(f"🔓 FIDO2 驗證成功：已登入 {username} (權限: {real_role})")
        write_log("LOGIN", f"FIDO2 登入成功 (權限: {real_role})")
        
        return jsonify({
            "ok": True,
            "status": "success",
            "message": "FIDO2 生物辨識成功",
            "user": session["user"],
            "permissions": get_permissions(real_role),
            "role_label": ROLE_LABELS.get(real_role, real_role)
        })
    except Exception as e:
        print(f"❌ 驗證失敗: {e}")
        return jsonify({"ok": False, "status": "error", "message": str(e)}), 400


# -------------------------------------------------------------
# 登出與當前使用者查詢 (保留並微調組員 A 功能以相容前端)
# -------------------------------------------------------------

@app.route("/api/logout", methods=["POST"])
@login_required
def logout():
    """登出系統，可選擇是否重置 guest 帳號"""
    user = session.get("user", {})
    username = user.get("username")
    
    # 接收前端傳來的選項 (如果沒傳，預設為 False 不重置)
    data = request.get_json(silent=True) or {}
    reset_guest = data.get("reset_guest", False)
    
    # 必須是 guest「而且」前端有要求重置，才刪除公鑰
    if username == "guest" and reset_guest:
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("DELETE FROM credentials WHERE username=?", (username,))
            conn.commit()
            conn.close()
            print("🔄 公共帳號 guest 已登出，且人臉資料已成功重置！")
        except Exception as e:
            print(f"guest 重置失敗: {e}")

    session.clear()
    write_log("LOGOUT", f"登出系統" + (" (並重置了 guest 人臉)" if username == "guest" and reset_guest else ""))
    return jsonify({"ok": True, "message": "已登出"})


@app.route("/api/me", methods=["GET"])
def me():
    """查詢當前身分，供前端更新角色面板使用"""
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "user": None}), 401

    role = user.get("role", "")
    return jsonify(
        {
            "ok": True,
            "user": user,
            "permissions": get_permissions(role),
            "role_label": ROLE_LABELS.get(role, role),
        }
    )


# -------------------------
# Orthanc read APIs
# -------------------------
@app.route("/api/studies", methods=["GET"])
@login_required
@permission_required("view")
def get_studies():
    try:
        # 1. 先抓出現在是誰在看清單 (轉小寫方便比對)
        user = session.get("user", {})
        username = user.get("username", "").lower()
        role = user.get("role", "")

        study_ids = orthanc_request("GET", "/studies").json()
        studies = []

        for study_id in study_ids:
            detail = orthanc_request("GET", f"/studies/{study_id}").json()
            main_tags = dicom_tags(detail, "MainDicomTags")
            patient_tags = dicom_tags(detail, "PatientMainDicomTags")
            
            # 2. 【資料隔離核心邏輯】
            # 抓出 DICOM 裡面的病患 ID 與 姓名
            dicom_patient_name = patient_tags.get("PatientName", "").lower()
            dicom_patient_id = patient_tags.get("PatientID", "").lower()

            # 如果登入的是「病患(patient)」，進行嚴格審查
            if role == "patient":
                # 如果該病患的帳號 (例如 patient1) 不在 DICOM 的姓名或 ID 裡
                if username != dicom_patient_name and username != dicom_patient_id:
                    continue  # 🚫 直接跳過這筆資料，不給他看！(不會加進 studies 清單)

            # 計算張數與重組資料
            series_ids = detail.get("Series", []) or []
            instances_count = 0
            cover_instance_id = None # 用來存封面圖片的 ID

            for series_id in series_ids:
                series_detail = orthanc_request("GET", f"/series/{series_id}").json()
                # 1. 先明確定義 instances 變數，把該 series 裡面的圖片陣列抓出來
                instances = series_detail.get("Instances", []) or []
                # 2. 透過剛剛定義的 instances 來計算數量並累加
                instances_count += len(instances)
                # 3. 如果這個 series 有圖片，且我們還沒拿到封面，就拿第一張當封面
                if instances and not cover_instance_id:
                    cover_instance_id = instances[0]

            studies.append(
                {
                    "id": study_id,
                    "study_instance_uid": main_tags.get("StudyInstanceUID", ""),
                    "study_date": main_tags.get("StudyDate", ""),
                    "study_description": main_tags.get("StudyDescription", ""),
                    "accession_number": main_tags.get("AccessionNumber", ""),
                    "patient_name": patient_tags.get("PatientName", ""),
                    "patient_id": patient_tags.get("PatientID", ""),
                    "instances_count": instances_count,
                    "series_count": len(series_ids),
                    "cover_instance_id": cover_instance_id # 把封面 ID 傳給前端
                }
            )

        # 依照日期排序
        studies.sort(key=lambda item: item.get("study_date", ""), reverse=True)
        return jsonify({"ok": True, "data": studies})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"Orthanc 讀取失敗：{exc}"}), 500

@app.route("/api/studies/<study_id>/modify", methods=["POST"])
@login_required
def modify_dicom_name(study_id):
    """修改 DICOM 檔案名稱 (單純改名)"""
    user = session.get("user", {})
    role = user.get("role", "")
    
    if role not in ["admin", "doctor"]:
        return jsonify({"ok": False, "message": "安全限制：您沒有權限修改醫療影像紀錄"}), 403
        
    data = request.get_json(silent=True) or {}
    new_name = data.get("new_patient_name")
    
    if not new_name:
        return jsonify({"ok": False, "message": "請提供新的病患名稱"}), 400

    # 1. 準備修改指令：這次我們「只」替換名字，絕對不要放 PatientID
    payload = {
        "Replace": {
            "PatientName": new_name,
            "SpecificCharacterSet": "ISO_IR 192" 
        },
        "Force": True,       
        "KeepSource": False  
    }

    try:
        orthanc_url = os.getenv('ORTHANC_URL', 'http://127.0.0.1:8042')
        auth = (os.getenv('ORTHANC_USERNAME', ''), os.getenv('ORTHANC_PASSWORD', ''))
        
        # 2. 對 studies 發出請求 (Orthanc 會放行，因為我們沒有動到 ID)
        resp = requests.post(
            f"{orthanc_url}/studies/{study_id}/modify",
            json=payload,
            auth=auth,
            verify=False
        )
        
        if not resp.ok:
            error_detail = resp.text
            try:
                error_detail = resp.json()
            except:
                pass
            return jsonify({"ok": False, "message": f"Orthanc 拒絕修改: {error_detail}"}), 400
            
        # 3. 成功的話，寫入日誌並回傳
        write_log("RENAME_STUDY", f"將病歷 (Study ID: {study_id}) 名稱修改為 [{new_name}]")
        return jsonify({"ok": True, "message": f"已成功將病患名稱修改為 [{new_name}]！"})
        
    except Exception as e:
        return jsonify({"ok": False, "message": f"網路或系統嚴重錯誤: {e}"}), 500

@app.route("/api/studies/<study_id>", methods=["GET"])
@login_required
@permission_required("view")
def get_study_detail(study_id):
    try:
        detail = orthanc_request("GET", f"/studies/{study_id}").json()
        main_tags = dicom_tags(detail, "MainDicomTags")
        patient_tags = dicom_tags(detail, "PatientMainDicomTags")
        series_ids = detail.get("Series", []) or []

        instances = []

        for series_id in series_ids:
            series_detail = orthanc_request("GET", f"/series/{series_id}").json()
            series_tags = dicom_tags(series_detail, "MainDicomTags")

            for instance_id in series_detail.get("Instances", []) or []:
                ins_detail = orthanc_request("GET", f"/instances/{instance_id}").json()
                tags = dicom_tags(ins_detail, "MainDicomTags")

                instances.append(
                    {
                        "id": instance_id,
                        "series_id": series_id,
                        "series_description": series_tags.get("SeriesDescription", ""),
                        "series_number": series_tags.get("SeriesNumber", ""),
                        "sop_instance_uid": tags.get("SOPInstanceUID", ""),
                        "instance_number": tags.get("InstanceNumber", ""),
                        "modality": tags.get("Modality", ""),
                        "content_date": tags.get("ContentDate", ""),
                        "content_time": tags.get("ContentTime", ""),
                    }
                )

        instances.sort(key=lambda item: str(item.get("series_number", "")) + "-" + str(item.get("instance_number", "")))

        study_info = {
            "id": study_id,
            "patient_name": patient_tags.get("PatientName", ""),
            "patient_id": patient_tags.get("PatientID", ""),
            "study_date": main_tags.get("StudyDate", ""),
            "study_description": main_tags.get("StudyDescription", ""),
            "study_instance_uid": main_tags.get("StudyInstanceUID", ""),
            "instances_count": len(instances),
        }
        write_log("VIEW_STUDY", f"查閱了病患 [{study_info.get('patient_name')}] 的影像紀錄 (Study ID: {study_id})")
        return jsonify({"ok": True, "study": study_info, "instances": instances})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"讀取 Study 詳細資料失敗：{exc}"}), 500


@app.route("/api/instances/<instance_id>/preview", methods=["GET"])
@login_required
@permission_required("view")
def get_instance_preview(instance_id):
    """
    Robust DICOM preview endpoint.

    It tries several Orthanc preview/image endpoints.
    If Orthanc returns an image format that the browser may not display,
    Pillow converts it to PNG before sending it to the frontend.
    """
    preview_paths = [
        f"/instances/{instance_id}/preview",
        f"/instances/{instance_id}/frames/0/preview",
        f"/instances/{instance_id}/image-uint8",
        f"/instances/{instance_id}/rendered",
        f"/instances/{instance_id}/frames/0/rendered",
    ]

    errors = []

    for path in preview_paths:
        try:
            resp = orthanc_request("GET", path, timeout=60)
            if not resp.content:
                errors.append(f"{path}：空白內容")
                continue

            image_response = make_image_response(resp.content, path)
            return image_response

        except requests.RequestException as exc:
            errors.append(f"{path}：{exc}")

    return jsonify(
        {
            "ok": False,
            "message": "此 DICOM 目前無法預覽，可能是 Orthanc 無法產生預覽圖、影像格式不支援，或該 instance 沒有可顯示的像素資料。",
            "attempts": errors,
        }
    ), 500


@app.route("/api/instances/<instance_id>/download", methods=["GET"])
@login_required
@permission_required("view")
def download_instance(instance_id):
    try:
        resp = orthanc_request("GET", f"/instances/{instance_id}/file", timeout=60)
        write_log("DOWNLOAD_DICOM", f"下載了 DICOM 原始檔案 (Instance ID: {instance_id})")
        return send_file(
            BytesIO(resp.content),
            mimetype="application/dicom",
            as_attachment=True,
            download_name=f"{instance_id}.dcm",
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 下載失敗：{exc}"}), 500

# ==========================================
# 管理員專屬 API (後台帳號管理系統)
# ==========================================

@app.route("/api/admin/users", methods=["GET"])
@login_required
@permission_required("manage_users")
def get_all_users():
    """1. 取得系統內所有使用者名單與角色"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # 查詢 users 表，並看看 credentials 表有沒有綁定人臉 (用 LEFT JOIN)
        c.execute('''
            SELECT u.username, u.role, 
                   CASE WHEN c.credential_data IS NOT NULL THEN 1 ELSE 0 END as has_fido 
            FROM users u
            LEFT JOIN credentials c ON u.username = c.username
        ''')
        
        users_list = []
        for row in c.fetchall():
            users_list.append({
                "username": row[0],
                "role": row[1],
                "has_fido": bool(row[2]) # 讓前端知道這個人綁過臉了沒
            })
            
        conn.close()
        return jsonify({"ok": True, "users": users_list})
    except Exception as e:
        return jsonify({"ok": False, "message": f"讀取失敗：{str(e)}"}), 500


@app.route("/api/admin/users", methods=["POST"])
@login_required
@permission_required("manage_users")
def upsert_user():
    """2. 新增帳號，或修改現有帳號的角色"""
    data = request.json
    target_username = data.get("username")
    target_role = data.get("role")
    
    if not target_username or not target_role:
        return jsonify({"ok": False, "message": "必須提供 username 與 role"}), 400
        
    if target_role not in ROLE_PERMISSIONS:
        return jsonify({"ok": False, "message": f"無效的角色！只能是: {list(ROLE_PERMISSIONS.keys())}"}), 400

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # 使用 REPLACE，如果帳號存在就更新 role，不存在就新增
        c.execute("REPLACE INTO users (username, role) VALUES (?, ?)", (target_username, target_role))
        conn.commit()
        conn.close()
        write_log("ADMIN_UPSERT_USER", f"設定帳號 [{target_username}] 為 [{target_role}] 角色")
        return jsonify({"ok": True, "message": f"成功設定帳號 [{target_username}] 為 [{target_role}] 角色！"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"設定失敗：{str(e)}"}), 500


@app.route("/api/admin/users/<target_username>", methods=["DELETE"])
@login_required
@permission_required("manage_users")
def delete_user(target_username):
    """3. 徹底刪除帳號與其綁定的人臉公鑰"""
    # 保護機制：不能刪除自己
    if session.get("user", {}).get("username") == target_username:
        return jsonify({"ok": False, "message": "安全限制：管理員無法刪除自己的帳號"}), 403

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # 刪除權限表的資料
        c.execute("DELETE FROM users WHERE username=?", (target_username,))
        # 聯動刪除人臉公鑰表的資料 (重要！避免留下幽靈公鑰)
        c.execute("DELETE FROM credentials WHERE username=?", (target_username,))
        
        conn.commit()
        conn.close()
        write_log("ADMIN_DELETE_USER", f"徹底刪除帳號 [{target_username}] 與其人臉綁定紀錄")
        return jsonify({"ok": True, "message": f"已徹底刪除帳號 [{target_username}] 與其人臉綁定紀錄"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"刪除失敗：{str(e)}"}), 500
    
@app.route("/api/admin/users/<target_username>/fido", methods=["DELETE"])
@login_required
@permission_required("manage_users")
def reset_user_fido(target_username):
    """4. 僅清除人臉綁定紀錄，保留帳號與角色"""
    # 保護機制：不能重置自己，以免管理員把自己鎖在門外
    if session.get("user", {}).get("username") == target_username:
        return jsonify({"ok": False, "message": "安全限制：管理員無法重置自己的人臉"}), 403

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # 僅刪除 credentials (公鑰) 表中的資料
        c.execute("DELETE FROM credentials WHERE username=?", (target_username,))
        conn.commit()
        conn.close()
        write_log("ADMIN_RESET_FIDO", f"清除了 [{target_username}] 的人臉綁定紀錄")
        return jsonify({"ok": True, "message": f"已成功清除 [{target_username}] 的人臉綁定紀錄，該帳號下次登入將重新啟動註冊流程。"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"清除失敗：{str(e)}"}), 500

@app.route("/api/admin/logs", methods=["GET"])
@login_required
@permission_required("manage_users")
def get_system_logs():
    """取得系統稽核日誌 (僅限管理員)"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # 抓取最新的 100 筆紀錄
        c.execute("SELECT timestamp, username, action, details, ip_address FROM system_logs ORDER BY id DESC LIMIT 100")
        rows = c.fetchall()
        conn.close()
        
        logs = []
        for row in rows:
            logs.append({
                "timestamp": row[0],
                "username": row[1],
                "action": row[2],
                "details": row[3],
                "ip_address": row[4]
            })
        return jsonify({"ok": True, "logs": logs})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

# -------------------------
# Role-based write APIs
# -------------------------
@app.route("/api/instances/upload", methods=["POST"])
@login_required
@permission_required("upload")
def upload_instance():
    if "dicom" not in request.files:
        return jsonify({"ok": False, "message": "請先選擇 DICOM 檔案"}), 400

    file = request.files["dicom"]
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "請先選擇 DICOM 檔案"}), 400

    try:
        uploaded = orthanc_request(
            "POST",
            "/instances",
            data=file.read(),
            headers={"Content-Type": "application/dicom"},
            timeout=60,
        ).json()
        write_log("UPLOAD_DICOM", f"上傳了新的 DICOM 檔案 (Orthanc ID: {uploaded.get('ID')})")
        return jsonify({"ok": True, "message": "DICOM 加入成功", "orthanc": uploaded})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 加入失敗：{exc}"}), 500


@app.route("/api/instances/<instance_id>/modify", methods=["POST"])
@login_required
@permission_required("modify")
def modify_instance(instance_id):
    data = request.get_json(silent=True) or {}

    replace = {}
    patient_name = (data.get("patient_name") or "").strip()
    study_description = (data.get("study_description") or "").strip()

    if patient_name:
        replace["PatientName"] = patient_name

    if study_description:
        replace["StudyDescription"] = study_description

    if not replace:
        return jsonify({"ok": False, "message": "至少要填一個欄位才能修改"}), 400

    payload = {
        "Replace": replace,
        "RemovePrivateTags": bool(data.get("remove_private_tags", False)),
    }

    try:
        modified_file = orthanc_request(
            "POST",
            f"/instances/{instance_id}/modify",
            json=payload,
            timeout=60,
        ).content

        uploaded = orthanc_request(
            "POST",
            "/instances",
            data=modified_file,
            headers={"Content-Type": "application/dicom"},
            timeout=60,
        ).json()

        deleted_original = False
        if data.get("replace_original", True):
            orthanc_request("DELETE", f"/instances/{instance_id}", timeout=60)
            deleted_original = True
            
        write_log("MODIFY_INSTANCE", f"修改了 DICOM 內部標籤 (Instance ID: {instance_id})")
        return jsonify(
            {
                "ok": True,
                "message": "DICOM 修改成功",
                "new_resource": uploaded,
                "deleted_original": deleted_original,
            }
        )

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 修改失敗：{exc}"}), 500

@app.route('/api/pi/upload', methods=['POST'])
def pi_upload():
    """接收樹莓派上傳的普通照片 (使用 HMAC 動態簽章驗證)"""
    
    # 1. 從環境變數讀取Secret Key
    secret_key = os.getenv("PI_API_SECRET")
    if not secret_key:
        write_log("PI_UPLOAD_ERROR", "伺服器環境變數未設定 PI_API_SECRET")
        return jsonify({"ok": False, "message": "伺服器環境變數未設定 PI_API_SECRET"}), 500

    # 2. 取得樹莓派傳來的「時間戳記」與「動態簽名」
    client_timestamp = request.headers.get('X-Pi-Timestamp')
    client_signature = request.headers.get('X-Pi-Signature')

    if not client_timestamp or not client_signature:
        write_log("PI_UPLOAD_FAILED", "拒絕存取：缺少 HMAC 安全憑證")
        return jsonify({"ok": False, "message": "缺少 HMAC 安全憑證，拒絕存取"}), 403

    # 3. 【防禦重播攻擊】檢查時間戳記是否超過 2 分鐘 (120秒)
    try:
        client_time_int = int(client_timestamp)
        current_time = int(time.time())
        if abs(current_time - client_time_int) > 120:
            write_log("PI_UPLOAD_FAILED", f"拒絕存取：請求已過期 (攔截到重播攻擊，時間戳記: {client_timestamp})")
            return jsonify({"ok": False, "message": "請求已過期 (防止重播攻擊攔截)"}), 403
    except ValueError:
        write_log("PI_UPLOAD_FAILED", "時間戳記格式錯誤")
        return jsonify({"ok": False, "message": "時間戳記格式錯誤"}), 400

    # 4. 伺服器自己算一次簽名
    # 規則：用 Secret Key 把 Timestamp 攪拌均勻 (SHA256)
    message = str(client_timestamp).encode('utf-8')
    secret = secret_key.encode('utf-8')
    expected_signature = hmac.new(secret, message, hashlib.sha256).hexdigest()

    # 5. 【防止時間差攻擊】使用 compare_digest 安全比對兩邊的簽名
    if not hmac.compare_digest(expected_signature, client_signature):
        write_log("PI_UPLOAD_FAILED", "拒絕存取：HMAC 簽章驗證失敗 (假冒的機器連線)")
        return jsonify({"ok": False, "message": "HMAC 簽章驗證失敗，你是假冒的機器！"}), 403

    # ============ 驗證通過！下面是圖片轉換邏輯 ============
    
    if 'image' not in request.files:
        write_log("PI_UPLOAD_ERROR", "已通過驗證，但未附帶圖片檔案")
        return jsonify({"ok": False, "message": "沒有找到圖片檔案"}), 400
        
    file = request.files['image']
    img_base64 = base64.b64encode(file.read()).decode('utf-8')
    img_data_uri = f"data:image/jpeg;base64,{img_base64}"

    # 動態接收病患資訊 (如果樹莓派沒傳，就用預設值防呆)
    patient_id = request.form.get("patient_id", "UNKNOWN-PI-001")
    patient_name = request.form.get("patient_name", "未命名病患(來自樹莓派)")
    
    orthanc_url = os.getenv('ORTHANC_URL', 'http://127.0.0.1:8042')
    auth = (os.getenv('ORTHANC_USERNAME', ''), os.getenv('ORTHANC_PASSWORD', ''))
    
    # 將接收到的資訊填入 DICOM 標籤
    payload = {
        "Tags": {
            "SpecificCharacterSet": "ISO_IR 192",  # ISO_IR 192 就是 DICOM 裡的 UTF-8
            "PatientName": patient_name,
            "PatientID": patient_id,
            "StudyDescription": "樹莓派採集影像"
        },
        "Content": img_data_uri
    }
    
    try:
        resp = requests.post(f"{orthanc_url}/tools/create-dicom", json=payload, auth=auth, verify=ORTHANC_VERIFY_SSL)
        if resp.ok:
            write_log("PI_UPLOAD_SUCCESS", "HMAC 驗證通過！樹莓派影像已成功轉為 DICOM 並存入資料庫")
            return jsonify({"ok": True, "message": "HMAC 驗證通過！樹莓派影像已成功存入資料庫！"})
        else:
            write_log("PI_UPLOAD_ERROR", f"Orthanc 處理失敗: {resp.text}")
            return jsonify({"ok": False, "message": f"Orthanc 處理失敗: {resp.text}"}), 500
    except Exception as e:
        write_log("PI_UPLOAD_ERROR", f"系統錯誤: {e}")
        return jsonify({"ok": False, "message": f"系統錯誤: {e}"}), 500

# ==========================================
# 邊緣設備 (IoT) 狀態管理模組
# ==========================================
# 記憶體全域變數，避免頻繁讀寫 SQLite 資料庫
pi_status = {
    "status": "idle",       # "idle" (待機) 或 "ready" (可拍照)
    "patient_id": "",
    "patient_name": ""
}

@app.route('/api/pi/trigger', methods=['POST'])
@login_required
def trigger_pi():
    user_data = session.get('user', {})
    current_role = user_data.get('role')

    # 權限檢查
    if current_role != 'patient':
        return jsonify({"ok": False, "message": "權限不足：僅限病患操作影像擷取！"}), 403

    data = request.get_json() or {}
    
    patient_id = data.get("patient_id") or user_data.get("username", "PATIENT-001")
    patient_name = data.get("patient_name") or "病患本人"

    pi_status["status"] = "ready"
    pi_status["patient_id"] = patient_id
    pi_status["patient_name"] = patient_name

    return jsonify({"ok": True, "message": "授權成功，邊緣設備準備拍攝中！"})

@app.route('/api/pi/status', methods=['GET'])
def get_pi_status():
    """供樹莓派每 2 秒輪詢一次目前指令"""
    return jsonify(pi_status)

@app.route('/api/pi/status/reset', methods=['POST'])
def reset_pi_status():
    """樹莓派上傳完成後，重置狀態回待機模式"""
    pi_status["status"] = "idle"
    pi_status["patient_id"] = ""
    pi_status["patient_name"] = ""
    return jsonify({"ok": True})

@app.route("/api/instances/<instance_id>", methods=["DELETE"])
@login_required
@permission_required("delete")
def delete_instance(instance_id):
    try:
        orthanc_request("DELETE", f"/instances/{instance_id}", timeout=60)
        write_log("DELETE_DICOM", f"刪除了 DICOM 檔案 (Instance ID: {instance_id})")
        return jsonify({"ok": True, "message": "DICOM 已移除"})
    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 移除失敗：{exc}"}), 500


if __name__ == "__main__":
    # 自動判斷要綁定在哪個 IP (雲端用 0.0.0.0，本機用 127.0.0.1)
    server_host = os.getenv("FLASK_HOST", "127.0.0.1")
    
    # 從環境變數讀取憑證路徑
    cert_path = os.getenv("SSL_CERT_PATH", "")
    key_path = os.getenv("SSL_KEY_PATH", "")

    # 自動偵測：如果環境變數有給憑證路徑，而且檔案真的存在，才啟動 HTTPS
    if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
        print(f"🚀 啟動正式環境 (HTTPS) | Host: {server_host} | Domain: {RP_ID}")
        app.run(host=server_host, port=5000, ssl_context=(cert_path, key_path), debug=IS_DEVELOPMENT)
    else:
        print(f"🔧 啟動開發環境 (HTTP) | Host: {server_host} | Domain: {RP_ID}")
        app.run(host=server_host, port=5000, debug=IS_DEVELOPMENT)
