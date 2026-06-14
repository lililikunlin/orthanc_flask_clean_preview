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

rp = PublicKeyCredentialRpEntity(id="telemed-sec.duckdns.org", name="遠距醫療安全網關")
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS credentials (
            username TEXT PRIMARY KEY,
            credential_data TEXT NOT NULL
        )
    ''')
    
    # 表格 2：users (負責 Authorization 角色權限)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            role TEXT NOT NULL
        )
    ''')
    
    # 【自動植入】如果 users 表是空的，就自動建立預設的三個角色
    c.execute("SELECT count(*) FROM users")
    if c.fetchone()[0] == 0:
        c.executemany("INSERT INTO users (username, role) VALUES (?, ?)", [
            ("patient1", "patient"),
            ("doctor1", "doctor"),
            ("admin1", "admin")
        ])
        
    conn.commit()
    conn.close()

# 啟動時立刻初始化資料庫
init_db()

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
        
        # 【修改】將公鑰資料 (bytes) 轉為可儲存的 Base64 字串
        cred_bytes = auth_data.credential_data
        cred_b64 = websafe_encode(cred_bytes)
        
        # 【修改】寫入 SQLite 資料庫 (如果帳號已存在則覆蓋更新)
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("REPLACE INTO credentials (username, credential_data) VALUES (?, ?)", (username, cred_b64))
        conn.commit()
        conn.close()

        del states[username] 
        print(f"✅ FIDO2 註冊成功：已在 SQLite 儲存 {username} 的公鑰")
        return jsonify({"ok": True, "status": "success"})
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
    """登出系統，全面清空 Session 授權狀態"""
    session.clear()
    return jsonify({"ok": True, "message": "已登出安全網關"})


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
        study_ids = orthanc_request("GET", "/studies").json()
        studies = []

        for study_id in study_ids:
            detail = orthanc_request("GET", f"/studies/{study_id}").json()
            main_tags = dicom_tags(detail, "MainDicomTags")
            patient_tags = dicom_tags(detail, "PatientMainDicomTags")
            series_ids = detail.get("Series", []) or []

            instances_count = 0
            for series_id in series_ids:
                series_detail = orthanc_request("GET", f"/series/{series_id}").json()
                instances_count += len(series_detail.get("Instances", []) or [])

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
                }
            )

        studies.sort(key=lambda item: item.get("study_date", ""), reverse=True)
        return jsonify({"ok": True, "data": studies})

    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"Orthanc 讀取失敗：{exc}"}), 500


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
        
        return jsonify({"ok": True, "message": f"已徹底刪除帳號 [{target_username}] 與其人臉綁定紀錄"})
    except Exception as e:
        return jsonify({"ok": False, "message": f"刪除失敗：{str(e)}"}), 500

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


@app.route("/api/instances/<instance_id>", methods=["DELETE"])
@login_required
@permission_required("delete")
def delete_instance(instance_id):
    try:
        orthanc_request("DELETE", f"/instances/{instance_id}", timeout=60)
        return jsonify({"ok": True, "message": "DICOM 已移除"})
    except requests.RequestException as exc:
        return jsonify({"ok": False, "message": f"DICOM 移除失敗：{exc}"}), 500


if __name__ == "__main__":
    is_debug_mode = os.getenv("FLASK_DEBUG", "true").lower() in ["true", "1"]
    cert_path = '/etc/letsencrypt/live/telemed-sec.duckdns.org/fullchain.pem'
    key_path = '/etc/letsencrypt/live/telemed-sec.duckdns.org/privkey.pem'
    app.run(host="0.0.0.0", port=5000, ssl_context=(cert_path, key_path), debug=True)
